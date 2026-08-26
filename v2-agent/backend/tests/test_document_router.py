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

from app.document_router import ROUTER_VERSION, route_document_asset, route_document_assets
from app.document_router_semantic import SemanticPrediction


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
    pdf_page_image_count: int = 0
    pdf_figure_caption_count: int = 0


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


def test_compound_academic_figure_components_are_not_independent_photos():
    random = np.random.default_rng(17)
    pixels = random.integers(0, 256, size=(240, 360, 3), dtype=np.uint8)
    decision = route_document_asset(
        Asset(
            _png(pixels),
            360,
            240,
            pdf_page_image_count=15,
            pdf_figure_caption_count=1,
        )
    )

    assert decision.route == "skip"
    assert decision.should_detect is False
    assert decision.category == "compound_figure_component"
    assert decision.features["pdfPageImageCount"] == 15


def test_many_pdf_images_without_a_figure_caption_still_fail_open():
    random = np.random.default_rng(18)
    pixels = random.integers(0, 256, size=(240, 360, 3), dtype=np.uint8)
    decision = route_document_asset(
        Asset(
            _png(pixels),
            360,
            240,
            pdf_page_image_count=15,
            pdf_figure_caption_count=0,
        )
    )

    assert decision.should_detect is True
    assert decision.category == "photo_or_artwork"


def test_broken_feature_read_fails_open():
    decision = route_document_asset(Asset(b"not-an-image", 640, 480))

    assert decision.route == "uncertain"
    assert decision.should_detect is True
    assert decision.category == "unreadable"


class FakeSemanticClassifier:
    def __init__(self, predictions):
        self.predictions = predictions
        self.received = []

    def classify(self, assets):
        self.received = list(assets)
        return self.predictions


def _semantic(
    category: str,
    confidence: float,
    meaningful_score: float,
) -> SemanticPrediction:
    return SemanticPrediction(
        category=category,
        category_label={
            "photograph": "自然照片",
            "chart": "图表或数据图",
            "interface": "软件或网页截图",
        }[category],
        confidence=confidence,
        meaningful_score=meaningful_score,
        scores={category: confidence},
        model="tinyclip-test",
    )


def test_semantic_router_skips_a_chart_that_looks_textured():
    random = np.random.default_rng(91)
    pixels = random.integers(0, 256, size=(240, 360, 3), dtype=np.uint8)
    asset = Asset(_png(pixels), 360, 240)
    classifier = FakeSemanticClassifier([_semantic("chart", 0.62, 0.12)])

    decision = route_document_assets([asset], classifier)[0]

    assert decision.route == "skip"
    assert decision.category == "semantic_chart"
    assert decision.features["semanticMeaningfulScore"] == 0.12
    assert decision.public_payload()["version"] == ROUTER_VERSION


def test_semantic_router_sends_a_natural_photo():
    random = np.random.default_rng(92)
    pixels = random.integers(0, 256, size=(240, 360, 3), dtype=np.uint8)
    asset = Asset(_png(pixels), 360, 240)
    classifier = FakeSemanticClassifier([_semantic("photograph", 0.68, 0.88)])

    decision = route_document_assets([asset], classifier)[0]

    assert decision.route == "detect"
    assert decision.category == "semantic_visual_work"
    assert decision.should_detect is True


def test_semantic_boundary_fails_open():
    random = np.random.default_rng(93)
    pixels = random.integers(0, 256, size=(240, 360, 3), dtype=np.uint8)
    classifier = FakeSemanticClassifier([_semantic("interface", 0.31, 0.44)])

    decision = route_document_assets([Asset(_png(pixels), 360, 240)], classifier)[0]

    assert decision.route == "uncertain"
    assert decision.should_detect is True
    assert decision.category == "semantic_ambiguous"


def test_semantic_failure_preserves_conservative_rule_result():
    random = np.random.default_rng(94)
    pixels = random.integers(0, 256, size=(240, 360, 3), dtype=np.uint8)

    class BrokenClassifier:
        def classify(self, _assets):
            raise RuntimeError("model unavailable")

    decision = route_document_assets(
        [Asset(_png(pixels), 360, 240)], BrokenClassifier()
    )[0]

    assert decision.should_detect is True
    assert "semanticModel" not in decision.features


def test_semantic_model_is_not_called_for_structural_skips():
    pixels = np.full((240, 320, 3), 148, dtype=np.uint8)
    classifier = FakeSemanticClassifier([])

    decision = route_document_assets(
        [Asset(_png(pixels), 320, 240)], classifier
    )[0]

    assert decision.route == "skip"
    assert classifier.received == []
