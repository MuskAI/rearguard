from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import json
import sys
import threading
import time

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from imagedetection import creat_app  # noqa: E402
from imagedetection.views import api, detection, login, profile, utils  # noqa: E402

ACCOUNT_UUID = "11111111-1111-4111-8111-111111111111"


def test_developer_result_is_hidden_until_billing_settles(monkeypatch):
    record = {
        "itemid": 42,
        "filename": "aabbccddeeff-developer-job_0123456789abcdefabcd.png",
    }
    monkeypatch.setattr(
        utils,
        "excute_sql",
        lambda *_args, **_kwargs: [{"task_status": "success", "billing_status": "reserved"}],
    )
    assert utils.detection_record_is_publishable(record) is False

    monkeypatch.setattr(
        utils,
        "excute_sql",
        lambda *_args, **_kwargs: [{"task_status": "success", "billing_status": "settled"}],
    )
    assert utils.detection_record_is_publishable(record) is True


def test_developer_result_visibility_fails_closed_when_billing_lookup_fails(monkeypatch):
    monkeypatch.setattr(utils, "excute_sql", lambda *_args, **_kwargs: None)
    assert utils.detection_record_is_publishable({
        "itemid": 42,
        "filename": "aabbccddeeff-developer-job_0123456789abcdefabcd.jpg",
    }) is False


def test_explicit_developer_task_id_does_not_depend_on_stored_filename(monkeypatch):
    captured = []

    def fake_sql(_sql, params=None, **_kwargs):
        captured.append(params)
        return [{"task_status": "success", "billing_status": "reserved"}]

    monkeypatch.setattr(utils, "excute_sql", fake_sql)
    assert utils.detection_record_is_publishable({
        "itemid": 43,
        "filename": "opaque-storage-name.png",
        "developer_task_id": "job_0123456789abcdefabcd",
    }) is False
    assert captured[0][0] == "job_0123456789abcdefabcd"


def test_web_result_is_hidden_until_its_durable_task_succeeds(monkeypatch):
    record = {
        "itemid": 44,
        "filename": "opaque-storage-name.png",
        "developer_task_id": "job_0123456789abcdefabcd",
    }
    responses = [[], [{"status": "running"}]]
    monkeypatch.setattr(utils, "excute_sql", lambda *_args, **_kwargs: responses.pop(0))
    assert utils.detection_record_is_publishable(record) is False

    responses = [[], [{"status": "success"}]]
    monkeypatch.setattr(utils, "excute_sql", lambda *_args, **_kwargs: responses.pop(0))
    assert utils.detection_record_is_publishable(record) is True


def test_developer_key_idempotency_derives_stable_scoped_secret(monkeypatch):
    monkeypatch.setattr(api, "DEVELOPER_IDEMPOTENCY_SECRET", "stable-idempotency-secret")

    first = api._idempotent_developer_api_key(7, "create", "operation-1")
    monkeypatch.setattr(api, "DEVELOPER_AUTH_SECRET", "rotated-internal-auth-secret")
    replay = api._idempotent_developer_api_key(7, "create", "operation-1")
    different_user = api._idempotent_developer_api_key(8, "create", "operation-1")
    different_operation = api._idempotent_developer_api_key(7, "rotate:2", "operation-1")

    assert first == replay
    assert first.startswith(api.DEVELOPER_API_KEY_PREFIX)
    assert first != different_user
    assert first != different_operation


def test_developer_key_idempotency_fails_closed_without_dedicated_secret(monkeypatch):
    monkeypatch.setattr(api, "DEVELOPER_AUTH_SECRET", "configured-internal-auth-secret")
    monkeypatch.setattr(api, "DEVELOPER_IDEMPOTENCY_SECRET", "")

    assert api._idempotent_developer_api_key(7, "create", "operation-1") is None


def test_security_audit_event_extends_hmac_chain(monkeypatch):
    monkeypatch.setenv("REALGUARD_SECURITY_AUDIT_HMAC_KEY", "a" * 64)
    monkeypatch.setenv("REALGUARD_SECURITY_AUDIT_HMAC_KEY_ID", "audit-v1")

    class Cursor:
        rowcount = 1

        def __init__(self):
            self.calls = []
            self.current = None

        def execute(self, sql, params=None):
            normalized = " ".join(sql.split())
            self.calls.append((normalized, params))
            if normalized.startswith("SELECT last_event_hash"):
                self.current = {"last_event_hash": "b" * 64}
            self.rowcount = 1

        def fetchone(self):
            return self.current

    cursor = Cursor()
    event_id = api._append_security_audit(
        cursor,
        "account",
        7,
        "developer_api_key.revoke",
        "key:9",
        {"scopes": "image:fast"},
    )

    insert = next(call for call in cursor.calls if call[0].startswith("INSERT INTO security_audit_events"))
    update = next(call for call in cursor.calls if call[0].startswith("UPDATE security_audit_chain_head"))
    assert len(event_id) == 36
    assert insert[1][7] == "b" * 64
    assert len(insert[1][8]) == 64
    assert insert[1][8] == update[1][0]
    assert insert[1][9] == "audit-v1"


def test_security_audit_checkpoint_detects_tail_deletion(monkeypatch, tmp_path):
    monkeypatch.setenv("REALGUARD_SECURITY_AUDIT_HMAC_KEY", "a" * 64)
    monkeypatch.setenv("REALGUARD_SECURITY_AUDIT_HMAC_KEY_ID", "audit-v1")
    monkeypatch.setenv("REALGUARD_SECURITY_AUDIT_HMAC_KEYS_JSON", "{}")
    monkeypatch.setenv("REALGUARD_SECURITY_AUDIT_STATUS_FILE", str(tmp_path / "status.json"))
    monkeypatch.setenv("REALGUARD_SECURITY_AUDIT_CHECKPOINT_FILE", str(tmp_path / "checkpoint.json"))

    class Cursor:
        rowcount = 1

        def __init__(self):
            self.calls = []
            self.current = None

        def execute(self, sql, params=None):
            normalized = " ".join(sql.split())
            self.calls.append((normalized, params))
            if normalized.startswith("SELECT last_event_hash"):
                self.current = {"last_event_hash": "0" * 64}
            self.rowcount = 1

        def fetchone(self):
            return self.current

    cursor = Cursor()
    api._append_security_audit(cursor, "account", 7, "developer_api_key.create", "key:8", {})
    values = next(params for sql, params in cursor.calls if sql.startswith("INSERT INTO security_audit_events"))
    row = dict(zip(
        ("event_id", "occurred_at", "actor_type", "actor_id", "action", "target", "meta_json", "previous_hash", "event_hash", "key_id"),
        values,
    ))
    row["id"] = 1
    current_rows = [row]

    def fake_sql(sql, params=None, fetch=True):
        if "FROM security_audit_events" in sql:
            return list(current_rows)
        if "FROM security_audit_chain_head" in sql:
            return [{"last_event_hash": current_rows[-1]["event_hash"] if current_rows else "0" * 64}]
        raise AssertionError(sql)

    monkeypatch.setattr(api, "excute_sql", fake_sql)
    assert api.verify_security_audit_chain(allow_bootstrap=True)["state"] == "passed"

    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint_bytes = checkpoint_path.read_bytes()
    checkpoint_path.unlink()
    missing = api.verify_security_audit_chain()
    assert missing["state"] == "failed"
    assert "explicit bootstrap" in missing["lastError"]
    checkpoint_path.write_bytes(checkpoint_bytes)

    current_rows.clear()
    failed = api.verify_security_audit_chain()

    assert failed["state"] == "failed"
    assert "moved backwards" in failed["lastError"]


def test_security_audit_checkpoint_accepts_repeated_empty_chain(monkeypatch, tmp_path):
    monkeypatch.setenv("REALGUARD_SECURITY_AUDIT_HMAC_KEY", "a" * 64)
    monkeypatch.setenv("REALGUARD_SECURITY_AUDIT_HMAC_KEY_ID", "audit-v1")
    monkeypatch.setenv("REALGUARD_SECURITY_AUDIT_HMAC_KEYS_JSON", "{}")
    monkeypatch.setenv("REALGUARD_SECURITY_AUDIT_STATUS_FILE", str(tmp_path / "status.json"))
    monkeypatch.setenv("REALGUARD_SECURITY_AUDIT_CHECKPOINT_FILE", str(tmp_path / "checkpoint.json"))

    def fake_sql(sql, params=None, fetch=True):
        if "FROM security_audit_events" in sql:
            return []
        if "FROM security_audit_chain_head" in sql:
            return [{"last_event_hash": "0" * 64}]
        raise AssertionError(sql)

    monkeypatch.setattr(api, "excute_sql", fake_sql)

    assert api.verify_security_audit_chain(allow_bootstrap=True)["state"] == "passed"
    assert api.verify_security_audit_chain()["state"] == "passed"


def test_api_key_revoke_rolls_back_when_security_audit_fails(monkeypatch):
    class Cursor:
        rowcount = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params=None):
            if "UPDATE developer_api_keys" in sql:
                self.rowcount = 1

        def fetchone(self):
            return {"id": 9, "name": "primary", "scopes": "image:fast", "status": "active"}

    class Connection:
        def __init__(self):
            self.rolled_back = False
            self.committed = False

        def begin(self):
            return None

        def cursor(self):
            return Cursor()

        def rollback(self):
            self.rolled_back = True

        def commit(self):
            self.committed = True

        def close(self):
            return None

    connection = Connection()
    monkeypatch.setattr(api, "get_db_connection", lambda: connection)
    monkeypatch.setattr(
        api,
        "_append_security_audit",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("audit unavailable")),
    )

    revoked, message, status = api._revoke_developer_key_atomic(7, 9)

    assert revoked is False
    assert status == 500
    assert "撤销" in message
    assert connection.rolled_back is True
    assert connection.committed is False


