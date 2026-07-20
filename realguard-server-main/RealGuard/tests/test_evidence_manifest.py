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
        "modelDecision": {
            "ready": True,
            "mode": "calibrated_verdict",
            "calibrationId": "commercial-calibration-2026-07",
            "manifestSha256": "b" * 64,
            "datasetSha256": "a" * 64,
            "modelSha256": "c" * 64,
            "preprocessingSha256": "d" * 64,
            "runtimeContractSha256": "e" * 64,
            "evaluationCodeRevision": "eval-commit-abc123",
            "expiresAt": "2099-12-31T23:59:59Z",
            "realSamples": 800,
            "fakeSamples": 700,
            "observedFpr": 0.03,
            "observedFnr": 0.08,
            "aiThreshold": 0.61,
            "gateReasons": [],
        },
        "visibleWatermark": {
            "enabled": True,
            "supported": True,
            "detected": True,
            "coordinateSpace": "display_normalized_v1",
            "displaySize": {"width": 320, "height": 320},
            "confidence": 0.97,
            "provider": "server-watermark-v2",
            "hits": [{
                "label": "AI watermark",
                "bbox": {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1},
            }],
        },
    },
}


def test_legacy_watermark_without_coordinate_protocol_is_not_signed_as_supported():
    structured = evidence_manifest._structured_visible_watermark({
        "meta": {"visibleWatermark": {"supported": True, "detected": True, "hits": []}}
    })

    assert structured["supported"] is False
    assert structured["coordinate_space"] == "unknown"


def test_evidence_manifest_drops_invalid_watermark_boxes_instead_of_clamping():
    structured = evidence_manifest._structured_visible_watermark({
        "meta": {"visibleWatermark": {
            "supported": True,
            "coordinateSpace": "display_normalized_v1",
            "displaySize": {"width": 320, "height": 320},
            "detected": True,
            "hits": [{
                "provider": "gemini",
                "confidence": 0.99,
                "bbox": {"x": -0.2, "y": 0.8, "w": 0.3, "h": 0.3},
                "method": "remove_ai_watermarks_registry",
                "decisive": True,
            }],
        }}
    })

    assert structured["hits"] == []
    assert structured["decisive_authorized"] is False


def test_explicit_review_only_is_authoritative_in_signed_evidence():
    model_run = copy.deepcopy(MODEL_RUN)
    model_run["meta"]["decisionAuthorization"] = {
        "status": "review_only",
        "authority": "none",
    }

    authorization = evidence_manifest._decision_authorization(model_run, None)

    assert authorization["status"] == "review_only"
    assert authorization["authority"] == "none"


@pytest.fixture(autouse=True)
def _persisted_metadata_store(monkeypatch):
    monkeypatch.setattr(evidence_manifest, "load_recorded_metadata", lambda record_id: {})


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
    assert manifest["conclusion"]["label"] == "需人工复核"
    assert manifest["conclusion"]["risk_score_percent"] is None
    assert manifest["conclusion"]["confidence"] == "不适用"
    assert manifest["evidence_summary"]["source"] == "persisted_server_record"
    assert manifest["evidence_summary"]["signals"] == [{
        "type": "visible_watermark",
        "supported": True,
        "detected": True,
        "confidence": 0.97,
        "provider": "server-watermark-v2",
        "hit_count": 1,
    }]
    assert manifest["generated_at"] == "2026-07-19T08:30:00Z"
    assert envelope["signature"]["algorithm"] == "HMAC-SHA256"
    assert evidence_manifest.verify_manifest(envelope, key=SIGNING_KEY) is True


def test_boundary_score_is_signed_as_manual_review_with_raw_label_preserved(tmp_path):
    source = tmp_path / "boundary.png"
    source.write_bytes(b"boundary-image")

    envelope = evidence_manifest.create_signed_image_manifest(
        _item(fake=52.0, aigc="AI生成图像", clarity="低"),
        source_path=source,
        model_run=MODEL_RUN,
        generated_at=GENERATED_AT,
        key=SIGNING_KEY,
    )

    conclusion = envelope["manifest"]["conclusion"]
    assert conclusion["label"] == "需人工复核"
    assert conclusion["raw_model_label"] == "AI生成图像"


