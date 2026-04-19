"""OCR for uploaded image files."""
from __future__ import annotations

import os
from typing import Callable, Optional

from .transcriber import _load_ocr_reader, detect_language

# Try to register HEIC/HEIF support (iPhone photos). Optional — skip if the
# plugin is missing; the app still handles JPEG/PNG/WebP via base Pillow.
try:
    import pillow_heif  # type: ignore
    pillow_heif.register_heif_opener()
except ImportError:
    pass


def _noop(_msg: str) -> None:
    pass


def _load_image_as_rgb_array(image_path: str):
    """Load any image format PIL supports, return a numpy RGB array.

    OpenCV's imread (used internally by EasyOCR) fails on some iPhone JPEGs,
    CMYK JPEGs, and anything that's actually HEIC with a .jpeg extension,
    surfacing as an opaque 'ssize.empty()' OpenCV assertion. PIL is far
    more tolerant of real-world image files.
    """
    from PIL import Image, ImageOps
    import numpy as np

    with Image.open(image_path) as img:
        # Apply EXIF orientation so OCR reads text right-side-up on phone photos
        img = ImageOps.exif_transpose(img)
        # Normalize to RGB (handles RGBA, CMYK, palette, grayscale, 16-bit)
        if img.mode != "RGB":
            img = img.convert("RGB")
        return np.array(img)


def ocr_image_file(
    image_path: str,
    langs: Optional[list[str]] = None,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> dict:
    """Extract text from a single image file."""
    progress_cb = progress_cb or _noop
    langs = langs or ["en", "vi"]

    progress_cb("Decoding image...")
    try:
        img_array = _load_image_as_rgb_array(image_path)
    except Exception as e:
        raise RuntimeError(f"Could not read image file: {e}") from e

    progress_cb(f"Loading OCR model ({', '.join(langs)})...")
    reader = _load_ocr_reader(langs)

    progress_cb("Running OCR...")
    # Pass the numpy array directly — bypasses EasyOCR's internal imread path.
    results = reader.readtext(img_array, detail=0, paragraph=True)
    text = " ".join(results).strip()

    return {
        "type": "image",
        "text": text,
        "language": detect_language(text),
        "filename": os.path.basename(image_path),
    }
