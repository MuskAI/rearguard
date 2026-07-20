import json
from pathlib import Path
import sys

import pytest
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import metadata, provenance, provenance_precheck


class _FakeC2paReader:
    validation_state = "Valid"

    def __init__(self, *_args, **_kwargs):
        pass

    def json(self):
        return json.dumps({
            "active_manifest": "manifest",
            "manifests": {
                "manifest": {
                    "claim_generator": "Example AI",
                    "assertions": [{
                        "label": "c2pa.actions",
                        "data": {"actions": [{
                            "action": "c2pa.created",
                            "digitalSourceType": "trainedAlgorithmicMedia",
                        }]},
                    }],
                },
            },
        })

    def get_validation_state(self):
        return self.validation_state

    def close(self):
        pass


def test_provenance_reader_marks_valid_signature_as_untrusted(monkeypatch):
    monkeypatch.setattr(provenance, "Reader", _FakeC2paReader)
    monkeypatch.setattr(
        provenance.metadata_reader,
        "inspect_metadata",
        lambda *_args, **_kwargs: {},
    )

    report = provenance.read_provenance(b"payload", "image/png", "sample.png")

    assert report["validationState"] == "Valid"
    assert report["credentialTrusted"] is False


def test_provenance_reader_marks_trusted_chain_as_trusted(monkeypatch):
    class TrustedReader(_FakeC2paReader):
        validation_state = "Trusted"

    monkeypatch.setattr(provenance, "Reader", TrustedReader)
    monkeypatch.setattr(
        provenance.metadata_reader,
        "inspect_metadata",
        lambda *_args, **_kwargs: {},
    )

    report = provenance.read_provenance(b"payload", "image/png", "sample.png")

    assert report["validationState"] == "Trusted"
    assert report["credentialTrusted"] is True


def test_provenance_parse_error_discards_partial_trusted_claim(monkeypatch):
    class MalformedIngredientsReader(_FakeC2paReader):
        validation_state = "Trusted"

        def json(self):
            document = json.loads(super().json())
            document["manifests"]["manifest"]["ingredients"] = [
                {"title": "source.png", "relationship": "parentOf"},
                "malformed ingredient",
            ]
            return json.dumps(document)

    monkeypatch.setattr(provenance, "Reader", MalformedIngredientsReader)
    monkeypatch.setattr(
        provenance.metadata_reader,
        "inspect_metadata",
        lambda *_args, **_kwargs: {},
    )

    report = provenance.read_provenance(b"payload", "image/png", "sample.png")

    assert report["error"].startswith("parse_error:")
    assert report["hasCredentials"] is False
    assert report["validationState"] is None
    assert report["credentialTrusted"] is False
    assert report["isAiGenerated"] is None
    assert report["actions"] == []
    assert report["ingredients"] == []
    assert provenance_precheck._local_source_decision(report) is None


def test_only_typed_manifest_not_found_is_reported_as_no_manifest(monkeypatch):
    class MissingReader:
        def __init__(self, *_args, **_kwargs):
            raise provenance.C2paError.ManifestNotFound("missing")

    monkeypatch.setattr(provenance, "Reader", MissingReader)
    monkeypatch.setattr(
        provenance.metadata_reader,
        "inspect_metadata",
        lambda *_args, **_kwargs: {},
    )

    report = provenance.read_provenance(b"payload", "image/png", "sample.png")

    assert report["error"] == "no_manifest"


def test_corrupt_c2pa_reader_failure_is_not_downgraded_to_no_manifest(monkeypatch):
    class CorruptReader:
        def __init__(self, *_args, **_kwargs):
            raise provenance.C2paError.Verify("invalid claim")

    monkeypatch.setattr(provenance, "Reader", CorruptReader)
    monkeypatch.setattr(
        provenance.metadata_reader,
        "inspect_metadata",
        lambda *_args, **_kwargs: {},
    )

    report = provenance.read_provenance(b"payload", "image/png", "sample.png")

    assert report["error"] == "c2pa_read_error:_C2paVerify"
    assert report["error"] != "no_manifest"


@pytest.mark.parametrize("source_type", ["negativeFilm", "positiveFilm", "print"])
def test_analog_or_print_sources_are_not_strong_direct_camera_evidence(
    monkeypatch,
    source_type,
):
    class AnalogReader(_FakeC2paReader):
        validation_state = "Trusted"

        def json(self):
            document = json.loads(super().json())
            action = document["manifests"]["manifest"]["assertions"][0]["data"]["actions"][0]
            action["digitalSourceType"] = (
                "http://cv.iptc.org/newscodes/digitalsourcetype/" + source_type
            )
            return json.dumps(document)

    monkeypatch.setattr(provenance, "Reader", AnalogReader)
    monkeypatch.setattr(
        provenance.metadata_reader,
        "inspect_metadata",
        lambda *_args, **_kwargs: {},
    )

    report = provenance.read_provenance(b"payload", "image/png", "sample.png")

    assert report["credentialTrusted"] is True
    assert report["isAiGenerated"] is None
    assert report["captureEvidence"] is None