def test_api_key_revoke_is_idempotent_after_first_success(monkeypatch):
    calls = []

    class Cursor:
        rowcount = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params=None):
            calls.append(" ".join(sql.split()))

        def fetchone(self):
            return {"id": 9, "name": "primary", "scopes": "image:fast", "status": "revoked"}

    class Connection:
        committed = False
        rolled_back = False

        def begin(self):
            return None

        def cursor(self):
            return Cursor()

        def rollback(self):
            self.rolled_back = True

        def commit(self):
            self.committed = True

        def close(self):
            return None

    connection = Connection()
    monkeypatch.setattr(api, "get_db_connection", lambda: connection)
    monkeypatch.setattr(
        api,
        "_append_security_audit",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("an idempotent replay must not append another audit event")
        ),
    )

    revoked, message, status = api._revoke_developer_key_atomic(7, 9)

    assert (revoked, message, status) == (True, None, 200)
    assert connection.committed is True
    assert connection.rolled_back is False
    assert not any(sql.startswith("UPDATE developer_api_keys") for sql in calls)


def test_model_run_persists_the_audit_integrity_seal(monkeypatch):
    captured = {}
    seal = {"schema": "realguard.persisted-inference-audit.v1", "keyId": "gpu-v1", "hmacSha256": "c" * 64}
    monkeypatch.setattr(detection.model_registry, "get_model", lambda model_id: {"id": model_id})
    monkeypatch.setattr(
        detection.admin_state,
        "append_model_run",
        lambda *args, **kwargs: captured.update(kwargs),
    )

    model_run = {
        "model": "realguard",
        "realProbability": 0.5,
        "fakeProbability": 0.5,
        "persistedAuditIntegrity": seal,
    }
    detection._record_model_run(17, {
        "_route_model_id": "v1-onnx-mil",
        "remote_evidence": {"modelRun": model_run},
    }, {"Userid": 1})

    assert captured["meta"]["inferenceAudit"] == model_run


def test_guest_capacity_groups_rotating_ipv6_addresses_by_64(monkeypatch):
    monkeypatch.setenv("REALGUARD_CONSENT_AUDIT_SALT", "private-test-salt")
    addresses = iter(["2001:db8:1234:5678::1", "2001:db8:1234:5678::abcd", "2001:db8:1234:5679::1"])
    monkeypatch.setattr(login, "_trusted_client_ip", lambda: next(addresses))

    first = detection._guest_capacity_subject()
    same_network = detection._guest_capacity_subject()
    different_network = detection._guest_capacity_subject()

    assert first == same_network
    assert first != different_network


def test_uncalibrated_remote_model_is_forced_to_review_only(monkeypatch):
    from detector_backend import _apply_remote_model_decision_gate
    import detector_backend

    monkeypatch.setattr(detector_backend, "REMOTE_INFERENCE_URL", "http://model/predict")
    payload = {
        "probability": 0.999,
        "detector_probability": 0.999,
        "final_label": "AI生成图像",
        "confidence": "高",
    }
    evidence = {
        "modelDecision": {
            "ready": False,
            "mode": "review_only",
            "rawModelScore": 0.999,
        }
    }

    _apply_remote_model_decision_gate(payload, evidence)

    assert payload["probability"] == pytest.approx(0.999)
    assert payload["detector_probability"] == pytest.approx(0.999)
    assert payload["final_label"] == "AI生成图像"
    assert payload["confidence"] == "低"
    assert "不作为已校准概率发布" in payload["explanation"]


def test_missing_remote_model_decision_contract_fails_closed(monkeypatch):
    import detector_backend

    monkeypatch.setattr(detector_backend, "REMOTE_INFERENCE_URL", "http://model/predict")
    payload = {"probability": 0.94, "detector_probability": 0.94}
    evidence = {"visibleWatermarkPrecheck": {"status": "ok"}}

    detector_backend._apply_remote_model_decision_gate(payload, evidence)

    assert payload["final_label"] == "AI生成图像"
    assert evidence["modelDecision"]["gateReasons"] == ["model_decision_contract_missing"]


def test_remote_model_contract_requires_runtime_identity_and_expiry():
    import detector_backend

    decision = {
        "ready": True,
        "mode": "calibrated_verdict",
        "calibrationId": "heldout-2026-07",
        "datasetSha256": "a" * 64,
        "manifestSha256": "b" * 64,
        "modelSha256": "c" * 64,
        "preprocessingSha256": "d" * 64,
        "runtimeContractSha256": "",
        "evaluationCodeRevision": "eval-commit",
        "expiresAt": "2099-12-31T23:59:59Z",
        "realSamples": 800,
        "fakeSamples": 800,
        "observedFpr": 0.02,
        "observedFnr": 0.05,
        "aiThreshold": 0.7,
        "gateReasons": [],
    }
    payload = {"probability": 0.98, "detector_probability": 0.98, "final_label": "AI生成图像"}
    evidence = {"modelDecision": decision}

    detector_backend._apply_remote_model_decision_gate(payload, evidence)

    assert payload["final_label"] == "AI生成图像"
    assert payload["probability"] == pytest.approx(0.98)
    assert "model_decision_contract_invalid" in evidence["modelDecision"]["gateReasons"]


def test_readiness_includes_detector_cuda_and_shared_queue_state(client, monkeypatch, tmp_path):
    heartbeat = tmp_path / "worker.heartbeat"
    heartbeat.write_text(json.dumps({
        "claimHealthy": True,
        "maintenanceHealthy": True,
        "lastClaimCheckAt": time.time(),
        "activeTasks": 0,
        "capacity": 2,
    }), encoding="ascii")
    monkeypatch.setattr(api, "DEVELOPER_WORKER_HEARTBEAT", heartbeat)
    monkeypatch.setattr(
        api,
        "excute_sql",
        lambda sql, *_args, **_kwargs: (
            [{"queued": 3, "running": 0, "pending": 3}]
            if "AS queued" in sql else [{"ok": 1}]
        ),
    )
    monkeypatch.setattr(api, "excute_detection_sql", lambda *_args, **_kwargs: [{"ok": 1}])

    class DetectorResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "capabilityReady": True,
                "tokenReady": True,
                "activeProvider": "CUDAExecutionProvider",
                "cudaDeviceId": 0,
            }

    class DetectorSession:
        trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def get(*_args, **_kwargs):
            return DetectorResponse()

    monkeypatch.setattr(api.requests, "Session", DetectorSession)

    response = client.get("/api/ready")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["queuePending"] == 3
    assert payload["checks"]["detectorModel"] is True
    assert payload["detector"]["provider"] == "CUDAExecutionProvider"


def test_readiness_stays_ready_when_worker_is_at_capacity(client, monkeypatch, tmp_path):
    heartbeat = tmp_path / "worker.heartbeat"
    heartbeat.write_text(json.dumps({
        "claimHealthy": True,
        "maintenanceHealthy": True,
        "lastClaimCheckAt": time.time() - 600,
        "activeTasks": 2,
        "capacity": 2,
    }), encoding="ascii")
    monkeypatch.setattr(api, "DEVELOPER_WORKER_HEARTBEAT", heartbeat)
    monkeypatch.setattr(
        api,
        "excute_sql",
        lambda sql, *_args, **_kwargs: (
            [{"queued": 4, "running": 2, "pending": 6, "oldest_age_seconds": 90}]
            if "AS queued" in sql else [{"ok": 1}]
        ),
    )
    monkeypatch.setattr(api, "excute_detection_sql", lambda *_args, **_kwargs: [{"ok": 1}])

    class DetectorResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"capabilityReady": True, "tokenReady": True}

    class DetectorSession:
        trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def get(*_args, **_kwargs):
            return DetectorResponse()

    monkeypatch.setattr(api.requests, "Session", DetectorSession)

    response = client.get("/api/ready")

    assert response.status_code == 200
    assert response.get_json()["queueQueued"] == 4
    assert response.get_json()["queueRunning"] == 2


def test_readiness_fails_when_worker_claim_path_is_unhealthy(client, monkeypatch, tmp_path):
    heartbeat = tmp_path / "worker.heartbeat"
    heartbeat.write_text(json.dumps({
        "claimHealthy": False,
        "maintenanceHealthy": True,
        "lastClaimCheckAt": time.time(),
        "lastError": "claim:developer:OperationalError",
    }), encoding="ascii")
    monkeypatch.setattr(api, "DEVELOPER_WORKER_HEARTBEAT", heartbeat)
    monkeypatch.setattr(
        api,
        "excute_sql",
        lambda sql, *_args, **_kwargs: ([{"pending": 1, "oldest_age_seconds": 5}] if "pending" in sql else [{"ok": 1}]),
    )
    monkeypatch.setattr(api, "excute_detection_sql", lambda *_args, **_kwargs: [{"ok": 1}])

    class DetectorResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"capabilityReady": True, "tokenReady": True}

    class DetectorSession:
        trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def get(*_args, **_kwargs):
            return DetectorResponse()

    monkeypatch.setattr(api.requests, "Session", DetectorSession)

    response = client.get("/api/ready")

    assert response.status_code == 503
    assert response.get_json()["checks"]["developerWorker"] is False
    assert response.get_json()["worker"]["lastError"].startswith("claim:")


def test_sms_schema_initialization_is_serialized_across_threads(monkeypatch):
    calls = []
    calls_lock = threading.Lock()

    def fake_sql(sql, params=None, fetch=True):
        with calls_lock:
            calls.append(sql)
        time.sleep(0.01)
        return 0

    monkeypatch.setattr(login, "_SMS_STORAGE_READY", False)
    monkeypatch.setattr(login, "excute_sql", fake_sql)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: login._ensure_sms_storage(), range(8)))

    assert results == [True] * 8
    assert len(calls) == 2


def test_detection_user_sync_uses_atomic_owner_preserving_upsert(monkeypatch):
    calls = []

    def fake_detection_sql(sql, params=None, fetch=True):
        calls.append((" ".join(sql.split()), params, fetch))
        if sql.lstrip().startswith("SELECT"):
            return [{"account_uuid": ACCOUNT_UUID}]
        return 1

    monkeypatch.setattr(login, "excute_detection_sql", fake_detection_sql)

    assert login._sync_detection_user(
        "13800000007",
        "owner",
        "openid-7",
        ACCOUNT_UUID,
    ) is True
    assert calls[0][0].startswith("INSERT INTO user")
    assert "ON DUPLICATE KEY UPDATE" in calls[0][0]
    assert "account_uuid IS NULL OR account_uuid = ''" in calls[0][0]
    assert calls[1][0].startswith("SELECT account_uuid")