def test_metadata_snapshot_includes_reproducible_normalized_digest(monkeypatch, tmp_path):
    metadata = {"Make": "Example Camera", "ISO": 100}
    monkeypatch.setattr(evidence_manifest, "load_recorded_metadata", lambda record_id: metadata)
    source = tmp_path / "metadata.png"
    source.write_bytes(b"metadata-image")

    envelope = evidence_manifest.create_signed_image_manifest(
        _item(),
        source_path=source,
        model_run=MODEL_RUN,
        generated_at=GENERATED_AT,
        key=SIGNING_KEY,
    )

    snapshot = envelope["manifest"]["structured_evidence"]["metadata"]
    assert snapshot["extractor"] == "server-persisted-exif-v1"
    assert snapshot["normalized_sha256"] == hashlib.sha256(
        evidence_manifest.canonical_json(metadata)
    ).hexdigest()


def test_detection_fails_closed_when_completion_snapshot_cannot_be_frozen(monkeypatch):
    monkeypatch.setattr(detection, "excute_detection_sql", lambda *args, **kwargs: 1)
    monkeypatch.setattr(detection, "_load_detection_record", lambda *_: _item())
    monkeypatch.setattr(
        detection.reporting,
        "freeze_image_evidence_snapshot",
        lambda *_: (_ for _ in ()).throw(RuntimeError("snapshot unavailable")),
    )

    with pytest.raises(RuntimeError, match="证据快照固化失败"):
        detection._persist_and_freeze_completed_image_result(73, {
            "probability": 0.8,
            "detector_probability": 0.8,
            "final_label": "AI生成图像",
            "confidence": "高",
            "explanation": "test",
        })


def test_background_completion_loads_record_from_explicit_actor(monkeypatch):
    actor = {"account_uuid": "11111111-1111-4111-8111-111111111111"}
    captured = {}
    monkeypatch.setattr(detection, "excute_detection_sql", lambda *args, **kwargs: 1)
    monkeypatch.setattr(
        detection,
        "_load_detection_record",
        lambda *_: (_ for _ in ()).throw(AssertionError("must not access request session")),
    )

    def load_for_actor(table, itemid, received_actor, *, is_guest=False):
        captured.update(table=table, itemid=itemid, actor=received_actor, is_guest=is_guest)
        return _item()

    monkeypatch.setattr(detection, "_load_detection_record_for_actor", load_for_actor)
    monkeypatch.setattr(detection.reporting, "freeze_image_evidence_snapshot", lambda item: {"ok": True})

    assert detection._persist_and_freeze_completed_image_result(
        73,
        {
            "probability": 0.2,
            "detector_probability": 0.2,
            "final_label": "真实图像",
            "confidence": "高",
            "explanation": "test",
        },
        actor=actor,
    ) is True
    assert captured == {"table": "data", "itemid": 73, "actor": actor, "is_guest": False}


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


def test_rotated_keyring_verifies_old_snapshot_and_new_signatures_use_active_key(monkeypatch, tmp_path):
    source = tmp_path / "original.png"
    source.write_bytes(b"rotation-original")
    old_key = "old-evidence-key-0123456789abcdef0123456789abcdef"
    new_key = "new-evidence-key-0123456789abcdef0123456789abcdef"
    monkeypatch.setenv("REALGUARD_EVIDENCE_HMAC_KEY_ID", "evidence-2026-01")
    monkeypatch.setenv("REALGUARD_EVIDENCE_HMAC_KEY", old_key)
    old_envelope = evidence_manifest.create_signed_image_manifest(
        _item(),
        source_path=source,
        model_run=MODEL_RUN,
        generated_at=GENERATED_AT,
    )

    monkeypatch.setenv("REALGUARD_EVIDENCE_HMAC_KEY_ID", "evidence-2026-07")
    monkeypatch.setenv("REALGUARD_EVIDENCE_HMAC_KEY", new_key)
    monkeypatch.setenv(
        "REALGUARD_EVIDENCE_HMAC_KEYS_JSON",
        '{"evidence-2026-01":"old-evidence-key-0123456789abcdef0123456789abcdef"}',
    )

    assert evidence_manifest.verify_manifest(old_envelope) is True
    new_envelope = evidence_manifest.create_signed_image_manifest(
        _item(itemid=74),
        source_path=source,
        model_run=MODEL_RUN,
        generated_at=GENERATED_AT,
    )
    assert new_envelope["signature"]["key_id"] == "evidence-2026-07"
    assert new_envelope["manifest"]["signature_key_id"] == "evidence-2026-07"
    assert evidence_manifest.verify_manifest(new_envelope) is True


