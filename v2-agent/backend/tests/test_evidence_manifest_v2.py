from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def isolated_storage(monkeypatch, tmp_path):
    from app import storage

    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "jianzhen-v2.sqlite3")
    monkeypatch.setattr(storage, "_INITIALIZED", False)
    yield storage
    storage._INITIALIZED = False


def _result(task_id: str = "evidence-task") -> tuple[dict, str, dict]:
    sha256 = "a" * 64
    actor = {
        "mode": "developer",
        "userId": "user-1",
        "accountUuid": "11111111-1111-4111-8111-111111111111",
        "keyId": "key-1",
    }
    result = {
        "taskId": task_id,
        "reportId": f"report-{task_id}",
        "createdAt": "2026-07-20T10:00:00+00:00",
        "fileMeta": {
            "name": "private-name.png",
            "type": "image",
            "size": "1.0KB",
            "resolution": "10x10",
            "sha256": sha256,
        },
        "verdict": "unknown",
        "confidence": 0.0,
        "riskScore": None,
        "decisionStatus": "review_only",
        "decisionAuthority": "none",
        "reviewRequired": True,
        "modelVersion": "pytest-model-revision-1",
        "source": "vlm",
        "cacheVersion": "pytest-cache-v1",
        "dimensions": [{"key": "texture", "score": 0.2, "result": "review"}],
        "regions": [],
        "explanation": "signed evidence explanation",
        "visibleWatermark": {"detected": False, "hits": []},
        "provenance": {"hasCredentials": False, "validationState": "none"},
    }
    return result, sha256, actor


def _put_history(storage, task_id: str = "evidence-task") -> tuple[dict, dict, str, dict]:
    result, sha256, actor = _result(task_id)
    manifest = storage.put_history(
        result,
        sha256=sha256,
        file_size=1024,
        thumbnail=None,
        actor=actor,
    )
    return manifest, result, sha256, actor


def test_private_key_file_is_strictly_opened_and_permission_checked(monkeypatch, tmp_path):
    from app import evidence_manifest_v2

    key_path = tmp_path / "evidence-signing.key"
    key_path.write_text(
        "base64:" + base64.b64encode(bytes(range(32))).decode("ascii"),
        encoding="ascii",
    )
    monkeypatch.delenv("JIANZHEN_EVIDENCE_SIGNING_PRIVATE_KEY", raising=False)
    monkeypatch.setenv("JIANZHEN_EVIDENCE_SIGNING_PRIVATE_KEY_FILE", str(key_path))

    key_path.chmod(0o644)
    with pytest.raises(evidence_manifest_v2.EvidenceConfigurationError, match="permissions"):
        evidence_manifest_v2.load_signing_key()

    key_path.chmod(0o600)
    assert evidence_manifest_v2.load_signing_key().key_id == "pytest-evidence-2026-01"

    symlink_path = tmp_path / "evidence-signing-link.key"
    symlink_path.symlink_to(key_path)
    monkeypatch.setenv("JIANZHEN_EVIDENCE_SIGNING_PRIVATE_KEY_FILE", str(symlink_path))
    with pytest.raises(evidence_manifest_v2.EvidenceConfigurationError, match="not readable"):
        evidence_manifest_v2.load_signing_key()


def test_history_and_manifest_are_idempotent_but_reject_inconsistent_rewrites(isolated_storage):
    storage = isolated_storage
    first, result, sha256, actor = _put_history(storage)
    replay = storage.put_history(
        deepcopy(result),
        sha256=sha256,
        file_size=1024,
        thumbnail=None,
        actor=actor,
    )
    assert replay == first

    changed = deepcopy(result)
    changed["explanation"] = "rewritten result"
    with pytest.raises(storage.EvidenceConflictError):
        storage.put_history(
            changed,
            sha256=sha256,
            file_size=1024,
            thumbnail=None,
            actor=actor,
        )

    with storage._connect() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "UPDATE history SET result_json = ? WHERE task_id = ?",
                (json.dumps(changed), result["taskId"]),
            )
        conn.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            conn.execute(
                """
                INSERT OR REPLACE INTO history
                    (task_id, report_id, created_at, sha256, file_type, file_name,
                     file_size, resolution, result_json, thumbnail,
                     developer_user_id, developer_account_uuid, developer_key_id)
                SELECT task_id, report_id, created_at, sha256, file_type, file_name,
                       file_size, resolution, result_json, thumbnail,
                       developer_user_id, developer_account_uuid, developer_key_id
                FROM history WHERE task_id = ?
                """,
                (result["taskId"],),
            )


