"""Image-format helpers shared by upload validation and model inference."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageOps


HEIF_EXTENSIONS = frozenset({"heic", "heif"})
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


def model_upload_from_path(image_path: str | Path) -> tuple[str, bytes, str]:
    """Return a single-frame model upload while leaving the source untouched."""
    path = Path(image_path)
    source_bytes = path.read_bytes()
    has_container_signature = (
        source_bytes[:6] in {b"GIF87a", b"GIF89a"}
        or (source_bytes[:4] == b"RIFF" and source_bytes[8:12] == b"WEBP")
        or source_bytes[:8] == b"\x89PNG\r\n\x1a\n"
        or source_bytes[:3] == b"\xff\xd8\xff"
    )
    requires_inspection = (
        is_heif_filename(path)
        or is_heif_bytes(source_bytes)
        or has_container_signature
    )
    if not requires_inspection:
        return path.name or "image.bin", source_bytes, "application/octet-stream"

    with Image.open(io.BytesIO(source_bytes)) as opened:
        requires_static_frame = (
            is_heif_filename(path)
            or is_heif_bytes(source_bytes)
            or int(getattr(opened, "n_frames", 1)) > 1
        )
        if not requires_static_frame:
            return path.name or "image.bin", source_bytes, "application/octet-stream"
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
