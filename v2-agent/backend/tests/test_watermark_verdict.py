from app import watermark_verdict


def _visible() -> dict:
    return {
        "detected": True,
        "provider": "yolo11x_watermark",
        "hits": [{
            "provider": "yolo11x_watermark",
            "confidence": 0.71,
            "bbox": {"x": 0.7, "y": 0.8, "w": 0.2, "h": 0.1},
        }],
    }


def test_localized_visible_watermark_forces_high_confidence_fake():
    result = {"verdict": "real", "confidence": 0.21, "explanation": "主模型偏向真实。"}

    watermark_verdict.apply(result, _visible())

    assert result["verdict"] == "highly_suspected_fake"
    assert result["confidence"] == 0.95
    assert result["watermarkVerdictOverride"]["modelConfidence"] == 0.21
    assert "有效定位框" in result["explanation"]


def test_detected_flag_without_valid_box_does_not_override():
    result = {"verdict": "real", "confidence": 0.21, "explanation": "主模型偏向真实。"}

    watermark_verdict.apply(result, {"detected": True, "hits": []})

    assert result == {"verdict": "real", "confidence": 0.21, "explanation": "主模型偏向真实。"}