def test_manifest_pdf_and_offline_bundle_verify_normally(
    isolated_storage,
    monkeypatch,
    tmp_path,
    capsys,
):
    from app import evidence_manifest_v2

    storage = isolated_storage
    manifest, result, _sha256, _actor = _put_history(storage)
    before_pdf = storage.verify_evidence(result["reportId"])
    assert before_pdf["status"] == "missing"
    assert before_pdf["manifest"]["status"] == "valid"
    assert before_pdf["artifact"]["status"] == "missing"
    assert before_pdf["complete"] is False
    assert manifest["payload"]["sealedAt"].endswith("+00:00")
    assert manifest["payload"]["timeEvidence"] == {
        "observedAt": manifest["payload"]["sealedAt"],
        "clockSource": "application_system_utc",
        "assurance": "untrusted_system_clock",
        "trustedTimestampAuthority": None,
        "externallyAnchored": False,
    }
    with storage._connect() as conn:
        signed_at = conn.execute(
            "SELECT signed_at FROM evidence_manifests_v2 WHERE task_id = ?",
            (result["taskId"],),
        ).fetchone()["signed_at"]
    assert signed_at == manifest["payload"]["sealedAt"]

    report_payload = {**result, "evidenceIntegrity": {"manifestSha256": manifest["sha256"]}}
    pdf = b"%PDF-1.7\nimmutable pytest report\n%%EOF\n"
    artifact = storage.put_report_artifact(
        result["reportId"],
        artifact_bytes=pdf,
        filename="report.pdf",
        media_type="application/pdf",
        report_payload=report_payload,
    )
    replay = storage.put_report_artifact(
        result["reportId"],
        artifact_bytes=pdf,
        filename="report.pdf",
        media_type="application/pdf",
        report_payload=deepcopy(report_payload),
    )
    assert replay["artifactSha256"] == artifact["artifactSha256"]

    verification = storage.verify_evidence(result["reportId"])
    assert verification["status"] == "valid"
    assert verification["packageIntegrityVerified"] is True
    assert verification["subjectVerified"] is False
    assert verification["complete"] is False
    assert verification["manifest"]["keyId"] == "pytest-evidence-2026-01"
    assert verification["artifact"]["artifactSha256"] == artifact["artifactSha256"]
    assert artifact["signedRecord"]["payload"]["sealedAt"]
    assert artifact["signedRecord"]["payload"]["timeEvidence"]["assurance"] == (
        "untrusted_system_clock"
    )

    bundle = storage.get_evidence_bundle(result["reportId"])
    key_id = manifest["signature"]["keyId"]
    public_key = manifest["signature"]["publicKey"]
    monkeypatch.delenv("JIANZHEN_EVIDENCE_SIGNING_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("JIANZHEN_EVIDENCE_SIGNING_PRIVATE_KEY_FILE", raising=False)
    unanchored = evidence_manifest_v2.verify_bundle(bundle, artifact_bytes=pdf)
    assert unanchored["status"] == "invalid"
    assert "trusted_public_key_required" in unanchored["manifest"]["errors"]
    offline = evidence_manifest_v2.verify_bundle(
        bundle,
        artifact_bytes=pdf,
        trusted_public_keys={key_id: base64.b64decode(public_key)},
    )
    assert offline["status"] == "valid"
    assert offline["packageIntegrityVerified"] is True
    assert offline["complete"] is False

    bundle_path = tmp_path / "evidence-bundle.json"
    artifact_path = tmp_path / "report.pdf"
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
    artifact_path.write_bytes(pdf)
    exit_code = evidence_manifest_v2.main([
        "verify",
        "--bundle",
        str(bundle_path),
        "--artifact",
        str(artifact_path),
        "--public-key",
        public_key,
        "--key-id",
        key_id,
    ])
    cli_result = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert cli_result["status"] == "valid"
    assert cli_result["complete"] is False


def test_offline_bundle_can_bind_and_reject_original_subject(monkeypatch, tmp_path, capsys):
    from app import evidence_manifest_v2

    subject = b"original subject bytes for offline verification"
    result, _ignored_sha256, actor = _result("subject-binding")
    subject_sha256 = hashlib.sha256(subject).hexdigest()
    result["fileMeta"]["sha256"] = subject_sha256
    manifest = evidence_manifest_v2.seal_manifest(
        evidence_manifest_v2.build_manifest(
            result,
            original_sha256=subject_sha256,
            file_size=len(subject),
            actor=actor,
        )
    )
    report_payload = {**result, "manifestSha256": manifest["sha256"]}
    pdf = b"%PDF-1.7\nsubject binding\n%%EOF\n"
    artifact = evidence_manifest_v2.seal_artifact_statement(
        evidence_manifest_v2.build_artifact_statement(
            manifest,
            report_payload=report_payload,
            artifact_bytes=pdf,
            filename="report.pdf",
        )
    )
    bundle = {
        "schema": evidence_manifest_v2.BUNDLE_SCHEMA,
        "manifest": manifest,
        "artifact": artifact,
    }
    key_id = manifest["signature"]["keyId"]
    public_key = manifest["signature"]["publicKey"]
    trusted_keys = {key_id: base64.b64decode(public_key)}

    valid = evidence_manifest_v2.verify_bundle(
        bundle,
        artifact_bytes=pdf,
        report_payload=report_payload,
        subject_bytes=subject,
        trusted_public_keys=trusted_keys,
    )
    tampered = evidence_manifest_v2.verify_bundle(
        bundle,
        artifact_bytes=pdf,
        report_payload=report_payload,
        subject_bytes=subject + b"tampered",
        trusted_public_keys=trusted_keys,
    )

    assert valid["status"] == "valid"
    assert valid["subjectVerified"] is True
    assert valid["subject"]["sha256"] == subject_sha256
    assert tampered["status"] == "invalid"
    assert tampered["complete"] is False
    assert "subject_sha256_mismatch" in tampered["subject"]["errors"]
    assert "subject_size_mismatch" in tampered["subject"]["errors"]

    bundle_path = tmp_path / "evidence-bundle.json"
    artifact_path = tmp_path / "report.pdf"
    payload_path = tmp_path / "report-payload.json"
    subject_path = tmp_path / "original.bin"
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
    artifact_path.write_bytes(pdf)
    payload_path.write_text(json.dumps(report_payload, ensure_ascii=False), encoding="utf-8")
    subject_path.write_bytes(subject)
    exit_code = evidence_manifest_v2.main([
        "verify",
        "--bundle",
        str(bundle_path),
        "--artifact",
        str(artifact_path),
        "--report-payload",
        str(payload_path),
        "--subject",
        str(subject_path),
        "--public-key",
        public_key,
        "--key-id",
        key_id,
    ])
    cli_result = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert cli_result["subjectVerified"] is True


def test_exported_key_registry_disclaims_same_package_trust():
    from app import evidence_manifest_v2

    registry = evidence_manifest_v2.verification_key_registry()

    assert registry["schema"] == evidence_manifest_v2.KEY_REGISTRY_SCHEMA
    assert registry["externallyAnchored"] is False
    assert registry["trustedTimestampAvailable"] is False
    assert registry["keys"][0]["status"] == "active"
    assert len(registry["keys"][0]["publicKeySha256"]) == 64
    assert "独立发布渠道" in registry["trustNotice"]


def test_manifest_and_pdf_tampering_are_detected(isolated_storage):
    from app import evidence_manifest_v2

    storage = isolated_storage
    manifest, result, _sha256, _actor = _put_history(storage)
    pdf = b"%PDF-1.7\nsigned bytes\n%%EOF\n"
    storage.put_report_artifact(
        result["reportId"],
        artifact_bytes=pdf,
        filename="report.pdf",
        media_type="application/pdf",
        report_payload={**result, "manifestSha256": manifest["sha256"]},
    )
    bundle = storage.get_evidence_bundle(result["reportId"])
    trusted_keys = {
        manifest["signature"]["keyId"]: base64.b64decode(manifest["signature"]["publicKey"]),
    }

    tampered_manifest = deepcopy(bundle)
    tampered_manifest["manifest"]["payload"]["binding"]["result"]["explanation"] = "tampered"
    manifest_verification = evidence_manifest_v2.verify_bundle(
        tampered_manifest,
        artifact_bytes=pdf,
        trusted_public_keys=trusted_keys,
    )
    pdf_verification = evidence_manifest_v2.verify_bundle(
        bundle,
        artifact_bytes=pdf + b"tampered",
        trusted_public_keys=trusted_keys,
    )

    assert manifest_verification["status"] == "invalid"
    assert "payload_sha256_mismatch" in manifest_verification["manifest"]["errors"]
    assert pdf_verification["status"] == "invalid"
    assert "artifact_sha256_mismatch" in pdf_verification["artifact"]["errors"]


def test_inconsistent_second_pdf_is_rejected_without_overwrite(isolated_storage):
    storage = isolated_storage
    manifest, result, _sha256, _actor = _put_history(storage)
    payload = {**result, "manifestSha256": manifest["sha256"]}
    original = b"%PDF-1.7\nfirst\n%%EOF\n"
    storage.put_report_artifact(
        result["reportId"],
        artifact_bytes=original,
        filename="report.pdf",
        media_type="application/pdf",
        report_payload=payload,
    )

    with pytest.raises(storage.EvidenceConflictError):
        storage.put_report_artifact(
            result["reportId"],
            artifact_bytes=b"%PDF-1.7\nsecond\n%%EOF\n",
            filename="report.pdf",
            media_type="application/pdf",
            report_payload=payload,
        )

    assert storage.get_report_artifact(result["reportId"])["bytes"] == original


def test_pdf_freezes_forensics_and_provenance_attachments(isolated_storage):
    storage = isolated_storage
    manifest, result, _sha256, _actor = _put_history(storage, "frozen-attachments")
    forensics = {"summary": "snapshot-a", "score": 0.4}
    provenance = {"issuer": "camera-a", "credentialTrusted": True}
    storage.put_history_artifacts(
        result["taskId"],
        forensics=forensics,
        provenance=provenance,
    )
    report_payload = {
        **result,
        "forensics": forensics,
        "provenance": provenance,
        "manifestSha256": manifest["sha256"],
    }
    storage.put_report_artifact(
        result["reportId"],
        artifact_bytes=b"%PDF-1.7\nfrozen attachments\n%%EOF\n",
        filename="report.pdf",
        media_type="application/pdf",
        report_payload=report_payload,
    )

    storage.put_history_artifacts(
        result["taskId"],
        forensics=deepcopy(forensics),
        provenance=deepcopy(provenance),
    )
    with pytest.raises(storage.EvidenceConflictError, match="cannot change"):
        storage.put_history_artifacts(
            result["taskId"],
            forensics={"summary": "snapshot-b", "score": 0.9},
        )
    with storage._connect() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="attachments are frozen"):
            conn.execute(
                "UPDATE history_artifacts SET forensics_json = '{}' WHERE task_id = ?",
                (result["taskId"],),
            )
        conn.rollback()

    assert storage.get_report_artifact(result["reportId"])["reportPayload"] == report_payload


