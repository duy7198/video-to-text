"""OCR for uploaded image files."""
from __future__ import annotations

import os
from typing import Callable, Optional

from .transcriber import _load_ocr_reader, detect_language


def _noop(_msg: str) -> None:
    pass


def ocr_image_file(
    image_path: str,
    langs: Optional[list[str]] = None,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> dict:
    """Extract text from a single image file."""
    progress_cb = progress_cb or _noop
    langs = langs or ["en", "vi"]

    progress_cb(f"Loading OCR model ({', '.join(langs)})...")
    reader = _load_ocr_reader(langs)

    progress_cb("Running OCR...")
    results = reader.readtext(image_path, detail=0, paragraph=True)
    text = " ".join(results).strip()

    return {
        "type": "image",
        "text": text,
        "language": detect_language(text),
        "filename": os.path.basename(image_path),
    }
