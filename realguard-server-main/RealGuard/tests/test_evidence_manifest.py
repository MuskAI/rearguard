from __future__ import annotations

import copy
import hashlib
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from imagedetection.views import evidence_manifest, reporting  # noqa: E402
from imagedetection import creat_app  # noqa: E402
from imagedetection.views import detection  # noqa: E402


SIGNING_KEY = "evidence-only-key-0123456789abcdef0123456789abcdef"
GENERATED_AT = datetime(2026, 7, 19, 8, 30, tzinfo=timezone.utc)
MODEL_RUN = {
    "id": "run-73",
    "route": "primary",
    "model": {
        "id": "v1-onnx-mil",
        "version": "v1-onnx-mil-2026.07",
    },
    "meta": {
        "visibleWatermark": {
            "detected": True,
            "confidence": 0.97,
            "provider": "server-watermark-v2",
            "hits": [{"label": "AI watermark"}],
        },
    },
}


def _item(**overrides):
    item = {
        "itemid": 73,
        "filename": "stored-image.png",
        "openid": "openid-73",
        "fake": 91.25,
        "aigc": "AI生成图像",
        "clarity": "高",
        "explantation": "主模型与服务端取证证据一致，判定为高风险。",
        "file_size": "14KB",
        "img_format": "PNG",
        "resolution": "320x320",
        "createtime": "2026-07-19 16:29:00",
    }
    item.update(overrides)
    return item


def test_signed_manifest_contains_required_server_evidence_and_verifies(tmp_path):
    source = tmp_path / "original.png"
    source.write_bytes(b"original-image-bytes")

    envelope = evidence_manifest.create_signed_image_manifest(
        _item(),
        source_path=source,
        model_run=MODEL_RUN,
        generated_at=GENERATED_AT,
        key=SIGNING_KEY,
    )

    manifest = envelope["manifest"]
    assert manifest["task_id"] == "IMG-73"
    assert manifest["record_id"] == "73"
    assert manifest["source"] == {
        "hash_algorithm": "SHA-256",
        "sha256": hashlib.sha256(b"original-image-bytes").hexdigest(),
        "size_bytes": len(b"original-image-bytes"),
    }
    assert manifest["model"]["version"] == "v1-onnx-mil-2026.07"
    assert manifest["model"]["run_id"] == "run-73"
    assert manifest["policy_version"] == evidence_manifest.DEFAULT_POLICY_VERSION
    assert manifest["conclusion"]["label"] == "AI生成图像"
    assert manifest["evidence_summary"]["source"] == "persisted_server_record"
    assert manifest["evidence_summary"]["signals"] == [{
        "type": "visible_watermark",
        "detected": True,
        "confidence": 0.97,
        "provider": "server-watermark-v2",
        "hit_count": 1,
    }]
    assert manifest["generated_at"] == "2026-07-19T08:30:00Z"
    assert envelope["signature"]["algorithm"] == "HMAC-SHA256"
    assert evidence_manifest.verify_manifest(envelope, key=SIGNING_KEY) is True


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("source", "sha256"), "0" * 64),
        (("model", "version"), "attacker-model"),
        (("conclusion", "label"), "真实图像"),
        (("evidence_summary", "text"), "attacker supplied evidence"),
        (("generated_at",), "2026-07-19T08:31:00Z"),
    ],
)
def test_manifest_tampering_invalidates_signature(tmp_path, path, value):
    source = tmp_path / "original.png"
    source.write_bytes(b"original")
    envelope = evidence_manifest.create_signed_image_manifest(
        _item(),
        source_path=source,
        model_run=MODEL_RUN,
        generated_at=GENERATED_AT,
        key=SIGNING_KEY,
    )
    tampered = copy.deepcopy(envelope)
    target = tampered["manifest"]
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value

    assert evidence_manifest.verify_manifest(tampered, key=SIGNING_KEY) is False


def test_signature_is_canonical_and_key_order_independent(tmp_path):
    source = tmp_path / "original.png"
    source.write_bytes(b"original")
    envelope = evidence_manifest.create_signed_image_manifest(
        _item(),
        source_path=source,
        model_run=MODEL_RUN,
        generated_at=GENERATED_AT,
        key=SIGNING_KEY,
    )
    reordered = dict(reversed(list(envelope["manifest"].items())))

    assert evidence_manifest.sign_manifest(reordered, key=SIGNING_KEY) == envelope["signature"]["value"]


