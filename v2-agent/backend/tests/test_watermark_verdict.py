from app import watermark_verdict


def _visible(provider: str = "gemini", *, decisive: bool = True) -> dict:
    return {
        "detected": True,
        "provider": provider,
        "hits": [{
            "provider": provider,
            "label": "Google Gemini" if provider == "gemini" else "可见水印（平台待确认）",
            "confidence": 0.86,
            "bbox": {"x": 0.7, "y": 0.8, "w": 0.2, "h": 0.1},
            "method": "remove_ai_watermarks_registry" if provider == "gemini" else "yolo11x_watermark_detection",
            "decisive": decisive,
            "localizationConfirmed": provider == "gemini",
        }],
    }


def test_confirmed_ai_platform_watermark_forces_high_confidence_fake():
    result = {
        "verdict": "real",
        "confidence": 0.21,
        "explanation": "主模型偏向真实。",
        "dimensions": [{"key": "aigc", "label": "AIGC生成检测", "score": 0.21}],
        "provenance": {"hasCredentials": False},
    }

    watermark_verdict.apply(result, _visible())
    watermark_verdict.apply(result, _visible())

    assert result["verdict"] == "highly_suspected_fake"
    assert result["confidence"] == 0.95
    assert result["watermarkVerdictOverride"]["modelConfidence"] == 0.21
    assert result["watermarkVerdictOverride"]["reason"] == "known_ai_platform_visible_watermark"
    assert "决定性证据" in result["explanation"]
    assert "Google Gemini" in result["explanation"]
    assert "原始 AI 风险为 21.0%" in result["explanation"]
    assert "凭证缺失本身不作为伪造证据" in result["explanation"]
    assert "当前置信度：高" in result["explanation"]


def test_generic_visible_watermark_never_overrides_ai_verdict():
    result = {"verdict": "real", "confidence": 0.21, "explanation": "主模型偏向真实。"}

    watermark_verdict.apply(result, _visible("yolo11x_watermark", decisive=False))

    assert result == {"verdict": "real", "confidence": 0.21, "explanation": "主模型偏向真实。"}


def test_untrusted_decisive_flag_cannot_promote_generic_watermark():
    result = {"verdict": "real", "confidence": 0.21, "explanation": "主模型偏向真实。"}

    watermark_verdict.apply(result, _visible("yolo11x_watermark", decisive=True))

    assert result == {"verdict": "real", "confidence": 0.21, "explanation": "主模型偏向真实。"}


def test_detected_flag_without_valid_box_does_not_override():
    result = {"verdict": "real", "confidence": 0.21, "explanation": "主模型偏向真实。"}

    watermark_verdict.apply(result, {"detected": True, "hits": []})

    assert result == {"verdict": "real", "confidence": 0.21, "explanation": "主模型偏向真实。"}


def test_low_confidence_tiny_box_cannot_force_fake_verdict():
    result = {"verdict": "real", "confidence": 0.21, "explanation": "主模型偏向真实。"}
    visible = _visible()
    visible["hits"][0].update({
        "confidence": 0.01,
        "bbox": {"x": 0.8, "y": 0.8, "w": 0.001, "h": 0.001},
    })

    watermark_verdict.apply(result, visible)

    assert result == {"verdict": "real", "confidence": 0.21, "explanation": "主模型偏向真实。"}
