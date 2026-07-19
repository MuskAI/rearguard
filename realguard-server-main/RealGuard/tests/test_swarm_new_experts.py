"""Unit tests for the C2PA and watermark Swarm experts."""

from __future__ import annotations

import io
import json
import os
import sys

import pytest

# Make repo root importable when running pytest from the RealGuard dir.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from imagedetection.views import swarm_c2pa_expert, swarm_wam_expert, swarm_watermark_expert


# --------------------------- shared fixtures --------------------------- #

@pytest.fixture(scope="module")
def synthetic_image_bytes():
    """A realistic-ish 512x512 PNG with gradients + noise — survives DCT/DWT."""
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    rng = np.random.default_rng(7)
    h, w = 512, 512
    y_idx, x_idx = np.mgrid[0:h, 0:w].astype(np.float32)
    r = (np.sin(x_idx / 40) * 32 + 128).clip(0, 255)
    g = (np.cos(y_idx / 35) * 32 + 140).clip(0, 255)
    b = (np.sin((x_idx + y_idx) / 55) * 32 + 120).clip(0, 255)
    img = np.stack([b, g, r], axis=-1).astype(np.uint8)
    img = (img + rng.normal(0, 4, img.shape).astype(np.int16)).clip(0, 255).astype(np.uint8)
    ok, encoded = cv2.imencode(".png", img)
    assert ok
    return encoded.tobytes(), img


# ----------------------------- C2PA expert ----------------------------- #

def _shape_ok(update: dict) -> None:
    """Every expert update must follow the Swarm contract."""
    assert set(update.keys()) >= {"status", "score", "verdict", "confidence", "evidence", "message", "latencyMs"}
    assert update["status"] in {"success", "failed", "skipped"}
    assert isinstance(update["evidence"], list)
    assert update["latencyMs"] is None or isinstance(update["latencyMs"], int)


def test_c2pa_expert_handles_image_with_no_manifest(synthetic_image_bytes):
    if not swarm_c2pa_expert.is_available():
        pytest.skip("c2pa-python not installed")
    data, _ = synthetic_image_bytes
    update = swarm_c2pa_expert.run_c2pa_expert(data, "clean.png", "image/png")
    _shape_ok(update)
    assert update["status"] == "success"
    assert update["score"] == 0.5
    assert update["verdict"] == "无 C2PA 内容凭证"
    assert update["confidence"] == "低"
    assert any("C2PA" in line for line in update["evidence"])


def test_c2pa_expert_skips_gracefully_when_library_missing(monkeypatch):
    monkeypatch.setattr(swarm_c2pa_expert, "_C2PA_AVAILABLE", False)
    monkeypatch.setattr(swarm_c2pa_expert, "_C2PA_IMPORT_ERROR", "ImportError: stubbed")
    update = swarm_c2pa_expert.run_c2pa_expert(b"\xff\xd8\xff", "a.jpg", "image/jpeg")
    _shape_ok(update)
    assert update["status"] == "skipped"
    assert update["score"] is None
    assert "c2pa-python" in update["message"]


def test_c2pa_expert_handles_empty_bytes():
    update = swarm_c2pa_expert.run_c2pa_expert(b"", "x.png", "image/png")
    _shape_ok(update)
    if swarm_c2pa_expert.is_available():
        assert update["status"] == "failed"
        assert update["score"] is None


def test_c2pa_decide_verdict_ai_generator_match():
    score, verdict, conf, evidence = swarm_c2pa_expert._decide_verdict(
        generators=["Adobe Firefly 2.5"],
        digital_source_types=[],
        validation_severity="ok",
        validation_issues=[],
    )
    assert score >= 0.9
    assert "生成内容" in verdict
    assert conf == "高"
    assert any("Adobe Firefly".lower() in e.lower() or "firefly" in e.lower() for e in evidence)


def test_c2pa_decide_verdict_camera_capture():
    score, verdict, conf, evidence = swarm_c2pa_expert._decide_verdict(
        generators=["Canon EOS R5 Firmware 1.2"],
        digital_source_types=["http://cv.iptc.org/newscodes/digitalsourcetype/digitalcapture"],
        validation_severity="ok",
        validation_issues=[],
    )
    assert score <= 0.15
    assert "拍摄" in verdict or "相机" in verdict
    assert conf == "高"


