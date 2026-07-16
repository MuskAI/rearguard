from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yolo_adapter


def test_merge_corroborates_platform_hit_and_keeps_unmatched_watermark():
    registry_hits = [{
        "provider": "gemini",
        "confidence": 0.86,
        "bbox": {"x": 0.91, "y": 0.89, "w": 0.06, "h": 0.07},
    }]
    candidates = [
        {
            "provider": "yolo11x_watermark",
            "confidence": 0.92,
            "bbox": {"x": 0.90, "y": 0.88, "w": 0.08, "h": 0.10},
            "model": "corzent/yolo11x_watermark_detection",
            "modelRevision": "revision-1",
        },
        {
            "provider": "yolo11x_watermark",
            "confidence": 0.99,
            "bbox": {"x": 0.05, "y": 0.05, "w": 0.20, "h": 0.12},
        },
    ]

    hits = yolo_adapter._merge_visible_hits(registry_hits, candidates)

    assert len(hits) == 2
    assert hits[0]["provider"] == "gemini"
    assert hits[0]["yoloCorroborated"] is True
    assert hits[0]["yoloConfidence"] == 0.92
    assert hits[0]["localizationModelRevision"] == "revision-1"
    assert hits[1]["provider"] == "yolo11x_watermark"
    assert hits[1]["confidence"] == 0.99


def test_unmatched_watermark_candidate_is_returned_as_non_decisive():
    candidates = [{
        "provider": "yolo11x_watermark",
        "confidence": 0.99,
        "bbox": {"x": 0.05, "y": 0.05, "w": 0.20, "h": 0.12},
        "decisive": False,
    }]

    assert yolo_adapter._merge_visible_hits([], candidates) == candidates
    assert yolo_adapter._merge_visible_hits([], candidates)[0]["decisive"] is False
