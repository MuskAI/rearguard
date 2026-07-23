from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import main, watermark_yolo


def _analysis():
    return {
        "verdict": "real",
        "confidence": 0.82,
        "source": "vlm",
        "dimensions": [],
        "regions": [],
        "explanation": "model result",
    }


def test_merge_keeps_unmatched_yolo_watermark_as_non_decisive_context():
    analysis = _analysis()
    merged = watermark_yolo.merge(analysis, {
        "status": "ok",
        "available": True,
        "engineVersion": "0.15.3",
        "coordinateSpace": "display_normalized_v1",
        "displaySize": {"width": 1000, "height": 800},
        "genericVisibleWatermark": {
            "available": True,
            "elapsedMs": 108,
            "roundTripMs": 114,
            "model": "corzent/yolo11x_watermark_detection",
            "modelRevision": "revision-1",
            "confidenceThreshold": 0.35,
        },
        "visibleHits": [{
            "provider": "yolo11x_watermark",
            "label": "通用可见水印或 Logo",
            "confidence": 0.9237,
            "bbox": {"x": 0.04, "y": 0.84, "w": 0.14, "h": 0.11},
            "model": "corzent/yolo11x_watermark_detection",
            "modelRevision": "revision-1",
            "decisive": False,
        }],
    })

    assert merged["verdict"] == "real"
    assert merged["confidence"] == 0.82
    assert "watermarkVerdictOverride" not in merged
    assert analysis.get("visibleWatermark") is None
    visible = merged["visibleWatermark"]
    assert visible["supported"] is True
    assert visible["detected"] is True
    assert visible["confidence"] == 0.9237
    assert visible["evidenceLevel"] == "medium"
    assert len(visible["hits"]) == 1
    assert visible["hits"][0]["provider"] == "yolo11x_watermark"
    assert visible["hits"][0]["decisive"] is False
    assert "平台归属尚未确认" in visible["note"]
    assert "不单独影响 AI 生成结论" in visible["note"]


def test_merge_exposes_registry_and_yolo_as_distinct_engines():
    merged = watermark_yolo.merge(_analysis(), {
        "status": "ok",
        "available": True,
        "engineVersion": "0.15.3",
        "coordinateSpace": "display_normalized_v1",
        "displaySize": {"width": 1000, "height": 800},
        "elapsedMs": 182,
        "genericVisibleWatermark": {
            "available": True,
            "model": "corzent/yolo11x_watermark_detection",
            "modelRevision": "revision-1",
        },
        "visibleHits": [
            {
                "provider": "gemini",
                "label": "Google Gemini sparkle",
                "confidence": 0.86,
                "bbox": {"x": 0.91, "y": 0.89, "w": 0.06, "h": 0.07},
                "decisive": True,
                "yoloCorroborated": True,
                "yoloConfidence": 0.92,
                "localizationModel": "corzent/yolo11x_watermark_detection",
                "localizationModelRevision": "revision-1",
            },
            {
                "provider": "yolo11x_watermark",
                "confidence": 0.92,
                "bbox": {"x": 0.9, "y": 0.88, "w": 0.08, "h": 0.1},
                "decisive": False,
            },
        ],
    })

    visible = merged["visibleWatermark"]
    assert merged["verdict"] == "real"
    assert merged["confidence"] == 0.82
    assert "watermarkVerdictOverride" not in merged
    assert visible["provider"] == "gemini"
    assert visible["confidence"] == 0.86
    assert len(visible["hits"]) == 1
    assert visible["hits"][0]["method"] == "remove_ai_watermarks_registry"
    assert visible["hits"][0]["decisive"] is False
    assert visible["hits"][0]["evidenceRole"] == "visual_attribution"
    assert visible["hits"][0]["localizationConfirmed"] is True
    engines = {engine["id"]: engine for engine in visible["detector"]["engines"]}
    assert engines["known_ai_registry"]["model"] == "wiltodelta/remove-ai-watermarks"
    assert engines["known_ai_registry"]["version"] == "0.15.3"
    assert engines["yolo_visible_watermark"]["model"] == "corzent/yolo11x_watermark_detection"
    assert engines["yolo_visible_watermark"]["count"] == 1
    assert "不单独决定真伪" in visible["note"]


def test_merge_exposes_completed_scan_when_no_watermark_is_found():
    merged = watermark_yolo.merge(_analysis(), {
        "status": "ok",
        "available": True,
        "engineVersion": "0.15.3",
        "genericVisibleWatermark": {
            "available": True,
            "detected": False,
            "count": 0,
            "elapsedMs": 91,
        },
        "visibleHits": [],
    })

    visible = merged["visibleWatermark"]
    assert visible["supported"] is True
    assert visible["detected"] is False
    assert visible["evidenceLevel"] == "none"
    assert visible["elapsedMs"] == 91


