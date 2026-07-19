from concurrent.futures import ThreadPoolExecutor
import copy
from io import BytesIO
from pathlib import Path
import threading
import sys

import pytest
from PIL import Image
from flask import request


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from imagedetection import creat_app  # noqa: E402
from imagedetection.views import developer_platform as platform  # noqa: E402


@pytest.fixture
def client():
    app = creat_app()
    app.config.update(TESTING=True)
    return app.test_client()


def _png_bytes():
    output = BytesIO()
    Image.new("RGB", (8, 8), (18, 125, 124)).save(output, format="PNG")
    return output.getvalue()


class _BillingStore:
    def __init__(self, free_total=5):
        self.lock = threading.RLock()
        self.task_active = True
        self.account = {
            "status": "active",
            "free_total": free_total,
            "free_used": 0,
            "free_reserved": 0,
            "balance_fen": 0,
            "balance_reserved_fen": 0,
        }
        self.pricing = {"fast": {"unit_price_fen": 10, "enabled": 0}}
        self.reservations = {}
        self.ledger = []

    def connection(self):
        return _BillingConnection(self)


class _BillingConnection:
    def __init__(self, store):
        self.store = store
        self.locked = False

    def begin(self):
        self.store.lock.acquire()
        self.locked = True

    def cursor(self):
        return _BillingCursor(self.store)

    def commit(self):
        self._release()

    def rollback(self):
        self._release()

    def close(self):
        self._release()

    def _release(self):
        if self.locked:
            self.locked = False
            self.store.lock.release()


class _BillingCursor:
    def __init__(self, store):
        self.store = store
        self.row = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        params = params or ()
        self.row = None
        if normalized.startswith("SELECT task_id FROM developer_detection_tasks"):
            self.row = {"task_id": params[0]} if self.store.task_active else None
            return int(bool(self.row))
        if normalized.startswith("INSERT IGNORE INTO developer_accounts"):
            return 0
        if normalized.startswith("SELECT status, free_total"):
            self.row = dict(self.store.account)
            return 1
        if normalized.startswith("SELECT unit_price_fen, enabled FROM developer_pricing"):
            self.row = dict(self.store.pricing[params[0]])
            return 1
        if normalized.startswith("UPDATE developer_accounts SET free_reserved = free_reserved + 1"):
            self.store.account["free_reserved"] += 1
            return 1
        if normalized.startswith("INSERT INTO developer_billing_reservations"):
            task_id, user_id, key_id, mode, source, amount = params
            self.store.reservations[task_id] = {
                "task_id": task_id,
                "user_id": user_id,
                "key_id": key_id,
                "mode": mode,
                "source": source,
                "amount_fen": amount,
                "status": "reserved",
            }
            return 1
        if normalized.startswith("SELECT task_id, user_id, key_id, mode, source"):
            row = self.store.reservations.get(params[0])
            self.row = dict(row) if row else None
            return int(bool(row))
        if normalized.startswith("SELECT balance_fen FROM developer_accounts"):
            self.row = {"balance_fen": self.store.account["balance_fen"]}
            return 1
        if "SET free_reserved = GREATEST(0, free_reserved - 1), free_used = free_used + 1" in normalized:
            self.store.account["free_reserved"] = max(0, self.store.account["free_reserved"] - 1)
            self.store.account["free_used"] += 1
            return 1
        if normalized.startswith("UPDATE developer_billing_reservations SET status = 'settled'"):
            self.store.reservations[params[0]]["status"] = "settled"
            return 1
        if normalized.startswith("INSERT INTO developer_billing_ledger"):
            self.store.ledger.append(params)
            return 1
        if normalized.startswith("SELECT user_id, source, amount_fen, status"):
            row = self.store.reservations.get(params[0])
            self.row = dict(row) if row else None
            return int(bool(row))
        if normalized.startswith("UPDATE developer_accounts SET free_reserved = GREATEST(0, free_reserved - 1)"):
            self.store.account["free_reserved"] = max(0, self.store.account["free_reserved"] - 1)
            return 1
        if normalized.startswith("UPDATE developer_billing_reservations SET status = 'released'"):
            self.store.reservations[params[0]]["status"] = "released"
            return 1
        raise AssertionError(f"unexpected SQL: {normalized}")

    def fetchone(self):
        return self.row