def test_c2pa_decide_verdict_signature_failure_dominates():
    score, verdict, conf, evidence = swarm_c2pa_expert._decide_verdict(
        generators=["Canon EOS R5"],
        digital_source_types=["http://cv.iptc.org/newscodes/digitalsourcetype/digitalcapture"],
        validation_severity="error",
        validation_issues=["签名校验失败: bad cert"],
    )
    assert "签名" in verdict
    # Signature failure produces a moderate risk score regardless of source claim.
    assert 0.6 <= score <= 0.9


def test_c2pa_missing_validation_cannot_lower_camera_risk():
    severity, issues = swarm_c2pa_expert._validation_summary({"manifests": {}})
    score, verdict, confidence, evidence = swarm_c2pa_expert._decide_verdict(
        generators=["Canon EOS R5"],
        digital_source_types=["http://cv.iptc.org/newscodes/digitalsourcetype/digitalcapture"],
        validation_severity=severity,
        validation_issues=issues,
    )

    assert severity == "unknown"
    assert score == 0.5
    assert "未验证" in verdict
    assert confidence == "低"
    assert any("保持中性" in item for item in evidence)


def test_c2pa_minor_human_edits_is_not_camera_capture():
    assert swarm_c2pa_expert._summarize_source({
        "assertions": [{
            "label": "c2pa.actions",
            "data": {
                "digitalSourceType": "http://cv.iptc.org/newscodes/digitalsourcetype/minorhumanedits",
            },
        }],
    }) == "unknown"


# ----------------------------- Watermark expert ----------------------------- #

def _encode_watermark(img_array, marker: bytes, method: str = "dwtDctSvd"):
    from imwatermark import WatermarkEncoder

    cv2 = pytest.importorskip("cv2")
    enc = WatermarkEncoder()
    enc.set_watermark("bytes", marker)
    wm_img = enc.encode(img_array.copy(), method)
    ok, encoded = cv2.imencode(".png", wm_img)
    assert ok
    return encoded.tobytes()


def test_watermark_expert_clean_image_returns_low_score(synthetic_image_bytes):
    if not swarm_watermark_expert.is_available():
        pytest.skip("invisible-watermark not installed")
    data, _ = synthetic_image_bytes
    update = swarm_watermark_expert.run_watermark_expert(data, "clean.png")
    _shape_ok(update)
    assert update["status"] == "success"
    assert update["verdict"] == "未检测到已知水印"
    assert update["score"] == 0.42


def test_watermark_expert_detects_stable_diffusion_v1(synthetic_image_bytes):
    if not swarm_watermark_expert.is_available():
        pytest.skip("invisible-watermark not installed")
    _, img_array = synthetic_image_bytes
    wm_bytes = _encode_watermark(img_array, b"StableDiffusionV1")
    update = swarm_watermark_expert.run_watermark_expert(wm_bytes, "sd.png")
    _shape_ok(update)
    assert update["status"] == "success"
    assert update["score"] >= 0.9
    assert "Stable Diffusion" in update["verdict"]
    assert any("Stable Diffusion" in line for line in update["evidence"])


def test_watermark_expert_detects_sdv2(synthetic_image_bytes):
    if not swarm_watermark_expert.is_available():
        pytest.skip("invisible-watermark not installed")
    _, img_array = synthetic_image_bytes
    wm_bytes = _encode_watermark(img_array, b"SDV2")
    update = swarm_watermark_expert.run_watermark_expert(wm_bytes, "sdxl.png")
    _shape_ok(update)
    assert update["status"] == "success"
    assert update["score"] >= 0.9
    assert "Stable Diffusion 2/XL" in update["verdict"]


def test_watermark_expert_detects_flux(synthetic_image_bytes):
    if not swarm_watermark_expert.is_available():
        pytest.skip("invisible-watermark not installed")
    _, img_array = synthetic_image_bytes
    wm_bytes = _encode_watermark(img_array, b"FLUX")
    update = swarm_watermark_expert.run_watermark_expert(wm_bytes, "flux.png")
    _shape_ok(update)
    assert update["status"] == "success"
    assert update["score"] >= 0.9
    assert "FLUX" in update["verdict"]


