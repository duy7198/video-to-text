"""
Transcription pipeline.

Flow
----
1. Try yt-dlp to download the video (works for YouTube, regular TikTok, many others).
2. Whisper transcribes the audio with automatic language detection.
3. Fallback: if yt-dlp fails with "Unsupported URL" + "/photo/" (TikTok photo
   slideshow), scrape images via Playwright and run EasyOCR instead.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import threading
import time
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
            _whisper_model = whisper.load_model(WHISPER_MODEL_NAME)
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


def _scrape_tiktok_photos(url: str, out_dir: Path, progress_cb: Callable[[str], None]) -> list[Path]:
    """Scrape slide images from a TikTok photo post using Playwright."""
    from playwright.sync_api import sync_playwright

    progress_cb("Rendering TikTok photo page with Playwright...")

    image_paths: list[Path] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            ignore_https_errors=True,
        )
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            time.sleep(3)  # let JS render the carousel

            progress_cb("Downloading slideshow images...")
            seen: set[str] = set()
            for img in page.query_selector_all("img"):
                src = img.get_attribute("src") or ""
                if not any(cdn in src for cdn in ("p16-sign", "p19-sign", "p77-sign", "tiktokcdn")):
                    continue
                if any(skip in src for skip in ("avatar", "100x100", "68x68")):
                    continue
                if src in seen:
                    continue
                seen.add(src)

                j = len(image_paths) + 1
                img_path = out_dir / f"slide_{j}.jpg"
                subprocess.run(
                    ["curl", "-L", "-s", "-k", "-o", str(img_path), src],
                    capture_output=True,
                    timeout=30,
                )
                if img_path.exists() and img_path.stat().st_size > 5000:
                    image_paths.append(img_path)
                else:
                    img_path.unlink(missing_ok=True)
        finally:
            browser.close()

    return image_paths


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
            # TikTok photo fallback: scrape slides + OCR
            images = _scrape_tiktok_photos(url, tmp_path, progress_cb)
            if not images:
                raise RuntimeError("No images could be scraped from the TikTok photo post")
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