class _RecoveryStore:
    def __init__(self, *, source="free", amount_fen=0, expired=True):
        self.lock = threading.RLock()
        self.task = {
            "task_id": "expired-task",
            "user_id": 3,
            "status": "running",
            "lease_owner": "dead-process",
            "lease_expires_at": "expired" if expired else "active",
            "expired": expired,
            "error_message": None,
        }
        self.reservation = {
            "user_id": 3,
            "source": source,
            "amount_fen": amount_fen,
            "status": "reserved",
        }
        self.account = {
            "free_reserved": 1 if source == "free" else 0,
            "balance_reserved_fen": amount_fen if source == "balance" else 0,
        }
        self.release_updates = 0
        self.fail_on = None

    def connection(self):
        return _RecoveryConnection(self)


class _RecoveryConnection:
    def __init__(self, store):
        self.store = store
        self.locked = False
        self.snapshot = None

    def begin(self):
        self.store.lock.acquire()
        self.locked = True
        self.snapshot = copy.deepcopy(
            (self.store.task, self.store.reservation, self.store.account, self.store.release_updates)
        )

    def cursor(self):
        return _RecoveryCursor(self.store)

    def commit(self):
        self.snapshot = None
        self._release()

    def rollback(self):
        if self.snapshot is not None:
            task, reservation, account, release_updates = self.snapshot
            self.store.task = task
            self.store.reservation = reservation
            self.store.account = account
            self.store.release_updates = release_updates
            self.snapshot = None
        self._release()

    def close(self):
        self._release()

    def _release(self):
        if self.locked:
            self.locked = False
            self.store.lock.release()


class _RecoveryCursor:
    def __init__(self, store):
        self.store = store
        self.row = None
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        params = params or ()
        self.row = None
        self.rowcount = 0
        if self.store.fail_on and self.store.fail_on in normalized:
            raise RuntimeError("injected database failure")
        if normalized.startswith("SELECT task_id, user_id, status, lease_owner"):
            task = self.store.task
            requires_expired = "lease_expires_at <= NOW(6)" in normalized
            requires_active_owner = "lease_expires_at > NOW(6)" in normalized
            lease_matches = (
                task["expired"] if requires_expired
                else (not task["expired"] and task["lease_owner"] == params[1]) if requires_active_owner
                else True
            )
            if task["task_id"] == params[0] and task["status"] in {"queued", "running"} and lease_matches:
                self.row = dict(task)
                self.rowcount = 1
            return self.rowcount
        if normalized.startswith("SELECT user_id, source, amount_fen, status"):
            self.row = dict(self.store.reservation) if self.store.reservation else None
            self.rowcount = int(bool(self.row))
            return self.rowcount
        if normalized.startswith("SELECT free_reserved, balance_reserved_fen"):
            self.row = dict(self.store.account)
            self.rowcount = 1
            return 1
        if normalized.startswith("UPDATE developer_accounts SET free_reserved = free_reserved - 1"):
            if self.store.account["free_reserved"] >= 1:
                self.store.account["free_reserved"] -= 1
                self.rowcount = 1
            return self.rowcount
        if normalized.startswith("UPDATE developer_accounts SET balance_reserved_fen = balance_reserved_fen -"):
            amount = params[0]
            if self.store.account["balance_reserved_fen"] >= amount:
                self.store.account["balance_reserved_fen"] -= amount
                self.rowcount = 1
            return self.rowcount
        if normalized.startswith("UPDATE developer_billing_reservations SET status = 'released'"):
            if self.store.reservation and self.store.reservation["status"] == "reserved":
                self.store.reservation["status"] = "released"
                self.store.release_updates += 1
                self.rowcount = 1
            return self.rowcount
        if normalized.startswith("UPDATE developer_detection_tasks SET status = 'failed'"):
            task = self.store.task
            requires_expired = "lease_expires_at <= NOW(6)" in normalized
            requires_active_owner = "lease_expires_at > NOW(6)" in normalized
            owner = params[2] if requires_active_owner else None
            lease_matches = (
                task["expired"] if requires_expired
                else (not task["expired"] and task["lease_owner"] == owner) if requires_active_owner
                else True
            )
            if task["status"] in {"queued", "running"} and lease_matches:
                task.update({
                    "status": "failed",
                    "error_message": params[0],
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "expired": False,
                })
                self.rowcount = 1
            return self.rowcount
        raise AssertionError(f"unexpected SQL: {normalized}")

    def fetchone(self):
        return self.row