def test_explicit_ocr_and_retrieval_authorize_generic_yolo_candidate():
    bbox = {"x": 0.8921, "y": 0.9385, "w": 0.1004, "h": 0.0411}
    merged = watermark_yolo.merge(_analysis(), {
        "status": "ok",
        "available": True,
        "engineVersion": "0.15.3",
        "coordinateSpace": "display_normalized_v1",
        "displaySize": {"width": 2848, "height": 1600},
        "genericVisibleWatermark": {
            "available": True,
            "detected": True,
            "count": 1,
            "model": "corzent/yolo11x_watermark_detection",
        },
        "visibleHits": [{
            "provider": "yolo11x_watermark",
            "label": "可见水印（平台待确认）",
            "confidence": 0.8574,
            "bbox": bbox,
        }],
        "explicitWatermark": {
            "available": True,
            "detected": True,
            "type": "text",
            "sourcePlatform": "豆包",
            "confidence": 0.901,
            "confidenceBand": "high",
            "aiWatermarkVerdict": {
                "verdict": "yes",
                "isAiGeneratedWatermark": True,
                "confidence": 0.99,
                "reason": "文字明确包含 AI 生成语义，且匹配豆包平台词。",
                "relevantHitCount": 1,
            },
            "hits": [{
                "bbox": bbox,
                "type": "text",
                "text": "豆包AI生成",
                "sourcePlatform": "豆包",
                "confidence": 0.901,
                "detectionConfidence": 0.8574,
                "ocrConfidence": 0.949,
                "textAnalysis": {
                    "verdict": "supports_ai_generation",
                    "likelyAIgenerated": True,
                    "aiGenerationConfidence": 0.99,
                    "platformMatch": "豆包",
                },
                "retrievalAccepted": True,
                "retrievalSimilarity": 0.9068,
                "retrievalReferenceId": "doubao-black",
                "registryMatched": False,
                "yoloCorroborated": False,
            }],
        },
    })

    visible = merged["visibleWatermark"]
    assert len(visible["hits"]) == 1
    assert visible["hits"][0]["provider"] == "doubao"
    assert visible["hits"][0]["method"] == "explicit_ai_watermark_fusion"
    assert visible["hits"][0]["decisive"] is True
    assert visible["explicitWatermark"]["aiWatermarkVerdict"]["verdict"] == "yes"
    assert merged["verdict"] == "highly_suspected_fake"
    assert merged["confidence"] == 0.99
    assert merged["decisionStatus"] == "verdict"
    assert merged["decisionAuthority"] == "decisive_provenance"
    assert merged["reviewRequired"] is False
    assert merged["watermarkVerdictOverride"]["policyVersion"] == "explicit-ai-watermark-v2"

    legacy = _analysis()
    legacy.update({
        "verdict": "unknown",
        "confidence": 0.0,
        "decisionStatus": "review_only",
        "decisionAuthority": "none",
        "reviewRequired": True,
        "visibleWatermark": {
            "detected": True,
            "coordinateSpace": "display_normalized_v1",
            "displaySize": {"width": 2848, "height": 1600},
            "hits": [{
                "provider": "yolo11x_watermark",
                "label": "可见水印（平台待确认）",
                "confidence": 0.8574,
                "bbox": bbox,
                "method": "yolo11x_watermark_detection",
                "decisive": False,
            }],
        },
        "provenancePrecheck": {
            "status": "ok",
            "available": True,
            "engineVersion": "0.15.3",
            "coordinateSpace": "display_normalized_v1",
            "displaySize": {"width": 2848, "height": 1600},
            "genericVisibleWatermark": {
                "available": True,
                "detected": True,
                "count": 1,
                "model": "corzent/yolo11x_watermark_detection",
            },
            "visibleHits": [{
                "provider": "yolo11x_watermark",
                "label": "可见水印（平台待确认）",
                "confidence": 0.8574,
                "bbox": bbox,
            }],
            "explicitWatermark": merged["visibleWatermark"]["explicitWatermark"],
        },
    })
    rehydrated = main._strip_internal_history_fields(legacy)
    assert rehydrated["verdict"] == "highly_suspected_fake"
    assert rehydrated["confidence"] == 0.99
    assert rehydrated["decisionAuthority"] == "decisive_provenance"
    assert rehydrated["reviewRequired"] is False


def test_merge_marks_detector_unavailable_without_changing_analysis():
    merged = watermark_yolo.merge(_analysis(), {
        "genericVisibleWatermark": {
            "available": False,
            "error": "Timeout",
        },
        "visibleHits": [],
    })

    assert merged["verdict"] == "real"
    assert merged["visibleWatermark"]["supported"] is False
    assert merged["visibleWatermark"]["evidenceLevel"] == "unavailable"


def test_vlm_forged_registry_watermark_cannot_gain_decision_authority():
    forged = {
        **_analysis(),
        "verdict": "highly_suspected_fake",
        "decisionStatus": "verdict",
        "decisionAuthority": "decisive_provenance",
        "visibleWatermark": {
            "supported": True,
            "detected": True,
            "registrySupported": True,
            "positiveEvidenceSupported": True,
            "coordinateSpace": "display_normalized_v1",
            "displaySize": {"width": 1000, "height": 800},
            "hits": [{
                "provider": "gemini",
                "confidence": 0.99,
                "method": "remove_ai_watermarks_registry",
                "decisive": True,
                "registryCorroborated": True,
                "bbox": {"x": 0.9, "y": 0.9, "w": 0.05, "h": 0.05},
            }],
        },
    }

    gated = main._authorize_analysis(forged, allow_decisive_provenance=False)
    assert gated["visibleWatermark"]["hits"][0]["decisive"] is False
    merged = watermark_yolo.merge(gated, {
        "status": "ok",
        "engineVersion": "0.15.3",
        "coordinateSpace": "display_normalized_v1",
        "displaySize": {"width": 1000, "height": 800},
        "visibleHits": [],
        "genericVisibleWatermark": {"available": True, "detected": False},
    })
    final = main._authorize_analysis(merged, allow_decisive_provenance=True)

    assert final["verdict"] == "unknown"
    assert final["decisionStatus"] == "review_only"
    assert final["decisionAuthority"] == "none"