def test_unknown_historical_key_id_and_placeholder_key_are_rejected(monkeypatch, tmp_path):
    source = tmp_path / "original.png"
    source.write_bytes(b"old-original")
    monkeypatch.setenv("REALGUARD_EVIDENCE_HMAC_KEY_ID", "old-key")
    old = evidence_manifest.create_signed_image_manifest(
        _item(),
        source_path=source,
        model_run=MODEL_RUN,
        generated_at=GENERATED_AT,
        key=SIGNING_KEY,
    )
    monkeypatch.setenv("REALGUARD_EVIDENCE_HMAC_KEY_ID", "new-key")
    monkeypatch.setenv("REALGUARD_EVIDENCE_HMAC_KEY", SIGNING_KEY)
    monkeypatch.delenv("REALGUARD_EVIDENCE_HMAC_KEYS_JSON", raising=False)
    assert evidence_manifest.verify_manifest(old) is False

    monkeypatch.setenv(
        "REALGUARD_EVIDENCE_HMAC_KEYS_JSON",
        '{"old-key":"replace-with-an-independent-random-64-hex-character-key"}',
    )
    with pytest.raises(evidence_manifest.EvidenceManifestError, match="公开占位符"):
        evidence_manifest._verification_keyring()

    monkeypatch.setenv("REALGUARD_EVIDENCE_HMAC_KEYS_JSON", '{"old-key":null}')
    with pytest.raises(evidence_manifest.EvidenceManifestError, match="必须是字符串"):
        evidence_manifest._verification_keyring()


def test_old_snapshot_pdf_remains_verifiable_after_active_key_rotation(monkeypatch, tmp_path):
    source = tmp_path / "original.png"
    source.write_bytes(b"historic-report-original")
    snapshot_root = tmp_path / "snapshots"
    old_key = "old-report-key-0123456789abcdef0123456789abcdef"
    new_key = "new-report-key-0123456789abcdef0123456789abcdef"
    monkeypatch.setattr(reporting, "_render_image_report_pdf", lambda item, result: b"%PDF-1.7\n%%EOF\n")
    monkeypatch.setenv("REALGUARD_EVIDENCE_HMAC_KEY_ID", "report-2026-01")
    monkeypatch.setenv("REALGUARD_EVIDENCE_HMAC_KEY", old_key)
    reporting.image_report_pdf(
        _item(),
        {},
        source_path=source,
        model_run=MODEL_RUN,
        generated_at=GENERATED_AT,
        snapshot_root=snapshot_root,
    )

    monkeypatch.setenv("REALGUARD_EVIDENCE_HMAC_KEY_ID", "report-2026-07")
    monkeypatch.setenv("REALGUARD_EVIDENCE_HMAC_KEY", new_key)
    monkeypatch.setenv(
        "REALGUARD_EVIDENCE_HMAC_KEYS_JSON",
        '{"report-2026-01":"old-report-key-0123456789abcdef0123456789abcdef"}',
    )
    historic_pdf = reporting.image_report_pdf(
        _item(),
        {"final_label": "客户端无效字段"},
        source_path=source,
        model_run={"id": "new-run"},
        generated_at="2026-07-20T08:30:00Z",
        snapshot_root=snapshot_root,
    )

    embedded = evidence_manifest.extract_envelope_from_pdf(historic_pdf)
    assert embedded["signature"]["key_id"] == "report-2026-01"
    assert embedded["artifact"]["signature"]["key_id"] == "report-2026-01"
    assert reporting.verify_image_report(historic_pdf) is True


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
    assert second["manifest"]["conclusion"]["label"] == "需人工复核"
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


