from pathlib import Path
import sys

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import metadata, provenance_precheck


def test_no_decision_does_not_build_analysis():
    assert provenance_precheck.build_analysis({"available": True, "decision": {"shortCircuit": False}}) is None


def test_visible_watermark_builds_model_free_analysis():
    payload = {
        "available": True,
        "engineVersion": "0.15.3",
        "decision": {
            "shortCircuit": True,
            "verdict": "highly_suspected_fake",
            "confidence": 0.93,
            "reason": "known_visible_ai_watermark",
            "summary": "检测到已知 AI 可见标记。",
        },
        "report": {"aiFromMetadata": False, "signals": []},
        "visibleHits": [
            {
                "provider": "gemini",
                "label": "Google Gemini sparkle",
                "confidence": 0.84,
                "decisive": True,
                "bbox": {"x": 0.9, "y": 0.9, "w": 0.08, "h": 0.08},
            }
        ],
    }
    analysis = provenance_precheck.build_analysis(payload)
    assert analysis is not None
    assert analysis["source"] == "provenance"
    assert analysis["tokenUsage"]["totalTokens"] == 0
    assert analysis["visibleWatermark"]["provider"] == "gemini"
    assert analysis["visibleWatermark"]["hits"][0]["method"] == "remove_ai_watermarks_registry"
    assert analysis["visibleWatermark"]["hits"][0]["model"] == "wiltodelta/remove-ai-watermarks"
    assert analysis["visibleWatermark"]["detector"]["engines"][0]["version"] == "0.15.3"
    assert analysis["regions"]


def test_camera_c2pa_cannot_be_forged_into_short_circuit_by_client():
    payload = {
        "available": True,
        "decision": {
            "shortCircuit": True,
            "verdict": "real",
            "confidence": 0.99,
            "reason": "camera_c2pa",
        },
    }
    assert provenance_precheck.build_analysis(payload) is None


def test_high_confidence_local_metadata_remains_context_for_pixel_model():
    local = provenance_precheck._local_source_decision(
        {
            "metadataAiGenerated": True,
            "aiMetadata": {"score": 82, "matchedTools": ["TC260 AIGC 标识"]},
            "actions": [],
        }
    )
    assert local is not None
    decision, report = local
    assert decision["reason"] == "ai_metadata_context"
    assert decision["shortCircuit"] is False
    assert decision["modelRequired"] is True
    assert report["aiFromMetadata"] is True


def test_user_controlled_filename_cannot_create_ai_metadata_evidence():
    report = metadata.analyze_ai_metadata(
        {"file": {"name": "chatgpt_prompt_seed_12345_generated.jpg"}}
    )

    assert report["score"] == 0
    assert report["signalCount"] == 0
    assert report["isAiLikely"] is False


def test_invalid_ai_c2pa_requires_pixel_model_without_corroboration():
    local = provenance_precheck._local_source_decision(
        {
            "isAiGenerated": True,
            "validationState": "Invalid",
            "generator": "Example AI",
            "actions": [{"digitalSourceType": "trainedAlgorithmicMedia"}],
        }
    )
    assert local is not None
    decision, _ = local
    assert decision["verdict"] is None
    assert decision["confidence"] == 0.0
    assert decision["modelRequired"] is True


def test_unknown_ai_c2pa_state_cannot_short_circuit_pixel_model():
    local = provenance_precheck._local_source_decision(
        {
            "isAiGenerated": True,
            "validationState": "unknown",
            "generator": "Example AI",
            "actions": [{"digitalSourceType": "trainedAlgorithmicMedia"}],
        }
    )
    assert local is not None
    decision, report = local
    assert decision["shortCircuit"] is False
    assert decision["modelRequired"] is True
    assert report["c2paTrusted"] is False


def test_valid_ai_c2pa_can_short_circuit_pixel_model():
    local = provenance_precheck._local_source_decision(
        {
            "isAiGenerated": True,
            "validationState": "valid",
            "generator": "Example AI",
            "actions": [{"digitalSourceType": "trainedAlgorithmicMedia"}],
        }
    )
    assert local is not None
    decision, report = local
    assert decision["shortCircuit"] is True
    assert decision["modelRequired"] is False
    assert report["c2paTrusted"] is True


def test_visible_scan_reduces_large_png_and_keeps_dimensions_bounded(tmp_path):
    source = tmp_path / "large.png"
    Image.new("RGB", (2048, 2048), (20, 80, 130)).save(source, "PNG")
    data = source.read_bytes()
    scan, name, details = provenance_precheck._visible_scan(data, source.name)

    assert name.endswith(".visible-scan.jpg")
    assert details["scanDimensions"] == {"width": 1536, "height": 1536}
    assert details["scanBytes"] == len(scan)


def test_remote_visible_hits_keep_platform_and_unmatched_generic_watermarks():
    payload = {
        "visibleHits": [
            {
                "provider": "gemini",
                "confidence": 0.86,
                "bbox": {"x": 0.91, "y": 0.89, "w": 0.06, "h": 0.07},
            },
            {
                "provider": "yolo11x_watermark",
                "confidence": 0.93,
                "bbox": {"x": 0.90, "y": 0.88, "w": 0.08, "h": 0.10},
                "model": "corzent/yolo11x_watermark_detection",
                "modelRevision": "revision-1",
            },
            {
                "provider": "yolo11x_watermark",
                "confidence": 0.99,
                "bbox": {"x": 0.05, "y": 0.05, "w": 0.20, "h": 0.12},
            },
        ],
        "genericVisibleWatermark": {"available": True, "detected": True, "count": 2},
    }

    provenance_precheck._normalize_visible_hits(payload)

    assert len(payload["visibleHits"]) == 2
    assert payload["visibleHits"][0]["provider"] == "gemini"
    assert payload["visibleHits"][0]["yoloCorroborated"] is True
    assert payload["visibleHits"][0]["yoloConfidence"] == 0.93
    assert payload["visibleHits"][1]["provider"] == "yolo11x_watermark"
    assert payload["visibleHits"][1]["decisive"] is False
    assert payload["genericVisibleWatermark"]["count"] == 2
    assert payload["genericVisibleWatermark"]["genericCount"] == 1
    assert payload["genericVisibleWatermark"]["platformConfirmedCount"] == 1
    assert payload["genericVisibleWatermark"]["mode"] == "visible_watermark_detection_with_platform_attribution"


def test_remote_visible_hits_expose_generic_watermark_without_platform_match():
    payload = {
        "visibleHits": [{
            "provider": "yolo11x_watermark",
            "confidence": 0.99,
            "bbox": {"x": 0.05, "y": 0.05, "w": 0.2, "h": 0.1},
        }],
        "genericVisibleWatermark": {"available": True, "detected": True, "count": 1},
    }

    provenance_precheck._normalize_visible_hits(payload)

    assert len(payload["visibleHits"]) == 1
    assert payload["visibleHits"][0]["provider"] == "yolo11x_watermark"
    assert payload["visibleHits"][0]["decisive"] is False
    assert payload["genericVisibleWatermark"]["detected"] is True
    assert payload["genericVisibleWatermark"]["count"] == 1
    assert payload["genericVisibleWatermark"]["genericCount"] == 1
