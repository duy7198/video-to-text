"""
Transcription pipeline.

Flow
----
1. Try yt-dlp to download the video (works for YouTube, regular TikTok, many others).
2. Whisper transcribes the audio with automatic language detection.
3. Fallback: if yt-dlp fails with "Unsupported URL" + "/photo/" (TikTok photo
   slideshow), fetch the post HTML, parse the embedded rehydration JSON to find
   the image URLs, download them, and run EasyOCR. No browser needed.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Callable, Optional

WHISPER_MODEL_NAME = os.environ.get("WHISPER_MODEL", "small")
YTDLP_TIMEOUT = int(os.environ.get("YTDLP_TIMEOUT", "600"))

# Lazy-loaded singletons. Model loading is expensive; load once per process.
_whisper_model = None
_whisper_lock = threading.Lock()

_ocr_reader = None
_ocr_reader_key: Optional[tuple] = None
_ocr_lock = threading.Lock()


class PhotoPostError(Exception):
    """Raised when yt-dlp refuses a TikTok /photo/ URL (slideshow post)."""


def _load_whisper():
    """Load Whisper model once. Subsequent calls return the cached model."""
    global _whisper_model
    with _whisper_lock:
        if _whisper_model is None:
            import whisper  # heavy import, keep it lazy
            cache_dir = os.environ.get("WHISPER_CACHE_DIR")
            _whisper_model = whisper.load_model(
                WHISPER_MODEL_NAME, download_root=cache_dir
            )
    return _whisper_model


def _load_ocr_reader(langs: list[str]):
    """Load EasyOCR once per unique language tuple. Rebuild if langs change."""
    global _ocr_reader, _ocr_reader_key
    key = tuple(sorted(langs))
    with _ocr_lock:
        if _ocr_reader is None or _ocr_reader_key != key:
            import easyocr
            _ocr_reader = easyocr.Reader(list(langs), gpu=False, verbose=False)
            _ocr_reader_key = key
    return _ocr_reader


def _noop(_msg: str) -> None:
    pass


def detect_language(text: str) -> str:
    """Identify language from extracted text. Returns ISO code or 'unknown'."""
    if not text or len(text.strip()) < 5:
        return "unknown"
    try:
        from langdetect import detect, DetectorFactory
        DetectorFactory.seed = 0  # deterministic results
        return detect(text)
    except Exception:
        return "unknown"


def _download_with_ytdlp(url: str, out_dir: Path, progress_cb: Callable[[str], None]) -> Path:
    """Download a video with yt-dlp. Returns path to the .mp4 file."""
    progress_cb("Downloading media with yt-dlp...")
    out_template = str(out_dir / "video.%(ext)s")
    result = subprocess.run(
        [
            "yt-dlp",
            "--no-warnings",
            "--no-check-certificates",
            "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--merge-output-format", "mp4",
            "-o", out_template,
            url,
        ],
        capture_output=True,
        text=True,
        timeout=YTDLP_TIMEOUT,
    )

    if result.returncode != 0:
        stderr = result.stderr or ""
        if "Unsupported URL" in stderr and "/photo/" in stderr:
            raise PhotoPostError("TikTok photo post (slideshow)")
        tail = stderr.strip().splitlines()[-3:] if stderr else ["unknown error"]
        raise RuntimeError("yt-dlp failed: " + " | ".join(tail))

    for p in sorted(out_dir.iterdir()):
        if p.name.startswith("video.") and p.stat().st_size > 1024:
            return p
    raise RuntimeError("yt-dlp succeeded but no output file found")


def _fetch_tiktok_photo_urls(
    url: str, progress_cb: Callable[[str], None]
) -> list[str]:
    """Extract image URLs from a TikTok photo post without a browser.

    TikTok embeds the entire post data as JSON inside a
    ``<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">`` tag in the initial HTML.
    We just need a plain HTTP request with browser-like headers; no JS render,
    no Chromium, no Playwright.
    """
    import re
    import json
    import requests

    progress_cb("Fetching TikTok post HTML...")
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.tiktok.com/",
    }
    resp = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
    resp.raise_for_status()

    m = re.search(
        r'<script[^>]*id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
        resp.text,
        re.DOTALL,
    )
    if not m:
        raise RuntimeError(
            "Rehydration script tag not found — TikTok may have rate-limited the "
            "request or changed page structure."
        )

    try:
        payload = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse rehydration JSON: {e}") from e

    # TikTok's schema varies between video and photo posts. Instead of hard-coding
    # a single path, walk the tree looking for the first dict that has an
    # `imagePost.images` array (that's a photo post) — agnostic to wrapper keys.
    image_post = _find_image_post(payload)
    if not image_post:
        scope = payload.get("__DEFAULT_SCOPE__", {})
        raise RuntimeError(
            "Could not find a photo slideshow in the page data. "
            f"Top-level scope keys: {list(scope.keys())[:12]}"
        )

    urls: list[str] = []
    for img in image_post.get("images", []):
        # imageURL.urlList is the canonical path; displayImage.urlList is a fallback
        image_data = img.get("imageURL") or img.get("displayImage") or {}
        url_list = image_data.get("urlList") or []
        if url_list:
            # urlList is ordered high-quality first
            urls.append(url_list[0])

    if not urls:
        raise RuntimeError("Photo post found but no image URLs inside it")
    return urls


def _find_image_post(obj, depth: int = 0, max_depth: int = 8):
    """Recursively search a JSON-ish tree for a dict shaped like
    ``{"images": [...]}``  under a key ``imagePost`` (TikTok photo post shape).
    Resilient to variations in the outer scope key names.
    """
    if depth > max_depth:
        return None
    if isinstance(obj, dict):
        ip = obj.get("imagePost")
        if isinstance(ip, dict) and isinstance(ip.get("images"), list) and ip["images"]:
            return ip
        for v in obj.values():
            found = _find_image_post(v, depth + 1, max_depth)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_image_post(v, depth + 1, max_depth)
            if found is not None:
                return found
    return None


def _download_images(
    image_urls: list[str], out_dir: Path, progress_cb: Callable[[str], None]
) -> list[Path]:
    """Download images to out_dir. Returns paths to successfully saved files."""
    import requests

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.tiktok.com/",
    }

    progress_cb(f"Downloading {len(image_urls)} slide image(s)...")
    saved: list[Path] = []
    for i, img_url in enumerate(image_urls, 1):
        progress_cb(f"Downloading slide {i}/{len(image_urls)}")
        try:
            r = requests.get(img_url, headers=headers, timeout=30)
            r.raise_for_status()
            img_path = out_dir / f"slide_{i}.jpg"
            img_path.write_bytes(r.content)
            if img_path.stat().st_size > 5000:
                saved.append(img_path)
        except Exception:
            # Skip individual failures and continue with the rest
            continue
    return saved


def _transcribe_with_whisper(
    video_path: Path, progress_cb: Callable[[str], None]
) -> dict:
    progress_cb(f"Transcribing with Whisper ({WHISPER_MODEL_NAME})...")
    model = _load_whisper()
    # language=None (default) -> Whisper auto-detects language
    result = model.transcribe(str(video_path), task="transcribe")
    return {
        "text": (result.get("text") or "").strip(),
        "language": result.get("language", "unknown"),
        "segments": [
            {
                "start": round(float(s["start"]), 2),
                "end": round(float(s["end"]), 2),
                "text": s["text"].strip(),
            }
            for s in result.get("segments", [])
        ],
    }


def _ocr_slides(
    image_paths: list[Path], langs: list[str], progress_cb: Callable[[str], None]
) -> list[str]:
    progress_cb(f"Running OCR ({', '.join(langs)}) on {len(image_paths)} slide(s)...")
    reader = _load_ocr_reader(langs)
    texts: list[str] = []
    for i, p in enumerate(image_paths, 1):
        progress_cb(f"OCR slide {i}/{len(image_paths)}")
        results = reader.readtext(str(p), detail=0, paragraph=True)
        texts.append(" ".join(results).strip())
    return texts


def transcribe_url(
    url: str,
    progress_cb: Optional[Callable[[str], None]] = None,
    ocr_langs: Optional[list[str]] = None,
) -> dict:
    """Main entry point: convert any supported URL to text."""
    progress_cb = progress_cb or _noop
    ocr_langs = ocr_langs or ["en", "vi"]

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # Primary path: yt-dlp + Whisper
        try:
            video_path = _download_with_ytdlp(url, tmp_path, progress_cb)
        except PhotoPostError:
            # TikTok photo fallback: fetch embedded JSON -> download images -> OCR
            image_urls = _fetch_tiktok_photo_urls(url, progress_cb)
            images = _download_images(image_urls, tmp_path, progress_cb)
            if not images:
                raise RuntimeError("No images could be downloaded from the TikTok photo post")
            texts = _ocr_slides(images, ocr_langs, progress_cb)
            combined = "\n\n".join(t for t in texts if t)
            return {
                "type": "photo",
                "text": combined,
                "language": detect_language(combined),
                "image_count": len(images),
                "url": url,
            }

        data = _transcribe_with_whisper(video_path, progress_cb)
        return {
            "type": "video",
            "text": data["text"],
            "language": data["language"],
            "segments": data["segments"],
            "url": url,
        }
