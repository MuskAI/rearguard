from imagedetection.decision_labels import (
    AI_GENERATED_LABEL,
    REAL_IMAGE_LABEL,
    binary_final_label,
    normalized_fake_probability,
)


def test_binary_label_uses_score_when_input_is_review_state():
    assert binary_final_label("需人工复核", 0.8) == AI_GENERATED_LABEL
    assert binary_final_label("需人工复核", 0.2) == REAL_IMAGE_LABEL


def test_binary_label_normalizes_specialized_risk_labels():
    assert binary_final_label("疑似篡改图像", 0.2) == AI_GENERATED_LABEL
    assert binary_final_label("疑似深伪图像", 0.2) == AI_GENERATED_LABEL


def test_explicit_binary_label_is_preserved():
    assert binary_final_label(AI_GENERATED_LABEL, 0.1) == AI_GENERATED_LABEL
    assert binary_final_label(REAL_IMAGE_LABEL, 0.9) == REAL_IMAGE_LABEL


def test_probability_normalization_accepts_percent_values():
    assert normalized_fake_probability(83) == 0.83
    assert normalized_fake_probability(-2) == 0.0
    assert normalized_fake_probability(120) == 1.0
