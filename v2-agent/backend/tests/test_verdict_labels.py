from app.verdict_labels import FAKE_VERDICT, REAL_VERDICT, binary_label, binary_verdict


def test_explicit_binary_verdict_is_preserved():
    assert binary_verdict({"verdict": "real", "confidence": 0.99}) == REAL_VERDICT
    assert binary_verdict({"verdict": "suspected_fake", "confidence": 0.1}) == FAKE_VERDICT


def test_unknown_verdict_uses_available_model_direction():
    assert binary_verdict({"verdict": "unknown", "confidence": 0.21}) == REAL_VERDICT
    assert binary_verdict({"verdict": "unknown", "riskScore": 0.81}) == FAKE_VERDICT


def test_missing_direction_defaults_to_real_with_low_confidence_handled_separately():
    assert binary_verdict({"verdict": "unknown"}) == REAL_VERDICT
    assert binary_label({"verdict": "unknown"}) == "真实图像"