def test_concurrent_free_quota_reservations_cannot_oversubscribe(monkeypatch):
    store = _BillingStore(free_total=5)
    monkeypatch.setattr(platform, "_ensure_developer_platform_tables", lambda: True)
    monkeypatch.setattr(platform, "get_db_connection", store.connection)

    def reserve(index):
        try:
            platform._reserve_billing(1, 8, f"task-{index}", "fast")
            return True
        except platform.BillingError as exc:
            assert exc.status_code == 402
            return False

    with ThreadPoolExecutor(max_workers=12) as executor:
        outcomes = list(executor.map(reserve, range(20)))

    assert sum(outcomes) == 5
    assert store.account["free_reserved"] == 5

    reserved_ids = [task_id for task_id, row in store.reservations.items() if row["status"] == "reserved"]
    for task_id in reserved_ids[:3]:
        assert platform._settle_billing(task_id) is True
    for task_id in reserved_ids[3:]:
        assert platform._release_billing(task_id) is True

    assert store.account["free_reserved"] == 0
    assert store.account["free_used"] == 3
    assert len(store.ledger) == 3


def test_billing_reservation_rejects_an_expired_task_lease(monkeypatch):
    store = _BillingStore(free_total=5)
    store.task_active = False
    monkeypatch.setattr(platform, "_ensure_developer_platform_tables", lambda: True)
    monkeypatch.setattr(platform, "get_db_connection", store.connection)

    with pytest.raises(platform.BillingError) as error:
        platform._reserve_billing(1, 8, "expired-task", "fast")

    assert error.value.code == "task_lease_expired"
    assert store.account["free_reserved"] == 0
    assert store.reservations == {}


def test_expired_task_recovery_releases_free_quota_and_fails_task(monkeypatch):
    store = _RecoveryStore(source="free", expired=True)
    job_updates = []
    monkeypatch.setattr(platform, "get_db_connection", store.connection)
    monkeypatch.setattr(
        platform.admin_state,
        "update_detection_job",
        lambda task_id, payload: job_updates.append((task_id, payload)),
    )

    assert platform._expire_task_lease("expired-task") is True

    assert store.task["status"] == "failed"
    assert "租约已过期" in store.task["error_message"]
    assert store.task["lease_owner"] is None
    assert store.reservation["status"] == "released"
    assert store.account["free_reserved"] == 0
    assert store.release_updates == 1
    assert job_updates[0][1]["status"] == "failed"


def test_expired_task_recovery_releases_reserved_balance(monkeypatch):
    store = _RecoveryStore(source="balance", amount_fen=35, expired=True)
    monkeypatch.setattr(platform, "get_db_connection", store.connection)
    monkeypatch.setattr(platform.admin_state, "update_detection_job", lambda *args, **kwargs: None)

    assert platform._expire_task_lease("expired-task") is True

    assert store.account["balance_reserved_fen"] == 0
    assert store.reservation["status"] == "released"
    assert store.task["status"] == "failed"


def test_active_task_lease_is_not_recovered(monkeypatch):
    store = _RecoveryStore(expired=False)
    monkeypatch.setattr(platform, "get_db_connection", store.connection)

    assert platform._expire_task_lease("expired-task") is False

    assert store.task["status"] == "running"
    assert store.reservation["status"] == "reserved"
    assert store.account["free_reserved"] == 1


def test_concurrent_recovery_releases_a_reservation_only_once(monkeypatch):
    store = _RecoveryStore(expired=True)
    monkeypatch.setattr(platform, "get_db_connection", store.connection)
    monkeypatch.setattr(platform.admin_state, "update_detection_job", lambda *args, **kwargs: None)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(platform._expire_task_lease, ["expired-task", "expired-task"]))

    assert sorted(outcomes) == [False, True]
    assert store.account["free_reserved"] == 0
    assert store.reservation["status"] == "released"
    assert store.release_updates == 1


def test_recovery_database_failure_rolls_back_task_and_billing(monkeypatch):
    store = _RecoveryStore(expired=True)
    store.fail_on = "UPDATE developer_billing_reservations SET status = 'released'"
    monkeypatch.setattr(platform, "get_db_connection", store.connection)

    with pytest.raises(platform.TaskRecoveryError, match="injected database failure"):
        platform._expire_task_lease("expired-task")

    assert store.task["status"] == "running"
    assert store.reservation["status"] == "reserved"
    assert store.account["free_reserved"] == 1
    assert store.release_updates == 0


