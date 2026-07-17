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
    }


def test_known_watermark_plus_ai_metadata_exceeds_99_percent():
    report = _report(
        isAiGenerated=True,
        aiFromMetadata=True,
        aiSourceKind="generated",
        signals=[{"name": "metadata", "confidence": "high"}],
    )
    model = evidence_probability.build_probability_model(report, [_known_hit()])
    assert model["posterior"] > 0.99
    assert model["corroborated"] is True


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
