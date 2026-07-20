from pathlib import Path
import sys
import uuid

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from imagedetection.views import utils  # noqa: E402


def _record(owner=None):
    return {
        "itemid": 41,
        "createtime": "2026-06-01 10:20:30",
        "filename": "legacy.jpg",
        "phone": "13800000000",
        "openid": "legacy-openid",
        "Userid": 7,
        "file_size": "1 MB",
        "img_format": "JPEG",
        "resolution": "1024x768",
        "owner_account_uuid": owner,
    }


class FakeCursor:
    def __init__(self, claim, record, exif_rows=None):
        self.claim = claim
        self.record = record
        self.current = None
        self.rowcount = 0
        self.executed = []
        self.exif_rows = list(exif_rows or [])

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        self.executed.append((normalized, params))
        if "FROM legacy_record_governance" in normalized:
            self.current = self.claim
            self.rowcount = 1
        elif "FROM `data`" in normalized:
            self.current = self.record
            self.rowcount = 1
        elif normalized.startswith("SELECT id, owner_account_uuid FROM exif"):
            self.current = self.exif_rows
            self.rowcount = len(self.exif_rows)
        elif normalized.startswith("UPDATE `data`"):
            self.rowcount = 1
        elif normalized.startswith("UPDATE exif"):
            self.rowcount = 1
        elif normalized.startswith("UPDATE legacy_record_governance"):
            self.rowcount = 1
        else:
            raise AssertionError(normalized)

    def fetchone(self):
        return self.current

    def fetchall(self):
        return self.current if isinstance(self.current, list) else []


class AccountCursor:
    def __init__(self, account):
        self.account = account

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        assert "FROM `user`" in sql

    def fetchone(self):
        return self.account


class FakeConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def begin(self):
        return None

    def cursor(self):
        return self.fake_cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def _claim(record, account, requester=11):
    media_sha256 = "d" * 64
    requester_identity_hash = __import__("hashlib").sha256(b"13800000001").hexdigest()
    return {
        "id": 9,
        "record_type": "data",
        "record_id": 41,
        "record_fingerprint": utils._legacy_record_fingerprint("data", record, media_sha256),
        "media_sha256": media_sha256,
        "status": "claim_pending",
        "target_account_uuid": account["account_uuid"],
        "target_user_id": account["Userid"],
        "target_account_fingerprint": utils._target_account_fingerprint(account),
        "request_operation_id": str(uuid.uuid4()),
        "evidence_reference": "case/41.json",
        "evidence_sha256": "e" * 64,
        "reason": "verified migration evidence",
        "requester_admin_id": requester,
        "requester_identity_hash": requester_identity_hash,
        "request_integrity_hmac": "request-seal",
        "audit_key_id": "test-v1",
        "version": 1,
    }


def _account():
    return {
        "Userid": 77,
        "account_uuid": str(uuid.uuid4()),
        "username": "target",
        "phone": "13900000000",
        "openid": "target-openid",
    }


def _prepare_approval(monkeypatch, claim, account, connection):
    account_connection = FakeConnection(AccountCursor(account))
    monkeypatch.setattr(utils, "get_detection_db_connection", lambda: connection)
    monkeypatch.setattr(utils, "get_db_connection", lambda: account_connection)
    monkeypatch.setattr(
        utils,
        "excute_detection_sql",
        lambda *args, **kwargs: [{
            "target_account_uuid": claim["target_account_uuid"],
            "target_account_fingerprint": claim["target_account_fingerprint"],
        }],
    )
    monkeypatch.setattr(utils, "_legacy_media_sha256", lambda *args: claim["media_sha256"])
    monkeypatch.setattr(utils, "_verify_evidence_reference", lambda *args: True)
    monkeypatch.setattr(utils, "_legacy_hmac", lambda payload, *args: "request-seal" if "operationId" in payload else "approval-seal")
    return account_connection


def test_legacy_claim_approval_requires_a_different_admin(monkeypatch):
    record = _record()
    account = _account()
    claim = _claim(record, account, requester=11)
    connection = FakeConnection(FakeCursor(claim, record))
    _prepare_approval(monkeypatch, claim, account, connection)

    with pytest.raises(utils.LegacyGovernanceError) as error:
        utils.approve_legacy_record_claim(9, str(uuid.uuid4()), 1, 11, "same-admin", "13800000001")

    assert error.value.code == "separation_of_duties"
    assert connection.rolled_back is True
    assert not any(sql.startswith("UPDATE `data`") for sql, _ in connection.fake_cursor.executed)


