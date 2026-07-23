import json
from datetime import datetime, timezone

from imagedetection.views import capture_evidence, detection, probability_fusion


def _camera_metadata():
    return {
        "EXIF:Make": "Canon",
        "EXIF:Model": "EOS R5",
        "EXIF:LensModel": "RF24-70mm F2.8 L IS USM",
        "EXIF:ExposureTime": "1/250",
        "EXIF:FNumber": "2.8",
        "EXIF:ISO": "200",
        "EXIF:FocalLength": "50 mm",
        "EXIF:DateTimeOriginal": "2025:06:12 10:21:33",
        "EXIF:OffsetTimeOriginal": "+08:00",
        "EXIF:BodySerialNumber": "secret-body-1234",
        "EXIF:GPSLatitude": "30.123456",
        "EXIF:GPSLongitude": "120.654321",
        "EXIF:MakerNote": {"FocusMode": "AI Servo"},
    }


def test_coherent_camera_metadata_is_medium_support_and_redacted():
    result = capture_evidence.analyze_capture_evidence(
        _camera_metadata(),
        now=datetime(2026, 7, 18, tzinfo=timezone.utc),
    )

    assert result["level"] == "medium"
    assert result["profile"] == "native_capture_chain"
    assert result["adjustmentEligible"] is True
    assert result["nativeSupportCount"] >= 2
    assert result["likelihoodRatio"] == 0.45
    assert result["supportsRealCapture"] is True
    assert 0 < result["likelihoodRatio"] < 1
    serialized = json.dumps(result, ensure_ascii=False)
    assert "secret-body-1234" not in serialized
    assert "30.123456" not in serialized
    assert "120.654321" not in serialized
    assert "2025:06:12 10:21:33" not in serialized


def test_ai_declaration_blocks_camera_metadata_from_lowering_risk():
    result = capture_evidence.analyze_capture_evidence(
        _camera_metadata(),
        ai_markers=["Stable Diffusion workflow"],
    )

    assert result["level"] == "conflict"
    assert result["supportsRealCapture"] is False
    assert result["likelihoodRatio"] == 1.0
    assert result["conflicts"][0]["key"] == "ai_declaration"


def test_verified_camera_credential_is_strong_but_conflicts_are_not_upgraded():
    clean = capture_evidence.analyze_capture_evidence(_camera_metadata())
    signed = capture_evidence.add_verified_camera_credential(clean, issuer="Example Camera CA")
    conflicted = capture_evidence.analyze_capture_evidence(_camera_metadata(), ai_markers=["ComfyUI"])

    assert signed["level"] == "strong"
    assert signed["likelihoodRatio"] == 0.08
    assert signed["evidence"][0]["key"] == "c2pa_camera"
    assert capture_evidence.add_verified_camera_credential(conflicted)["level"] == "conflict"


def test_metadata_expert_exposes_capture_evidence_and_reduces_risk_modestly():
    metadata = detection._swarm_metadata_expert({"all_metadata": _camera_metadata()})
    model = probability_fusion.fuse([
        {"id": "primary", "status": "success", "score": 0.6, "weight": 1.0},
        {"id": "metadata", "status": "success", "weight": 0.2, **metadata},
    ])

    assert metadata["details"]["captureEvidence"]["level"] == "medium"
    assert model["pixelBaseline"] == 0.6
    assert 0.45 < model["posterior"] < 0.6
    assert model["factors"][0]["direction"] == "real"


def test_fast_detection_uses_same_capture_probability_fusion():
    fused, model, metadata_probability = detection._fuse_fast_metadata_probability(
        0.6,
        _camera_metadata(),
    )

    assert 0.45 < fused < 0.6
    assert metadata_probability == 0.28
    assert model["pixelBaseline"] == 0.6
    assert model["factors"][0]["kind"] == "camera_capture_metadata"


def test_rich_native_capture_chain_can_correct_borderline_model_risk():
    capture = capture_evidence.analyze_capture_evidence(_camera_metadata())
    _fused, model, _metadata_probability = detection._fuse_fast_metadata_probability(
        0.7,
        _camera_metadata(),
    )

    guardrail = detection._capture_evidence_guardrail(0.7, model, capture)

    assert guardrail["eligible"] is True
    assert guardrail["applied"] is True
    assert guardrail["adjusted"] < 0.62
    assert guardrail["reason"] == "rich_native_capture_chain"


def test_capture_guardrail_does_not_override_high_model_risk_or_strong_ai_evidence():
    capture = capture_evidence.analyze_capture_evidence(_camera_metadata())
    high_risk_model = {"posterior": 0.78, "factors": []}
    blocked_model = {
        "posterior": 0.4,
        "factors": [{
            "kind": "known_visible_ai_watermark",
            "label": "已知 AI 平台水印",
            "group": "known_watermark",
            "effectiveLikelihoodRatio": 120.0,
        }],
    }

    high_risk = detection._capture_evidence_guardrail(0.9, high_risk_model, capture)
    blocked = detection._capture_evidence_guardrail(0.7, blocked_model, capture)

    assert high_risk["applied"] is False
    assert high_risk["reason"] == "model_risk_above_guardrail_range"
    assert blocked["applied"] is False
    assert blocked["reason"] == "strong_conflicting_evidence"


def test_copyable_basic_exif_does_not_activate_capture_guardrail():
    basic_metadata = {
        "EXIF:Make": "Apple",
        "EXIF:Model": "iPhone",
        "EXIF:ExposureTime": "1/100",
        "EXIF:FNumber": "1.8",
        "EXIF:ISO": "100",
        "EXIF:DateTimeOriginal": "2026:07:20 10:21:33",
    }
    capture = capture_evidence.analyze_capture_evidence(basic_metadata)
    _fused, model, _metadata_probability = detection._fuse_fast_metadata_probability(
        0.7,
        basic_metadata,
    )

    guardrail = detection._capture_evidence_guardrail(0.7, model, capture)

    assert capture["profile"] == "coherent_exif"
    assert capture["adjustmentEligible"] is False
    assert guardrail["applied"] is False
    assert guardrail["reason"] == "capture_chain_not_strong_enough"