def test_same_server_input_and_generation_time_reproduce_identical_signed_snapshot(tmp_path, monkeypatch):
    source = tmp_path / "original.png"
    source.write_bytes(b"stable-original")
    monkeypatch.setenv("REALGUARD_EVIDENCE_HMAC_KEY_ID", "evidence-key-2026-07")
    monkeypatch.setenv("REALGUARD_EVIDENCE_POLICY_VERSION", "policy-2026-07")
    kwargs = {
        "source_path": source,
        "model_run": MODEL_RUN,
        "generated_at": GENERATED_AT,
        "key": SIGNING_KEY,
    }

    first = evidence_manifest.create_signed_image_manifest(_item(), **kwargs)
    replay = evidence_manifest.create_signed_image_manifest(copy.deepcopy(_item()), **kwargs)

    assert replay == first
    assert evidence_manifest.canonical_json(replay["manifest"]) == evidence_manifest.canonical_json(first["manifest"])
    assert evidence_manifest.verify_manifest(replay, key=SIGNING_KEY) is True

    later = evidence_manifest.create_signed_image_manifest(
        _item(),
        **{**kwargs, "generated_at": "2026-07-19T08:30:01Z"},
    )
    assert later["manifest"]["generated_at"] != first["manifest"]["generated_at"]
    assert later["signature"]["value"] != first["signature"]["value"]
    assert evidence_manifest.verify_manifest(later, key=SIGNING_KEY) is True


def test_repeated_report_downloads_reuse_first_persisted_manifest_and_signature(monkeypatch, tmp_path):
    source = tmp_path / "original.png"
    source.write_bytes(b"immutable-original")
    snapshot_root = tmp_path / "snapshots"
    monkeypatch.setattr(reporting, "_render_image_report_pdf", lambda item, result: b"%PDF-1.7\n%%EOF\n")

    first_pdf = reporting.image_report_pdf(
        _item(),
        {"final_label": "客户端字段不可信"},
        source_path=source,
        model_run=MODEL_RUN,
        generated_at="2026-07-19T08:30:00Z",
        signing_key=SIGNING_KEY,
        snapshot_root=snapshot_root,
    )
    changed_server_row = _item(
        fake=1.0,
        aigc="真实图像",
        clarity="低",
        explantation="数据库记录在首次报告后被修改。",
    )
    second_pdf = reporting.image_report_pdf(
        changed_server_row,
        {"final_label": "另一个客户端字段"},
        source_path=source,
        model_run={"id": "later-run", "model": {"id": "later", "version": "later-v2"}},
        generated_at="2026-07-20T09:00:00Z",
        signing_key=SIGNING_KEY,
        snapshot_root=snapshot_root,
    )

    first = evidence_manifest.extract_envelope_from_pdf(first_pdf)
    second = evidence_manifest.extract_envelope_from_pdf(second_pdf)
    assert second["manifest"] == first["manifest"]
    assert second["signature"] == first["signature"]
    assert second["manifest"]["generated_at"] == "2026-07-19T08:30:00Z"
    assert second["manifest"]["conclusion"]["label"] == "AI生成图像"
    assert second["manifest"]["model"]["version"] == "v1-onnx-mil-2026.07"
    snapshots = list(snapshot_root.glob("*.manifest.json"))
    assert len(snapshots) == 1
    assert stat.S_IMODE(snapshots[0].stat().st_mode) == 0o400
    assert snapshots[0].read_bytes() == evidence_manifest.canonical_json({
        "manifest": first["manifest"],
        "signature": first["signature"],
    })