def test_pdf_freeze_rejects_stale_attachment_snapshot(isolated_storage):
    storage = isolated_storage
    manifest, result, _sha256, _actor = _put_history(storage, "stale-attachments")
    storage.put_history_artifacts(result["taskId"], forensics={"summary": "current"})

    with pytest.raises(storage.EvidenceConflictError, match="does not match"):
        storage.put_report_artifact(
            result["reportId"],
            artifact_bytes=b"%PDF-1.7\nstale attachments\n%%EOF\n",
            filename="report.pdf",
            media_type="application/pdf",
            report_payload={
                **result,
                "forensics": {"summary": "stale"},
                "manifestSha256": manifest["sha256"],
            },
        )

    assert storage.get_report_artifact(result["reportId"]) is None


def test_consistent_pdf_replay_survives_signing_key_rotation(isolated_storage, monkeypatch):
    storage = isolated_storage
    manifest, result, _sha256, _actor = _put_history(storage)
    payload = {**result, "manifestSha256": manifest["sha256"]}
    pdf = b"%PDF-1.7\nrotation-safe\n%%EOF\n"
    first = storage.put_report_artifact(
        result["reportId"],
        artifact_bytes=pdf,
        filename="report.pdf",
        media_type="application/pdf",
        report_payload=payload,
    )

    old_key_id = manifest["signature"]["keyId"]
    old_public_key = manifest["signature"]["publicKey"]
    monkeypatch.setenv(
        "JIANZHEN_EVIDENCE_SIGNING_PRIVATE_KEY",
        "base64:" + base64.b64encode(bytes(range(32, 64))).decode("ascii"),
    )
    monkeypatch.setenv("JIANZHEN_EVIDENCE_SIGNING_KEY_ID", "pytest-evidence-2026-02")
    monkeypatch.setenv(
        "JIANZHEN_EVIDENCE_VERIFY_PUBLIC_KEYS",
        json.dumps({old_key_id: old_public_key}),
    )

    replay = storage.put_report_artifact(
        result["reportId"],
        artifact_bytes=pdf,
        filename="report.pdf",
        media_type="application/pdf",
        report_payload=deepcopy(payload),
    )

    assert replay["artifactSha256"] == first["artifactSha256"]
    assert replay["signedRecord"]["signature"]["keyId"] == old_key_id


