from imagedetection.decision_labels import (
    AI_GENERATED_VIDEO_LABEL,
    AI_GENERATED_LABEL,
    REAL_IMAGE_LABEL,
    REAL_VIDEO_LABEL,
    binary_final_label,
    binary_video_final_label,
    normalized_fake_probability,
    public_video_probability,
)
from imagedetection.views import report_pdf


def test_binary_label_uses_score_when_input_is_review_state():
    assert binary_final_label("需人工复核", 0.8) == AI_GENERATED_LABEL
    assert binary_final_label("需人工复核", 0.2) == REAL_IMAGE_LABEL


def test_binary_label_normalizes_specialized_risk_labels():
    assert binary_final_label("疑似篡改图像", 0.2) == AI_GENERATED_LABEL
    assert binary_final_label("疑似深伪图像", 0.2) == AI_GENERATED_LABEL
    assert binary_final_label("高风险", 0.2) == AI_GENERATED_LABEL
    assert binary_final_label("低风险", 0.8) == REAL_IMAGE_LABEL


def test_explicit_binary_label_is_preserved():
    assert binary_final_label(AI_GENERATED_LABEL, 0.1) == AI_GENERATED_LABEL
    assert binary_final_label(REAL_IMAGE_LABEL, 0.9) == REAL_IMAGE_LABEL


def test_video_label_uses_video_specific_copy():
    assert binary_video_final_label("fake", 0.1) == AI_GENERATED_VIDEO_LABEL
    assert binary_video_final_label("真实视频", 0.9) == REAL_VIDEO_LABEL
    assert binary_video_final_label("需人工复核", 18) == REAL_VIDEO_LABEL


def test_probability_normalization_accepts_percent_values():
    assert normalized_fake_probability(83) == 0.83
    assert normalized_fake_probability(-2) == 0.0
    assert normalized_fake_probability(120) == 1.0


def test_public_video_probability_exposes_complementary_uncalibrated_scores():
    score = public_video_probability(83.275)

    assert score["fake_percentage"] == 83.28
    assert score["real_percentage"] == 16.72
    assert score["probability_calibrated"] is False
    assert "未经" in score["probability_notice"]


def test_public_video_probability_does_not_fabricate_missing_score():
    score = public_video_probability(None)

    assert score["fake_percentage"] is None
    assert score["real_percentage"] is None


def test_image_pdf_normalizes_low_risk_before_render(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        report_pdf,
        "_build_report",
        lambda **kwargs: captured.update(kwargs) or b"%PDF-test",
    )

    output = report_pdf.image_report_pdf(
        {"itemid": 7, "fake": 80, "createtime": "2026-08-08 10:00:00"},
        {
            "decisionStatus": "verdict",
            "final_label": "低风险",
            "probability": 0.2,
            "confidence": "高",
        },
    )

    assert output == b"%PDF-test"
    assert captured["final_label"] == REAL_IMAGE_LABEL


def test_video_pdf_exposes_score_and_uses_video_label(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        report_pdf,
        "_build_report",
        lambda **kwargs: captured.update(kwargs) or b"%PDF-video-test",
    )

    output = report_pdf.video_report_pdf(
        {"itemid": 8, "fake": 72.5, "createtime": "2026-08-29 10:00:00"},
        {
            "final_label": "AI生成视频",
            "fake_percentage": 72.5,
            "confidence": "低",
            "decisionStatus": "review_only",
            "reviewRequired": True,
            "meta": {},
            "evidence": {},
        },
    )

    assert output == b"%PDF-video-test"
    assert captured["final_label"] == AI_GENERATED_VIDEO_LABEL
    assert "72.50%" in captured["score_summary"]
    assert captured["summary_rows"][1][2] == "72.50%"
    assert captured["summary_rows"][2][2] == "27.50%"