def test_failure_terminalization_cannot_leave_running_with_released_billing(monkeypatch):
    store = _RecoveryStore(expired=False)
    store.fail_on = "UPDATE developer_detection_tasks SET status = 'failed'"
    monkeypatch.setattr(platform, "get_db_connection", store.connection)

    with pytest.raises(platform.TaskRecoveryError, match="injected database failure"):
        platform._fail_task_and_release(
            "expired-task",
            "model timeout",
            lease_owner="dead-process",
        )

    assert store.task["status"] == "running"
    assert store.reservation["status"] == "reserved"
    assert store.account["free_reserved"] == 1
    assert store.release_updates == 0


def test_active_failure_terminalizes_task_and_billing_together(monkeypatch):
    store = _RecoveryStore(expired=False)
    monkeypatch.setattr(platform, "get_db_connection", store.connection)

    assert platform._fail_task_and_release(
        "expired-task",
        "model timeout",
        lease_owner="dead-process",
    ) is True

    assert store.task["status"] == "failed"
    assert store.reservation["status"] == "released"
    assert store.account["free_reserved"] == 0


def test_recovery_rejects_inconsistent_reserved_counter(monkeypatch):
    store = _RecoveryStore(expired=True)
    store.account["free_reserved"] = 0
    monkeypatch.setattr(platform, "get_db_connection", store.connection)

    with pytest.raises(platform.TaskRecoveryError, match="reserved balance is inconsistent"):
        platform._expire_task_lease("expired-task")

    assert store.task["status"] == "running"
    assert store.reservation["status"] == "reserved"


def test_reconcile_scan_database_failure_is_not_silent(monkeypatch):
    monkeypatch.setattr(platform, "_ensure_developer_platform_tables", lambda: True)
    monkeypatch.setattr(platform, "get_db_connection", lambda: (_ for _ in ()).throw(RuntimeError("db offline")))

    with pytest.raises(platform.TaskRecoveryError, match="db offline"):
        platform._reconcile_expired_tasks()


def test_task_status_is_isolated_by_developer_account(client, monkeypatch):
    rows = {
        ("job-owned", 1): {
            "task_id": "job-owned",
            "user_id": 1,
            "key_id": 7,
            "mode": "fast",
            "filename": "owned.png",
            "status": "success",
            "result_json": '{"status":"success","result":{"itemid":12,"final_label":"真实图像"}}',
            "created_at": "2026-07-17 10:00:00",
            "updated_at": "2026-07-17 10:00:02",
            "completed_at": "2026-07-17 10:00:02",
        },
    }

    def auth():
        user_id = 1 if request.headers.get("X-Test-Key") == "owner" else 2
        return ({"id": user_id + 10, "user_id": user_id, "scopes": ["image:fast", "reports"]}, None)

    monkeypatch.setattr(platform, "_developer_key_required", auth)
    monkeypatch.setattr(platform, "_maybe_reconcile_expired_tasks", lambda: 0)
    monkeypatch.setattr(platform, "_task_row_for_user", lambda task_id, user_id: rows.get((task_id, user_id)))
    monkeypatch.setattr(platform.admin_state, "get_detection_job", lambda task_id: None)
    monkeypatch.setattr(platform, "_reservation_payload", lambda task_id: {"source": "free", "amountFen": 0, "status": "settled"})
    monkeypatch.setattr(platform, "_reservation_status_strict", lambda task_id: "settled")

    owned = client.get("/api/openapi/v1/image-detections/job-owned", headers={"X-Test-Key": "owner"})
    foreign = client.get("/api/openapi/v1/image-detections/job-owned", headers={"X-Test-Key": "foreign"})

    assert owned.status_code == 200
    assert owned.get_json()["result"]["itemid"] == 12
    assert foreign.status_code == 404


def test_task_status_is_not_blocked_by_unrelated_reconciliation_failure(client, monkeypatch):
    monkeypatch.setattr(
        platform,
        "_developer_key_required",
        lambda: ({"id": 11, "user_id": 1, "scopes": ["image:fast"]}, None),
    )
    monkeypatch.setattr(
        platform,
        "_maybe_reconcile_expired_tasks",
        lambda: 0,
    )
    monkeypatch.setattr(platform, "_task_row_for_user", lambda task_id, user_id: None)

    response = client.get("/api/openapi/v1/image-detections/any-task")

    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "task_not_found"