def test_no_decision_does_not_build_analysis():
    assert provenance_precheck.build_analysis({"available": True, "decision": {"shortCircuit": False}}) is None


def test_visible_watermark_cannot_build_model_free_analysis():
    payload = {
        "status": "ok",
        "available": True,
        "engineVersion": "0.15.3",
        "coordinateSpace": "display_normalized_v1",
        "displaySize": {"width": 1000, "height": 800},
        "decision": {
            "shortCircuit": True,
            "verdict": "highly_suspected_fake",
            "confidence": 0.93,
            "reason": "known_visible_ai_watermark",
            "summary": "检测到已知 AI 可见标记。",
        },
        "report": {"aiFromMetadata": False, "signals": []},
        "visibleHits": [
            {
                "provider": "gemini",
                "label": "Google Gemini sparkle",
                "confidence": 0.84,
                "decisive": True,
                "bbox": {"x": 0.9, "y": 0.9, "w": 0.08, "h": 0.08},
            }
        ],
    }
    assert provenance_precheck.build_analysis(payload) is None


def test_remote_visual_result_reconciles_without_verdict_or_name_error():
    result = {
        "available": True,
        "report": {
            "aiFromMetadata": False,
            "isAiGenerated": None,
            "signals": [],
            "integrityClashes": [],
        },
        "visibleHits": [{
            "provider": "gemini",
            "confidence": 0.95,
            "decisive": True,
            "bbox": {"x": 0.8, "y": 0.8, "w": 0.1, "h": 0.1},
        }],
    }

    provenance_precheck._reconcile_probability(result, None)

    assert result["decision"]["shortCircuit"] is False
    assert result["decision"]["modelRequired"] is True
    assert result["decision"]["reason"] == "no_decisive_ai_provenance"


def test_remote_c2pa_trust_claim_cannot_short_circuit_without_local_validation():
    result = {
        "available": True,
        "report": {
            "isAiGenerated": True,
            "aiFromMetadata": True,
            "aiSourceKind": "generated",
            "c2paTrusted": True,
            "signals": [{"name": "c2pa", "kind": "valid_ai_c2pa"}],
            "integrityClashes": [],
        },
        "visibleHits": [],
    }

    provenance_precheck._reconcile_probability(result, None)

    assert result["report"]["c2paTrusted"] is False
    assert result["decision"]["shortCircuit"] is False
    assert result["decision"]["modelRequired"] is True


def test_visible_watermark_short_circuit_rejects_missing_coordinate_contract():
    payload = {
        "available": True,
        "engineVersion": "0.15.3",
        "decision": {
            "shortCircuit": True,
            "verdict": "highly_suspected_fake",
            "confidence": 0.99,
            "reason": "known_visible_ai_watermark",
        },
        "visibleHits": [{
            "provider": "gemini",
            "confidence": 0.99,
            "decisive": True,
            "bbox": {"x": 0.9, "y": 0.9, "w": 0.08, "h": 0.08},
        }],
    }

    assert provenance_precheck.build_analysis(payload) is None


def test_camera_c2pa_cannot_be_forged_into_short_circuit_by_client():
    payload = {
        "available": True,
        "decision": {
            "shortCircuit": True,
            "verdict": "real",
            "confidence": 0.99,
            "reason": "camera_c2pa",
        },
    }
    assert provenance_precheck.build_analysis(payload) is None


def test_high_confidence_local_metadata_remains_context_for_pixel_model():
    local = provenance_precheck._local_source_decision(
        {
            "metadataAiGenerated": True,
            "aiMetadata": {"score": 82, "matchedTools": ["TC260 AIGC 标识"]},
            "actions": [],
        }
    )
    assert local is not None
    decision, report = local
    assert decision["reason"] == "ai_metadata_context"
    assert decision["shortCircuit"] is False
    assert decision["modelRequired"] is True
    assert report["aiFromMetadata"] is True


def test_user_controlled_filename_cannot_create_ai_metadata_evidence():
    report = metadata.analyze_ai_metadata(
        {"file": {"name": "chatgpt_prompt_seed_12345_generated.jpg"}}
    )

    assert report["score"] == 0
    assert report["signalCount"] == 0
    assert report["isAiLikely"] is False


def test_invalid_ai_c2pa_requires_pixel_model_without_corroboration():
    local = provenance_precheck._local_source_decision(
        {
            "isAiGenerated": True,
            "validationState": "Invalid",
            "generator": "Example AI",
            "actions": [{"digitalSourceType": "trainedAlgorithmicMedia"}],
        }
    )
    assert local is not None
    decision, report = local
    assert decision["verdict"] is None
    assert decision["confidence"] == 0.0
    assert decision["modelRequired"] is True
    assert decision["evidenceKinds"] == ["c2pa", "integrity_clash"]
    assert report["c2paValidationState"] == "invalid"
    assert report["integrityClashes"] == ["c2pa_validation_invalid"]