def test_database_configuration_has_no_known_default_password():
    source = (ROOT / "imagedetection" / "views" / "utils.py").read_text(encoding="utf-8")

    assert "'123456'" not in source


@pytest.fixture
def client():
    app = creat_app()
    app.config.update(TESTING=True)
    return app.test_client()


class _SmsTestDatabase:
    def __init__(self):
        self.lock = threading.Lock()
        self.challenges = {}
        self.limits = {}
        self.connections = 0

    def connect(self):
        self.connections += 1
        return _SmsTestConnection(self)


class _SmsTestConnection:
    def __init__(self, database):
        self.database = database
        self.active = False
        self.snapshot = None

    def begin(self):
        self.database.lock.acquire()
        self.active = True
        self.snapshot = (deepcopy(self.database.challenges), deepcopy(self.database.limits))

    def cursor(self):
        return _SmsTestCursor(self)

    def commit(self):
        if self.active:
            self.active = False
            self.snapshot = None
            self.database.lock.release()

    def rollback(self):
        if self.active:
            challenges, limits = self.snapshot
            self.database.challenges = challenges
            self.database.limits = limits
            self.active = False
            self.snapshot = None
            self.database.lock.release()

    def close(self):
        if self.active:
            self.rollback()


class _SmsTestCursor:
    def __init__(self, connection):
        self.connection = connection
        self.result = []
        self.rowcount = 0

    @property
    def database(self):
        return self.connection.database

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        params = params or ()
        self.result = []
        self.rowcount = 0
        if normalized.startswith("INSERT INTO sms_send_limits"):
            scope_key, scope_type = params
            if scope_key not in self.database.limits:
                self.database.limits[scope_key] = {
                    "scope_key": scope_key,
                    "scope_type": scope_type,
                    "window_started_at": 0,
                    "request_count": 0,
                    "last_sent_at": 0,
                }
                self.rowcount = 1
            return self.rowcount
        if normalized.startswith("SELECT scope_key, scope_type"):
            keys = set(params)
            self.result = [
                deepcopy(row)
                for key, row in sorted(self.database.limits.items())
                if key in keys
            ]
            self.rowcount = len(self.result)
            return self.rowcount
        if normalized.startswith("UPDATE sms_send_limits"):
            if "request_count = request_count + 1" in normalized:
                window_started_at, last_sent_at, scope_key = params
                row = self.database.limits[scope_key]
                row.update({
                    "window_started_at": window_started_at,
                    "request_count": row["request_count"] + 1,
                    "last_sent_at": last_sent_at,
                })
            else:
                window_started_at, request_count, last_sent_at, scope_key = params
                self.database.limits[scope_key].update({
                    "window_started_at": window_started_at,
                    "request_count": request_count,
                    "last_sent_at": last_sent_at,
                })
            self.rowcount = 1
            return self.rowcount
        if normalized.startswith("INSERT INTO sms_verification_challenges"):
            scene, phone, code_hash, code_salt, expires_at, sent_at = params
            self.database.challenges[(scene, phone)] = {
                "code_hash": code_hash,
                "code_salt": code_salt,
                "expires_at": expires_at,
                "sent_at": sent_at,
                "failed_attempts": 0,
                "consumed_at": None,
            }
            self.rowcount = 1
            return self.rowcount
        if normalized.startswith("SELECT code_hash, code_salt"):
            row = self.database.challenges.get(tuple(params))
            self.result = [deepcopy(row)] if row else []
            self.rowcount = len(self.result)
            return self.rowcount
        if normalized.startswith("UPDATE sms_verification_challenges"):
            if "SET failed_attempts" in normalized:
                attempts, consumed_at, scene, phone = params
                row = self.database.challenges.get((scene, phone))
                if row and row["consumed_at"] is None:
                    row.update({"failed_attempts": attempts, "consumed_at": consumed_at})
                    self.rowcount = 1
                return self.rowcount
            if "failed_attempts < %s" in normalized:
                consumed_at, scene, phone, max_attempts = params
                row = self.database.challenges.get((scene, phone))
                if row and row["consumed_at"] is None and row["failed_attempts"] < max_attempts:
                    row["consumed_at"] = consumed_at
                    self.rowcount = 1
                return self.rowcount
            consumed_at, scene, phone = params
            row = self.database.challenges.get((scene, phone))
            if row and row["consumed_at"] is None:
                row["consumed_at"] = consumed_at
                self.rowcount = 1
            return self.rowcount
        if normalized.startswith("DELETE FROM sms_verification_challenges"):
            self.rowcount = int(self.database.challenges.pop(tuple(params), None) is not None)
            return self.rowcount
        if normalized.startswith("SELECT last_sent_at FROM sms_send_limits"):
            row = self.database.limits.get(params[0])
            self.result = [{"last_sent_at": row["last_sent_at"]}] if row else []
            self.rowcount = len(self.result)
            return self.rowcount
        raise AssertionError(f"unexpected SMS SQL: {normalized}")

    def fetchone(self):
        return deepcopy(self.result[0]) if self.result else None

    def fetchall(self):
        return deepcopy(self.result)


@pytest.fixture
def sms_database(monkeypatch):
    database = _SmsTestDatabase()
    monkeypatch.setattr(login, "_SMS_STORAGE_READY", True)
    monkeypatch.setattr(login, "get_db_connection", database.connect)
    return database


def _login_session(client, phone="13800000000"):
    with client.session_transaction() as sess:
        sess["user_info"] = {
            "Userid": 1,
            "account_uuid": ACCOUNT_UUID,
            "username": "tester",
            "phone": phone,
            "openid": "openid-1",
        }


def test_developer_ip_does_not_trust_forwarded_header_from_public_client():
    app = creat_app()
    with app.test_request_context(
        "/api/openapi/v1/image-detections",
        headers={"X-Forwarded-For": "198.51.100.9", "X-Real-IP": "198.51.100.8"},
        environ_base={"REMOTE_ADDR": "203.0.113.10"},
    ):
        assert api._developer_request_ip() == "203.0.113.10"


