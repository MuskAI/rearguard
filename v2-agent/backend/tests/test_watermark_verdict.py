from app import watermark_verdict


def _visible(provider: str = "gemini", *, decisive: bool = True) -> dict:
    return {
        "detected": True,
        "provider": provider,
        "coordinateSpace": "display_normalized_v1",
        "displaySize": {"width": 1000, "height": 800},
        "registrySupported": True,
        "positiveEvidenceSupported": True,
        "hits": [{
            "provider": provider,
            "label": "Google Gemini" if provider == "gemini" else "可见水印（平台待确认）",
            "confidence": 0.86,
            "bbox": {"x": 0.7, "y": 0.8, "w": 0.2, "h": 0.1},
            "method": "remove_ai_watermarks_registry" if provider == "gemini" else "yolo11x_watermark_detection",
            "decisive": decisive,
            "registryCorroborated": False,
            "localizationConfirmed": provider == "gemini",
        }],
    }


def test_confirmed_ai_platform_watermark_remains_visual_evidence_only():
    result = {
        "verdict": "real",
        "confidence": 0.21,
        "explanation": "主模型偏向真实。",
        "dimensions": [{"key": "aigc", "label": "AIGC生成检测", "score": 0.21}],
        "provenance": {"hasCredentials": False},
    }

    watermark_verdict.apply(result, _visible())
    watermark_verdict.apply(result, _visible())

    assert result["verdict"] == "real"
    assert result["confidence"] == 0.21
    assert "watermarkVerdictOverride" not in result
    assert result["explanation"] == "主模型偏向真实。"


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


def test_registry_corroboration_does_not_make_copyable_mark_decisive():
    result = {"verdict": "real", "confidence": 0.21, "explanation": "主模型偏向真实。"}
    visible = _visible()
    visible["hits"][0].update({"confidence": 0.55, "registryCorroborated": True})

    watermark_verdict.apply(result, visible)

    assert result["verdict"] == "real"
    assert "decisionAuthority" not in result


def test_bbox_overflow_is_rejected_instead_of_clamped():
    result = {"verdict": "real", "confidence": 0.21, "explanation": "主模型偏向真实。"}
    visible = _visible()
    visible["hits"][0]["bbox"] = {"x": 0.9001, "y": 0.2, "w": 0.1, "h": 0.1}

    watermark_verdict.apply(result, visible)

    assert result["verdict"] == "real"


def test_trusted_direct_model_watermark_can_authorize_fake_without_fusion():
    result = {"verdict": "real", "confidence": 0.12, "explanation": "主模型偏向真实。"}
    visible = {
        "supported": True,
        "detected": True,
        "coordinateSpace": "display_normalized_v1",
        "displaySize": {"width": 1000, "height": 800},
        "explicitWatermark": {
            "available": True,
            "detected": True,
            "confidence": 0.94,
            "mode": "model_direct",
            "aiWatermarkVerdict": {"verdict": "yes", "confidence": 0.94},
        },
        "hits": [{
            "provider": "yolo11x_watermark",
            "label": "显式水印",
            "confidence": 0.94,
            "bbox": {"x": 0.7, "y": 0.8, "w": 0.2, "h": 0.1},
            "method": "explicit_watermark_model_direct",
            "decisive": True,
        }],
    }

    watermark_verdict.apply(result, visible)

    assert result["verdict"] == "highly_suspected_fake"
    assert result["confidence"] == 0.95
    assert result["watermarkVerdictOverride"]["policyVersion"] == "explicit-watermark-model-direct-v3"
    assert "不依赖文字识别、平台模板或图像检索" in result["explanation"]


def test_direct_model_non_corner_detection_authorizes_fake():
    result = {"verdict": "real", "confidence": 0.12, "explanation": "主模型偏向真实。"}
    visible = {
        "supported": True,
        "detected": True,
        "coordinateSpace": "display_normalized_v1",
        "displaySize": {"width": 1000, "height": 800},
        "hits": [{
            "provider": "yolo11x_watermark",
            "label": "疑似标记区域（待核验）",
            "confidence": 0.98,
            "bbox": {"x": 0.28, "y": 0.04, "w": 0.44, "h": 0.08},
            "method": "explicit_watermark_model_direct",
            "decisive": True,
        }],
    }

    watermark_verdict.apply(result, visible)

    assert result["verdict"] == "highly_suspected_fake"
    assert result["watermarkVerdictOverride"]["policyVersion"] == "explicit-watermark-model-direct-v3"


def test_direct_model_detection_below_display_threshold_stays_non_decisive():
    result = {"verdict": "real", "confidence": 0.12, "explanation": "主模型偏向真实。"}
    visible = {
        "detected": True,
        "coordinateSpace": "display_normalized_v1",
        "displaySize": {"width": 1000, "height": 800},
        "hits": [{
            "provider": "yolo11x_watermark",
            "label": "疑似显式水印",
            "confidence": 0.49,
            "bbox": {"x": 0.28, "y": 0.04, "w": 0.44, "h": 0.08},
            "method": "explicit_watermark_model_direct",
            "decisive": True,
        }],
    }

    watermark_verdict.apply(result, visible)

    assert result["verdict"] == "real"
    assert "watermarkVerdictOverride" not in result