def test_watermark_expert_skips_when_library_missing(monkeypatch):
    monkeypatch.setattr(swarm_watermark_expert, "_WM_AVAILABLE", False)
    monkeypatch.setattr(swarm_watermark_expert, "_WM_IMPORT_ERROR", "ImportError: stubbed")
    update = swarm_watermark_expert.run_watermark_expert(b"\xff\xd8\xff", "a.jpg")
    _shape_ok(update)
    assert update["status"] == "skipped"
    assert update["score"] is None


def test_watermark_expert_handles_undecodable_bytes():
    if not swarm_watermark_expert.is_available():
        pytest.skip("invisible-watermark not installed")
    update = swarm_watermark_expert.run_watermark_expert(b"definitely-not-an-image", "x.png")
    _shape_ok(update)
    assert update["status"] == "failed"


def test_watermark_expert_survives_jpeg_recompression(synthetic_image_bytes):
    if not swarm_watermark_expert.is_available():
        pytest.skip("invisible-watermark not installed")
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from imwatermark import WatermarkEncoder

    _, img_array = synthetic_image_bytes
    enc = WatermarkEncoder()
    enc.set_watermark("bytes", b"StableDiffusionV1")
    wm_img = enc.encode(img_array.copy(), "dwtDctSvd")
    ok, encoded = cv2.imencode(".jpg", wm_img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    assert ok
    update = swarm_watermark_expert.run_watermark_expert(encoded.tobytes(), "sd_q80.jpg")
    _shape_ok(update)
    assert update["status"] == "success"
    assert "Stable Diffusion" in update["verdict"]


# ------------------------ Integration into specs ------------------------ #

def test_swarm_expert_specs_include_new_experts():
    from imagedetection.views import detection

    spec_ids = {spec["id"] for spec in detection.SWARM_EXPERT_SPECS}
    assert "c2pa" in spec_ids
    assert "watermark" in spec_ids
    c2pa_spec = next(s for s in detection.SWARM_EXPERT_SPECS if s["id"] == "c2pa")
    wm_spec = next(s for s in detection.SWARM_EXPERT_SPECS if s["id"] == "watermark")
    assert c2pa_spec["provider"] == "c2pa"
    assert wm_spec["provider"] == "watermark"
    assert c2pa_spec["weight"] > 0
    assert wm_spec["weight"] > 0


def test_public_name_mapping_for_new_experts():
    from imagedetection.views import detection

    c2pa_public = detection._public_swarm_expert_name(
        {"id": "c2pa", "role": "内容凭证"}, index=4
    )
    wm_public = detection._public_swarm_expert_name(
        {"id": "watermark", "role": "生成水印"}, index=5
    )
    wam_public = detection._public_swarm_expert_name(
        {"id": "wam", "role": "通用水印"}, index=6
    )
    assert "C2PA" in c2pa_public
    assert "水印" in wm_public
    assert "WAM" in wam_public


# ----------------------------- C2PA chain walk ----------------------------- #

def test_c2pa_chain_walk_root_only():
    envelope = {
        "active_manifest": "urn:root",
        "manifests": {
            "urn:root": {"claim_generator": "Adobe Firefly 2.5", "ingredients": []},
        },
    }
    chain = swarm_c2pa_expert._walk_manifest_chain(envelope)
    assert [entry["label"] for entry in chain] == ["urn:root"]
    assert chain[0]["role"] == "active"


def test_c2pa_chain_walk_includes_ingredients():
    envelope = {
        "active_manifest": "urn:root",
        "manifests": {
            "urn:root": {
                "claim_generator": "Photoshop 25.1 with Generative Fill",
                "ingredients": [
                    {"active_manifest": "urn:child", "title": "source.png"},
                ],
            },
            "urn:child": {
                "claim_generator": "Canon EOS R5 firmware 1.2",
            },
        },
    }
    chain = swarm_c2pa_expert._walk_manifest_chain(envelope)
    labels = [entry["label"] for entry in chain]
    assert labels == ["urn:root", "urn:child"]
    assert chain[0]["role"] == "active"
    assert chain[1]["role"] == "ingredient"


def test_c2pa_chain_walk_handles_cycles():
    envelope = {
        "active_manifest": "urn:a",
        "manifests": {
            "urn:a": {"ingredients": [{"active_manifest": "urn:b"}]},
            "urn:b": {"ingredients": [{"active_manifest": "urn:a"}]},
        },
    }
    chain = swarm_c2pa_expert._walk_manifest_chain(envelope)
    assert {entry["label"] for entry in chain} == {"urn:a", "urn:b"}


def test_c2pa_summarize_source_detects_ai():
    assert swarm_c2pa_expert._summarize_source({"claim_generator": "Midjourney V6"}) == "ai"
    assert swarm_c2pa_expert._summarize_source({"claim_generator": "Canon"}) == "unknown"
    assert swarm_c2pa_expert._summarize_source({
        "assertions": [
            {
                "label": "c2pa.actions",
                "data": {"digitalSourceType": "http://cv.iptc.org/newscodes/digitalsourcetype/digitalcapture"},
            },
        ],
    }) == "camera"


def test_c2pa_collect_actions_extracts_action_chain():
    manifest = {
        "assertions": [
            {
                "label": "c2pa.actions.v2",
                "data": {
                    "actions": [
                        {"action": "c2pa.opened", "when": "2025-01-01T00:00:00Z"},
                        {"action": "c2pa.edited", "softwareAgent": "Photoshop"},
                        {"action": "c2pa.created", "digitalSourceType": "trainedAlgorithmicMedia"},
                    ],
                },
            },
        ],
    }
    actions = swarm_c2pa_expert._collect_actions(manifest)
    assert len(actions) == 3
    assert actions[0]["action"] == "c2pa.opened"
    assert actions[1]["softwareAgent"] == "Photoshop"
    assert actions[2]["digitalSourceType"] == "trainedAlgorithmicMedia"


def test_c2pa_collect_signer_info():
    manifest = {
        "signature_info": {
            "issuer": "C=US, O=Adobe Inc, CN=Adobe Content Authenticity Initiative CA",
            "common_name": "Adobe Photoshop",
            "alg": "Es256",
        },
    }
    info = swarm_c2pa_expert._collect_signer_info(manifest)
    assert "Adobe" in info["issuer"]
    assert info["common_name"] == "Adobe Photoshop"


# ----------------------------- Watermark anomaly ----------------------------- #

def test_watermark_anomaly_probe_rejects_clean_image(synthetic_image_bytes):
    if not swarm_watermark_expert.is_available():
        pytest.skip("invisible-watermark not installed")
    _, img_array = synthetic_image_bytes
    result = swarm_watermark_expert._anomaly_probe(img_array)
    assert result is None, f"clean image should not anomaly-trip, got: {result}"


def test_watermark_anomaly_probe_catches_unknown_marker(synthetic_image_bytes):
    if not swarm_watermark_expert.is_available():
        pytest.skip("invisible-watermark not installed")
    np = pytest.importorskip("numpy")  # noqa: F841
    cv2 = pytest.importorskip("cv2")  # noqa: F841
    from imwatermark import WatermarkEncoder

    _, img_array = synthetic_image_bytes
    # Use a marker NOT in KNOWN_WATERMARKS.
    enc = WatermarkEncoder()
    enc.set_watermark("bytes", b"CustomGenAI")
    wm_img = enc.encode(img_array.copy(), "dwtDctSvd")
    result = swarm_watermark_expert._anomaly_probe(wm_img)
    assert result is not None
    assert result["distinct_bytes"] <= 4
    assert result["printable_count"] >= 8


def test_watermark_expert_reports_unknown_anomaly_in_pipeline(synthetic_image_bytes):
    if not swarm_watermark_expert.is_available():
        pytest.skip("invisible-watermark not installed")
    cv2 = pytest.importorskip("cv2")
    from imwatermark import WatermarkEncoder

    _, img_array = synthetic_image_bytes
    enc = WatermarkEncoder()
    enc.set_watermark("bytes", b"CustomGenAI")
    wm_img = enc.encode(img_array.copy(), "dwtDctSvd")
    ok, encoded = cv2.imencode(".png", wm_img)
    assert ok

    update = swarm_watermark_expert.run_watermark_expert(encoded.tobytes(), "unknown.png")
    _shape_ok(update)
    assert update["status"] == "success"
    assert "未知" in update["verdict"] or "可疑" in update["verdict"]
    assert update["score"] == pytest.approx(0.6, abs=0.01)
    assert update.get("details", {}).get("distinct_bytes") is not None


def test_watermark_expert_detects_extended_marker_sdxl(synthetic_image_bytes):
    if not swarm_watermark_expert.is_available():
        pytest.skip("invisible-watermark not installed")
    _, img_array = synthetic_image_bytes
    wm_bytes = _encode_watermark(img_array, b"SDXL")
    update = swarm_watermark_expert.run_watermark_expert(wm_bytes, "sdxl.png")
    _shape_ok(update)
    assert update["status"] == "success"
    assert "Stable Diffusion XL" in update["verdict"]


def test_watermark_expert_emits_provenance_kind(synthetic_image_bytes):
    if not swarm_watermark_expert.is_available():
        pytest.skip("invisible-watermark not installed")
    data, _ = synthetic_image_bytes
    update = swarm_watermark_expert.run_watermark_expert(data, "clean.png")
    assert update.get("provenance_kind") == "watermark"


# ----------------------------- WAM expert ----------------------------- #

def test_wam_expert_skipped_when_no_url(monkeypatch):
    monkeypatch.delenv(swarm_wam_expert.SIDECAR_URL_ENV, raising=False)
    update = swarm_wam_expert.run_wam_expert(b"\xff\xd8\xff", "x.jpg", "image/jpeg")
    _shape_ok(update)
    assert update["status"] == "skipped"
    assert update["provenance_kind"] == "wam"
    assert "未启用" in update["verdict"] or "未设置" in update["message"]


def test_wam_expert_failed_on_oversize(monkeypatch):
    monkeypatch.setenv(swarm_wam_expert.SIDECAR_URL_ENV, "http://stub/detect")
    update = swarm_wam_expert.run_wam_expert(
        b"\x00" * (swarm_wam_expert.MAX_UPLOAD_BYTES + 1), "huge.bin", "image/jpeg"
    )
    _shape_ok(update)
    assert update["status"] == "failed"
    assert "超限" in update["verdict"]


def test_wam_expert_classifies_present_response():
    update = swarm_wam_expert._classify({
        "ok": True,
        "watermark_present": True,
        "watermark_score": 0.85,
        "watermark_area_ratio": 0.12,
        "predicted_message_hex": "1a2b3c4d",
        "model_attribution": "Stable Diffusion XL",
    })
    assert update["status"] == "success"
    assert update["score"] >= 0.7
    assert update["confidence"] == "高"
    assert any("Stable Diffusion XL" in line for line in update["evidence"])


def test_wam_expert_classifies_absent_response():
    update = swarm_wam_expert._classify({
        "ok": True,
        "watermark_present": False,
        "watermark_score": 0.2,
        "watermark_area_ratio": 0.0,
    })
    assert update["status"] == "success"
    assert update["score"] == 0.4
    assert "未检出" in update["verdict"]


def test_wam_expert_dispatches_via_requests(monkeypatch):
    """Patch requests.post to return a fake sidecar response and verify the
    full HTTP path."""
    monkeypatch.setenv(swarm_wam_expert.SIDECAR_URL_ENV, "http://stub/detect")
    monkeypatch.setenv(swarm_wam_expert.SIDECAR_TOKEN_ENV, "secret")

    captured = {}

    class FakeResp:
        status_code = 200
        text = ""

        def json(self):
            return {
                "ok": True,
                "watermark_present": True,
                "watermark_score": 0.93,
                "watermark_area_ratio": 0.18,
                "predicted_message_hex": "deadbeef",
                "model_attribution": "FLUX.1-dev",
            }

    def fake_post(url, files=None, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return FakeResp()

    monkeypatch.setattr(swarm_wam_expert.requests, "post", fake_post)
    update = swarm_wam_expert.run_wam_expert(b"\xff\xd8\xff", "img.jpg", "image/jpeg")
    _shape_ok(update)
    assert captured["url"] == "http://stub/detect"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert update["status"] == "success"
    assert update["score"] >= 0.7
    assert "FLUX.1-dev" in " ".join(update["evidence"])


def test_wam_expert_handles_timeout(monkeypatch):
    monkeypatch.setenv(swarm_wam_expert.SIDECAR_URL_ENV, "http://stub/detect")
    import requests as _real_requests

    def _raise(*a, **kw):
        raise _real_requests.exceptions.Timeout("simulated")

    monkeypatch.setattr(swarm_wam_expert.requests, "post", _raise)
    update = swarm_wam_expert.run_wam_expert(b"\xff\xd8\xff", "img.jpg", "image/jpeg")
    _shape_ok(update)
    assert update["status"] == "failed"
    assert "超时" in update["verdict"]


def test_wam_expert_handles_non_json(monkeypatch):
    monkeypatch.setenv(swarm_wam_expert.SIDECAR_URL_ENV, "http://stub/detect")

    class FakeResp:
        status_code = 200
        text = "<html>not json</html>"

        def json(self):
            raise ValueError("decode error")

    monkeypatch.setattr(swarm_wam_expert.requests, "post", lambda *a, **kw: FakeResp())
    update = swarm_wam_expert.run_wam_expert(b"\xff\xd8\xff", "img.jpg", "image/jpeg")
    _shape_ok(update)
    assert update["status"] == "failed"
    assert "非 JSON" in update["verdict"]


# ----------------------------- Provenance aggregator ----------------------------- #

def test_provenance_summary_majority_ai():
    from imagedetection.views import detection

    experts = [
        {"id": "c2pa", "provenance_kind": "c2pa", "status": "success", "score": 0.94,
         "verdict": "AI", "weight": 0.1},
        {"id": "watermark", "provenance_kind": "watermark", "status": "success", "score": 0.96,
         "verdict": "SD watermark", "weight": 0.1},
        {"id": "wam", "provenance_kind": "wam", "status": "skipped", "score": None},
        {"id": "primary", "status": "success", "score": 0.7, "weight": 0.34},
    ]
    summary = detection._swarm_provenance_summary(experts)
    assert summary is not None
    assert summary["ai_count"] == 2
    assert summary["real_count"] == 0
    assert "AI 生成" in summary["headline"]
    # Excludes non-provenance experts:
    assert all(m["kind"] in ("c2pa", "watermark", "wam") for m in summary["members"])


def test_provenance_summary_majority_real():
    from imagedetection.views import detection

    experts = [
        {"id": "c2pa", "provenance_kind": "c2pa", "status": "success", "score": 0.08,
         "verdict": "Canon", "weight": 0.1},
        {"id": "watermark", "provenance_kind": "watermark", "status": "success", "score": 0.32,
         "verdict": "no marker", "weight": 0.1},
    ]
    summary = detection._swarm_provenance_summary(experts)
    assert summary is not None
    assert summary["real_count"] == 2
    assert summary["ai_count"] == 0
    assert "真实拍摄" in summary["headline"]


def test_provenance_summary_conflict():
    from imagedetection.views import detection

    experts = [
        {"id": "c2pa", "provenance_kind": "c2pa", "status": "success", "score": 0.94,
         "verdict": "AI", "weight": 0.1},
        {"id": "watermark", "provenance_kind": "watermark", "status": "success", "score": 0.08,
         "verdict": "no marker", "weight": 0.1},
        {"id": "wam", "provenance_kind": "wam", "status": "success", "score": 0.5,
         "verdict": "uncertain", "weight": 0.08},
    ]
    summary = detection._swarm_provenance_summary(experts)
    assert summary is not None
    assert summary["ai_count"] == 1
    assert summary["real_count"] == 1
    assert summary["uncertain_count"] == 1
    assert "冲突" in summary["headline"]


def test_provenance_summary_returns_none_without_providers():
    from imagedetection.views import detection

    experts = [
        {"id": "primary", "status": "success", "score": 0.7},
        {"id": "metadata", "status": "success", "score": 0.5},
    ]
    assert detection._swarm_provenance_summary(experts) is None


def test_public_provenance_summary_strips_internal_fields():
    from imagedetection.views import detection

    internal = {
        "ai_count": 2,
        "real_count": 0,
        "uncertain_count": 1,
        "members": [
            {"id": "c2pa", "kind": "c2pa", "score": 0.94, "verdict": "AI"},
            {"id": "watermark", "kind": "watermark", "score": 0.96, "verdict": "SD"},
            {"id": "wam", "kind": "wam", "score": 0.5, "verdict": "uncertain"},
        ],
        "headline": "来源溯源：2/3 路证据指向 AI 生成",
    }
    public = detection._public_provenance_summary(internal)
    assert public == {
        "headline": "来源溯源：2/3 路证据指向 AI 生成",
        "aiCount": 2,
        "realCount": 0,
        "uncertainCount": 1,
        "memberCount": 3,
    }
    assert "members" not in public
    assert "ai_count" not in public