def test_tampered_persisted_snapshot_is_rejected(tmp_path):
    source = tmp_path / "original.png"
    source.write_bytes(b"immutable-original")
    snapshot_root = tmp_path / "snapshots"
    original = evidence_manifest.get_or_create_signed_image_manifest(
        _item(),
        source_path=source,
        model_run=MODEL_RUN,
        generated_at=GENERATED_AT,
        key=SIGNING_KEY,
        snapshot_root=snapshot_root,
    )
    snapshot = next(snapshot_root.glob("*.manifest.json"))
    tampered = copy.deepcopy(original)
    tampered["manifest"]["conclusion"]["label"] = "真实图像"
    replacement = snapshot_root / ".tampered.json"
    replacement.write_bytes(evidence_manifest.canonical_json(tampered))
    replacement.chmod(0o400)
    os.replace(replacement, snapshot)

    with pytest.raises(evidence_manifest.EvidenceManifestError, match="签名校验失败"):
        evidence_manifest.get_or_create_signed_image_manifest(
            _item(),
            source_path=source,
            model_run=MODEL_RUN,
            generated_at="2026-07-20T09:00:00Z",
            key=SIGNING_KEY,
            snapshot_root=snapshot_root,
        )


def test_source_change_after_first_snapshot_fails_closed(tmp_path):
    source = tmp_path / "original.png"
    source.write_bytes(b"first-original")
    snapshot_root = tmp_path / "snapshots"
    evidence_manifest.get_or_create_signed_image_manifest(
        _item(),
        source_path=source,
        model_run=MODEL_RUN,
        generated_at=GENERATED_AT,
        key=SIGNING_KEY,
        snapshot_root=snapshot_root,
    )
    source.write_bytes(b"changed-original")

    with pytest.raises(evidence_manifest.EvidenceManifestError, match="原始图像已变化"):
        evidence_manifest.get_or_create_signed_image_manifest(
            _item(),
            source_path=source,
            model_run=MODEL_RUN,
            generated_at="2026-07-20T09:00:00Z",
            key=SIGNING_KEY,
            snapshot_root=snapshot_root,
        )


def test_valid_snapshot_cannot_be_substituted_for_another_record(tmp_path):
    source = tmp_path / "original.png"
    source.write_bytes(b"same-source")
    snapshot_root = tmp_path / "snapshots"
    evidence_manifest.get_or_create_signed_image_manifest(
        _item(itemid=73),
        source_path=source,
        model_run=MODEL_RUN,
        generated_at=GENERATED_AT,
        key=SIGNING_KEY,
        snapshot_root=snapshot_root,
    )
    record_73 = snapshot_root / "image-73.manifest.json"
    record_74 = snapshot_root / "image-74.manifest.json"
    record_74.write_bytes(record_73.read_bytes())
    record_74.chmod(0o400)

    with pytest.raises(evidence_manifest.EvidenceManifestError, match="记录 ID 不匹配"):
        evidence_manifest.get_or_create_signed_image_manifest(
            _item(itemid=74),
            source_path=source,
            model_run=MODEL_RUN,
            generated_at=GENERATED_AT,
            key=SIGNING_KEY,
            snapshot_root=snapshot_root,
        )