def test_unknown_ai_c2pa_state_cannot_short_circuit_pixel_model():
    local = provenance_precheck._local_source_decision(
        {
            "isAiGenerated": True,
            "validationState": "unknown",
            "generator": "Example AI",
            "actions": [{"digitalSourceType": "trainedAlgorithmicMedia"}],
        }
    )
    assert local is not None
    decision, report = local
    assert decision["shortCircuit"] is False
    assert decision["modelRequired"] is True
    assert report["c2paTrusted"] is False


def test_valid_ai_c2pa_cannot_short_circuit_without_trusted_chain():
    local = provenance_precheck._local_source_decision(
        {
            "isAiGenerated": True,
            "validationState": "valid",
            "generator": "Example AI",
            "actions": [{"digitalSourceType": "trainedAlgorithmicMedia"}],
        }
    )
    assert local is not None
    decision, report = local
    assert decision["shortCircuit"] is False
    assert decision["modelRequired"] is True
    assert decision["evidenceKinds"] == ["c2pa"]
    assert report["c2paTrusted"] is False
    assert report["c2paValidationState"] == "valid"
    assert report["integrityClashes"] == []
    assert "保持中性" in decision["summary"]


def test_trusted_ai_c2pa_can_short_circuit_pixel_model():
    local = provenance_precheck._local_source_decision(
        {
            "isAiGenerated": True,
            "validationState": "Trusted",
            "credentialTrusted": True,
            "generator": "Example AI",
            "actions": [{"digitalSourceType": "trainedAlgorithmicMedia"}],
        }
    )
    assert local is not None
    decision, report = local
    assert decision["shortCircuit"] is True
    assert decision["modelRequired"] is False
    assert report["c2paTrusted"] is True


def test_visible_scan_reduces_large_png_and_keeps_dimensions_bounded(tmp_path):
    source = tmp_path / "large.png"
    Image.new("RGB", (2048, 2048), (20, 80, 130)).save(source, "PNG")
    data = source.read_bytes()
    scan, name, details = provenance_precheck._visible_scan(data, source.name)

    assert name.endswith(".visible-scan.jpg")
    assert details["scanDimensions"] == {"width": 1536, "height": 1536}
    assert details["scanBytes"] == len(scan)


def test_remote_visible_hits_keep_platform_and_unmatched_generic_watermarks():
    payload = {
        "visibleHits": [
            {
                "provider": "gemini",
                "confidence": 0.86,
                "bbox": {"x": 0.91, "y": 0.89, "w": 0.06, "h": 0.07},
            },
            {
                "provider": "yolo11x_watermark",
                "confidence": 0.93,
                "bbox": {"x": 0.90, "y": 0.88, "w": 0.08, "h": 0.10},
                "model": "corzent/yolo11x_watermark_detection",
                "modelRevision": "revision-1",
            },
            {
                "provider": "yolo11x_watermark",
                "confidence": 0.99,
                "bbox": {"x": 0.05, "y": 0.05, "w": 0.20, "h": 0.12},
            },
        ],
        "genericVisibleWatermark": {"available": True, "detected": True, "count": 2},
    }

    provenance_precheck._normalize_visible_hits(payload)

    assert len(payload["visibleHits"]) == 2
    assert payload["visibleHits"][0]["provider"] == "gemini"
    assert payload["visibleHits"][0]["yoloCorroborated"] is True
    assert payload["visibleHits"][0]["yoloConfidence"] == 0.93
    assert payload["visibleHits"][1]["provider"] == "yolo11x_watermark"
    assert payload["visibleHits"][1]["decisive"] is False
    assert payload["genericVisibleWatermark"]["count"] == 2
    assert payload["genericVisibleWatermark"]["genericCount"] == 1
    assert payload["genericVisibleWatermark"]["platformConfirmedCount"] == 1
    assert payload["genericVisibleWatermark"]["mode"] == "visible_watermark_detection_with_platform_attribution"


def test_remote_visible_hits_expose_generic_watermark_without_platform_match():
    payload = {
        "visibleHits": [{
            "provider": "yolo11x_watermark",
            "confidence": 0.99,
            "bbox": {"x": 0.05, "y": 0.05, "w": 0.2, "h": 0.1},
        }],
        "genericVisibleWatermark": {"available": True, "detected": True, "count": 1},
    }

    provenance_precheck._normalize_visible_hits(payload)

    assert len(payload["visibleHits"]) == 1
    assert payload["visibleHits"][0]["provider"] == "yolo11x_watermark"
    assert payload["visibleHits"][0]["decisive"] is False
    assert payload["genericVisibleWatermark"]["detected"] is True
    assert payload["genericVisibleWatermark"]["count"] == 1
    assert payload["genericVisibleWatermark"]["genericCount"] == 1