def test_report_artifact_task_and_report_must_reference_same_manifest(isolated_storage):
    storage = isolated_storage
    _first_manifest, first, _sha256, _actor = _put_history(storage, "pair-first")
    _second_manifest, second, _sha256, _actor = _put_history(storage, "pair-second")

    with storage._connect() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="binding"):
            conn.execute(
                """
                INSERT INTO report_artifacts_v2
                    (report_id, task_id, filename, media_type, artifact_bytes,
                     artifact_sha256, artifact_size, report_payload_json,
                     report_payload_sha256, statement_json, statement_sha256,
                     signature, algorithm, key_id, public_key, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    second["reportId"],
                    first["taskId"],
                    "cross-linked.pdf",
                    "application/pdf",
                    sqlite3.Binary(b"%PDF-1.7\n%%EOF\n"),
                    "1" * 64,
                    16,
                    "{}",
                    "2" * 64,
                    "{}",
                    "3" * 64,
                    "invalid",
                    "Ed25519",
                    "pytest-evidence-2026-01",
                    "invalid",
                    "2026-07-20T00:00:00+00:00",
                ),
            )
        conn.rollback()


def test_privacy_delete_removes_signed_content_in_fk_order(isolated_storage):
    storage = isolated_storage
    manifest, result, _sha256, actor = _put_history(storage)
    private_forensics = {"summary": "private evidence"}
    storage.put_history_artifacts(result["taskId"], forensics=private_forensics)
    storage.put_report_artifact(
        result["reportId"],
        artifact_bytes=b"%PDF-1.7\nprivate report\n%%EOF\n",
        filename="private-report.pdf",
        media_type="application/pdf",
        report_payload={
            **result,
            "forensics": private_forensics,
            "manifestSha256": manifest["sha256"],
        },
    )
    share_id = "privacy-share"
    storage.create_report_share(
        share_id=share_id,
        report_id=result["reportId"],
        expires_at=2_000_000_000,
        created_by_user_id=actor["userId"],
        created_by_key_id=actor["keyId"],
        created_by_mode="developer",
    )
    storage.record_report_share_access(
        share_id=share_id,
        report_id=result["reportId"],
        client_ip="192.0.2.5",
        user_agent="private-agent",
        outcome="granted",
    )
    storage.record_token_usage(
        actor=actor,
        endpoint="/api/privacy-delete-test",
        file_type="image",
        result=result,
        usage={"totalTokens": 17},
    )

    deleted = storage.delete_history(result["taskId"])

    assert deleted is not None
    assert storage.get_history(result["taskId"]) is None
    with storage._connect() as conn:
        for table in (
            "history",
            "history_artifacts",
            "evidence_manifests_v2",
            "report_artifacts_v2",
            "report_shares",
            "report_share_access_events",
        ):
            assert conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"] == 0
        usage = conn.execute(
            """
            SELECT developer_user_id, developer_key_id, task_id, report_id
            FROM token_usage_events
            WHERE endpoint = '/api/privacy-delete-test'
            """
        ).fetchone()
        assert dict(usage) == {
            "developer_user_id": None,
            "developer_key_id": None,
            "task_id": None,
            "report_id": None,
        }
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