def test_developer_ip_uses_nginx_real_ip_from_trusted_loopback():
    app = creat_app()
    with app.test_request_context(
        "/api/openapi/v1/image-detections",
        headers={"X-Forwarded-For": "198.51.100.9, 203.0.113.11", "X-Real-IP": "203.0.113.11"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    ):
        assert api._developer_request_ip() == "203.0.113.11"


def test_browser_pageview_endpoint_records_same_origin_event(client, monkeypatch):
    recorded = {}

    def fake_record(**kwargs):
        recorded.update(kwargs)
        return True

    monkeypatch.setattr(api.traffic_geo, "record_confirmed_pageview", fake_record)

    response = client.post(
        "/api/analytics/pageview",
        json={
            "visitorId": "visitor-00000001",
            "eventId": "event-00000000001",
            "page": "home",
        },
        headers={
            "Sec-Fetch-Site": "same-origin",
            "X-RealGuard-Browser-Event": "1",
            "X-Real-IP": "203.0.113.12",
            "User-Agent": "Mozilla/5.0 Chrome/126.0",
        },
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )

    assert response.status_code == 204
    assert recorded["ip"] == "203.0.113.12"
    assert recorded["page"] == "home"
    assert recorded["visitor_id"] == "visitor-00000001"


def test_browser_pageview_endpoint_rejects_cross_site_and_unmarked_requests(client, monkeypatch):
    monkeypatch.setattr(api.traffic_geo, "record_confirmed_pageview", lambda **_kwargs: True)
    payload = {"visitorId": "visitor-00000001", "eventId": "event-00000000001", "page": "home"}

    cross_site = client.post(
        "/api/analytics/pageview",
        json=payload,
        headers={"Sec-Fetch-Site": "cross-site", "X-RealGuard-Browser-Event": "1"},
    )
    unmarked = client.post(
        "/api/analytics/pageview",
        json=payload,
        headers={"Sec-Fetch-Site": "same-origin"},
    )

    assert cross_site.status_code == 403
    assert unmarked.status_code == 400


def test_authenticate_password_user_upgrades_legacy_secret(monkeypatch):
    monkeypatch.setenv("REALGUARD_ENV", "test")
    monkeypatch.setenv("REALGUARD_ALLOW_LEGACY_PLAINTEXT_PASSWORDS", "1")
    recorded = []
    user = {
        "Userid": 1,
        "phone": "13800000000",
        "secret": "legacy-pass",
        "username": "tester",
        "openid": "openid-1",
    }

    monkeypatch.setattr(login, "_find_user_by_phone", lambda phone: dict(user))

    def fake_execute(sql, params=None, fetch=True):
        recorded.append((sql, params, fetch))
        return 1

    monkeypatch.setattr(login, "excute_sql", fake_execute)

    result = login._authenticate_password_user("13800000000", "legacy-pass")

    assert result["Userid"] == 1
    assert recorded, "expected legacy password upgrade to persist a hash"
    update_sql, update_params, update_fetch = recorded[-1]
    assert "UPDATE user SET secret" in update_sql
    assert update_params[1] == "13800000000"
    assert update_params[0] != "legacy-pass"
    assert login._is_password_hash(update_params[0])
    assert update_fetch is False


def test_production_rejects_plaintext_password_compatibility(monkeypatch):
    monkeypatch.setenv("REALGUARD_ENV", "production")
    monkeypatch.setenv("REALGUARD_ALLOW_LEGACY_PLAINTEXT_PASSWORDS", "1")
    assert not login._password_matches("legacy-pass", "legacy-pass")


def test_developer_api_key_expiry_defaults_and_has_a_hard_cap():
    from datetime import datetime, timedelta

    options, error = api._developer_key_options({"scopes": ["image:fast"]})
    assert error is None
    expires_at = datetime.strptime(options["expires_at"], "%Y-%m-%d %H:%M:%S")
    assert timedelta(days=89) < expires_at - datetime.now() <= timedelta(days=90, minutes=1)

    too_long, too_long_error = api._developer_key_options({
        "scopes": ["image:fast"],
        "expiresAt": (datetime.now() + timedelta(days=366)).isoformat(),
    })
    assert too_long is None
    assert "不能超过" in too_long_error


def test_image_result_api_queries_with_account_uuid(client, monkeypatch):
    calls = []

    def fake_detection_sql(sql, params=None, fetch=True):
        calls.append((sql, params))
        if sql == "SELECT * FROM data WHERE itemid = %s AND (owner_account_uuid = %s) LIMIT 1":
            assert params == ("7", ACCOUNT_UUID)
            return [{
                "itemid": 7,
                "filename": "sample.png",
                "fake": 52.0,
                "clarity": "高",
                "file_size": "12KB",
                "img_format": "png",
                "resolution": "640x480",
                "feedback": None,
            }]
        if sql == "SELECT all_metadata FROM exif WHERE data_itemid = %s LIMIT 1":
            assert params == ("7",)
            return []
        raise AssertionError(f"unexpected SQL: {sql}")

    monkeypatch.setattr(detection, "excute_detection_sql", fake_detection_sql)
    monkeypatch.setattr(detection.admin_state, "list_detection_jobs", lambda limit=500: [])
    _login_session(client)

    response = client.get("/image_upload/result?itemid=7")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["result"]["itemid"] == 7
    assert any(params == ("7", ACCOUNT_UUID) for _, params in calls)


def test_image_result_recovers_generic_watermark_from_runtime_precheck(client, monkeypatch):
    monkeypatch.setattr(
        detection,
        "_load_detection_record",
        lambda table, itemid: {
            "itemid": 678,
            "filename": "doubao.png",
            "fake": 96.5,
            "clarity": "高",
            "file_size": "4.6MB",
            "img_format": "PNG",
            "resolution": "2848x1600",
            "feedback": None,
        },
    )
    monkeypatch.setattr(detection, "_metadata_for_item", lambda itemid: {})
    monkeypatch.setattr(detection, "_backend_static_url", lambda kind, item: "/api/media/image/678")
    monkeypatch.setattr(
        detection.admin_state,
        "list_detection_jobs",
        lambda limit=500: [{
            "id": "job-watermark",
            "result": {
                "result": {
                    "itemid": 678,
                    "visibleWatermark": {"detected": False, "hits": []},
                },
            },
            "experts": [{
                "id": "primary",
                "remoteEvidence": {
                    "visibleWatermarkPrecheck": {
                        "status": "ok",
                        "coordinateSpace": "display_normalized_v1",
                        "displaySize": {"width": 2848, "height": 1600},
                        "elapsedMs": 1608,
                        "genericVisibleWatermark": {
                            "available": True,
                            "detected": True,
                            "count": 1,
                            "model": "corzent/yolo11x_watermark_detection",
                        },
                        "visibleHits": [{
                            "provider": "yolo11x_watermark",
                            "label": "可见水印（平台待确认）",
                            "confidence": 0.8893,
                            "bbox": {"x": 0.8923, "y": 0.939, "w": 0.0984, "h": 0.0401},
                        }],
                    },
                },
            }],
        }],
    )
    _login_session(client)

    response = client.get("/image_upload/result?itemid=678")

    assert response.status_code == 200
    visible = response.get_json()["result"]["visibleWatermark"]
    assert visible["detected"] is True
    assert visible["confidence"] == pytest.approx(0.8893)
    assert visible["hits"][0]["bbox"] == {
        "x": 0.8923,
        "y": 0.939,
        "w": 0.0984,
        "h": 0.0401,
    }
    assert visible["hits"][0]["decisive"] is False


def test_public_swarm_job_recovers_generic_watermark_from_primary_precheck():
    precheck = {
        "status": "ok",
        "coordinateSpace": "display_normalized_v1",
        "displaySize": {"width": 2848, "height": 1600},
        "elapsedMs": 1608,
        "genericVisibleWatermark": {
            "available": True,
            "detected": True,
            "count": 1,
            "model": "corzent/yolo11x_watermark_detection",
        },
        "visibleHits": [{
            "provider": "yolo11x_watermark",
            "label": "可见水印（平台待确认）",
            "confidence": 0.8893,
            "bbox": {"x": 0.8923, "y": 0.939, "w": 0.0984, "h": 0.0401},
        }],
    }
    stale_visible = {
        "enabled": True,
        "supported": True,
        "detected": False,
        "confidence": 0.0,
        "hits": [],
    }
    job = {
        "id": "job-watermark",
        "mode": "swarm",
        "status": "success",
        "progress": 100,
        "experts": [
            {
                "id": "primary",
                "status": "success",
                "remoteEvidence": {"visibleWatermarkPrecheck": precheck},
            },
            {
                "id": "visible_watermark",
                "status": "success",
                "verdict": "未检出 AI 平台水印",
                "watermarkCount": 0,
                "visibleWatermark": stale_visible,
            },
        ],
        "result": {
            "status": "success",
            "result": {
                "itemid": 678,
                "visibleWatermark": stale_visible,
                "swarm": {
                    "experts": [{
                        "id": "visible_watermark",
                        "status": "success",
                        "verdict": "未检出 AI 平台水印",
                        "watermarkCount": 0,
                        "visibleWatermark": stale_visible,
                    }],
                },
            },
        },
    }

    public_job = detection._public_detection_job(job)

    visible = public_job["result"]["result"]["visibleWatermark"]
    assert visible["detected"] is True
    assert visible["confidence"] == pytest.approx(0.8893)
    assert visible["hits"][0]["bbox"] == {
        "x": 0.8923,
        "y": 0.939,
        "w": 0.0984,
        "h": 0.0401,
    }
    visible_expert = next(
        expert for expert in public_job["experts"]
        if expert["publicName"] == "AI 平台水印专家"
    )
    assert visible_expert["publicVerdict"] == "定位 1 处可见水印（平台待确认）"


def test_owner_query_never_uses_loose_identity_or_conditions():
    where, params = detection._detection_owner_where(7, "13800000007", "openid-7")

    assert "Userid" not in where
    assert where == "1 = 0"
    assert params == ()


def test_immutable_owner_query_uses_only_account_uuid():
    account_uuid = "f2de4eb4-a3b1-4fde-b760-3af9d74d7a2d"

    where, params = detection._detection_owner_where(
        7,
        "13800000007",
        "openid-7",
        account_uuid,
    )

    assert where == "owner_account_uuid = %s"
    assert params == (account_uuid,)
    assert "phone" not in where
    assert "openid" not in where


def test_profile_counts_use_immutable_account_uuid(client, monkeypatch):
    calls = []

    def fake_detection_sql(sql, params=None, fetch=True):
        calls.append((sql, params))
        return [{"cnt": 0}]

    monkeypatch.setattr(profile, "excute_detection_sql", fake_detection_sql)
    _login_session(client)

    response = client.get("/profile")

    assert response.status_code == 200
    assert len(calls) == 2
    for sql, params in calls:
        assert "Userid" not in sql
        assert "owner_account_uuid = %s" in sql
        assert params == (ACCOUNT_UUID,)


def test_legacy_login_clears_previous_account_state(client, monkeypatch):
    monkeypatch.setattr(
        login,
        "_authenticate_password_user",
        lambda phone, secret: {"Userid": 9, "username": "next-user", "phone": phone, "openid": "openid-9"},
    )
    monkeypatch.setattr(login, "_record_terms_acceptance", lambda phone: True)
    monkeypatch.setattr(login, "_sync_detection_user", lambda *args, **kwargs: None)
    monkeypatch.setattr(login, "_reserve_password_login_attempt", lambda phone: None)
    monkeypatch.setattr(login, "_clear_password_phone_attempts", lambda phone: None)
    with client.session_transaction() as sess:
        sess["user_info"] = {"Userid": 1, "phone": "13800000001"}
        sess["guest_openid"] = "guest-stale"
        sess["unrelated_account_cache"] = "must-disappear"

    response = client.post(
        "/login_verify",
        data={"phone": "13800000009", "secret": "Password123", "accepted_terms": "1"},
    )

    assert response.status_code == 302
    with client.session_transaction() as sess:
        assert sess["user_info"]["Userid"] == 9
        assert "guest_openid" not in sess
        assert "unrelated_account_cache" not in sess


def test_profile_password_change_rejects_accounts_without_phone(client, monkeypatch):
    def fail_query(*args, **kwargs):
        pytest.fail("an account without a phone must not query the empty-phone user row")

    monkeypatch.setattr(profile, "excute_sql", fail_query)
    with client.session_transaction() as sess:
        sess["user_info"] = {"Userid": 5, "username": "wechat-user", "phone": "", "openid": "openid-5"}

    response = client.post(
        "/profile/change_password",
        json={"old_password": "OldPassword1", "new_password": "NewPassword2"},
    )

    assert response.status_code == 400
    assert "未绑定手机号" in response.get_json()["message"]


def test_runtime_job_owner_conflict_prefers_stable_user_id():
    owner = {"Userid": 22, "phone": "13800000000", "openid": "openid-1"}

    assert detection._runtime_owner_matches(owner, 1, "13800000000", "openid-1", False) is False


def test_runtime_job_rejects_recycled_phone_with_different_account_uuid():
    owner = {
        "Userid": 22,
        "account_uuid": "f2de4eb4-a3b1-4fde-b760-3af9d74d7a2d",
        "phone": "13800000000",
        "openid": "openid-1",
    }

    assert detection._runtime_owner_matches(
        owner,
        22,
        "13800000000",
        "openid-1",
        False,
        "4936858d-7081-4a20-8862-ddb7c43f11f5",
    ) is False


def test_guest_runtime_job_cannot_access_a_bound_account_job():
    owner = {"Userid": 22, "phone": "", "openid": "guest-shared"}

    assert detection._runtime_owner_matches(owner, None, "", "guest-shared", True) is False


def test_image_result_hides_foreign_record(client, monkeypatch):
    calls = []

    def fake_detection_sql(sql, params=None, fetch=True):
        calls.append((sql, params))
        return []

    monkeypatch.setattr(detection, "excute_detection_sql", fake_detection_sql)
    _login_session(client)

    response = client.get("/image_upload/result?itemid=88")

    assert response.status_code == 404
    assert "Userid" not in calls[0][0]
    assert "owner_account_uuid = %s" in calls[0][0]
    assert calls[0][1] == ("88", ACCOUNT_UUID)


def test_detection_owner_repair_uses_detection_database_identities(monkeypatch):
    class FakeCursor:
        def __init__(self):
            self.rowcount = 0
            self.queries = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql):
            self.queries.append(" ".join(sql.split()))
            self.rowcount = (2, 3, 1, 0)[len(self.queries) - 1]

    class FakeConnection:
        def __init__(self):
            self.cursor_instance = FakeCursor()
            self.committed = False
            self.closed = False

        def cursor(self):
            return self.cursor_instance

        def commit(self):
            self.committed = True

        def rollback(self):
            raise AssertionError("repair should not roll back")

        def close(self):
            self.closed = True

    connection = FakeConnection()
    monkeypatch.setattr(utils, "get_detection_db_connection", lambda: connection)

    changed = utils.repair_detection_history_owners()

    assert changed == {"data": 5, "video_data": 1}
    assert connection.committed is True
    assert connection.closed is True
    assert len(connection.cursor_instance.queries) == 4
    assert all("JOIN `user` owners" in sql for sql in connection.cursor_instance.queries)
    assert all("ON BINARY records." in sql for sql in connection.cursor_instance.queries)


