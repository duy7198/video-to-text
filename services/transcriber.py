"""
Transcription pipeline.

Scope: TikTok (videos + photo slideshows) and Facebook. Anything else
yt-dlp handles cleanly also works, but YouTube is intentionally excluded
from this demo — Google's cloud-IP bot wall breaks it and there's no
reliable free workaround.

Flow
----
1. Try yt-dlp to download the media.
2. Whisper transcribes the audio with automatic language detection.
3. Fallbacks:
   a. If yt-dlp reports "Unsupported URL" + "/photo/" (TikTok photo
      slideshow), fetch the post HTML, parse the embedded rehydration
      JSON, download the images, and run EasyOCR.
   b. If yt-dlp fails on a TikTok URL (datacenter IP rate-limiting, etc.),
      try tikwm.com's public API to get a direct CDN URL.
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
    """Download media with yt-dlp. Returns path to the downloaded file.

    Scope for this demo: TikTok, Facebook, and other sites whose extractors
    work cleanly from cloud IPs. YouTube is intentionally NOT supported on the
    hosted demo — Google's datacenter-IP bot wall breaks it and no free
    client-side trick reliably beats it anymore. Run locally for YouTube.
    """
    progress_cb("Downloading media with yt-dlp...")
    out_template = str(out_dir / "video.%(ext)s")

    cmd = [
        "yt-dlp",
        "--no-warnings",
        "--no-check-certificates",
        # Audio-only is cheaper to download AND all we need for transcription.
        # Falls back to video+audio if audio-only isn't available.
        "-f", "bestaudio[ext=m4a]/bestaudio/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best",
        "-o", out_template,
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=YTDLP_TIMEOUT)

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


def _fetch_via_direct_html(url: str, progress_cb: Callable[[str], None]) -> list[str]:
    """Parse image URLs directly from TikTok's HTML (rehydration JSON).

    Works when the request comes from a "trusted" IP — typically residential
    networks. Datacenter IPs (AWS, HF Spaces, GCP, etc.) usually receive a
    stripped bot-detection page without the post data.
    """
    import re
    import json
    import requests

    progress_cb("Fetching TikTok post HTML directly...")
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
        raise RuntimeError("Rehydration script tag not found")

    try:
        payload = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse rehydration JSON: {e}") from e

    image_post = _find_image_post(payload)
    if not image_post:
        raise RuntimeError("Bot-detection page (no post data in JSON)")

    urls: list[str] = []
    for img in image_post.get("images", []):
        image_data = img.get("imageURL") or img.get("displayImage") or {}
        url_list = image_data.get("urlList") or []
        if url_list:
            urls.append(url_list[0])
    if not urls:
        raise RuntimeError("Photo post found but no image URLs inside it")
    return urls


def _fetch_via_tikwm(url: str, progress_cb: Callable[[str], None]) -> list[str]:
    """Fallback: use tikwm.com's public API. Works from any IP because tikwm
    proxies through their own trusted network. Free, no auth, ~1 req/sec."""
    import requests

    progress_cb("Fetching via tikwm.com fallback...")
    resp = requests.post(
        "https://www.tikwm.com/api/",
        data={"url": url, "hd": "1"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("code") != 0:
        raise RuntimeError(f"tikwm error: {payload.get('msg', 'unknown')}")

    data = payload.get("data") or {}
    images = data.get("images") or []
    if not images:
        raise RuntimeError("tikwm returned no images for this URL")
    # tikwm returns plain string URLs
    return [u for u in images if isinstance(u, str) and u.startswith("http")]


def _fetch_tiktok_photo_urls(
    url: str, progress_cb: Callable[[str], None]
) -> list[str]:
    """Get image URLs from a TikTok photo post.

    Strategy: try direct HTML parse first (zero 3rd-party dependency, works
    on residential IPs). If that hits a bot wall (common on datacenter IPs
    like HF Spaces, Fly.io, etc.), fall back to tikwm.com's public API.
    """
    try:
        urls = _fetch_via_direct_html(url, progress_cb)
        if urls:
            return urls
    except Exception as e:
        progress_cb(f"Direct parse failed ({e}); using tikwm.com fallback...")

    # Fallback path — if this also fails, surface the error to the user.
    return _fetch_via_tikwm(url, progress_cb)


def _is_tiktok_url(url: str) -> bool:
    """True if the URL is on any TikTok host."""
    return any(host in url for host in ("tiktok.com", "vt.tiktok", "vm.tiktok"))



def _download_tiktok_video_via_tikwm(
    url: str, out_dir: Path, progress_cb: Callable[[str], None]
) -> Path:
    """Fallback when yt-dlp fails on a TikTok video URL. Uses tikwm.com's
    public API to get a direct CDN URL for the video, then downloads it.
    """
    import requests

    progress_cb("Fetching TikTok video metadata from tikwm.com...")
    resp = requests.post(
        "https://www.tikwm.com/api/",
        data={"url": url, "hd": "1"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("code") != 0:
        raise RuntimeError(f"tikwm error: {payload.get('msg', 'unknown')}")

    data = payload.get("data") or {}
    # Prefer HD play (no watermark), fall back to standard play URL.
    video_url = data.get("hdplay") or data.get("play") or data.get("wmplay")
    if not video_url:
        raise RuntimeError("tikwm returned no playable video URL")

    progress_cb("Downloading video from tikwm CDN...")
    out_path = out_dir / "video.mp4"
    r = requests.get(
        video_url,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.tiktok.com/"},
        stream=True,
        timeout=120,
    )
    r.raise_for_status()
    with open(out_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=65536):
            if chunk:
                f.write(chunk)
    if out_path.stat().st_size < 10_000:
        raise RuntimeError("tikwm video file too small or incomplete")
    return out_path


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
    # Import here to avoid a hard dep at module-import time
    from .ocr_service import _load_image_as_rgb_array

    texts: list[str] = []
    for i, p in enumerate(image_paths, 1):
        progress_cb(f"OCR slide {i}/{len(image_paths)}")
        try:
            img_array = _load_image_as_rgb_array(str(p))
        except Exception:
            continue  # skip slides we couldn't decode
        results = reader.readtext(img_array, detail=0, paragraph=True)
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
        except RuntimeError as exc:
            # TikTok video fallback: yt-dlp fails on datacenter IP → try tikwm
            if _is_tiktok_url(url):
                progress_cb(f"yt-dlp failed ({exc}); trying tikwm.com fallback...")
                video_path = _download_tiktok_video_via_tikwm(url, tmp_path, progress_cb)
            else:
                raise

        data = _transcribe_with_whisper(video_path, progress_cb)
        return {
            "type": "video",
            "text": data["text"],
            "language": data["language"],
            "segments": data["segments"],
            "url": url,
        }
