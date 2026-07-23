from pathlib import Path
import copy
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from imagedetection.views import swarm_visible_watermark_expert as expert  # noqa: E402
from imagedetection.views import watermark_verdict  # noqa: E402


def _strong_precheck():
    bbox = {"x": 0.72, "y": 0.81, "w": 0.18, "h": 0.09}
    return {
        "status": "ok",
        "coordinateSpace": "display_normalized_v1",
        "displaySize": {"width": 1280, "height": 720},
        "genericVisibleWatermark": {
            "available": True,
            "detected": True,
            "count": 1,
        },
        "visibleHits": [{
            "provider": "doubao",
            "label": "Doubao 豆包AI生成 text",
            "confidence": 0.94,
            "bbox": bbox,
            "corroborated": True,
            "yoloCorroborated": True,
            "yoloConfidence": 0.91,
        }],
        "explicitWatermark": {
            "available": True,
            "detected": True,
            "type": "text",
            "sourcePlatform": "豆包",
            "confidence": 0.94,
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
                "sourcePlatform": "豆包",
                "confidence": 0.94,
                "textAnalysis": {
                    "verdict": "supports_ai_generation",
                    "likelyAIgenerated": True,
                    "aiGenerationConfidence": 0.99,
                    "platformMatch": "豆包",
                },
                "retrievalAccepted": True,
                "registryMatched": True,
                "yoloCorroborated": True,
            }],
        },
    }


def _review_result():
    return {
        "final_label": "需人工复核",
        "probability": 0.5,
        "detector_probability": 0.5,
        "confidence": "低",
        "reviewRequired": True,
        "decisionStatus": "review_only",
        "decisionAuthority": "none",
        "explanation": "主模型尚未通过独立校准。",
    }


def test_strong_explicit_ai_watermark_authorizes_final_verdict():
    visible = expert._visible_result(_strong_precheck())
    result = _review_result()

    assert visible["hits"][0]["decisive"] is True
    assert watermark_verdict.has_decisive_ai_watermark(visible) is True
    assert watermark_verdict.apply_to_result(result, visible) is True
    assert result["final_label"] == "AI生成图像"
    assert result["probability"] == 0.99
    assert result["confidence"] == "高"
    assert result["reviewRequired"] is False
    assert result["decisionStatus"] == "verdict"
    assert result["decisionAuthority"] == "decisive_provenance"
    assert result["watermark_verdict_override"]["applied"] is True
    assert "强 AI 水印证据" in result["explanation"]


def test_single_signal_watermark_does_not_authorize_verdict():
    payload = _strong_precheck()
    explicit_hit = payload["explicitWatermark"]["hits"][0]
    explicit_hit["textAnalysis"] = {}
    explicit_hit["retrievalAccepted"] = False
    explicit_hit["yoloCorroborated"] = False
    visible = expert._visible_result(payload)
    result = _review_result()

    assert watermark_verdict.has_decisive_ai_watermark(visible) is False
    assert watermark_verdict.apply_to_result(result, visible) is False
    assert result["final_label"] == "需人工复核"


def test_backend_data_uses_same_strong_watermark_decision():
    visible = expert._visible_result(_strong_precheck())
    data = {
        "fake_percentage": 21.0,
        "detector_probability": 0.21,
        "final_label": "真实图像",
        "confidence": "中",
        "explanation": "主模型偏向真实。",
    }

    assert watermark_verdict.apply_to_backend_data(data, copy.deepcopy(visible)) is True
    assert data["fake_percentage"] == 99.0
    assert data["detector_probability"] == 0.21
    assert data["final_label"] == "AI生成图像"
    assert data["confidence"] == "高"
    assert data["watermark_verdict_override"]["decisionAuthority"] == "decisive_provenance"
