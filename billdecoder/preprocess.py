"""Image preparation: orientation fix, deskew, contrast enhancement and zoomed crops."""
from __future__ import annotations

import io
from pathlib import Path

import cv2
import numpy as np
from PIL import Image as PILImage, ImageOps

from .llm import Image

MAX_SIDE = 3072


def load_rgb(path: Path) -> PILImage.Image:
    img = PILImage.open(path)
    img = ImageOps.exif_transpose(img)  # phone photos carry EXIF rotation
    return img.convert("RGB")


def _encode(img: PILImage.Image, quality: int = 92) -> Image:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return Image(buf.getvalue(), "image/jpeg")


def _fit(img: PILImage.Image, max_side: int = MAX_SIDE, min_side: int = 2000) -> PILImage.Image:
    w, h = img.size
    long_side = max(w, h)
    scale = 1.0
    if long_side > max_side:
        scale = max_side / long_side
    elif long_side < min_side:
        scale = min(2.0, min_side / long_side)  # upscale small screenshots so digits get more pixels
    if scale != 1.0:
        img = img.resize((int(w * scale), int(h * scale)), PILImage.LANCZOS)
    return img


def estimate_skew(gray: np.ndarray) -> float:
    """Estimate page rotation in degrees from long near-horizontal lines (table borders, text rows)."""
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 1800, threshold=200,
                            minLineLength=gray.shape[1] // 4, maxLineGap=20)
    if lines is None:
        return 0.0
    angles = []
    for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4):  # shape differs across OpenCV versions
        a = np.degrees(np.arctan2(y2 - y1, x2 - x1))
        if abs(a) < 10:
            angles.append(a)
    return float(np.median(angles)) if angles else 0.0


def deskew(img: PILImage.Image) -> tuple[PILImage.Image, float]:
    gray = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2GRAY)
    angle = estimate_skew(gray)
    if abs(angle) < 0.4:
        return img, 0.0
    return img.rotate(angle, resample=PILImage.BICUBIC, expand=True, fillcolor=(255, 255, 255)), angle


def enhance(img: PILImage.Image) -> PILImage.Image:
    """CLAHE on lightness + mild unsharp mask: helps faded phone photos and low-contrast scans."""
    lab = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2LAB)
    l, a, b = cv2.split(lab)
    l = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(l)
    out = cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2RGB)
    blur = cv2.GaussianBlur(out, (0, 0), 1.2)
    out = cv2.addWeighted(out, 1.4, blur, -0.4, 0)
    return PILImage.fromarray(out)


def prepare_views(path: Path) -> dict:
    """Return the image views used by the extraction passes, plus preprocessing metadata."""
    original = load_rgb(path)
    straight, angle = deskew(original)
    enhanced = enhance(_fit(straight))
    w, h = enhanced.size
    top = enhanced.crop((0, 0, w, int(h * 0.55)))
    bottom = enhanced.crop((0, int(h * 0.45), w, h))
    return {
        "original": _encode(_fit(original, min_side=0)),
        "enhanced": _encode(enhanced),
        "crop_top": _encode(top),
        "crop_bottom": _encode(bottom),
        "meta": {"size": list(original.size), "deskew_deg": round(angle, 2)},
    }
