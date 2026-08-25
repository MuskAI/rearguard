from __future__ import annotations

from dataclasses import dataclass
import io
from pathlib import Path
import sys

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.document_router import route_document_asset


@dataclass
class Asset:
    data: bytes
    width: int
    height: int
    source_kind: str = "pdf_embedded"
    duplicate_of: int | None = None
    pdf_object_id: int | None = None
    pdf_smask_object_id: int | None = None
    pdf_is_soft_mask: bool = False
    pdf_is_image_mask: bool = False
    pdf_color_space: str | None = "/DeviceRGB"
    pdf_bits_per_component: int | None = 8


def _png(array: np.ndarray, mode: str = "RGB") -> bytes:
    output = io.BytesIO()
    Image.fromarray(array, mode=mode).save(output, "PNG")
    return output.getvalue()


def test_pdf_masks_are_skipped_from_structural_evidence():
    data = _png(np.full((160, 240), 128, dtype=np.uint8), "L")
    decision = route_document_asset(
        Asset(data, 240, 160, pdf_object_id=12, pdf_is_soft_mask=True)
    )

    assert decision.route == "skip"
    assert decision.should_detect is False
    assert decision.category == "pdf_mask"
    assert decision.confidence >= 0.99


def test_uniform_background_is_skipped_without_a_model():
    data = _png(np.full((240, 320, 3), (148, 148, 148), dtype=np.uint8))
    decision = route_document_asset(Asset(data, 320, 240))

    assert decision.route == "skip"
    assert decision.category in {"uniform_layer", "low_information"}
    assert decision.features["dominantColorRatio"] >= 0.98


def test_textured_photo_like_image_is_sent_to_detection():
    random = np.random.default_rng(20260826)
    pixels = random.integers(0, 256, size=(240, 360, 3), dtype=np.uint8)
    pixels[:, :, 1] = np.clip(pixels[:, :, 1].astype(np.int16) + 35, 0, 255).astype(np.uint8)
    decision = route_document_asset(Asset(_png(pixels), 360, 240))

    assert decision.route == "detect"
    assert decision.should_detect is True
    assert decision.category == "photo_or_artwork"
    assert decision.features["entropy"] >= 4


def test_duplicates_do_not_consume_another_model_call():
    pixels = np.zeros((200, 260, 3), dtype=np.uint8)
    decision = route_document_asset(Asset(_png(pixels), 260, 200, duplicate_of=3))

    assert decision.route == "skip"
    assert decision.category == "duplicate"
    assert decision.confidence == 1


def test_broken_feature_read_fails_open():
    decision = route_document_asset(Asset(b"not-an-image", 640, 480))

    assert decision.route == "uncertain"
    assert decision.should_detect is True
    assert decision.category == "unreadable"