def test_full_media_endpoint_checks_owner_before_backend_fetch(client, monkeypatch):
    monkeypatch.setattr(api, "excute_detection_sql", lambda *args, **kwargs: [])

    def fail_fetch(*args, **kwargs):
        pytest.fail("foreign media must not be fetched from the detector backend")

    monkeypatch.setattr(api.requests.Session, "get", fail_fetch)
    _login_session(client)

    response = client.get("/api/media/image/99")

    assert response.status_code == 404
    assert response.get_json()["message"] == "媒体不存在"
    assert response.headers["Cache-Control"].startswith("private, no-store")


def test_public_video_url_validation_rejects_private_networks(monkeypatch):
    monkeypatch.setattr(
        detection.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (detection.socket.AF_INET, detection.socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80)),
        ],
    )

    assert detection._validate_public_video_url("http://example.com/video.mp4") is False
    assert detection._validate_public_video_url("http://user:pass@example.com/video.mp4") is False
    assert detection._validate_public_video_url("http://example.com:8080/video.mp4") is False


def test_remote_video_urls_are_disabled_by_default(client, monkeypatch):
    monkeypatch.setattr(detection, "ALLOW_REMOTE_VIDEO_URLS", False)

    def fail_backend(*args, **kwargs):
        pytest.fail("disabled remote URLs must never reach the detector backend")

    monkeypatch.setattr(detection, "_backend_post", fail_backend)
    _login_session(client)

    response = client.post("/video_upload/detect", data={"video_url": "https://example.com/video.mp4"})

    assert response.status_code == 400
    assert "已禁用" in response.get_json()["message"]


def test_login_password_requires_terms_acceptance(client, monkeypatch):
    def fail_auth(phone, secret):
        pytest.fail("password authentication should not run before terms acceptance")

    monkeypatch.setattr(api, "_authenticate_password_user", fail_auth)

    response = client.post(
        "/api/login/password",
        json={"phone": "13800000000", "secret": "Password123", "accepted_terms": False},
    )

    assert response.status_code == 400
    assert "用户协议" in response.get_json()["message"]


def test_login_password_returns_retry_after_when_rate_limited(client, monkeypatch):
    monkeypatch.setattr(
        api,
        "_reserve_password_login_attempt",
        lambda phone: (_ for _ in ()).throw(login.PasswordLoginRateLimitError(37)),
    )
    monkeypatch.setattr(
        api,
        "_authenticate_password_user",
        lambda *args: pytest.fail("rate-limited request reached password verification"),
    )

    response = client.post(
        "/api/login/password",
        json={"phone": "13800000000", "secret": "Password123", "accepted_terms": True},
    )

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "37"
    assert response.get_json()["code"] == "login_rate_limited"


def test_login_sms_requires_terms_acceptance(client, monkeypatch):
    def fail_verify(scene, phone, code):
        pytest.fail("SMS verification should not run before terms acceptance")

    monkeypatch.setattr(api, "_verify_sms_code", fail_verify)

    response = client.post(
        "/api/login/sms",
        json={"phone": "13800000000", "sms_code": "123456", "accepted_terms": False},
    )

    assert response.status_code == 400
    assert "用户协议" in response.get_json()["message"]


def test_login_sms_unknown_user_requires_password_setup_before_authentication(client, monkeypatch):
    monkeypatch.setattr(api, "_verify_sms_code", lambda scene, phone, code: (True, ""))
    monkeypatch.setattr(api, "_find_user_by_phone", lambda phone: None)

    response = client.post(
        "/api/login/sms",
        json={"phone": "13800000000", "sms_code": "123456", "accepted_terms": True},
    )

    assert response.status_code == 200
    assert response.get_json()["requiresPasswordSetup"] is True
    with client.session_transaction() as session:
        assert "user_info" not in session
        assert session[login.SMS_PASSWORD_SETUP_SESSION_KEY]["phone"] == "13800000000"


def test_login_sms_existing_password_user_logs_in_without_setup(client, monkeypatch):
    user = {
        "Userid": 7,
        "phone": "13800000000",
        "secret": login._hash_password("Password123"),
        "username": "tester",
    }
    monkeypatch.setattr(api, "_verify_sms_code", lambda scene, phone, code: (True, ""))
    monkeypatch.setattr(api, "_find_user_by_phone", lambda phone: user)
    monkeypatch.setattr(api, "_record_terms_acceptance", lambda phone: True)
    monkeypatch.setattr(api, "_set_session_user", lambda current, phone: {"Userid": current["Userid"]})

    response = client.post(
        "/api/login/sms",
        json={"phone": "13800000000", "sms_code": "123456", "accepted_terms": True},
    )

    assert response.status_code == 200
    assert response.get_json()["user"]["Userid"] == 7
    assert "requiresPasswordSetup" not in response.get_json()


def test_first_sms_password_setup_rejects_mismatched_confirmation(client, monkeypatch):
    monkeypatch.setattr(api, "_verify_sms_code", lambda scene, phone, code: (True, ""))
    monkeypatch.setattr(api, "_find_user_by_phone", lambda phone: None)
    started = client.post(
        "/api/login/sms",
        json={"phone": "13800000000", "sms_code": "123456", "accepted_terms": True},
    )

    completed = client.post(
        "/api/login/sms/complete",
        json={"secret": "Password123", "secret_confirm": "Password456"},
    )

    assert started.status_code == 200
    assert completed.status_code == 400
    assert "不一致" in completed.get_json()["message"]
    with client.session_transaction() as session:
        assert "user_info" not in session
        assert login.SMS_PASSWORD_SETUP_SESSION_KEY in session


def test_first_sms_password_setup_rejects_expired_verification(client):
    now = int(time.time())
    with client.session_transaction() as session:
        session[login.SMS_PASSWORD_SETUP_SESSION_KEY] = {
            "phone": "13800000000",
            "user_id": None,
            "verified_at": now - login.SMS_PASSWORD_SETUP_TTL - 1,
            "expires_at": now - 1,
            "terms_version": login.TERMS_VERSION,
        }

    response = client.post(
        "/api/login/sms/complete",
        json={"secret": "Password123", "secret_confirm": "Password123"},
    )

    assert response.status_code == 400
    assert "过期" in response.get_json()["message"]
    with client.session_transaction() as session:
        assert login.SMS_PASSWORD_SETUP_SESSION_KEY not in session


def test_first_sms_password_setup_creates_account_and_logs_in(client, monkeypatch):
    monkeypatch.setattr(api, "_verify_sms_code", lambda scene, phone, code: (True, ""))
    monkeypatch.setattr(api, "_find_user_by_phone", lambda phone: None)
    monkeypatch.setattr(login, "_ensure_user_account_columns", lambda: True)
    monkeypatch.setattr(login, "_record_terms_acceptance", lambda phone, channel="": True)
    monkeypatch.setattr(api, "_sync_detection_user", lambda *args, **kwargs: None)
    state = {"created": False, "secret": ""}
    created_user = {
        "Userid": 51,
        "account_uuid": "11111111-1111-4111-8111-111111111111",
        "phone": "13800000000",
        "username": "13800000000",
        "openid": "",
        "session_version": 1,
    }

    def find_user(_phone):
        return {**created_user, "secret": state["secret"]} if state["created"] else None

    def execute(sql, params=None, fetch=True):
        normalized = " ".join(sql.split())
        if normalized.startswith("INSERT INTO user"):
            state["created"] = True
            state["secret"] = params[1]
            return 1
        raise AssertionError(f"unexpected SQL: {sql}")

    monkeypatch.setattr(login, "_find_user_by_phone", find_user)
    monkeypatch.setattr(login, "excute_sql", execute)

    started = client.post(
        "/api/login/sms",
        json={"phone": "13800000000", "sms_code": "123456", "accepted_terms": True},
    )
    completed = client.post(
        "/api/login/sms/complete",
        json={"secret": "Password123", "secret_confirm": "Password123"},
    )

    assert started.get_json()["requiresPasswordSetup"] is True
    assert completed.status_code == 200
    assert login._is_password_hash(state["secret"])
    with client.session_transaction() as session:
        assert session["user_info"]["Userid"] == 51
        assert login.SMS_PASSWORD_SETUP_SESSION_KEY not in session


