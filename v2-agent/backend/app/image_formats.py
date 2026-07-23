"""HEIF/HEIC registration for iPhone photos and Live Photo still frames."""

from __future__ import annotations


def register_heif_opener() -> None:
    try:
        from pillow_heif import register_heif_opener as register
    except ImportError:
        return
    register()