def test_concurrent_first_generation_persists_exactly_one_snapshot(tmp_path):
    source = tmp_path / "original.png"
    source.write_bytes(b"concurrent-original")
    snapshot_root = tmp_path / "snapshots"

    def create(index):
        return evidence_manifest.get_or_create_signed_image_manifest(
            _item(),
            source_path=source,
            model_run=MODEL_RUN,
            generated_at=f"2026-07-19T08:30:{index:02d}Z",
            key=SIGNING_KEY,
            snapshot_root=snapshot_root,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        snapshots = list(executor.map(create, range(8)))

    assert all(snapshot == snapshots[0] for snapshot in snapshots)
    assert len(list(snapshot_root.glob("*.manifest.json"))) == 1
    assert evidence_manifest.verify_manifest(snapshots[0], key=SIGNING_KEY) is True


def test_evidence_key_is_independent_and_never_falls_back_to_flask_secret(monkeypatch, tmp_path):
    source = tmp_path / "original.png"
    source.write_bytes(b"original")
    monkeypatch.delenv("REALGUARD_EVIDENCE_HMAC_KEY", raising=False)
    monkeypatch.setenv("SECRET_KEY", "flask-session-secret-that-must-not-sign-evidence")

    with pytest.raises(evidence_manifest.EvidenceManifestError, match="独立证据签名密钥"):
        evidence_manifest.create_signed_image_manifest(
            _item(),
            source_path=source,
            model_run=MODEL_RUN,
            generated_at=GENERATED_AT,
        )


def test_source_resolution_is_confined_to_server_root(monkeypatch, tmp_path):
    static_root = tmp_path / "static"
    stored = static_root / "uploads" / "openid-73" / "image" / "stored-image.png"
    stored.parent.mkdir(parents=True)
    stored.write_bytes(b"trusted-original")
    monkeypatch.setenv("REALGUARD_EVIDENCE_SOURCE_ROOTS", str(static_root))

    assert evidence_manifest.resolve_source_path(_item()) == stored.resolve()
    with pytest.raises(evidence_manifest.EvidenceManifestError, match="安全的存储目录"):
        evidence_manifest.resolve_source_path(_item(openid="../outside"))
    with pytest.raises(evidence_manifest.EvidenceManifestError, match="安全的原文件名"):
        evidence_manifest.resolve_source_path(_item(filename="../../secret"))


def test_missing_original_fails_closed_instead_of_signing_placeholder(monkeypatch, tmp_path):
    monkeypatch.setenv("REALGUARD_EVIDENCE_SOURCE_ROOTS", str(tmp_path / "empty-static"))

    with pytest.raises(evidence_manifest.EvidenceManifestError, match="拒绝生成无原件哈希"):
        evidence_manifest.create_signed_image_manifest(
            _item(),
            model_run=MODEL_RUN,
            generated_at=GENERATED_AT,
            key=SIGNING_KEY,
        )


@pytest.mark.parametrize("invalid_score", [None, "", "not-a-number", float("nan"), float("inf")])
def test_invalid_server_risk_score_fails_closed(tmp_path, invalid_score):
    source = tmp_path / "original.png"
    source.write_bytes(b"original")

    with pytest.raises(evidence_manifest.EvidenceManifestError, match="风险分数"):
        evidence_manifest.create_signed_image_manifest(
            _item(fake=invalid_score),
            source_path=source,
            model_run=MODEL_RUN,
            generated_at=GENERATED_AT,
            key=SIGNING_KEY,
        )


def test_known_placeholder_key_is_rejected(tmp_path):
    source = tmp_path / "original.png"
    source.write_bytes(b"original")

    with pytest.raises(evidence_manifest.EvidenceManifestError, match="公开占位符"):
        evidence_manifest.create_signed_image_manifest(
            _item(),
            source_path=source,
            model_run=MODEL_RUN,
            generated_at=GENERATED_AT,
            key="replace-with-an-independent-random-64-hex-character-key",
        )


def test_pdf_embeds_extractable_manifest_and_verification_detects_tampering(tmp_path):
    source = tmp_path / "original.png"
    source.write_bytes(b"original")
    envelope = evidence_manifest.create_signed_image_manifest(
        _item(),
        source_path=source,
        model_run=MODEL_RUN,
        generated_at=GENERATED_AT,
        key=SIGNING_KEY,
    )
    visible_pdf = b"%PDF-1.7\nvisible report body\n%%EOF\n"
    bound = evidence_manifest.bind_pdf_artifact(visible_pdf, envelope, key=SIGNING_KEY)
    pdf = evidence_manifest.embed_envelope_in_pdf(visible_pdf, bound)

    assert evidence_manifest.extract_envelope_from_pdf(pdf) == bound
    assert evidence_manifest.verify_pdf_report(pdf, key=SIGNING_KEY) is True
    assert pdf.rstrip().endswith(b"%%EOF")
    tampered = copy.deepcopy(bound)
    tampered["manifest"]["conclusion"]["label"] = "真实图像"
    tampered_pdf = evidence_manifest.embed_envelope_in_pdf(b"%PDF-1.7\n%%EOF\n", tampered)
    assert evidence_manifest.verify_pdf_report(tampered_pdf, key=SIGNING_KEY) is False
    assert evidence_manifest.verify_pdf_report(pdf.replace(b"visible report body", b"forged report body!"), key=SIGNING_KEY) is False
    polluted = pdf.replace(
        evidence_manifest._PDF_END,
        b"not-a-pdf-comment\n" + evidence_manifest._PDF_END,
    )
    assert evidence_manifest.verify_pdf_report(polluted, key=SIGNING_KEY) is False
    assert evidence_manifest.verify_pdf_report(b"%PDF-1.7\n%%EOF\n", key=SIGNING_KEY) is False


def test_report_ignores_unsigned_client_fields_and_renders_signed_server_snapshot(monkeypatch, tmp_path):
    source = tmp_path / "original.png"
    source.write_bytes(b"authoritative-source")
    rendered = {}

    def fake_renderer(item, result):
        rendered["item"] = dict(item)
        rendered["result"] = dict(result)
        return b"%PDF-1.7\n%%EOF\n"

    monkeypatch.setattr(reporting, "_render_image_report_pdf", fake_renderer)
    malicious_result = {
        "final_label": "真实图像",
        "probability": 0.01,
        "confidence": "低",
        "filename": "attacker.png",
        "explanation": "客户端声称没有风险",
        "source_sha256": "0" * 64,
        "model_version": "attacker-model",
        "visual_issues": ["客户端伪造证据"],
        "visibleWatermark": {"detected": False},
    }

    pdf = reporting.image_report_pdf(
        _item(),
        malicious_result,
        source_path=source,
        model_run=MODEL_RUN,
        generated_at=GENERATED_AT,
        signing_key=SIGNING_KEY,
        snapshot_root=tmp_path / "snapshots",
    )
    envelope = evidence_manifest.extract_envelope_from_pdf(pdf)

    assert rendered["result"]["final_label"] == "AI生成图像"
    assert rendered["result"]["probability"] == pytest.approx(0.9125)
    assert rendered["result"]["filename"] == "stored-image.png"
    assert "主模型与服务端取证证据一致" in rendered["result"]["explanation"]
    assert "客户端声称没有风险" not in rendered["result"]["explanation"]
    assert rendered["result"]["visual_issues"] == []
    assert rendered["result"]["visibleWatermark"] is None
    assert envelope["manifest"]["source"]["sha256"] == hashlib.sha256(b"authoritative-source").hexdigest()
    assert envelope["manifest"]["model"]["version"] == "v1-onnx-mil-2026.07"
    assert reporting.verify_image_report(pdf, signing_key=SIGNING_KEY) is True


def test_html_report_embeds_signed_envelope_and_not_client_verdict(tmp_path):
    source = tmp_path / "original.png"
    source.write_bytes(b"original")

    html = reporting.image_report_content(
        _item(fake=66.1, clarity="低"),
        {"final_label": "真实图像", "probability": 0.01, "explanation": "client"},
        source_path=source,
        model_run=MODEL_RUN,
        generated_at=GENERATED_AT,
        signing_key=SIGNING_KEY,
        snapshot_root=tmp_path / "snapshots",
    )

    assert "需人工复核 · 66.1%" in html
    assert "不可变证据清单" in html
    assert hashlib.sha256(b"original").hexdigest() in html
    assert "v1-onnx-mil-2026.07" in html
    assert "客户端声称没有风险" not in html


def test_report_endpoint_returns_clear_422_when_historical_original_is_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("REALGUARD_EVIDENCE_HMAC_KEY", SIGNING_KEY)
    monkeypatch.setenv("REALGUARD_EVIDENCE_SOURCE_ROOTS", str(tmp_path / "missing-static"))
    monkeypatch.setattr(detection, "_load_detection_record", lambda table, itemid: _item(itemid=int(itemid)))
    monkeypatch.setattr(detection, "_metadata_for_item", lambda itemid: {})
    monkeypatch.setattr(detection, "_runtime_visible_watermark_for_item", lambda itemid: None)
    app = creat_app()
    app.config.update(TESTING=True)
    client = app.test_client()
    with client.session_transaction() as session:
        session["guest_openid"] = "openid-73"

    response = client.get("/image_upload/report?itemid=73")

    assert response.status_code == 422
    assert "无法生成可验证的图像报告" in response.get_data(as_text=True)
    assert "拒绝生成无原件哈希" in response.get_data(as_text=True)