def test_register_requires_matching_password_confirmation(client, monkeypatch):
    monkeypatch.setattr(
        api,
        "_verify_sms_code",
        lambda *args: pytest.fail("mismatched passwords must fail before SMS consumption"),
    )

    response = client.post(
        "/api/register",
        json={
            "phone": "13800000000",
            "secret": "Password123",
            "secret_confirm": "Password456",
            "username": "tester",
            "sms_code": "123456",
            "accepted_terms": True,
            "terms_version": api.TERMS_VERSION,
        },
    )

    assert response.status_code == 400
    assert "不一致" in response.get_json()["message"]


def test_register_requires_terms_acceptance(client, monkeypatch):
    monkeypatch.setattr(api, "_verify_sms_code", lambda scene, phone, code: (True, ""))

    response = client.post(
        "/api/register",
        json={
            "phone": "13800000000",
            "secret": "Password123",
            "secret_confirm": "Password123",
            "username": "tester",
            "sms_code": "123456",
            "accepted_terms": False,
        },
    )

    assert response.status_code == 400
    assert "用户协议" in response.get_json()["message"]


def test_register_rejects_stale_legal_document_version(client, monkeypatch):
    monkeypatch.setattr(api, "_verify_sms_code", lambda scene, phone, code: (True, ""))

    response = client.post(
        "/api/register",
        json={
            "phone": "13800000000",
            "secret": "Password123",
            "secret_confirm": "Password123",
            "username": "tester",
            "sms_code": "123456",
            "accepted_terms": True,
            "terms_version": "2026-07-15",
        },
    )

    assert response.status_code == 428
    assert response.get_json()["code"] == "legal_documents_changed"


def test_send_login_code_supports_first_use_without_enumerating_phone(client, monkeypatch):
    sent = []
    monkeypatch.setattr(login, "excute_sql", lambda sql, params=None, fetch=True: [])
    monkeypatch.setattr(login, "_reserve_sms_send", lambda scene, phone, client_ip: None)
    monkeypatch.setattr(login, "_send_sms_code", lambda phone, scene: sent.append((phone, scene)))

    response = client.post("/sms/send_code", json={"phone": "13800000000", "scene": "login"})

    assert response.status_code == 200
    assert response.get_json()["success"] is True
    assert "符合当前操作条件" in response.get_json()["message"]
    assert sent == [("13800000000", "login")]


def test_sms_code_locks_after_five_wrong_attempts(sms_database):
    login._save_sms_code("login", "13800000000", "246810", now=1000)

    for attempt in range(1, login.SMS_MAX_ATTEMPTS + 1):
        ok, message = login._verify_sms_code("login", "13800000000", "000000", now=1001 + attempt)
        assert ok is False
        if attempt == login.SMS_MAX_ATTEMPTS:
            assert "错误次数过多" in message

    ok, message = login._verify_sms_code("login", "13800000000", "246810", now=1010)

    assert ok is False
    assert "无效或已过期" in message
    challenge = sms_database.challenges[("login", "13800000000")]
    assert challenge["failed_attempts"] == login.SMS_MAX_ATTEMPTS
    assert challenge["consumed_at"] is not None


def test_sms_code_success_is_atomically_consumed_once(sms_database):
    login._save_sms_code("reset", "13800000001", "135790", now=2000)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(
            lambda _index: login._verify_sms_code(
                "reset", "13800000001", "135790", now=2001
            ),
            range(8),
        ))

    assert sum(1 for ok, _message in results if ok) == 1
    assert sms_database.connections == 9


def test_sms_send_phone_limit_survives_cookie_change(sms_database, monkeypatch):
    app = creat_app()
    app.config.update(TESTING=True)
    first_client = app.test_client()
    second_client = app.test_client()
    monkeypatch.setenv("SMS_PROVIDER", "mock")
    monkeypatch.setenv("SMS_DEBUG_RETURN_CODE", "1")
    monkeypatch.setattr(login, "SMS_IP_MIN_INTERVAL", 0)
    monkeypatch.setattr(
        login,
        "excute_sql",
        lambda sql, params=None, fetch=True: [{"Userid": 1}]
        if "SELECT Userid FROM user" in sql else 1,
    )

    first = first_client.post(
        "/sms/send_code",
        json={"phone": "13800000002", "scene": "login"},
        environ_base={"REMOTE_ADDR": "203.0.113.10"},
    )
    second = second_client.post(
        "/sms/send_code",
        json={"phone": "13800000002", "scene": "login"},
        environ_base={"REMOTE_ADDR": "203.0.113.11"},
    )

    assert first.status_code == 200
    assert first.get_json().get("debug_code")
    assert second.status_code == 429
    assert int(second.headers["Retry-After"]) > 0


def test_sms_send_ip_limit_applies_across_phone_numbers(sms_database, monkeypatch):
    monkeypatch.setattr(login, "SMS_IP_WINDOW_LIMIT", 1)
    monkeypatch.setattr(login, "SMS_IP_MIN_INTERVAL", 0)

    login._reserve_sms_send("login", "13800000003", "203.0.113.20", now=3000)
    with pytest.raises(login.SmsRateLimitError):
        login._reserve_sms_send("login", "13800000004", "203.0.113.20", now=3001)


def test_password_login_limit_is_shared_across_connections(sms_database, monkeypatch):
    monkeypatch.setattr(login, "PASSWORD_LOGIN_PHONE_LIMIT", 2)
    monkeypatch.setattr(login, "PASSWORD_LOGIN_IP_LIMIT", 20)

    login._reserve_password_login_attempt("13800000003", "203.0.113.20", now=3000)
    login._reserve_password_login_attempt("13800000003", "203.0.113.21", now=3001)
    with pytest.raises(login.PasswordLoginRateLimitError) as error:
        login._reserve_password_login_attempt("13800000003", "203.0.113.22", now=3002)

    assert error.value.retry_after == login.PASSWORD_LOGIN_WINDOW - 2


def test_sms_send_rate_reservation_is_atomic_across_connections(sms_database, monkeypatch):
    monkeypatch.setattr(login, "SMS_IP_MIN_INTERVAL", 0)

    def reserve(_index):
        try:
            login._reserve_sms_send("login", "13800000006", "203.0.113.21", now=4000)
            return True
        except login.SmsRateLimitError:
            return False

    with ThreadPoolExecutor(max_workers=8) as executor:
        admitted = list(executor.map(reserve, range(8)))

    assert admitted.count(True) == 1
    assert sms_database.connections == 8


def test_sms_verification_fails_closed_when_database_is_unavailable(monkeypatch):
    monkeypatch.setattr(login, "_SMS_STORAGE_READY", True)
    monkeypatch.setattr(
        login,
        "get_db_connection",
        lambda: (_ for _ in ()).throw(RuntimeError("database offline")),
    )

    ok, message = login._verify_sms_code("login", "13800000005", "123456")

    assert ok is False
    assert "暂不可用" in message


def test_sms_send_fails_closed_before_provider_when_database_is_unavailable(client, monkeypatch):
    monkeypatch.setattr(login, "_SMS_STORAGE_READY", True)
    monkeypatch.setattr(login, "excute_sql", lambda sql, params=None, fetch=True: [{"Userid": 1}])
    monkeypatch.setattr(
        login,
        "get_db_connection",
        lambda: (_ for _ in ()).throw(RuntimeError("database offline")),
    )

    def fail_send(*_args, **_kwargs):
        pytest.fail("the SMS provider must not run without a durable rate-limit reservation")

    monkeypatch.setattr(login, "_send_sms_code", fail_send)

    response = client.post(
        "/sms/send_code",
        json={"phone": "13800000007", "scene": "login"},
    )

    assert response.status_code == 503
    assert "暂不可用" in response.get_json()["message"]


def test_sms_send_uses_same_public_message_for_existing_and_unknown_accounts(client, monkeypatch):
    monkeypatch.setattr(login, "_reserve_sms_send", lambda scene, phone, client_ip: None)
    monkeypatch.setattr(login, "_send_sms_code", lambda phone, scene: None)

    def fake_execute(sql, params=None, fetch=True):
        assert "SELECT Userid FROM user" in sql
        return [{"Userid": 1}] if params[0] == "13800000008" else []

    monkeypatch.setattr(login, "excute_sql", fake_execute)

    existing = client.post(
        "/sms/send_code",
        json={"phone": "13800000008", "scene": "login"},
    )
    unknown = client.post(
        "/sms/send_code",
        json={"phone": "13800000009", "scene": "login"},
    )

    assert existing.status_code == unknown.status_code == 200
    assert existing.get_json()["message"] == unknown.get_json()["message"]