def test_lazy_reconciliation_defers_scan_failure(monkeypatch):
    monkeypatch.setattr(platform, "_TASK_RECONCILE_LAST_MONOTONIC", 0)
    monkeypatch.setattr(
        platform,
        "_reconcile_expired_tasks",
        lambda: (_ for _ in ()).throw(platform.TaskRecoveryError("db offline")),
    )

    assert platform._maybe_reconcile_expired_tasks(force=True) == 0


def test_idempotency_key_rejects_different_content(client, monkeypatch):
    actor = {"id": 11, "user_id": 1, "scopes": ["image:fast"], "phone": "13800000000"}
    monkeypatch.setattr(platform, "_developer_key_required", lambda: (actor, None))
    monkeypatch.setattr(platform, "_ensure_developer_platform_tables", lambda: True)
    monkeypatch.setattr(platform, "_maybe_reconcile_expired_tasks", lambda: 0)
    monkeypatch.setattr(
        platform,
        "_idempotent_task",
        lambda user_id, key: {"mode": "fast", "request_sha256": "different"} if key else None,
    )

    response = client.post(
        "/api/openapi/v1/image-detections",
        headers={"Idempotency-Key": "same-logical-request"},
        data={"mode": "fast", "image": (BytesIO(_png_bytes()), "sample.png")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "idempotency_conflict"


def test_finish_task_settles_success_and_releases_failure(monkeypatch):
    settled = []
    terminalized = []
    usage = []
    sql = []
    monkeypatch.setattr(platform, "_settle_billing", lambda task_id: settled.append(task_id) or True)
    monkeypatch.setattr(
        platform,
        "_fail_task_and_release",
        lambda task_id, message, lease_owner=None: terminalized.append((task_id, message, lease_owner)) or True,
    )
    monkeypatch.setattr(platform, "_record_developer_usage_event", lambda actor, **kwargs: usage.append((actor, kwargs)) or True)
    monkeypatch.setattr(platform, "excute_sql", lambda statement, params=None, fetch=True: sql.append((statement, params)) or 1)
    monkeypatch.setattr(platform, "_public_result_payload", lambda payload, mode: payload)

    actor = {"id": 9, "user_id": 1}
    assert platform._finish_task("ok", actor, "fast", {"status": "success", "result": {"itemid": 2}}, 200) is True
    assert platform._finish_task("bad", actor, "swarm", {"status": "error", "message": "model timeout"}, 503) is True

    assert settled == ["ok"]
    assert terminalized == [("bad", "model timeout", None)]
    assert len(usage) == 1
    assert usage[0][1]["endpoint"].endswith(":fast")
    assert len(sql) == 1


def test_finish_task_reports_failed_billing_release(monkeypatch):
    monkeypatch.setattr(platform, "_fail_task_and_release", lambda *args, **kwargs: False)
    monkeypatch.setattr(platform, "excute_sql", lambda *args, **kwargs: 1)

    assert platform._finish_task(
        "bad",
        {"id": 9, "user_id": 1},
        "fast",
        {"status": "error", "message": "model timeout"},
        503,
    ) is False


def test_finish_task_reports_success_with_pending_settlement(monkeypatch):
    monkeypatch.setattr(platform, "_settle_billing", lambda task_id: False)
    monkeypatch.setattr(platform, "excute_sql", lambda *args, **kwargs: 1)
    monkeypatch.setattr(platform, "_public_result_payload", lambda payload, mode: payload)

    assert platform._finish_task(
        "success-pending-billing",
        {"id": 9, "user_id": 1},
        "fast",
        {"status": "success", "result": {"itemid": 2}},
        200,
    ) is False


def test_success_reservation_reconciliation_settles_and_restores_success_job(monkeypatch):
    updates = []
    monkeypatch.setattr(platform, "_settle_billing", lambda task_id: True)
    monkeypatch.setattr(
        platform.admin_state,
        "update_detection_job",
        lambda task_id, payload: updates.append((task_id, payload)),
    )

    assert platform._reconcile_success_reservation("success-task") is True
    assert updates == [(
        "success-task",
        {"status": "success", "progress": 100, "summary": "检测完成，计费对账已完成"},
    )]


def test_success_reservation_reconciliation_accepts_other_process_settlement(monkeypatch):
    monkeypatch.setattr(platform, "_settle_billing", lambda task_id: False)
    monkeypatch.setattr(platform, "_reservation_status_strict", lambda task_id: "settled")
    monkeypatch.setattr(platform.admin_state, "update_detection_job", lambda *args, **kwargs: None)

    assert platform._reconcile_success_reservation("success-task") is False


def test_success_reservation_reconciliation_surfaces_still_reserved_failure(monkeypatch):
    monkeypatch.setattr(platform, "_settle_billing", lambda task_id: False)
    monkeypatch.setattr(platform, "_reservation_status_strict", lambda task_id: "reserved")

    with pytest.raises(platform.TaskRecoveryError, match="remains reserved"):
        platform._reconcile_success_reservation("success-task")


def test_lazy_reconciliation_scans_success_with_reserved_billing(monkeypatch):
    class Cursor:
        def __init__(self):
            self.rows = []

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, sql, params=None):
            normalized = " ".join(sql.split())
            self.rows = [{"task_id": "success-task"}] if "task.status = 'success'" in normalized else []

        def fetchall(self):
            return list(self.rows)

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            return None

    reconciled = []
    monkeypatch.setattr(platform, "_ensure_developer_platform_tables", lambda: True)
    monkeypatch.setattr(platform, "get_db_connection", Connection)
    monkeypatch.setattr(platform, "_reconcile_success_reservation", lambda task_id: reconciled.append(task_id) or True)

    assert platform._reconcile_expired_tasks() == 1
    assert reconciled == ["success-task"]


def test_database_scope_string_is_parsed_as_scopes():
    actor = {"scopes": "image:fast,reports"}

    assert platform._require_scope(actor, "image:fast") is None


def test_finish_task_never_settles_before_success_is_persisted(monkeypatch):
    settled = []
    monkeypatch.setattr(platform, "_settle_billing", lambda task_id: settled.append(task_id) or True)
    monkeypatch.setattr(platform, "excute_sql", lambda *args, **kwargs: 0)
    monkeypatch.setattr(platform, "_public_result_payload", lambda payload, mode: payload)

    result = platform._finish_task(
        "not-persisted",
        {"id": 9, "user_id": 1},
        "fast",
        {"status": "success", "result": {"itemid": 2}},
        200,
    )

    assert result is False
    assert settled == []


def test_finish_task_requires_the_same_unexpired_lease(monkeypatch):
    statements = []
    settled = []
    monkeypatch.setattr(
        platform,
        "excute_sql",
        lambda statement, params=None, fetch=True: statements.append((statement, params)) or 0,
    )
    monkeypatch.setattr(platform, "_settle_billing", lambda task_id: settled.append(task_id) or True)
    monkeypatch.setattr(platform, "_public_result_payload", lambda payload, mode: payload)

    result = platform._finish_task(
        "stale-worker",
        {"id": 9, "user_id": 1},
        "fast",
        {"status": "success", "result": {"itemid": 2}},
        200,
        lease_owner="process-a",
    )

    assert result is False
    assert "lease_owner = %s AND lease_expires_at > NOW(6)" in statements[0][0]
    assert statements[0][1][-1] == "process-a"
    assert settled == []


def test_lease_claim_and_renew_surface_database_failures(monkeypatch):
    monkeypatch.setattr(platform, "excute_sql", lambda *args, **kwargs: None)

    with pytest.raises(platform.TaskRecoveryError, match="claim task lease"):
        platform._claim_task_lease("task-1", "owner-1")
    with pytest.raises(platform.TaskRecoveryError, match="renew task lease"):
        platform._renew_task_lease("task-1", "owner-1")


def test_rejected_task_exposes_terminal_error(monkeypatch):
    monkeypatch.setattr(platform.admin_state, "get_detection_job", lambda task_id: None)
    monkeypatch.setattr(platform, "_reservation_payload", lambda task_id: None)
    row = {
        "task_id": "rejected",
        "mode": "swarm",
        "filename": "sample.png",
        "status": "rejected",
        "error_message": "余额不足",
    }

    payload = platform._task_payload(row)

    assert payload["progress"] == 100
    assert payload["error"]["message"] == "余额不足"


def test_admin_sets_exact_remaining_calls_without_rewriting_usage(client, monkeypatch):
    account = {
        "user_id": 7,
        "status": "active",
        "free_total": 100,
        "free_used": 37,
        "free_reserved": 2,
        "balance_fen": 0,
        "balance_reserved_fen": 0,
        "created_at": None,
        "updated_at": None,
    }
    ledger = []
    audits = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, sql, params=None):
            normalized = " ".join(sql.split())
            if normalized.startswith("SELECT user_id, status, free_total"):
                return 1
            if normalized.startswith("UPDATE developer_accounts SET free_total"):
                account["free_total"] = params[0]
                return 1
            if normalized.startswith("INSERT INTO developer_billing_ledger"):
                ledger.append(params)
                return 1
            raise AssertionError(f"unexpected SQL: {normalized}")

        def fetchone(self):
            return dict(account)

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

    monkeypatch.setattr(platform, "_admin_required", lambda permission: ({"adminId": 1}, None))
    monkeypatch.setattr(platform, "_ensure_developer_account", lambda user_id: True)
    monkeypatch.setattr(platform, "get_db_connection", Connection)
    monkeypatch.setattr(platform, "_audit", lambda *args, **kwargs: audits.append((args, kwargs)))
    monkeypatch.setattr(platform, "excute_sql", lambda *args, **kwargs: [{"Userid": 7}])

    with client.application.test_request_context(json={"remainingCalls": 250, "note": "测试额度"}):
        response = platform.admin_set_developer_quota(7)

    payload = response.get_json()
    assert payload["status"] == "success"
    assert payload["account"]["freeRemaining"] == 250
    assert payload["account"]["freeUsed"] == 37
    assert payload["account"]["freeReserved"] == 2
    assert account["free_total"] == 289
    assert ledger == [(7, 189, 0, "测试额度")]
    assert audits[0][0][1] == "developer.account.quota.set"


