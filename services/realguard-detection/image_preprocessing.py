from __future__ import annotations

from typing import Any

from PIL import Image, ImageOps


def normalize_orientation(image: Image.Image) -> tuple[Image.Image, dict[str, Any]]:
    encoded_size = image.size
    try:
        orientation = int(image.getexif().get(274, 1) or 1)
    except (AttributeError, TypeError, ValueError):
        orientation = 1
    normalized = ImageOps.exif_transpose(image)
    return normalized, {
        "exifOrientation": orientation,
        "orientationApplied": orientation not in {0, 1},
        "encodedSize": {"width": int(encoded_size[0]), "height": int(encoded_size[1])},
        "displaySize": {"width": int(normalized.size[0]), "height": int(normalized.size[1])},
    }


def fit_within(width: int, height: int, max_side: int) -> tuple[int, int]:
    if width <= 0 or height <= 0:
        raise ValueError("image dimensions must be positive")
    if max_side <= 0:
        raise ValueError("max_side must be positive")
    scale = min(1.0, float(max_side) / float(max(width, height)))
    return max(1, round(width * scale)), max(1, round(height * scale))


def downsample_for_analysis(image: Image.Image, max_side: int) -> tuple[Image.Image, dict[str, Any]]:
    original_width, original_height = image.size
    target_width, target_height = fit_within(original_width, original_height, max_side)
    downsampled = (target_width, target_height) != (original_width, original_height)
    processed = image
    if downsampled:
        processed = image.resize((target_width, target_height), Image.Resampling.LANCZOS)
    scale = target_width / float(original_width)
    return processed, {
        "downsampled": downsampled,
        "maxAnalysisSide": int(max_side),
        "originalSize": {"width": int(original_width), "height": int(original_height)},
        "processedSize": {"width": int(target_width), "height": int(target_height)},
        "scale": round(scale, 6),
    }
