from policy import build_decision, visible_hit_is_decisive


def report(**overrides):
    base = {
        "isAiGenerated": None,
        "aiFromMetadata": False,
        "aiSourceKind": None,
        "signals": [],
        "integrityClashes": [],
    }
    base.update(overrides)
    return base


def test_no_signal_requires_model():
    decision = build_decision(report(), [])
    assert decision["shortCircuit"] is False
    assert decision["modelRequired"] is True


def test_generic_visible_logo_does_not_short_circuit():
    decision = build_decision(report(), [{"provider": "unknown", "confidence": 0.99}])
    assert decision["shortCircuit"] is False


def test_known_visible_ai_mark_short_circuits():
    hit = {"provider": "gemini", "label": "Google Gemini sparkle", "confidence": 0.86, "decisive": True}
    decision = build_decision(report(), [hit])
    assert decision["shortCircuit"] is True
    assert decision["modelRequired"] is False
    assert decision["reason"] == "known_visible_ai_watermark"


def test_uncorroborated_medium_visible_match_remains_a_clue_only():
    hit = {"provider": "gemini", "label": "Google Gemini sparkle", "confidence": 0.70, "decisive": False}
    assert build_decision(report(), [hit])["shortCircuit"] is False


def test_gemini_visible_hit_requires_bottom_right_location():
    assert visible_hit_is_decisive(
        "gemini",
        0.70,
        {"x": 0.48, "y": 0.85, "w": 0.09, "h": 0.07},
        corroborated=False,
    ) is False
    assert visible_hit_is_decisive(
        "gemini",
        0.65,
        {"x": 0.92, "y": 0.92, "w": 0.05, "h": 0.05},
        corroborated=False,
    ) is True


def test_camera_c2pa_does_not_short_circuit():
    capture = report(
        isAiGenerated=None,
        aiFromMetadata=False,
        signals=[{"name": "c2pa", "detail": "Leica digitalCapture", "confidence": "high"}],
    )
    assert build_decision(capture, [])["shortCircuit"] is False


def test_c2pa_generated_short_circuits():
    generated = report(
        isAiGenerated=True,
        aiFromMetadata=True,
        aiSourceKind="generated",
        signals=[{"name": "c2pa", "detail": "trainedAlgorithmicMedia", "confidence": "high"}],
    )
    decision = build_decision(generated, [])
    assert decision["shortCircuit"] is True
    assert decision["verdict"] == "highly_suspected_fake"
    assert decision["confidence"] >= 0.99


def test_c2pa_enhanced_is_not_claimed_as_fully_generated():
    enhanced = report(
        isAiGenerated=True,
        aiFromMetadata=True,
        aiSourceKind="enhanced",
        signals=[{"name": "c2pa", "detail": "compositeWithTrainedAlgorithmicMedia", "confidence": "high"}],
    )
    decision = build_decision(enhanced, [])
    assert decision["shortCircuit"] is True
    assert decision["verdict"] == "suspected_fake"


def test_known_watermark_and_ai_metadata_exceed_99_percent():
    generated = report(
        isAiGenerated=True,
        aiFromMetadata=True,
        aiSourceKind="generated",
        signals=[{"name": "metadata", "detail": "Stable Diffusion parameters", "confidence": "high"}],
    )
    hit = {"provider": "gemini", "label": "Google Gemini sparkle", "confidence": 0.86, "decisive": True}
    decision = build_decision(generated, [hit])

    assert decision["reason"] == "corroborated_ai_provenance"
    assert decision["confidence"] > 0.99
    assert decision["probabilityModel"]["corroborated"] is True


def test_known_watermark_and_metadata_integrity_clash_exceed_99_percent():
    conflicted = report(integrityClashes=["c2pa_signature_invalid"])
    hit = {"provider": "gemini", "label": "Google Gemini sparkle", "confidence": 0.86, "decisive": True}
    decision = build_decision(conflicted, [hit])

    assert decision["reason"] == "watermark_metadata_conflict"
    assert decision["confidence"] > 0.99


def test_generic_logo_never_becomes_ai_evidence_even_with_integrity_clash():
    conflicted = report(integrityClashes=["metadata_conflict"])
    generic = {"provider": "yolo11x_watermark", "confidence": 0.99, "decisive": False}
    decision = build_decision(conflicted, [generic])

    assert decision["shortCircuit"] is False
    assert decision["probabilityModel"]["posterior"] < 0.9
    assert all(
        factor["kind"] != "known_visible_ai_watermark"
        for factor in decision["probabilityModel"]["factors"]
    )


def test_corruption_without_corroboration_does_not_skip_pixel_model():
    invalid = report(
        isAiGenerated=True,
        aiFromMetadata=True,
        aiSourceKind="generated",
        signals=[{"name": "c2pa", "confidence": "high"}],
        integrityClashes=["c2pa_signature_invalid"],
    )
    decision = build_decision(invalid, [])

    assert decision["shortCircuit"] is False
    assert decision["modelRequired"] is True
    assert 0.5 < decision["probabilityModel"]["posterior"] < 0.65
    assert decision["probabilityModel"]["corroborated"] is False
    assert {factor["group"] for factor in decision["probabilityModel"]["factors"]} == {"untrusted_provenance"}
