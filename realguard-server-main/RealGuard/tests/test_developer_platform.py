from concurrent.futures import ThreadPoolExecutor
import copy
from io import BytesIO
from pathlib import Path
import threading
import sys
import time

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
        if normalized.startswith("SELECT prompt_tokens, completion_tokens, total_tokens"):
            self.row = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
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
        if normalized.startswith("INSERT IGNORE INTO developer_usage_events"):
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
        if normalized.startswith("SELECT task_id, user_id, status FROM developer_detection_tasks"):
            task = self.store.task
            if task["task_id"] == params[0] and task["status"] in {"queued", "running"}:
                self.row = dict(task)
                self.rowcount = 1
            return self.rowcount
        if normalized.startswith("SELECT user_id, source, amount_fen, status"):
            self.row = dict(self.store.reservation) if self.store.reservation else None
            self.rowcount = int(bool(self.row))
            return self.rowcount
        if normalized.startswith("SELECT user_id, status FROM developer_billing_reservations"):
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
        return ({"id": user_id + 10, "user_id": user_id, "account_uuid": f"00000000-0000-4000-8000-00000000000{user_id}", "scopes": ["image:fast", "reports"]}, None)

    monkeypatch.setattr(platform, "_developer_key_required", auth)
    monkeypatch.setattr(platform, "_maybe_reconcile_expired_tasks", lambda: 0)
    monkeypatch.setattr(platform, "_task_row_for_user", lambda task_id, user_id, account_uuid: rows.get((task_id, user_id)))
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
        lambda: ({"id": 11, "user_id": 1, "account_uuid": "00000000-0000-4000-8000-000000000001", "scopes": ["image:fast"]}, None),
    )
    monkeypatch.setattr(
        platform,
        "_maybe_reconcile_expired_tasks",
        lambda: 0,
    )
    monkeypatch.setattr(platform, "_task_row_for_user", lambda task_id, user_id, account_uuid: None)

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
    actor = {"id": 11, "user_id": 1, "account_uuid": "00000000-0000-4000-8000-000000000001", "scopes": ["image:fast"], "phone": "13800000000"}
    monkeypatch.setattr(platform, "_developer_key_required", lambda: (actor, None))
    monkeypatch.setattr(platform, "_ensure_developer_platform_tables", lambda: True)
    monkeypatch.setattr(platform, "_maybe_reconcile_expired_tasks", lambda: 0)
    monkeypatch.setattr(
        platform,
        "_idempotent_task",
        lambda user_id, account_uuid, key: {"mode": "fast", "request_sha256": "different"} if key else None,
    )

    response = client.post(
        "/api/openapi/v1/image-detections",
        headers={"Idempotency-Key": "same-logical-request"},
        data={"mode": "fast", "image": (BytesIO(_png_bytes()), "sample.png")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "idempotency_conflict"


def test_developer_api_rejects_images_above_pixel_limit(client, monkeypatch):
    actor = {"id": 11, "user_id": 1, "account_uuid": "00000000-0000-4000-8000-000000000001", "scopes": ["image:fast"], "phone": "13800000000"}
    monkeypatch.setattr(platform, "_developer_key_required", lambda: (actor, None))
    monkeypatch.setattr(platform, "_ensure_developer_platform_tables", lambda: True)
    monkeypatch.setattr(platform, "_maybe_reconcile_expired_tasks", lambda: 0)
    monkeypatch.setattr(platform, "DEVELOPER_MAX_IMAGE_PIXELS", 32)

    response = client.post(
        "/api/openapi/v1/image-detections",
        data={"mode": "fast", "image": (BytesIO(_png_bytes()), "sample.png")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 413
    assert response.get_json()["error"]["code"] == "image_pixels_too_large"


def test_finish_task_settles_success_and_releases_failure(monkeypatch):
    settled = []
    terminalized = []
    sql = []
    monkeypatch.setattr(platform, "_settle_billing", lambda task_id: settled.append(task_id) or True)
    monkeypatch.setattr(
        platform,
        "_fail_task_and_release",
        lambda task_id, message, lease_owner=None: terminalized.append((task_id, message, lease_owner)) or True,
    )
    monkeypatch.setattr(platform, "excute_sql", lambda statement, params=None, fetch=True: sql.append((statement, params)) or 1)
    monkeypatch.setattr(platform, "_public_result_payload", lambda payload, mode: payload)

    actor = {"id": 9, "user_id": 1}
    assert platform._finish_task("ok", actor, "fast", {"status": "success", "result": {"itemid": 2}}, 200) is True
    assert platform._finish_task("bad", actor, "swarm", {"status": "error", "message": "model timeout"}, 503) is True

    assert settled == ["ok"]
    assert terminalized == [("bad", "model timeout", None)]
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


def test_maintenance_scans_active_tasks_with_nonreserved_billing(monkeypatch):
    class Cursor:
        def __init__(self):
            self.rows = []

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, sql, params=None):
            normalized = " ".join(sql.split())
            if "LEFT JOIN developer_billing_reservations" in normalized:
                self.rows = [{"task_id": "orphaned-reservation-task"}]
            else:
                self.rows = []

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
    monkeypatch.setattr(
        platform,
        "_terminalize_anomalous_reservation",
        lambda task_id: reconciled.append(task_id) or True,
    )

    assert platform._reconcile_expired_tasks() == 1
    assert reconciled == ["orphaned-reservation-task"]


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


def test_admin_account_adjustment_requires_idempotency_key(client, monkeypatch):
    monkeypatch.setattr(platform, "_admin_required", lambda permission: ({"adminId": 1}, None))

    with client.application.test_request_context(json={"freeTotalDelta": 10}):
        response, status = platform.admin_adjust_developer_account(7)

    assert status == 400
    assert "operationId" in response.get_json()["message"]


def test_admin_account_adjustment_records_one_idempotent_operation(client, monkeypatch):
    account = {
        "user_id": 7,
        "status": "active",
        "free_total": 100,
        "free_used": 5,
        "free_reserved": 1,
        "balance_fen": 300,
        "balance_reserved_fen": 20,
    }
    operations = []
    ledger = []
    audits = []

    class Cursor:
        current = None

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, sql, params=None):
            normalized = " ".join(sql.split())
            if normalized.startswith("INSERT INTO developer_admin_operations"):
                operations.append(params)
                return 1
            if normalized.startswith("SELECT free_total, free_used"):
                self.current = dict(account)
                return 1
            if normalized.startswith("UPDATE developer_accounts SET free_total"):
                account["free_total"], account["balance_fen"] = params[:2]
                return 1
            if normalized.startswith("INSERT INTO developer_billing_ledger"):
                ledger.append(params)
                return 1
            raise AssertionError(f"unexpected SQL: {normalized}")

        def fetchone(self):
            return dict(self.current)

    class Connection:
        def begin(self):
            return None

        def cursor(self):
            return Cursor()

        def commit(self):
            return None

        def rollback(self):
            raise AssertionError("valid adjustment must not roll back")

        def close(self):
            return None

    monkeypatch.setattr(platform, "_admin_required", lambda permission: ({"adminId": 1}, None))
    monkeypatch.setattr(platform, "_ensure_developer_account", lambda user_id: True)
    monkeypatch.setattr(platform, "_account_row", lambda user_id: dict(account))
    monkeypatch.setattr(platform, "get_db_connection", Connection)
    monkeypatch.setattr(platform, "_audit", lambda *args, **kwargs: audits.append((args, kwargs)))
    monkeypatch.setattr(platform, "excute_sql", lambda *args, **kwargs: [{"Userid": 7}])

    payload = {
        "freeTotalDelta": 10,
        "balanceDeltaFen": 200,
        "note": "commercial credit",
        "operationId": "adjustment-20260719-001",
    }
    with client.application.test_request_context(json=payload):
        response = platform.admin_adjust_developer_account(7)

    body = response.get_json()
    assert body["idempotentReplay"] is False
    assert body["operationId"] == payload["operationId"]
    assert account["free_total"] == 110
    assert account["balance_fen"] == 500
    assert len(operations) == 1
    assert operations[0][0] == payload["operationId"]
    assert len(ledger) == 1
    assert audits[0][1]["meta"]["operationId"] == payload["operationId"]


def test_duplicate_admin_account_adjustment_replays_without_mutation(client, monkeypatch):
    payload = {
        "freeTotalDelta": 10,
        "balanceDeltaFen": 200,
        "note": "commercial credit",
        "operationId": "adjustment-20260719-001",
    }
    fingerprint = platform.hashlib.sha256(
        platform.json.dumps(
            {
                "userId": 7,
                "balanceDeltaFen": 200,
                "freeTotalDelta": 10,
                "note": "commercial credit",
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    audits = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, sql, params=None):
            normalized = " ".join(sql.split())
            if normalized.startswith("INSERT INTO developer_admin_operations"):
                raise platform.pymysql.err.IntegrityError(1062, "duplicate operation")
            raise AssertionError(f"unexpected SQL: {normalized}")

    class Connection:
        def begin(self):
            return None

        def cursor(self):
            return Cursor()

        def commit(self):
            raise AssertionError("duplicate operation must not commit")

        def rollback(self):
            return None

        def close(self):
            return None

    def execute(statement, params=None, fetch=True):
        normalized = " ".join(statement.split())
        if normalized.startswith("SELECT Userid FROM user"):
            return [{"Userid": 7}]
        if normalized.startswith("SELECT operation_type, user_id, request_sha256"):
            return [{
                "operation_type": "account_adjustment",
                "user_id": 7,
                "request_sha256": fingerprint,
            }]
        raise AssertionError(f"unexpected SQL: {normalized}")

    account = {
        "user_id": 7,
        "status": "active",
        "free_total": 110,
        "free_used": 0,
        "free_reserved": 0,
        "balance_fen": 200,
        "balance_reserved_fen": 0,
    }
    monkeypatch.setattr(platform, "_admin_required", lambda permission: ({"adminId": 1}, None))
    monkeypatch.setattr(platform, "_ensure_developer_account", lambda user_id: True)
    monkeypatch.setattr(platform, "_account_row", lambda user_id: dict(account))
    monkeypatch.setattr(platform, "get_db_connection", Connection)
    monkeypatch.setattr(platform, "excute_sql", execute)
    monkeypatch.setattr(platform, "_audit", lambda *args, **kwargs: audits.append((args, kwargs)))

    with client.application.test_request_context(json=payload):
        response = platform.admin_adjust_developer_account(7)

    body = response.get_json()
    assert body["status"] == "success"
    assert body["idempotentReplay"] is True
    assert body["operationId"] == payload["operationId"]
    assert body["account"]["freeTotal"] == 110
    assert audits == []


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
    actor = {"user_id": 7, "account_uuid": "00000000-0000-4000-8000-000000000007", "phone": "13800000007", "openid": "openid-7", "scopes": "image:fast"}
    row = {"task_id": "task-media", "mode": "fast", "status": "success", "result_item_id": 42}
    item = {"itemid": 42, "filename": "sample.png", "phone": "13800000007"}
    monkeypatch.setattr(platform, "_developer_key_required", lambda: (actor, None))
    monkeypatch.setattr(platform, "_task_row_for_user", lambda task_id, user_id, account_uuid: row)
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
    actor = {"user_id": 7, "account_uuid": "00000000-0000-4000-8000-000000000007", "scopes": "image:fast"}
    monkeypatch.setattr(platform, "_developer_key_required", lambda: (actor, None))
    monkeypatch.setattr(platform, "_task_row_for_user", lambda task_id, user_id, account_uuid: None)

    with client.application.test_request_context("/api/openapi/v1/image-detections/foreign/media"):
        response, status = platform.get_image_detection_media("foreign")

    assert status == 404
    assert response.get_json()["error"]["code"] == "task_not_found"


def test_task_spool_is_private_and_sha256_verified(monkeypatch, tmp_path):
    monkeypatch.setattr(platform, "DEVELOPER_SPOOL_ROOT", tmp_path / "spool")
    image_bytes = _png_bytes()
    spool_name = platform._write_task_spool("job_spool", image_bytes)
    spool_path = platform.DEVELOPER_SPOOL_ROOT / spool_name
    row = {
        "spool_path": spool_name,
        "spool_size": len(image_bytes),
        "request_sha256": __import__("hashlib").sha256(image_bytes).hexdigest(),
    }

    assert oct(platform.DEVELOPER_SPOOL_ROOT.stat().st_mode & 0o777) == "0o700"
    assert oct(spool_path.stat().st_mode & 0o777) == "0o600"
    assert platform._read_task_spool(row) == image_bytes

    spool_path.write_bytes(image_bytes[:-1] + b"x")
    with pytest.raises(platform.TaskSpoolError, match="SHA-256"):
        platform._read_task_spool(row)


def test_developer_request_reliably_enqueues_without_daemon_thread(client, monkeypatch, tmp_path):
    actor = {
        "id": 11,
        "user_id": 7,
        "account_uuid": "2a5f4e50-2216-4c45-b22c-232e156090d4",
        "scopes": ["image:fast"],
        "username": "developer",
        "phone": "13800000007",
        "openid": "openid-7",
    }
    inserted_params = []
    job_updates = []
    monkeypatch.setattr(platform, "DEVELOPER_SPOOL_ROOT", tmp_path / "spool")
    monkeypatch.setattr(platform, "_developer_key_required", lambda: (actor, None))
    monkeypatch.setattr(platform, "_ensure_developer_platform_tables", lambda: True)
    monkeypatch.setattr(platform, "_maybe_reconcile_expired_tasks", lambda: 0)
    monkeypatch.setattr(platform, "_idempotent_task", lambda *_: None)
    monkeypatch.setattr(platform, "_queue_capacity_error", lambda *_: None)
    monkeypatch.setattr(platform, "_reserve_task_daily_quota", lambda *_: (None, None, None))
    monkeypatch.setattr(platform, "_reserve_billing", lambda *_: {"status": "reserved"})
    monkeypatch.setattr(
        platform.admin_state,
        "create_detection_job",
        lambda *_args, **_kwargs: {"id": "job_reliable"},
    )
    monkeypatch.setattr(
        platform.admin_state,
        "update_detection_job",
        lambda task_id, payload: job_updates.append((task_id, payload)),
    )
    monkeypatch.setattr(platform.admin_state, "get_detection_job", lambda *_: None)
    monkeypatch.setattr(platform, "_reservation_payload", lambda *_: {"status": "reserved"})
    monkeypatch.setattr(
        platform,
        "_task_row_for_user",
        lambda *_: {
            "task_id": "job_reliable",
            "mode": "fast",
            "filename": "sample.png",
            "status": "queued",
        },
    )

    def execute(statement, params=None, fetch=True):
        normalized = " ".join(statement.split())
        if normalized.startswith("INSERT INTO developer_detection_tasks"):
            inserted_params.append(params)
            return 1
        if normalized.startswith("UPDATE developer_detection_tasks SET status = 'queued'"):
            return 1
        raise AssertionError(f"unexpected SQL: {normalized}")

    monkeypatch.setattr(platform, "excute_sql", execute)
    monkeypatch.setattr(
        platform.detection,
        "_start_background_job",
        lambda *_: (_ for _ in ()).throw(AssertionError("request thread dispatched work")),
    )

    response = client.post(
        "/api/openapi/v1/image-detections",
        data={"mode": "fast", "image": (BytesIO(_png_bytes()), "sample.png")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 202
    assert response.get_json()["status"] == "queued"
    assert (platform.DEVELOPER_SPOOL_ROOT / "job_reliable.upload").is_file()
    assert inserted_params[0][7] == "developer-job_reliable.png"
    context = __import__("json").loads(inserted_params[0][11])
    assert context["actor"]["account_uuid"] == actor["account_uuid"]
    assert context["user_info"]["account_uuid"] == actor["account_uuid"]
    assert job_updates[-1][1]["status"] == "queued"


def test_worker_executes_verified_spool_and_cleans_terminal_file(monkeypatch, tmp_path):
    account_uuid = "0db344ad-0282-43ba-a2a0-a8e46c3aca51"
    image_bytes = _png_bytes()
    monkeypatch.setattr(platform, "DEVELOPER_SPOOL_ROOT", tmp_path / "spool")
    spool_name = platform._write_task_spool("job_worker", image_bytes)
    actor = {"id": 5, "user_id": 9, "account_uuid": account_uuid}
    user_info = {
        "Userid": 9,
        "account_uuid": account_uuid,
        "username": "developer",
        "phone": "",
        "openid": "developer-9",
    }
    task = {
        "task_id": "job_worker",
        "user_id": 9,
        "key_id": 5,
        "mode": "fast",
        "filename": "sample.png",
        "mime_type": "image/png",
        "request_sha256": __import__("hashlib").sha256(image_bytes).hexdigest(),
        "spool_path": spool_name,
        "spool_size": len(image_bytes),
        "request_context_json": platform._request_context(actor, user_info),
        "lease_owner": "worker-lease",
    }
    observed = {}
    monkeypatch.setattr(platform.admin_state, "update_detection_job", lambda *_: None)
    monkeypatch.setattr(
        platform.detection,
        "_run_image_detection_payload",
        lambda payload, filename, mimetype, current_user, **_kwargs: (
            observed.update({"bytes": payload, "filename": filename, "user": current_user})
            or ({"status": "success", "result": {"itemid": 88}}, 200)
        ),
    )
    monkeypatch.setattr(platform, "_recover_task_effect", lambda *_: None)
    monkeypatch.setattr(platform, "_record_task_effect", lambda *_: True)
    monkeypatch.setattr(platform, "_finish_task", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        platform,
        "_task_terminal_row",
        lambda *_: {"status": "success", "spool_path": spool_name},
    )
    monkeypatch.setattr(platform, "excute_sql", lambda *_args, **_kwargs: 1)

    platform._run_openapi_job(task)

    assert observed["bytes"] == image_bytes
    assert observed["filename"] == "developer-job_worker.png"
    assert observed["user"]["account_uuid"] == account_uuid
    assert not (platform.DEVELOPER_SPOOL_ROOT / spool_name).exists()


def test_recovery_reuses_task_tagged_business_result_without_model_call(monkeypatch):
    account_uuid = "7b581f4f-e857-4010-9c9b-e3fe343e3f47"
    task = {
        "task_id": "job_recover",
        "mode": "fast",
        "filename": "portrait.png",
        "execution_filename": "developer-job_recover.png",
        "mime_type": "image/png",
    }
    user_info = {
        "Userid": 9,
        "account_uuid": account_uuid,
        "phone": "",
        "openid": "developer-9",
    }
    record = {
        "itemid": 88,
        "filename": "aabbccddeeff-developer-job_recover.png",
        "fake": 22.0,
        "detector_probability": 0.22,
        "aigc": "真实图像",
        "clarity": "高",
        "explantation": "模型判断为真实图像。",
        "file_size": "1 KB",
        "img_format": "PNG",
        "resolution": "8x8",
    }
    effect_updates = []

    def execute(statement, params=None, fetch=True):
        normalized = " ".join(statement.split())
        if normalized.startswith("SELECT status, effect_item_id, effect_result_json"):
            return [{"status": "running", "effect_item_id": None, "effect_result_json": None}]
        if normalized.startswith("UPDATE developer_detection_tasks SET effect_item_id"):
            effect_updates.append(params)
            return 1
        raise AssertionError(f"unexpected SQL: {normalized}")

    owner_calls = []
    monkeypatch.setattr(platform, "excute_sql", execute)
    monkeypatch.setattr(platform, "excute_detection_sql", lambda *_args, **_kwargs: [record])
    monkeypatch.setattr(
        platform.detection,
        "_detection_owner_where",
        lambda *args: owner_calls.append(args) or ("owner_account_uuid = %s", (args[3],)),
    )
    monkeypatch.setattr(platform.detection, "_metadata_for_item", lambda *_: {})
    monkeypatch.setattr(platform.detection, "_capture_evidence_for_metadata", lambda *_: {})
    monkeypatch.setattr(platform.detection, "_runtime_visible_watermark_for_item", lambda *_: None)
    monkeypatch.setattr(
        platform.detection,
        "_run_image_detection_payload",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("model reran")),
    )

    payload, status_code, recovered = platform._execute_or_recover_task(task, user_info, b"image")

    assert recovered is True
    assert status_code == 200
    assert payload["result"]["itemid"] == 88
    assert payload["result"]["filename"] == "portrait.png"
    assert effect_updates[0][0] == 88
    assert owner_calls and all(call[3] == account_uuid for call in owner_calls)


def test_recovery_never_claims_unowned_remote_result(monkeypatch):
    account_uuid = "6b43fa19-a507-458a-9256-c740572205ba"
    task = {
        "task_id": "job_unowned",
        "mode": "fast",
        "filename": "source.png",
        "execution_filename": "developer-job_unowned.png",
    }
    user_info = {
        "Userid": 9,
        "account_uuid": account_uuid,
        "phone": "13800000009",
        "openid": "openid-9",
    }
    unowned = {
        "itemid": 91,
        "filename": "abcdef123456-developer-job_unowned.png",
        "owner_account_uuid": None,
    }
    queries = []
    claims = []

    def detection_query(statement, params=None, fetch=True):
        queries.append((" ".join(statement.split()), params))
        return [] if len(queries) == 1 else [unowned]

    monkeypatch.setattr(platform, "excute_detection_sql", detection_query)
    monkeypatch.setattr(
        platform.detection,
        "_detection_owner_where",
        lambda _uid, phone, openid, owner="": (
            ("owner_account_uuid = %s", (owner,))
            if owner
            else ("phone = %s", (phone,))
        ),
    )
    monkeypatch.setattr(
        platform.detection,
        "claim_detection_record_owner",
        lambda *args: claims.append(args) or True,
    )

    assert platform._task_business_rows(task, user_info) == []
    assert len(queries) == 1
    assert claims == []


def test_incomplete_swarm_primary_result_is_not_mistaken_for_final_result(monkeypatch):
    task = {
        "task_id": "job_swarm_partial",
        "mode": "swarm",
        "filename": "source.png",
    }
    monkeypatch.setattr(
        platform,
        "excute_sql",
        lambda *_args, **_kwargs: [
            {"status": "running", "effect_item_id": None, "effect_result_json": None}
        ],
    )
    monkeypatch.setattr(
        platform,
        "_task_business_rows",
        lambda *_args, **_kwargs: [
            {"itemid": 92, "explantation": "主鉴伪模型完成，但专家复核尚未完成。"}
        ],
    )

    with pytest.raises(platform.TaskRecoveryError, match="incomplete Swarm primary result"):
        platform._recover_task_effect(task, {"Userid": 9})


def test_task_spool_lock_serializes_lease_handover(monkeypatch, tmp_path):
    monkeypatch.setattr(platform, "DEVELOPER_SPOOL_ROOT", tmp_path / "spool")
    spool_name = platform._write_task_spool("job_lock", _png_bytes())
    task = {"spool_path": spool_name}
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()

    def first_attempt():
        with platform._task_execution_lock(task):
            first_entered.set()
            assert release_first.wait(timeout=2)

    def second_attempt():
        assert first_entered.wait(timeout=2)
        with platform._task_execution_lock(task):
            second_entered.set()

    first = threading.Thread(target=first_attempt)
    second = threading.Thread(target=second_attempt)
    first.start()
    second.start()
    assert first_entered.wait(timeout=2)
    time.sleep(0.05)
    assert not second_entered.is_set()
    release_first.set()
    first.join(timeout=2)
    second.join(timeout=2)
    assert second_entered.is_set()


@pytest.mark.parametrize("reservation_status", [None, "released", "settled", "invalid"])
def test_active_task_with_nonreserved_reservation_is_terminalized(
    monkeypatch,
    reservation_status,
):
    store = _RecoveryStore(expired=False)
    store.task["status"] = "queued"
    if reservation_status is None:
        store.reservation = None
    else:
        store.reservation["status"] = reservation_status
    updates = []
    monkeypatch.setattr(platform, "get_db_connection", store.connection)
    monkeypatch.setattr(platform, "_update_job_cache", lambda *args: updates.append(args))

    assert platform._terminalize_anomalous_reservation("expired-task") is True
    assert store.task["status"] == "failed"
    assert updates[0][1]["status"] == "failed"
    if reservation_status is not None:
        assert store.reservation["status"] == reservation_status


def test_active_task_with_valid_reservation_is_not_terminalized(monkeypatch):
    store = _RecoveryStore(expired=False)
    store.task["status"] = "queued"
    monkeypatch.setattr(platform, "get_db_connection", store.connection)

    assert platform._terminalize_anomalous_reservation("expired-task") is False
    assert store.task["status"] == "queued"


def test_success_terminalization_and_billing_settlement_happen_once(monkeypatch):
    persisted = iter((1, 0))
    settlements = []
    monkeypatch.setattr(platform, "excute_sql", lambda *_args, **_kwargs: next(persisted))
    monkeypatch.setattr(platform, "_settle_billing", lambda task_id: settlements.append(task_id) or True)

    payload = {"status": "success", "result": {"itemid": 88}}
    assert platform._finish_task("task-once", {"id": 1}, "fast", payload, 200) is True
    assert platform._finish_task("task-once", {"id": 1}, "fast", payload, 200) is False
    assert settlements == ["task-once"]


def test_owned_detection_item_passes_immutable_account_uuid(monkeypatch):
    observed = []
    actor = {
        "user_id": 7,
        "phone": "13800000007",
        "openid": "openid-7",
        "account_uuid": "e7b858b5-cc0d-4f81-a194-ebad28da0a70",
    }
    monkeypatch.setattr(
        platform.detection,
        "_detection_owner_where",
        lambda *args: observed.append(args) or ("owner_account_uuid = %s", (args[3],)),
    )
    monkeypatch.setattr(platform, "excute_detection_sql", lambda *_: [])

    assert platform._owned_detection_item(actor, 3) is None
    assert observed == [(
        7,
        "13800000007",
        "openid-7",
        "e7b858b5-cc0d-4f81-a194-ebad28da0a70",
    )]


def test_queue_capacity_applies_task_and_spool_limits(monkeypatch):
    monkeypatch.setattr(platform, "DEVELOPER_TASK_MAX_PENDING", 3)
    monkeypatch.setattr(platform, "DEVELOPER_SPOOL_MAX_BYTES", 100)
    monkeypatch.setattr(
        platform,
        "excute_sql",
        lambda *_args, **_kwargs: [{"pending_count": 3, "pending_bytes": 25}],
    )
    assert "队列已满" in platform._queue_capacity_error(10)

    monkeypatch.setattr(
        platform,
        "excute_sql",
        lambda *_args, **_kwargs: [{"pending_count": 2, "pending_bytes": 95}],
    )
    assert "存储空间" in platform._queue_capacity_error(10)


def test_worker_removes_only_unreferenced_old_spool_files(monkeypatch, tmp_path):
    monkeypatch.setattr(platform, "DEVELOPER_SPOOL_ROOT", tmp_path / "spool")
    monkeypatch.setattr(platform, "DEVELOPER_SPOOL_ORPHAN_GRACE_SECONDS", 300)
    platform._ensure_spool_root()
    referenced = platform.DEVELOPER_SPOOL_ROOT / "referenced.upload"
    orphan = platform.DEVELOPER_SPOOL_ROOT / ".abandoned.tmp"
    recent = platform.DEVELOPER_SPOOL_ROOT / ".recent.tmp"
    for path in (referenced, orphan, recent):
        path.write_bytes(b"payload")
    old = time.time() - 600
    __import__("os").utime(referenced, (old, old))
    __import__("os").utime(orphan, (old, old))
    monkeypatch.setattr(
        platform,
        "excute_sql",
        lambda *_args, **_kwargs: [{"spool_path": referenced.name}],
    )

    assert platform._cleanup_orphan_spool_files() == 1
    assert referenced.exists()
    assert recent.exists()
    assert not orphan.exists()
