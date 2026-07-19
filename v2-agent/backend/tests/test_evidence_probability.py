from app import evidence_probability


def _report(**overrides):
    value = {
        "isAiGenerated": None,
        "aiFromMetadata": False,
        "aiSourceKind": None,
        "signals": [],
        "integrityClashes": [],
    }
    value.update(overrides)
    return value


def _known_hit(confidence=0.86):
    return {
        "provider": "gemini",
        "label": "Google Gemini sparkle",
        "confidence": confidence,
        "decisive": True,
        "bbox": {"x": 0.8, "y": 0.8, "w": 0.08, "h": 0.08},
        "yoloCorroborated": True,
    }


def test_known_watermark_plus_editable_ai_metadata_is_not_false_corroboration():
    report = _report(
        isAiGenerated=True,
        aiFromMetadata=True,
        aiSourceKind="generated",
        signals=[{"name": "metadata", "confidence": "high"}],
    )
    model = evidence_probability.build_probability_model(report, [_known_hit()])
    watermark_only = evidence_probability.build_probability_model(_report(), [_known_hit()])
    assert model["posterior"] > watermark_only["posterior"]
    assert model["posterior"] < 0.99
    assert model["decisive"] is True


def test_known_watermark_plus_integrity_clash_exceeds_99_percent():
    model = evidence_probability.build_probability_model(
        _report(integrityClashes=["c2pa_signature_invalid"]),
        [_known_hit()],
    )
    assert model["posterior"] > 0.99


def test_generic_yolo_logo_is_probability_neutral():
    generic = {"provider": "yolo11x_watermark", "confidence": 0.99, "decisive": False}
    clean = evidence_probability.build_probability_model(_report(), [])
    with_logo = evidence_probability.build_probability_model(_report(), [generic])
    assert with_logo["posterior"] == clean["posterior"]
    assert with_logo["effectiveLikelihoodRatio"] == 1.0


def test_integrity_clash_alone_cannot_create_99_percent_result():
    model = evidence_probability.build_probability_model(
        _report(integrityClashes=["metadata_conflict"]),
        [],
    )
    fused = evidence_probability.fuse_with_analysis(
        {"confidence": 0.2, "verdict": "real", "dimensions": [], "explanation": "像素模型倾向真实。"},
        model,
    )
    assert 0.5 < fused["confidence"] < 0.9


def test_invalid_c2pa_declaration_and_signature_error_are_same_source():
    model = evidence_probability.build_probability_model(
        _report(
            isAiGenerated=True,
            aiFromMetadata=True,
            aiSourceKind="generated",
            signals=[{"name": "c2pa", "confidence": "high"}],
            integrityClashes=["c2pa_signature_invalid"],
        ),
        [],
    )
    assert model["corroborated"] is False
    assert {factor["group"] for factor in model["factors"]} == {"untrusted_provenance"}
    assert any(factor["correlationExponent"] < 1 for factor in model["factors"])
    assert 0.5 < model["posterior"] < 0.65


def test_more_independent_positive_evidence_is_monotonic():
    watermark_only = evidence_probability.build_probability_model(_report(), [_known_hit()])
    corroborated = evidence_probability.build_probability_model(
        _report(
            isAiGenerated=True,
            aiFromMetadata=True,
            aiSourceKind="generated",
            signals=[{"name": "metadata", "confidence": "high"}],
        ),
        [_known_hit()],
    )
    assert corroborated["posterior"] > watermark_only["posterior"]


def test_editable_metadata_alone_is_not_decisive():
    model = evidence_probability.build_probability_model(
        _report(
            isAiGenerated=True,
            aiFromMetadata=True,
            aiSourceKind="generated",
            signals=[{"name": "metadata", "confidence": "high"}],
        ),
        [],
    )
    assert model["decisive"] is False
    assert model["posterior"] < 0.2


def test_coherent_camera_metadata_reduces_pixel_risk_modestly():
    model = evidence_probability.build_probability_model(
        _report(captureEvidence={
            "level": "medium",
            "supportsRealCapture": True,
            "likelihoodRatio": 0.65,
        }),
        [],
    )
    fused = evidence_probability.fuse_with_analysis(
        {"confidence": 0.6, "verdict": "suspected_fake", "dimensions": [], "explanation": "像素模型处于边界区间。"},
        model,
    )

    assert 0.45 < fused["confidence"] < 0.6
    assert fused["probabilityModel"]["factors"][0]["direction"] == "real"
    assert "适度下调" in fused["explanation"]


def test_camera_metadata_cannot_override_known_ai_watermark():
    model = evidence_probability.build_probability_model(
        _report(captureEvidence={
            "level": "medium",
            "supportsRealCapture": True,
            "likelihoodRatio": 0.65,
        }),
        [_known_hit(0.95)],
    )
    fused = evidence_probability.fuse_with_analysis(
        {"confidence": 0.2, "verdict": "real", "dimensions": [], "explanation": "像素模型倾向真实。"},
        model,
    )

    assert model["conflicting"] is True
    assert fused["confidence"] > 0.9
    assert "相互制衡" in fused["explanation"]