def test_source_resolution_allows_controlled_upload_symlink(monkeypatch, tmp_path):
    static_root = tmp_path / "release" / "static"
    persistent_root = tmp_path / "persistent" / "uploads"
    stored = persistent_root / "openid-73" / "image" / "stored-image.png"
    stored.parent.mkdir(parents=True)
    stored.write_bytes(b"trusted-original")
    static_root.mkdir(parents=True)
    (static_root / "uploads").symlink_to(persistent_root, target_is_directory=True)
    monkeypatch.setenv("REALGUARD_EVIDENCE_SOURCE_ROOTS", str(static_root))

    assert evidence_manifest.resolve_source_path(_item()) == stored.resolve()


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

    assert rendered["result"]["final_label"] == "需人工复核"
    assert rendered["result"]["probability"] is None
    assert rendered["result"]["filename"] == "stored-image.png"
    assert "缺少可验证的已校准模型授权" in rendered["result"]["explanation"]
    assert "客户端声称没有风险" not in rendered["result"]["explanation"]
    assert rendered["result"]["visual_issues"] == []
    assert rendered["result"]["visibleWatermark"]["detected"] is True
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

    assert "需人工复核 · 未发布自动风险分数" in html
    assert "签名完整性清单" in html
    assert hashlib.sha256(b"original").hexdigest() in html
    assert "v1-onnx-mil-2026.07" in html
    assert "客户端声称没有风险" not in html


def test_server_watermark_and_capture_metadata_are_signed_and_rendered(monkeypatch, tmp_path):
    source = tmp_path / "camera-original.jpg"
    source.write_bytes(b"camera-original")
    metadata = {
        "EXIF:Make": "Canon",
        "EXIF:Model": "EOS R5",
        "EXIF:ExposureTime": "1/250",
        "EXIF:FNumber": "2.8",
        "EXIF:ISO": "400",
        "EXIF:FocalLength": "50 mm",
        "EXIF:DateTimeOriginal": "2026:07:18 10:20:30",
        "EXIF:BodySerialNumber": "private-serial-must-not-be-signed",
        "GPSLatitude": "30.123456",
    }
    monkeypatch.setattr(evidence_manifest, "load_recorded_metadata", lambda record_id: metadata)
    model_run = copy.deepcopy(MODEL_RUN)
    model_run["meta"]["visibleWatermark"]["hits"] = [{
        "provider": "gemini",
        "label": "Gemini",
        "confidence": 0.96,
        "bbox": {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.12},
        "method": "registry",
        "model": "watermark-model",
        "modelRevision": "sha256:abc",
        "evidenceRole": "provenance",
        "decisive": True,
    }]
    model_run["meta"]["inferenceAudit"] = {
        "model": "realguardv2-int8",
        "rawModelScore": 0.7342,
        "publishedProbability": 0.5,
        "finalLabel": "需人工复核",
        "originalSize": {"width": 4096, "height": 3072},
        "processedSize": {"width": 2048, "height": 1536},
        "downsample": {"applied": True, "scale": 0.5},
        "chunkCount": 12,
        "parameters": {"chunkSize": 512, "stride": 384},
        "runtime": {"device": "cuda", "modelSha256": "c" * 64},
    }
    rendered = {}

    def fake_renderer(item, result):
        rendered["result"] = copy.deepcopy(result)
        return b"%PDF-1.7\n%%EOF\n"

    monkeypatch.setattr(reporting, "_render_image_report_pdf", fake_renderer)
    pdf = reporting.image_report_pdf(
        _item(filename="camera-original.jpg"),
        {
            "all_metadata": {"EXIF:Make": "attacker"},
            "capture_evidence": {"summary": "attacker"},
            "visibleWatermark": {"detected": False},
        },
        source_path=source,
        model_run=model_run,
        generated_at=GENERATED_AT,
        signing_key=SIGNING_KEY,
        snapshot_root=tmp_path / "snapshots",
    )
    envelope = evidence_manifest.extract_envelope_from_pdf(pdf)
    structured = envelope["manifest"]["structured_evidence"]

    assert structured["visible_watermark"]["hits"][0]["bbox"] == {
        "x": 0.1, "y": 0.2, "w": 0.3, "h": 0.12,
    }
    assert structured["capture_evidence"]["supportsRealCapture"] is True
    assert structured["capture_evidence"]["privacy"]["gpsRedacted"] is True
    assert structured["metadata"]["present"] is True
    assert structured["model_inference"]["rawModelScore"] == pytest.approx(0.7342)
    assert structured["model_inference"]["downsample"]["applied"] is True
    assert structured["model_inference"]["parameters"]["chunkSize"] == 512
    assert "private-serial-must-not-be-signed" not in evidence_manifest.canonical_json(envelope).decode("utf-8")
    assert "30.123456" not in evidence_manifest.canonical_json(envelope).decode("utf-8")
    assert rendered["result"]["visibleWatermark"] == structured["visible_watermark"]
    assert rendered["result"]["capture_evidence"] == structured["capture_evidence"]
    assert rendered["result"]["all_metadata"] == structured["metadata"]
    assert reporting.verify_image_report(pdf, signing_key=SIGNING_KEY) is True


