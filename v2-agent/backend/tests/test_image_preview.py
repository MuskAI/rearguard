from __future__ import annotations

import base64
import io
import importlib
import sys

from PIL import Image


def test_transparent_preview_is_composited_on_white(monkeypatch, tmp_path):
    monkeypatch.setenv("JIANZHEN_DATA_DIR", str(tmp_path))
    for module_name in ("app.storage", "app.main"):
        sys.modules.pop(module_name, None)
    main = importlib.import_module("app.main")

    source = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    for x in range(8, 24):
        for y in range(8, 24):
            source.putpixel((x, y), (220, 40, 30, 255))
    raw = io.BytesIO()
    source.save(raw, format="PNG")

    preview = main._image_data_uri(raw.getvalue(), "image", max_side=32, quality=100)
    encoded = preview.split(",", 1)[1]
    with Image.open(io.BytesIO(base64.b64decode(encoded))) as rendered:
        rgb = rendered.convert("RGB")
        background = rgb.getpixel((2, 2))
        foreground = rgb.getpixel((16, 16))

    assert min(background) >= 245
    assert foreground[0] > foreground[1] + 80
