"""Image-format helpers shared by upload validation and model inference."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageOps


HEIF_EXTENSIONS = frozenset({"heic", "heif"})
HEIF_FORMATS = frozenset({"HEIC", "HEIF"})
HEIF_BRANDS = frozenset({
    b"heic",
    b"heix",
    b"hevc",
    b"hevx",
    b"heim",
    b"heis",
    b"mif1",
    b"msf1",
})


def register_heif_opener() -> None:
    """Register Pillow's HEIF/HEIC decoder once for the current process."""
    try:
        from pillow_heif import register_heif_opener
    except ImportError:
        return
    register_heif_opener()


register_heif_opener()


def is_heif_filename(filename: str | Path) -> bool:
    return Path(str(filename)).suffix.lower().lstrip(".") in HEIF_EXTENSIONS


def is_heif_bytes(data: bytes) -> bool:
    if len(data) < 12 or data[4:8] != b"ftyp":
        return False
    brands = {data[index:index + 4] for index in range(8, min(len(data), 40), 4)}
    return bool(brands & HEIF_BRANDS)


def is_unsupported_animation(image: Image.Image) -> bool:
    """HEIF may contain auxiliary images; only reject actual animated formats."""
    image_format = str(image.format or "").upper()
    return (
        image_format not in HEIF_FORMATS
        and bool(getattr(image, "is_animated", False))
        and int(getattr(image, "n_frames", 1)) > 1
    )


def model_upload_from_path(image_path: str | Path) -> tuple[str, bytes, str]:
    """Return a browser/model-safe upload while leaving the source file untouched."""
    path = Path(image_path)
    source_bytes = path.read_bytes()
    if not is_heif_filename(path) and not is_heif_bytes(source_bytes):
        return path.name or "image.bin", source_bytes, "application/octet-stream"

    with Image.open(io.BytesIO(source_bytes)) as opened:
        if getattr(opened, "n_frames", 1) > 1:
            opened.seek(0)
        image = ImageOps.exif_transpose(opened)
        if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
            rgba = image.convert("RGBA")
            flattened = Image.new("RGB", rgba.size, "white")
            flattened.paste(rgba, mask=rgba.getchannel("A"))
            image = flattened
        else:
            image = image.convert("RGB")
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=95, subsampling=0, optimize=True)

    return f"{path.stem or 'live-photo'}.jpg", output.getvalue(), "image/jpeg"