def test_freeze_helper_reuses_server_snapshot_api_and_accepts_no_client_result(monkeypatch):
    item = _item()
    model_run = {"id": "run-server"}
    expected = {"manifest": {"record_id": "73"}, "signature": {"value": "signed"}}
    captured = {}

    def fake_freeze(received_item, **kwargs):
        captured["item"] = received_item
        captured["kwargs"] = kwargs
        return expected

    monkeypatch.setattr(evidence_manifest, "get_or_create_signed_image_manifest", fake_freeze)

    assert reporting.freeze_image_evidence_snapshot(item, model_run=model_run) is expected
    assert captured == {"item": item, "kwargs": {"model_run": model_run}}
    with pytest.raises(TypeError):
        reporting.freeze_image_evidence_snapshot(item, model_run=model_run, result={"fake": True})


def test_user_erasure_removes_corrupt_regular_snapshot(tmp_path):
    root = tmp_path / "snapshots"
    root.mkdir(mode=0o700)
    snapshot = root / "image-73.manifest.json"
    snapshot.write_bytes(b"corrupt snapshot")

    assert evidence_manifest.delete_signed_image_manifest(73, snapshot_root=root) is True
    assert not snapshot.exists()


def test_user_erasure_rejects_snapshot_symlink(tmp_path):
    root = tmp_path / "snapshots"
    root.mkdir(mode=0o700)
    target = tmp_path / "outside.json"
    target.write_text("must remain", encoding="utf-8")
    (root / "image-73.manifest.json").symlink_to(target)

    with pytest.raises(evidence_manifest.EvidenceManifestError):
        evidence_manifest.delete_signed_image_manifest(73, snapshot_root=root)
    assert target.read_text(encoding="utf-8") == "must remain"


def test_user_erasure_can_stage_restore_and_finalize_snapshot(tmp_path):
    root = tmp_path / "snapshots"
    root.mkdir(mode=0o700)
    snapshot = root / "image-73.manifest.json"
    snapshot.write_bytes(b"signed snapshot")

    staged = evidence_manifest.stage_signed_image_manifest_deletion(73, snapshot_root=root)

    assert staged is not None
    assert not snapshot.exists()
    assert staged[1].read_bytes() == b"signed snapshot"

    evidence_manifest.restore_staged_image_manifest_deletion(staged)
    assert snapshot.read_bytes() == b"signed snapshot"
    assert not staged[1].exists()

    staged = evidence_manifest.stage_signed_image_manifest_deletion(73, snapshot_root=root)
    evidence_manifest.finalize_staged_image_manifest_deletion(staged)
    assert not snapshot.exists()
    assert not staged[1].exists()


def test_report_endpoint_returns_clear_422_when_historical_original_is_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("REALGUARD_EVIDENCE_HMAC_KEY", SIGNING_KEY)
    monkeypatch.setenv("REALGUARD_EVIDENCE_SOURCE_ROOTS", str(tmp_path / "missing-static"))
    monkeypatch.setattr(detection, "_load_detection_record", lambda table, itemid: _item(itemid=int(itemid)))
    monkeypatch.setattr(detection, "_metadata_for_item", lambda itemid: {})
    monkeypatch.setattr(detection, "_runtime_visible_watermark_for_item", lambda itemid: None)
    monkeypatch.setattr(
        detection,
        "_stored_decision_authorization_for_item",
        lambda itemid: {"status": "verdict", "authority": "calibrated_model"},
    )
    app = creat_app()
    app.config.update(TESTING=True)
    client = app.test_client()
    with client.session_transaction() as session:
        session["guest_openid"] = "openid-73"

    response = client.get("/image_upload/report?itemid=73")

    assert response.status_code == 422
    assert "无法生成可验证的图像报告" in response.get_data(as_text=True)
    assert "拒绝生成无原件哈希" in response.get_data(as_text=True)