def test_sms_client_ip_only_trusts_configured_proxy():
    app = creat_app()
    with app.test_request_context(
        "/sms/send_code",
        headers={"X-Real-IP": "198.51.100.8"},
        environ_base={"REMOTE_ADDR": "203.0.113.30"},
    ):
        assert login._trusted_client_ip() == "203.0.113.30"
    with app.test_request_context(
        "/sms/send_code",
        headers={"X-Real-IP": "198.51.100.8"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    ):
        assert login._trusted_client_ip() == "198.51.100.8"


def test_legal_pages_are_public(client):
    terms = client.get("/legal/terms.html")
    privacy = client.get("/legal/privacy.html")
    blocked = client.get("/legal/../run.py")

    assert terms.status_code == 200
    assert "用户协议" in terms.get_data(as_text=True)
    assert privacy.status_code == 200
    assert "隐私政策" in privacy.get_data(as_text=True)
    assert blocked.status_code == 404


def test_register_persists_terms_metadata(client, monkeypatch):
    recorded_insert = {}

    monkeypatch.setattr(api, "_verify_sms_code", lambda scene, phone, code: (True, ""))
    monkeypatch.setattr(api, "_ensure_user_account_columns", lambda: True)
    monkeypatch.setattr(api, "_sync_detection_user", lambda *args, **kwargs: None)
    monkeypatch.setattr(api, "_record_terms_acceptance", lambda phone: True)
    monkeypatch.setattr(api, "TERMS_VERSION", "test-terms-v1")

    def fake_execute(sql, params=None, fetch=True):
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT Userid FROM user WHERE phone"):
            return []
        if normalized.startswith("INSERT INTO user"):
            recorded_insert["sql"] = normalized
            recorded_insert["params"] = params
            return 1
        raise AssertionError(f"unexpected SQL: {sql}")

    monkeypatch.setattr(api, "excute_sql", fake_execute)

    response = client.post(
        "/api/register",
        json={
            "phone": "13800000000",
            "secret": "Password123",
            "secret_confirm": "Password123",
            "username": "tester",
            "sms_code": "123456",
            "accepted_terms": True,
            "terms_version": "test-terms-v1",
        },
    )

    assert response.status_code == 200
    assert "terms_version" in recorded_insert["sql"]
    assert recorded_insert["params"][-1] == "test-terms-v1"
    assert login._is_password_hash(recorded_insert["params"][1])


def test_reset_password_updates_hashed_secret(client, monkeypatch):
    updated = {}

    monkeypatch.setattr(api, "_verify_sms_code", lambda scene, phone, code: (scene == "reset", ""))
    monkeypatch.setattr(api, "_ensure_user_account_columns", lambda: True)
    monkeypatch.setattr(api, "_find_user_by_phone", lambda phone: {"Userid": 9, "phone": phone, "username": "tester", "openid": ""})

    def fake_execute(sql, params=None, fetch=True):
        normalized = " ".join(sql.split())
        if normalized.startswith("UPDATE user SET secret"):
            updated["sql"] = normalized
            updated["params"] = params
            return 1
        raise AssertionError(f"unexpected SQL: {sql}")

    monkeypatch.setattr(api, "excute_sql", fake_execute)

    response = client.post(
        "/api/password/reset",
        json={"phone": "13800000000", "secret": "NewPassword123", "sms_code": "123456"},
    )

    assert response.status_code == 200
    assert updated["params"][1] == "13800000000"
    assert login._is_password_hash(updated["params"][0])
    assert "session_version = session_version + 1" in updated["sql"]


def test_versioned_user_session_is_revoked_when_account_version_changes(client, monkeypatch):
    monkeypatch.setattr(login, "_ensure_user_account_columns", lambda: True)
    monkeypatch.setattr(
        login,
        "excute_sql",
        lambda *args, **kwargs: [{
            "Userid": 7,
            "account_uuid": "f2de4eb4-a3b1-4fde-b760-3af9d74d7a2d",
            "phone": "13800000007",
            "openid": "openid-7",
            "session_version": 3,
        }],
    )
    with client.session_transaction() as sess:
        sess["user_info"] = {
            "Userid": 7,
            "account_uuid": "f2de4eb4-a3b1-4fde-b760-3af9d74d7a2d",
            "username": "tester",
            "phone": "13800000007",
            "openid": "openid-7",
            "session_version": 2,
        }

    response = client.get("/api/me")

    assert response.status_code == 200
    assert response.get_json()["authenticated"] is False
    with client.session_transaction() as sess:
        assert "user_info" not in sess


def test_current_versioned_user_session_remains_valid(client, monkeypatch):
    monkeypatch.setattr(login, "_ensure_user_account_columns", lambda: True)
    monkeypatch.setattr(
        login,
        "excute_sql",
        lambda *args, **kwargs: [{
            "Userid": 7,
            "account_uuid": "f2de4eb4-a3b1-4fde-b760-3af9d74d7a2d",
            "phone": "13800000007",
            "openid": "openid-7",
            "session_version": 3,
        }],
    )
    monkeypatch.setattr(api, "excute_detection_sql", lambda *args, **kwargs: [{"cnt": 0}])
    with client.session_transaction() as sess:
        sess["user_info"] = {
            "Userid": 7,
            "account_uuid": "f2de4eb4-a3b1-4fde-b760-3af9d74d7a2d",
            "username": "tester",
            "phone": "13800000007",
            "openid": "openid-7",
            "session_version": 3,
        }

    response = client.get("/api/me")

    assert response.status_code == 200
    assert response.get_json()["authenticated"] is True


def test_user_session_has_absolute_expiry_even_with_sliding_cookie(client):
    with client.session_transaction() as sess:
        sess["user_info"] = {
            "Userid": 7,
            "account_uuid": ACCOUNT_UUID,
            "username": "tester",
            "phone": "13800000007",
            "openid": "openid-7",
            "session_version": 3,
            "auth_issued_at": int(time.time()) - login.SESSION_ABSOLUTE_MAX_AGE - 1,
        }

    response = client.get("/api/me")

    assert response.status_code == 200
    assert response.get_json()["authenticated"] is False
    with client.session_transaction() as sess:
        assert "user_info" not in sess


def test_legacy_logout_rejects_get_and_accepts_post(client, monkeypatch):
    monkeypatch.setattr(login, "validate_current_user_session", lambda **kwargs: True)
    monkeypatch.setattr(login, "excute_sql", lambda *args, **kwargs: 1)
    with client.session_transaction() as sess:
        sess["user_info"] = {
            "Userid": 7,
            "account_uuid": ACCOUNT_UUID,
            "session_version": 3,
        }

    denied = client.get("/logout")
    response = client.post("/logout")

    assert denied.status_code == 405
    assert response.status_code == 302
    with client.session_transaction() as sess:
        assert "user_info" not in sess


def test_app_rejects_explicit_template_session_secret(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "change-me")

    with pytest.raises(RuntimeError, match="session signing key"):
        creat_app()


def test_logout_fails_closed_when_session_revocation_cannot_commit(client, monkeypatch):
    monkeypatch.setattr(login, "validate_current_user_session", lambda **kwargs: True)
    monkeypatch.setattr(login, "excute_sql", lambda *args, **kwargs: None)
    with client.session_transaction() as sess:
        sess["user_info"] = {
            "Userid": 7,
            "account_uuid": ACCOUNT_UUID,
            "session_version": 3,
        }

    response = client.post("/logout")

    assert response.status_code == 503
    with client.session_transaction() as sess:
        assert "user_info" in sess


def test_valid_versioned_session_is_upgraded_with_immutable_uuid(client, monkeypatch):
    account_uuid = "f2de4eb4-a3b1-4fde-b760-3af9d74d7a2d"
    monkeypatch.setattr(login, "_ensure_user_account_columns", lambda: True)
    monkeypatch.setattr(
        login,
        "excute_sql",
        lambda *args, **kwargs: [{
            "Userid": 7,
            "account_uuid": account_uuid,
            "phone": "13800000007",
            "openid": "openid-7",
            "session_version": 3,
        }],
    )
    monkeypatch.setattr(api, "excute_detection_sql", lambda *args, **kwargs: [{"cnt": 0}])
    with client.session_transaction() as sess:
        sess["user_info"] = {
            "Userid": 7,
            "username": "tester",
            "phone": "13800000007",
            "openid": "openid-7",
            "session_version": 3,
        }

    response = client.get("/api/me")

    assert response.status_code == 200
    assert response.get_json()["authenticated"] is True
    with client.session_transaction() as sess:
        assert sess["user_info"]["account_uuid"] == account_uuid


def test_claim_detection_owner_never_overwrites_another_uuid(monkeypatch):
    calls = []

    def fake_execute(sql, params=None, fetch=True):
        calls.append((" ".join(sql.split()), params, fetch))
        if sql.lstrip().upper().startswith("UPDATE"):
            return 0
        return []

    monkeypatch.setattr(utils, "excute_detection_sql", fake_execute)
    account_uuid = "f2de4eb4-a3b1-4fde-b760-3af9d74d7a2d"

    claimed = utils.claim_detection_record_owner(
        "data",
        81,
        account_uuid,
        phone="13800000007",
        openid="openid-7",
    )

    assert claimed is False
    assert calls == [(
        "SELECT itemid FROM `data` WHERE itemid = %s AND owner_account_uuid = %s LIMIT 1",
        (81, account_uuid),
        True,
    )]


def test_claim_detection_owner_accepts_already_bound_same_uuid(monkeypatch):
    account_uuid = "f2de4eb4-a3b1-4fde-b760-3af9d74d7a2d"

    def fake_execute(sql, params=None, fetch=True):
        if sql.lstrip().upper().startswith("UPDATE"):
            return 0
        return [{"itemid": 81}]

    monkeypatch.setattr(utils, "excute_detection_sql", fake_execute)

    assert utils.claim_detection_record_owner(
        "data", 81, account_uuid, phone="13800000007"
    ) is True


def test_profile_password_change_revokes_all_sessions_and_clears_current(client, monkeypatch):
    statements = []

    def fake_execute(sql, params=None, fetch=True):
        normalized = " ".join(sql.split())
        statements.append(normalized)
        if normalized.startswith("SELECT secret FROM user"):
            return [{"secret": login._hash_password("OldPassword1")}]
        if normalized.startswith("UPDATE user SET secret"):
            return 1
        raise AssertionError(f"unexpected SQL: {normalized}")

    monkeypatch.setattr(profile, "excute_sql", fake_execute)
    _login_session(client)

    response = client.post(
        "/profile/change_password",
        json={"old_password": "OldPassword1", "new_password": "NewPassword2"},
    )

    assert response.status_code == 200
    assert any("session_version = session_version + 1" in sql for sql in statements)
    with client.session_transaction() as sess:
        assert "user_info" not in sess


def test_owned_image_history_delete_removes_database_media_and_thumbnail(tmp_path, monkeypatch):
    original = tmp_path / "uploads" / "13800000007" / "image" / "sample.png"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"image")
    thumbnail = tmp_path / "thumbnail.webp"
    thumbnail.write_bytes(b"thumb")
    statements = []
    erasure_events = []

    class Cursor:
        rowcount = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params=None):
            normalized = " ".join(sql.split())
            statements.append((normalized, params))
            if normalized.startswith("SELECT * FROM data"):
                self._row = {
                    "itemid": 42,
                    "filename": "sample.png",
                    "phone": "13800000007",
                    "openid": "",
                    "createtime": "2026-07-19 10:00:00",
                }
                self.rowcount = 1
            elif normalized.startswith("DELETE FROM exif"):
                self.rowcount = 1
            elif normalized.startswith("DELETE FROM data"):
                self.rowcount = 1
            else:
                raise AssertionError(f"unexpected SQL: {normalized}")

        def fetchone(self):
            return self._row

    class Connection:
        def begin(self):
            return None

        def cursor(self):
            return Cursor()

        def commit(self):
            return None

        def rollback(self):
            return None

        def close(self):
            return None

    monkeypatch.setattr(api, "get_detection_db_connection", Connection)
    monkeypatch.setattr(api, "_local_detection_media_path", lambda kind, item: (tmp_path.resolve(), original.resolve()))
    monkeypatch.setattr(api, "_thumbnail_cache_path", lambda item: thumbnail)
    monkeypatch.setattr(api, "_ensure_privacy_erasure_table", lambda: True)
    monkeypatch.setattr(api, "_create_privacy_erasure_job", lambda *args: "erase-job-42")
    monkeypatch.setattr(
        api.privacy_erasure_ledger,
        "record_tombstone",
        lambda *_args, **_kwargs: erasure_events.append("tombstone")
        or {"tombstoneId": "erase-test"},
    )

    def persist_staging_plan(*_args, **kwargs):
        assert erasure_events == ["tombstone"]
        erasure_events.append("paths-persisted")
        assert original.is_file()
        assert thumbnail.is_file()
        assert kwargs["staged_path"] is not None
        assert kwargs["thumbnail_staged_path"] is not None
        return True

    monkeypatch.setattr(api, "_stage_privacy_erasure_job", persist_staging_plan)
    monkeypatch.setattr(api, "_set_privacy_erasure_state", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        api,
        "_privacy_erasure_job",
        lambda job_id: {"job_id": job_id, "resource_kind": "image", "resource_id": 42},
    )
    monkeypatch.setattr(
        api,
        "_finalize_privacy_erasure_job",
        lambda job: original.unlink(missing_ok=True) or thumbnail.unlink(missing_ok=True) or True,
    )
    monkeypatch.setattr(api, "_restore_privacy_erasure_job", lambda job: True)
    staged_manifest = (tmp_path / "manifest.json", tmp_path / ".manifest.deleting")
    monkeypatch.setattr(
        api.evidence_manifest,
        "plan_signed_image_manifest_deletion",
        lambda itemid: staged_manifest,
    )
    monkeypatch.setattr(
        api.evidence_manifest,
        "stage_signed_image_manifest_deletion",
        lambda itemid, **kwargs: kwargs.get("planned_deletion") or staged_manifest,
    )
    monkeypatch.setattr(
        api.evidence_manifest,
        "finalize_staged_image_manifest_deletion",
        lambda staged: None,
    )
    monkeypatch.setattr(
        api.evidence_manifest,
        "restore_staged_image_manifest_deletion",
        lambda staged: None,
    )

    deleted, message, status = api._delete_owned_history_record(
        "image",
        42,
        {"mode": "user", "account_uuid": ACCOUNT_UUID, "phone": "13800000007", "openid": ""},
    )

    assert (deleted, message, status) == (True, "", 204)
    assert not original.exists()
    assert not thumbnail.exists()
    assert any(sql.startswith("DELETE FROM exif") for sql, _ in statements)
    delete_sql, delete_params = next((sql, params) for sql, params in statements if sql.startswith("DELETE FROM data"))
    assert "owner_account_uuid = %s" in delete_sql
    assert delete_params == (42, ACCOUNT_UUID)