def test_task_payload_rewrites_browser_only_media_link(monkeypatch):
    monkeypatch.setattr(platform.admin_state, "get_detection_job", lambda task_id: None)
    monkeypatch.setattr(platform, "_reservation_payload", lambda task_id: None)
    row = {
        "task_id": "task-media",
        "mode": "fast",
        "filename": "sample.png",
        "status": "success",
        "result_item_id": 42,
        "result_json": {
            "status": "success",
            "result": {"itemid": 42, "image_url": "/api/media/image/42"},
        },
    }

    payload = platform._task_payload(row)

    expected = "/api/openapi/v1/image-detections/task-media/media"
    assert payload["result"]["image_url"] == expected
    assert payload["links"]["media"] == expected


def test_openapi_media_download_uses_api_key_owner_and_mode_scope(client, monkeypatch):
    actor = {"user_id": 7, "phone": "13800000007", "openid": "openid-7", "scopes": "image:fast"}
    row = {"task_id": "task-media", "mode": "fast", "status": "success", "result_item_id": 42}
    item = {"itemid": 42, "filename": "sample.png", "phone": "13800000007"}
    monkeypatch.setattr(platform, "_developer_key_required", lambda: (actor, None))
    monkeypatch.setattr(platform, "_task_row_for_user", lambda task_id, user_id: row)
    monkeypatch.setattr(platform, "_owned_detection_item", lambda current_actor, item_id: item)
    monkeypatch.setattr(
        platform,
        "_serve_detection_media_item",
        lambda kind, current_item: ({"kind": kind, "itemid": current_item["itemid"]}, 200),
    )

    with client.application.test_request_context("/api/openapi/v1/image-detections/task-media/media"):
        payload, status = platform.get_image_detection_media("task-media")

    assert status == 200
    assert payload == {"kind": "image", "itemid": 42}


def test_openapi_media_download_hides_foreign_task(client, monkeypatch):
    actor = {"user_id": 7, "scopes": "image:fast"}
    monkeypatch.setattr(platform, "_developer_key_required", lambda: (actor, None))
    monkeypatch.setattr(platform, "_task_row_for_user", lambda task_id, user_id: None)

    with client.application.test_request_context("/api/openapi/v1/image-detections/foreign/media"):
        response, status = platform.get_image_detection_media("foreign")

    assert status == 404
    assert response.get_json()["error"]["code"] == "task_not_found"