def test_legacy_claim_approval_changes_owner_and_audit_in_one_transaction(monkeypatch):
    record = _record()
    account = _account()
    claim = _claim(record, account)
    connection = FakeConnection(FakeCursor(claim, record))
    _prepare_approval(monkeypatch, claim, account, connection)
    monkeypatch.setattr(
        utils,
        "get_legacy_record_claim",
        lambda claim_id: {"id": claim_id, "status": "claimed", "version": 2},
    )

    result = utils.approve_legacy_record_claim(9, str(uuid.uuid4()), 1, 12, "reviewer", "13800000002")

    executed = [sql for sql, _ in connection.fake_cursor.executed]
    assert result == {"id": 9, "status": "claimed", "version": 2}
    assert any(sql.startswith("UPDATE `data`") for sql in executed)
    assert any(sql.startswith("UPDATE exif") for sql in executed)
    assert any(sql.startswith("UPDATE legacy_record_governance") for sql in executed)
    assert connection.committed is True
    assert connection.rolled_back is False


def test_legacy_record_fingerprint_detects_material_change():
    original = _record()
    changed = {**original, "filename": "different.jpg"}

    assert utils._legacy_record_fingerprint("data", original) != utils._legacy_record_fingerprint("data", changed)


def test_legacy_preview_fingerprint_is_bound_to_media_bytes(monkeypatch, tmp_path):
    record = _record()
    media = tmp_path / "legacy-openid" / "image" / "legacy.jpg"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"original-media")
    connection = FakeConnection(FakeCursor({}, record))
    monkeypatch.setenv("REALGUARD_UPLOADS_DIR", str(tmp_path))
    monkeypatch.setattr(utils, "get_detection_db_connection", lambda: connection)

    first = utils.legacy_record_preview("data", 41)
    media.write_bytes(b"replaced-media")
    second = utils.legacy_record_preview("data", 41)

    assert first["mediaSha256"] != second["mediaSha256"]
    assert first["fingerprint"] != second["fingerprint"]


def test_legacy_approval_rejects_conflicting_exif_owner(monkeypatch):
    record = _record()
    account = _account()
    claim = _claim(record, account)
    connection = FakeConnection(FakeCursor(
        claim,
        record,
        exif_rows=[{"id": 3, "owner_account_uuid": str(uuid.uuid4())}],
    ))
    _prepare_approval(monkeypatch, claim, account, connection)

    with pytest.raises(utils.LegacyGovernanceError) as error:
        utils.approve_legacy_record_claim(9, str(uuid.uuid4()), 1, 12, "reviewer", "13800000002")

    assert error.value.code == "exif_owner_conflict"
    assert connection.rolled_back is True
    assert not any(sql.startswith("UPDATE `data`") for sql, _ in connection.fake_cursor.executed)


def test_rejected_claim_releases_active_record_key(monkeypatch):
    account = _account()
    claim = _claim(_record(), account)
    connection = FakeConnection(FakeCursor(claim, _record()))
    monkeypatch.setattr(utils, "get_detection_db_connection", lambda: connection)
    monkeypatch.setattr(
        utils,
        "_legacy_hmac",
        lambda payload, *args: "request-seal" if "operationId" in payload else "rejection-seal",
    )
    monkeypatch.setattr(
        utils,
        "get_legacy_record_claim",
        lambda claim_id: {"id": claim_id, "status": "rejected", "version": 2},
    )

    result = utils.reject_legacy_record_claim(
        9, str(uuid.uuid4()), 1, "evidence belongs to another account",
        12, "reviewer", "13800000002",
    )

    update = next(sql for sql, _ in connection.fake_cursor.executed if sql.startswith("UPDATE legacy_record_governance"))
    assert "active_record_key = NULL" in update
    assert result["status"] == "rejected"
    assert connection.committed is True


def test_completed_claim_approval_seal_is_verified(monkeypatch):
    monkeypatch.setenv("REALGUARD_SECURITY_AUDIT_HMAC_KEY", "a" * 64)
    monkeypatch.setenv("REALGUARD_SECURITY_AUDIT_HMAC_KEY_ID", "test-v1")
    account = _account()
    claim = _claim(_record(), account)
    claim["request_integrity_hmac"] = utils._legacy_hmac(
        utils._legacy_request_seal_payload(claim), "test-v1"
    )
    claim.update({
        "status": "claimed",
        "version": 2,
        "approval_operation_id": str(uuid.uuid4()),
        "approver_admin_id": 12,
        "approver_identity_hash": __import__("hashlib").sha256(b"reviewer-person").hexdigest(),
    })
    decision = {
        "claimId": 9,
        "approvalOperationId": claim["approval_operation_id"],
        "expectedVersion": 1,
        "requestIntegrityHmac": claim["request_integrity_hmac"],
        "approverAdminId": 12,
        "approverIdentityHash": claim["approver_identity_hash"],
    }
    claim["approval_integrity_hmac"] = utils._legacy_hmac(decision, "test-v1")

    assert utils._verify_legacy_claim_integrity(claim) is True
    with pytest.raises(utils.LegacyGovernanceError) as error:
        utils._verify_legacy_claim_integrity({**claim, "approver_admin_id": 99})
    assert error.value.code == "audit_integrity_failed"