def test_erasure_cleanup_failure_stays_pending_for_retry(tmp_path, monkeypatch):
    staged = tmp_path / ".sample.png.deleting-abc123"
    staged.write_bytes(b"sensitive")
    states = []
    original_unlink = Path.unlink

    def failing_unlink(path, *args, **kwargs):
        if path == staged:
            raise OSError("simulated disk failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(api, "_privacy_erasure_allowed_roots", lambda: (tmp_path.resolve(),))
    monkeypatch.setattr(api, "_scrub_history_replicas", lambda *args, **kwargs: True)
    monkeypatch.setattr(api, "_set_privacy_erasure_state", lambda *args, **kwargs: states.append((args, kwargs)) or True)
    monkeypatch.setattr(Path, "unlink", failing_unlink)

    completed = api._finalize_privacy_erasure_job({
        "job_id": "erase-pending-1",
        "resource_kind": "image",
        "resource_id": 42,
        "staged_path": str(staged),
        "thumbnail_staged_path": None,
        "manifest_staged_path": None,
    })

    assert completed is False
    assert staged.exists()
    assert states[-1][0][1] == "cleanup_failed"
    assert "OSError" in states[-1][1]["error"]


def test_replica_scrub_unlinks_spools_before_nulling_database_paths(tmp_path, monkeypatch):
    developer_root = tmp_path / "developer-spool"
    web_root = tmp_path / "web-spool"
    developer_root.mkdir()
    web_root.mkdir()
    developer_spool = developer_root / "task-1.upload"
    web_spool = web_root / "job-1.upload"
    developer_spool.write_bytes(b"developer-private")
    web_spool.write_bytes(b"web-private")
    executed = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params=None):
            normalized = " ".join(sql.split())
            executed.append((normalized, params))
            if normalized.startswith("SELECT task_id, spool_path"):
                self.rows = [{"task_id": "task-1", "spool_path": developer_spool.name}]
            elif normalized.startswith("SELECT job_id, spool_path"):
                self.rows = [{"job_id": "job-1", "spool_path": web_spool.name}]
            elif normalized.startswith("UPDATE developer_detection_tasks"):
                assert not developer_spool.exists()
                assert not web_spool.exists()
            elif not normalized.startswith(("UPDATE web_detection_tasks", "UPDATE admin_model_runs")):
                raise AssertionError(f"unexpected SQL: {normalized}")

        def fetchall(self):
            return self.rows

    class Connection:
        def begin(self):
            return None

        def cursor(self):
            return Cursor()

        def commit(self):
            return None

        def rollback(self):
            return None

        def close(self):
            return None

    monkeypatch.setattr(api, "PRIVACY_DEVELOPER_SPOOL_ROOT", developer_root)
    monkeypatch.setattr(api, "PRIVACY_WEB_SPOOL_ROOT", web_root)
    monkeypatch.setattr(api, "get_db_connection", Connection)
    monkeypatch.setattr(api.admin_state, "scrub_detection_item", lambda *_args: True)

    assert api._scrub_history_replicas("image", 42, "erase-job-42") is True
    assert not developer_spool.exists()
    assert not web_spool.exists()
    assert any(sql.startswith("UPDATE developer_detection_tasks") for sql, _ in executed)


def test_privacy_worker_does_not_touch_recent_precommit_staging(monkeypatch):
    captured = {}

    def execute(statement, params=None, fetch=True):
        captured["statement"] = " ".join(statement.split())
        captured["params"] = params
        return []

    monkeypatch.setattr(api, "_ensure_privacy_erasure_table", lambda: True)
    monkeypatch.setattr(api, "excute_sql", execute)

    result = api._retry_pending_privacy_erasures(limit=25)

    assert result == {"retried": 0, "completed": 0, "restored": 0}
    assert "state IN ('pending_cleanup', 'cleanup_failed', 'restore_failed')" in captured["statement"]
    assert "state IN ('preparing', 'staged')" in captured["statement"]
    assert "updated_at <= DATE_SUB(NOW(6), INTERVAL %s SECOND)" in captured["statement"]
    assert captured["params"] == (api.PRIVACY_ERASURE_PRECOMMIT_GRACE_SECONDS, 25)
    assert api.PRIVACY_ERASURE_PRECOMMIT_GRACE_SECONDS > 330


def test_history_delete_returns_trackable_pending_erasure(client, monkeypatch):
    monkeypatch.setattr(
        api,
        "_delete_owned_history_record",
        lambda kind, itemid, actor: (True, "erase-request-42", 202),
    )
    _login_session(client, phone="13800000007")

    response = client.delete("/api/history/image-detections/42")

    assert response.status_code == 202
    assert response.get_json()["status"] == "pending"
    assert response.get_json()["erasureRequestId"] == "erase-request-42"


def test_history_delete_does_not_remove_foreign_record(client, monkeypatch):
    monkeypatch.setattr(api, "_delete_owned_history_record", lambda kind, itemid, actor: (False, "记录不存在", 404))
    _login_session(client, phone="13800000007")

    response = client.delete("/api/history/image-detections/42")

    assert response.status_code == 404
    assert response.get_json()["message"] == "记录不存在"


def test_cross_site_browser_write_is_rejected_before_history_mutation(client, monkeypatch):
    monkeypatch.setattr(
        api,
        "_delete_owned_history_record",
        lambda *args, **kwargs: pytest.fail("cross-site request reached the mutation handler"),
    )
    _login_session(client, phone="13800000007")

    response = client.delete(
        "/api/history/image-detections/42",
        headers={
            "Origin": "https://attacker.example",
            "Sec-Fetch-Site": "cross-site",
        },
    )

    assert response.status_code == 403
    assert response.get_json()["message"] == "拒绝跨站请求"


def test_same_origin_browser_write_reaches_api_handler(client, monkeypatch):
    monkeypatch.setattr(api, "_delete_owned_history_record", lambda *args, **kwargs: (False, "记录不存在", 404))
    _login_session(client, phone="13800000007")

    response = client.delete(
        "/api/history/image-detections/42",
        headers={
            "Origin": "http://localhost",
            "Host": "localhost",
            "Sec-Fetch-Site": "same-origin",
        },
    )

    assert response.status_code == 404


def test_result_template_never_coerces_review_only_probability_to_real_verdict():
    template = (
        ROOT / "imagedetection" / "templates" / "image_detection_result.html"
    ).read_text(encoding="utf-8")

    assert "value===null||value===undefined||value===''" in template
    assert "decisionStatus==='review_only'" in template
    assert "verdictFromResult==='需人工复核'" in template
    assert "var verdictLabel=isFake?'AI生成图像':'真实图像'" in template
    assert "已给出二元结论；当前置信度较低" in template
    assert "isReview?'待复核':prob+'%'" in template
    assert "Number(r.probability)" not in template
