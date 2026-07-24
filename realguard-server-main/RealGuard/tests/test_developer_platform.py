from concurrent.futures import ThreadPoolExecutor
import copy
from contextlib import contextmanager, nullcontext
from io import BytesIO
import json
from datetime import date
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


def _allow_financial_admin(monkeypatch):
    actor = {
        "Userid": "admin:1",
        "adminId": 1,
        "username": "root",
        "phone": "13800000000",
        "role": "super_admin",
        "authType": "admin_account",
        "issuedAt": int(time.time()),
    }
    monkeypatch.setattr(platform, "_financial_admin_required", lambda permission: (actor, None))
    monkeypatch.setattr(platform, "_append_security_audit", lambda *args, **kwargs: "audit-event")
    return actor


def _animated_gif_bytes():
    output = BytesIO()
    frames = [Image.new("RGB", (2, 2), color) for color in ("white", "black")]
    frames[0].save(output, format="GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
    return output.getvalue()


def _multi_image_heif_bytes():
    from pillow_heif import from_pillow

    output = BytesIO()
    container = from_pillow(Image.new("RGB", (12, 8), "navy"))
    container.add_from_pillow(Image.new("RGB", (6, 4), "white"))
    container.save(output, quality=90)
    return output.getvalue()


def _multi_image_mpo_bytes():
    output = BytesIO()
    frames = [Image.new("RGB", (12, 8), color) for color in ("navy", "white")]
    frames[0].save(output, format="MPO", save_all=True, append_images=frames[1:])
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
        self.task_daily_reserved = 0
        self.task_daily_day = date(2026, 7, 19)
        self.daily_count = 0

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
        if normalized.startswith("SELECT free_total, free_used, balance_fen FROM developer_accounts"):
            self.row = {
                "free_total": self.store.account["free_total"],
                "free_used": self.store.account["free_used"],
                "balance_fen": self.store.account["balance_fen"],
            }
            return 1
        if normalized.startswith("SELECT balance_fen FROM developer_accounts"):
            self.row = {"balance_fen": self.store.account["balance_fen"]}
            return 1
        if normalized.startswith("SELECT user_id, key_id, mode, status"):
            reservation = self.store.reservations[params[0]]
            self.row = {
                "user_id": reservation["user_id"],
                "key_id": reservation["key_id"],
                "mode": reservation["mode"],
                "status": "success",
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "task_id": params[0],
                "daily_quota_reserved": self.store.task_daily_reserved,
                "daily_quota_day": self.store.task_daily_day,
            }
            return 1
        if normalized.startswith("SELECT day_bucket, daily_count"):
            self.row = {
                "day_bucket": self.store.task_daily_day,
                "daily_count": self.store.daily_count,
            }
            return 1
        if normalized.startswith("UPDATE developer_api_account_quota_usage SET daily_count"):
            if self.store.daily_count > 0:
                self.store.daily_count -= 1
                self.rowcount = 1
            return self.rowcount
        if normalized.startswith("UPDATE developer_detection_tasks SET daily_quota_reserved = 0"):
            if self.store.task_daily_reserved == 1:
                self.store.task_daily_reserved = 0
                self.rowcount = 1
            return self.rowcount
        if "SET free_reserved = free_reserved - 1, free_used = free_used + 1" in normalized:
            if self.store.account["free_reserved"] >= 1:
                self.store.account["free_reserved"] -= 1
                self.store.account["free_used"] += 1
                self.rowcount = 1
            return self.rowcount
        if normalized.startswith("UPDATE developer_accounts SET balance_reserved_fen = balance_reserved_fen -"):
            amount = params[0]
            if "balance_fen = balance_fen -" not in normalized:
                if self.store.account["balance_reserved_fen"] >= amount:
                    self.store.account["balance_reserved_fen"] -= amount
                    self.rowcount = 1
            elif (
                self.store.account["balance_reserved_fen"] >= amount
                and self.store.account["balance_fen"] >= amount
            ):
                self.store.account["balance_reserved_fen"] -= amount
                self.store.account["balance_fen"] -= amount
                self.rowcount = 1
            return self.rowcount
        if normalized.startswith("UPDATE developer_billing_reservations SET status = 'settled'"):
            if self.store.reservations[params[0]]["status"] == "reserved":
                self.store.reservations[params[0]]["status"] = "settled"
                self.rowcount = 1
            return self.rowcount
        if normalized.startswith("INSERT INTO developer_billing_ledger"):
            self.store.ledger.append(params)
            return 1
        if normalized.startswith("INSERT IGNORE INTO developer_usage_events"):
            self.rowcount = 1
            return self.rowcount
        if normalized.startswith("SELECT user_id, source, amount_fen, status"):
            row = self.store.reservations.get(params[0])
            self.row = dict(row) if row else None
            return int(bool(row))
        if normalized.startswith("SELECT user_id, key_id, mode, source, amount_fen, status"):
            row = self.store.reservations.get(params[0])
            self.row = dict(row) if row else None
            return int(bool(row))
        if normalized.startswith("UPDATE developer_accounts SET free_reserved = free_reserved - 1"):
            if self.store.account["free_reserved"] >= 1:
                self.store.account["free_reserved"] -= 1
                self.rowcount = 1
            return self.rowcount
        if normalized.startswith("UPDATE developer_billing_reservations SET status = 'released'"):
            self.store.reservations[params[0]]["status"] = "released"
            self.rowcount = 1
            return self.rowcount
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


def test_settlement_rejects_inconsistent_free_reservation(monkeypatch):
    store = _BillingStore(free_total=1)
    store.reservations["task-corrupt"] = {
        "task_id": "task-corrupt",
        "user_id": 1,
        "key_id": 8,
        "mode": "fast",
        "source": "free",
        "amount_fen": 0,
        "status": "reserved",
    }
    monkeypatch.setattr(platform, "_ensure_developer_platform_tables", lambda: True)
    monkeypatch.setattr(platform, "get_db_connection", store.connection)

    assert platform._settle_billing("task-corrupt") is False
    assert store.account["free_reserved"] == 0
    assert store.account["free_used"] == 0
    assert store.reservations["task-corrupt"]["status"] == "reserved"
    assert store.ledger == []


def test_successful_settlement_consumes_daily_reservation_marker_once(monkeypatch):
    store = _BillingStore(free_total=1)
    monkeypatch.setattr(platform, "_ensure_developer_platform_tables", lambda: True)
    monkeypatch.setattr(platform, "get_db_connection", store.connection)

    platform._reserve_billing(1, 8, "task-daily-success", "fast")
    store.task_daily_reserved = 1
    store.daily_count = 1

    assert platform._settle_billing("task-daily-success") is True
    assert store.task_daily_reserved == 0
    assert store.daily_count == 1
    assert platform._settle_billing("task-daily-success") is False
    assert store.daily_count == 1


@pytest.mark.parametrize(
    ("source", "amount_fen"),
    [("free", 0), ("balance", 25)],
)
def test_release_rejects_inconsistent_reserved_counter(monkeypatch, source, amount_fen):
    store = _BillingStore(free_total=1)
    store.account["free_reserved"] = 0
    store.account["balance_reserved_fen"] = 0
    store.reservations["task-corrupt-release"] = {
        "task_id": "task-corrupt-release",
        "user_id": 1,
        "key_id": 8,
        "mode": "fast",
        "source": source,
        "amount_fen": amount_fen,
        "status": "reserved",
    }
    monkeypatch.setattr(platform, "_ensure_developer_platform_tables", lambda: True)
    monkeypatch.setattr(platform, "get_db_connection", store.connection)

    assert platform._release_billing("task-corrupt-release") is False
    assert store.reservations["task-corrupt-release"]["status"] == "reserved"
    assert store.account["free_reserved"] == 0
    assert store.account["balance_reserved_fen"] == 0


def test_review_only_release_restores_daily_quota_in_same_transaction(monkeypatch):
    store = _BillingStore(free_total=5)
    store.account["free_reserved"] = 1
    store.reservations["task-review"] = {
        "task_id": "task-review",
        "user_id": 1,
        "key_id": 8,
        "mode": "fast",
        "source": "free",
        "amount_fen": 0,
        "status": "reserved",
    }
    store.task_daily_reserved = 1
    store.daily_count = 1
    monkeypatch.setattr(platform, "_ensure_developer_platform_tables", lambda: True)
    monkeypatch.setattr(platform, "get_db_connection", store.connection)

    assert platform._release_billing(
        "task-review",
        "review only",
        audit_entry_type="detection_review_only",
    ) is True

    assert store.reservations["task-review"]["status"] == "released"
    assert store.account["free_reserved"] == 0
    assert store.task_daily_reserved == 0
    # A completed review-only request is non-billable, but still counts toward
    # abuse-control daily request volume.
    assert store.daily_count == 1


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
            "result_json": '{"status":"success","decisionStatus":"verdict","billable":true,"result":{"itemid":12,"final_label":"真实图像","decisionStatus":"verdict","billable":true}}',
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


def test_visual_review_task_is_low_priority_and_reuses_parent_owner(monkeypatch):
    inserts = []
    monkeypatch.setattr(platform, "_ensure_developer_platform_tables", lambda: True)
    monkeypatch.setattr(platform, "_write_web_task_spool", lambda job_id, payload: f"{job_id}.upload")

    def execute(statement, params=None, fetch=True):
        normalized = " ".join(statement.split())
        if normalized.startswith("SELECT owner_type, owner_key, request_context_json"):
            return [{
                "owner_type": "account",
                "owner_key": "account-7",
                "request_context_json": '{"actor":{"account_uuid":"account-7"}}',
            }]
        if normalized.startswith("INSERT INTO web_detection_tasks"):
            inserts.append((normalized, params))
            return 1
        raise AssertionError(f"unexpected SQL: {normalized}")

    monkeypatch.setattr(platform, "excute_sql", execute)
    child_job_id = platform._enqueue_visual_review_task(
        {
            "job_id": "job_parent",
            "filename": "sample.png",
            "mime_type": "image/png",
        },
        b"image-bytes",
    )

    statement, params = inserts[0]
    assert child_job_id.startswith("job_")
    assert params[1] == platform.VISUAL_REVIEW_MODE
    assert params[8:10] == ("account", "account-7")
    assert json.loads(params[-1]) == {"parentJobId": "job_parent"}
    assert "DATE_ADD(NOW(6), INTERVAL 1 SECOND)" in statement


def test_visual_review_update_never_overwrites_primary_verdict(monkeypatch):
    updates = []
    parent_payload = {
        "status": "success",
        "result": {
            "itemid": 42,
            "final_label": "真实图像",
            "probability": 0.12,
        },
    }

    def execute(statement, params=None, fetch=True):
        normalized = " ".join(statement.split())
        if normalized.startswith("SELECT status, result_json"):
            return [{"status": "success", "result_json": json.dumps(parent_payload)}]
        if normalized.startswith("UPDATE web_detection_tasks SET result_json"):
            updates.append(json.loads(params[0]))
            return 1
        raise AssertionError(f"unexpected SQL: {normalized}")

    monkeypatch.setattr(platform, "excute_sql", execute)
    monkeypatch.setattr(platform.admin_state, "get_detection_job", lambda job_id: None)

    assert platform._update_parent_visual_review(
        "job_parent",
        {
            "status": "success",
            "nonAuthoritative": True,
            "verdict": "AI生成图像",
            "evidence": ["视觉模型发现一项可疑线索"],
        },
    ) is True

    result = updates[0]["result"]
    assert result["final_label"] == "真实图像"
    assert result["probability"] == pytest.approx(0.12)
    assert result["visualReview"]["nonAuthoritative"] is True


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


def test_developer_detection_requires_idempotency_key(client, monkeypatch):
    actor = {
        "id": 11,
        "user_id": 1,
        "account_uuid": "00000000-0000-4000-8000-000000000001",
        "scopes": ["image:fast"],
    }
    monkeypatch.setattr(platform, "_developer_key_required", lambda: (actor, None))

    response = client.post(
        "/api/openapi/v1/image-detections",
        data={"mode": "fast", "image": (BytesIO(_png_bytes()), "sample.png")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "idempotency_key_required"


def test_concurrent_idempotent_detection_creates_and_reserves_once(client, monkeypatch, tmp_path):
    actor = {
        "id": 11,
        "user_id": 7,
        "account_uuid": "2a5f4e50-2216-4c45-b22c-232e156090d4",
        "scopes": ["image:fast"],
        "username": "developer",
    }
    rows = {}
    admission_lock = threading.Lock()
    created_jobs = []
    daily_reservations = []
    billing_reservations = []
    monkeypatch.setattr(platform, "DEVELOPER_SPOOL_ROOT", tmp_path / "spool")
    monkeypatch.setattr(platform, "_developer_key_required", lambda: (actor, None))
    monkeypatch.setattr(platform, "_ensure_developer_platform_tables", lambda: True)
    monkeypatch.setattr(platform, "_maybe_reconcile_expired_tasks", lambda: 0)
    monkeypatch.setattr(platform, "_queue_capacity_error", lambda *_: None)

    @contextmanager
    def submission_guard(*_args):
        with admission_lock:
            yield

    monkeypatch.setattr(platform, "_queue_submission_guard", submission_guard)
    monkeypatch.setattr(
        platform,
        "_idempotent_task",
        lambda _user_id, _account_uuid, key: copy.deepcopy(rows.get(key)),
    )

    def create_job(*_args, **_kwargs):
        created_jobs.append("job-idempotent")
        return {"id": "job-idempotent"}

    monkeypatch.setattr(platform.admin_state, "create_detection_job", create_job)
    monkeypatch.setattr(platform.admin_state, "update_detection_job", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        platform,
        "_reserve_task_daily_quota",
        lambda _actor, task_id: daily_reservations.append(task_id) or (None, None, None),
    )
    monkeypatch.setattr(
        platform,
        "_reserve_billing",
        lambda _user_id, _key_id, task_id, _mode: billing_reservations.append(task_id)
        or {"status": "reserved"},
    )
    monkeypatch.setattr(
        platform,
        "_task_row_for_user",
        lambda *_args: copy.deepcopy(rows["same-network-request"]),
    )
    monkeypatch.setattr(
        platform,
        "_task_payload",
        lambda row: {"id": row["task_id"], "status": row["status"]},
    )

    def execute(statement, params=None, fetch=True):
        normalized = " ".join(statement.split())
        if normalized.startswith("INSERT INTO developer_detection_tasks"):
            rows[params[12]] = {
                "task_id": params[0],
                "mode": params[4],
                "request_sha256": params[8],
                "status": "preparing",
            }
            return 1
        if normalized.startswith("UPDATE developer_detection_tasks SET status = 'queued'"):
            rows["same-network-request"]["status"] = "queued"
            return 1
        raise AssertionError(f"unexpected SQL: {normalized}")

    monkeypatch.setattr(platform, "excute_sql", execute)

    def submit():
        with client.application.test_client() as thread_client:
            return thread_client.post(
                "/api/openapi/v1/image-detections",
                headers={"Idempotency-Key": "same-network-request"},
                data={"mode": "fast", "image": (BytesIO(_png_bytes()), "sample.png")},
                content_type="multipart/form-data",
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: submit(), range(2)))

    assert sorted(response.status_code for response in responses) == [200, 202]
    assert {response.get_json()["id"] for response in responses} == {"job-idempotent"}
    assert created_jobs == ["job-idempotent"]
    assert daily_reservations == ["job-idempotent"]
    assert billing_reservations == ["job-idempotent"]


def test_developer_api_rejects_images_above_pixel_limit(client, monkeypatch):
    actor = {"id": 11, "user_id": 1, "account_uuid": "00000000-0000-4000-8000-000000000001", "scopes": ["image:fast"], "phone": "13800000000"}
    monkeypatch.setattr(platform, "_developer_key_required", lambda: (actor, None))
    monkeypatch.setattr(platform, "_ensure_developer_platform_tables", lambda: True)
    monkeypatch.setattr(platform, "_maybe_reconcile_expired_tasks", lambda: 0)
    monkeypatch.setattr(platform, "DEVELOPER_MAX_IMAGE_PIXELS", 32)

    response = client.post(
        "/api/openapi/v1/image-detections",
        headers={"Idempotency-Key": "pixel-limit-request"},
        data={"mode": "fast", "image": (BytesIO(_png_bytes()), "sample.png")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 413
    assert response.get_json()["error"]["code"] == "image_pixels_too_large"


def test_developer_image_validation_accepts_animated_gif():
    dimensions, error = platform._validate_image(_animated_gif_bytes())

    assert error is None
    assert dimensions == {"width": 2, "height": 2}


def test_developer_image_validation_accepts_multi_image_heif():
    dimensions, error = platform._validate_image(_multi_image_heif_bytes())

    assert error is None
    assert dimensions == {"width": 12, "height": 8}


def test_developer_image_validation_accepts_multi_image_mpo():
    dimensions, error = platform._validate_image(_multi_image_mpo_bytes())

    assert error is None
    assert dimensions == {"width": 12, "height": 8}


def test_developer_upload_requires_content_length_before_multipart_parsing(client, monkeypatch):
    actor = {
        "id": 11,
        "user_id": 1,
        "account_uuid": "00000000-0000-4000-8000-000000000001",
        "scopes": ["image:fast"],
    }
    monkeypatch.setattr(platform, "_developer_key_required", lambda: (actor, None))

    with client.application.test_request_context(
        "/api/openapi/v1/image-detections",
        method="POST",
    ):
        response, status = platform.create_image_detection()

    assert status == 411
    assert response.get_json()["error"]["code"] == "length_required"


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


def test_review_only_success_releases_reservation_without_charging(monkeypatch):
    settled = []
    released = []
    monkeypatch.setattr(platform, "_settle_billing", lambda task_id: settled.append(task_id) or True)
    monkeypatch.setattr(
        platform,
        "_release_billing",
        lambda task_id, note, audit_entry_type=None: released.append(
            (task_id, note, audit_entry_type)
        ) or True,
    )
    monkeypatch.setattr(platform, "excute_sql", lambda *_args, **_kwargs: 1)

    payload = {
        "status": "success",
        "result": {
            "itemid": 7,
            "final_label": "需人工复核",
            "reviewRequired": True,
            "modelDecisionReady": False,
        },
    }
    assert platform._finish_task("review-task", {}, "fast", payload, 200) is True

    assert settled == []
    assert released == [
        (
            "review-task",
            "检测仅形成复核态结论，不消耗调用额度",
            "detection_review_only",
        )
    ]


def test_public_result_marks_review_only_as_non_billable():
    payload = platform._public_result_payload(
        {
            "status": "success",
            "result": {
                "itemid": 8,
                "reviewRequired": True,
                "modelDecisionReady": False,
            },
        },
        "fast",
    )

    assert payload["decisionStatus"] == "review_only"
    assert payload["billable"] is False
    assert payload["result"]["decisionStatus"] == "review_only"
    assert payload["result"]["billable"] is False
    assert payload["result"]["probability"] is None
    assert payload["result"]["detector_probability"] is None
    assert payload["result"]["confidence"] == "低"


def test_public_result_requires_explicit_boolean_true_to_bill():
    missing = object()
    for value in (missing, None, False, "true", 1):
        result = {"itemid": 8, "decisionStatus": "verdict"}
        if value is not missing:
            result["billable"] = value
        payload = platform._public_result_payload({"status": "success", "result": result}, "fast")
        assert payload["decisionStatus"] == "review_only"
        assert payload["billable"] is False
        assert payload["result"]["billable"] is False

    billable = platform._public_result_payload(
        {
            "status": "success",
            "result": {"itemid": 8, "decisionStatus": "verdict", "billable": True},
        },
        "fast",
    )
    assert billable["decisionStatus"] == "verdict"
    assert billable["billable"] is True


def test_usage_and_ledger_storage_failures_never_look_like_zero_usage(client, monkeypatch):
    monkeypatch.setattr(
        platform,
        "_developer_usage_from_v1",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("database unavailable")),
    )
    with pytest.raises(platform.BillingError) as usage_error:
        platform._developer_usage(7, 30)
    assert usage_error.value.code == "usage_storage_unavailable"

    monkeypatch.setattr(platform, "excute_sql", lambda *args, **kwargs: None)
    with pytest.raises(platform.BillingError):
        platform._mode_summary(7, 30)
    with pytest.raises(platform.BillingError):
        platform._recent_tasks(7, "account-7")

    monkeypatch.setattr(
        platform,
        "_auth_required",
        lambda: ({"Userid": 7, "account_uuid": "account-7"}, None),
    )
    monkeypatch.setattr(platform, "_ensure_developer_account", lambda user_id: True)
    with client.application.test_request_context("/api/developer/ledger"):
        response, status = platform.developer_ledger()
    assert status == 503
    assert response.get_json()["error"]["code"] == "ledger_storage_unavailable"
    assert response.headers["X-Request-Id"]


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
    monkeypatch.setattr(
        platform,
        "_task_billing_outcome_for_id",
        lambda *_args, **_kwargs: ("verdict", True, "settled"),
    )
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
    monkeypatch.setattr(
        platform,
        "_task_billing_outcome_for_id",
        lambda *_args, **_kwargs: ("verdict", True, "settled"),
    )
    monkeypatch.setattr(platform, "_settle_billing", lambda task_id: False)
    monkeypatch.setattr(platform, "_reservation_status_strict", lambda task_id: "settled")
    monkeypatch.setattr(platform.admin_state, "update_detection_job", lambda *args, **kwargs: None)

    assert platform._reconcile_success_reservation("success-task") is False


def test_success_reservation_reconciliation_surfaces_still_reserved_failure(monkeypatch):
    monkeypatch.setattr(
        platform,
        "_task_billing_outcome_for_id",
        lambda *_args, **_kwargs: ("verdict", True, "settled"),
    )
    monkeypatch.setattr(platform, "_settle_billing", lambda task_id: False)
    monkeypatch.setattr(platform, "_reservation_status_strict", lambda task_id: "reserved")

    with pytest.raises(platform.TaskRecoveryError, match="remains reserved"):
        platform._reconcile_success_reservation("success-task")


def test_review_only_reconciliation_accepts_released_reservation(monkeypatch):
    released = []
    monkeypatch.setattr(
        platform,
        "_task_billing_outcome_for_id",
        lambda *_args, **_kwargs: ("review_only", False, "released"),
    )
    monkeypatch.setattr(
        platform,
        "_release_billing",
        lambda task_id, note, audit_entry_type=None: released.append(
            (task_id, audit_entry_type)
        ) or True,
    )
    monkeypatch.setattr(platform.admin_state, "update_detection_job", lambda *_args, **_kwargs: None)

    assert platform._reconcile_success_reservation("review-task") is True
    assert released == [("review-task", "detection_review_only")]


def test_review_only_reconciliation_reverses_legacy_settlement(monkeypatch):
    reversed_tasks = []
    monkeypatch.setattr(
        platform,
        "_task_billing_outcome_for_id",
        lambda *_args, **_kwargs: ("review_only", False, "released"),
    )
    monkeypatch.setattr(platform, "_release_billing", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(platform, "_reservation_status_strict", lambda _task_id: "settled")
    monkeypatch.setattr(
        platform,
        "_reverse_settled_review_only",
        lambda task_id: reversed_tasks.append(task_id) or True,
    )
    monkeypatch.setattr(platform.admin_state, "update_detection_job", lambda *_args, **_kwargs: None)

    assert platform._reconcile_success_reservation("legacy-review-task") is True
    assert reversed_tasks == ["legacy-review-task"]


def test_success_artifacts_are_blocked_until_billing_is_settled(client, monkeypatch):
    actor = {
        "user_id": 7,
        "account_uuid": "00000000-0000-4000-8000-000000000007",
        "scopes": "image:fast,reports",
    }
    row = {
        "task_id": "task-unsettled",
        "mode": "fast",
        "status": "success",
        "result_item_id": 42,
    }
    owned_lookups = []
    monkeypatch.setattr(platform, "_developer_key_required", lambda: (actor, None))
    monkeypatch.setattr(platform, "_task_row_for_user", lambda *_args: row)
    monkeypatch.setattr(platform, "_reservation_status_strict", lambda _task_id: "reserved")
    monkeypatch.setattr(
        platform,
        "_reconcile_success_reservation",
        lambda task_id, *_args: (_ for _ in ()).throw(
            platform.TaskRecoveryError(f"successful task {task_id} billing settlement remains reserved")
        ),
    )
    monkeypatch.setattr(
        platform,
        "_owned_detection_item",
        lambda *args: owned_lookups.append(args),
    )

    report = client.get("/api/openapi/v1/image-detections/task-unsettled/report")
    media = client.get("/api/openapi/v1/image-detections/task-unsettled/media")

    assert report.status_code == 503
    assert media.status_code == 503
    assert report.headers["Retry-After"] == "5"
    assert media.headers["Retry-After"] == "5"
    assert report.get_json()["error"]["code"] == "task_recovery_unavailable"
    assert media.get_json()["error"]["code"] == "task_recovery_unavailable"
    assert owned_lookups == []


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
            self.rows = (
                [{"task_id": "success-task"}]
                if "task.status = 'success'" in normalized and "JSON_VALID(task.result_json) = 0" not in normalized
                else []
            )

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


def test_openapi_auth_error_uses_stable_error_envelope(client, monkeypatch):
    def denied():
        response = platform.jsonify({
            "status": "error",
            "code": "rate_limit_exceeded",
            "message": "请求过于频繁",
        })
        response.headers["Retry-After"] = "12"
        return None, (response, 429)

    monkeypatch.setattr(platform, "_developer_key_required", denied)

    with client.application.test_request_context(
        "/api/openapi/v1/image-detections/task-1",
        headers={"X-Request-Id": "client-request-1"},
    ):
        actor, error = platform._openapi_key_required()

    response, status = error
    assert actor is None
    assert status == 429
    assert response.get_json() == {
        "error": {
            "code": "rate_limit_exceeded",
            "message": "请求过于频繁",
            "requestId": "client-request-1",
        },
    }
    assert response.headers["Retry-After"] == "12"
    assert response.headers["X-Request-Id"] == "client-request-1"


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
    operation_results = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, sql, params=None):
            normalized = " ".join(sql.split())
            if normalized.startswith("INSERT INTO developer_admin_operations"):
                return 1
            if normalized.startswith("SELECT user_id, status, free_total"):
                return 1
            if normalized.startswith("UPDATE developer_accounts SET free_total"):
                account["free_total"] = params[0]
                return 1
            if normalized.startswith("INSERT INTO developer_billing_ledger"):
                ledger.append(params)
                return 1
            if normalized.startswith("INSERT INTO admin_audit_logs"):
                audits.append(params)
                return 1
            if normalized.startswith("INSERT INTO developer_admin_operation_results"):
                operation_results.append(params)
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

    _allow_financial_admin(monkeypatch)
    monkeypatch.setattr(platform, "_ensure_developer_account", lambda user_id: True)
    monkeypatch.setattr(platform, "get_db_connection", Connection)
    monkeypatch.setattr(platform, "excute_sql", lambda *args, **kwargs: [{"Userid": 7}])

    with client.application.test_request_context(json={
        "remainingCalls": 250,
        "note": "测试额度",
        "operationId": "quota-exact-remaining-001",
    }):
        response = platform.admin_set_developer_quota(7)

    payload = response.get_json()
    assert payload["status"] == "success"
    assert payload["account"]["freeRemaining"] == 250
    assert payload["account"]["freeUsed"] == 37
    assert payload["account"]["freeReserved"] == 2
    assert account["free_total"] == 289
    assert ledger == [(
        7,
        "quota-exact-remaining-001",
        "admin-quota:quota-exact-remaining-001",
        189,
        252,
        0,
        "测试额度",
    )]
    assert audits[0][3] == "developer.account.quota.set"
    assert json.loads(audits[0][7])["operationId"] == "quota-exact-remaining-001"
    assert json.loads(operation_results[0][2])["account"]["freeRemaining"] == 250


def test_admin_quota_set_requires_operation_id(client, monkeypatch):
    _allow_financial_admin(monkeypatch)

    with client.application.test_request_context(json={"remainingCalls": 250}):
        response, status = platform.admin_set_developer_quota(7)

    assert status == 400
    assert "operationId" in response.get_json()["message"]


def _install_quota_operation_store(monkeypatch, account):
    lock = threading.RLock()
    operations = {}
    operation_results = {}
    ledger = []

    class Cursor:
        current = None

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, sql, params=None):
            normalized = " ".join(sql.split())
            if normalized.startswith("INSERT INTO developer_admin_operations"):
                operation_id, user_id, fingerprint = params
                if operation_id in operations:
                    raise platform.pymysql.err.IntegrityError(1062, "duplicate operation")
                operations[operation_id] = {
                    "operation_type": "quota_set",
                    "user_id": user_id,
                    "request_sha256": fingerprint,
                }
                return 1
            if normalized.startswith("SELECT user_id, status, free_total"):
                self.current = dict(account)
                return 1
            if normalized.startswith("UPDATE developer_accounts SET free_total"):
                account["free_total"] = params[0]
                return 1
            if normalized.startswith("INSERT INTO developer_billing_ledger"):
                ledger.append(params)
                return 1
            if normalized.startswith("INSERT INTO admin_audit_logs"):
                return 1
            if normalized.startswith("INSERT INTO developer_admin_operation_results"):
                operation_id, status_code, response_json = params
                operation_results[operation_id] = {
                    "status_code": status_code,
                    "response_json": response_json,
                }
                return 1
            raise AssertionError(f"unexpected SQL: {normalized}")

        def fetchone(self):
            return dict(self.current) if self.current is not None else None

    class Connection:
        locked = False

        def begin(self):
            lock.acquire()
            self.locked = True

        def cursor(self):
            return Cursor()

        def _release(self):
            if self.locked:
                self.locked = False
                lock.release()

        def commit(self):
            self._release()

        def rollback(self):
            self._release()

        def close(self):
            self._release()

    def execute(statement, params=None, fetch=True):
        normalized = " ".join(statement.split())
        if normalized.startswith("SELECT Userid FROM user"):
            return [{"Userid": 7}]
        if normalized.startswith("SELECT operation_type, user_id, request_sha256"):
            operation = operations.get(params[0])
            return [dict(operation)] if operation else []
        if normalized.startswith("SELECT status_code, response_json"):
            result = operation_results.get(params[0])
            return [dict(result)] if result else []
        raise AssertionError(f"unexpected SQL: {normalized}")

    _allow_financial_admin(monkeypatch)
    monkeypatch.setattr(platform, "_ensure_developer_account", lambda user_id: True)
    monkeypatch.setattr(platform, "_account_row", lambda user_id: dict(account))
    monkeypatch.setattr(platform, "get_db_connection", Connection)
    monkeypatch.setattr(platform, "excute_sql", execute)
    return operations, ledger


def test_quota_operation_replay_does_not_overwrite_calls_consumed_after_response_loss(
    client,
    monkeypatch,
):
    account = {
        "user_id": 7,
        "status": "active",
        "free_total": 100,
        "free_used": 20,
        "free_reserved": 0,
        "balance_fen": 0,
        "balance_reserved_fen": 0,
        "created_at": None,
        "updated_at": None,
    }
    operations, ledger = _install_quota_operation_store(monkeypatch, account)
    payload = {
        "remainingCalls": 250,
        "note": "response may be lost",
        "operationId": "quota-response-loss-001",
    }

    with client.application.test_request_context(json=payload):
        first = platform.admin_set_developer_quota(7)
    assert first.get_json()["idempotentReplay"] is False
    assert account["free_total"] == 270

    account["free_used"] += 11
    with client.application.test_request_context(json=payload):
        replay = platform.admin_set_developer_quota(7)

    assert replay.get_json()["idempotentReplay"] is True
    assert replay.get_json()["account"]["freeRemaining"] == 250
    assert account["free_total"] == 270
    assert len(operations) == 1
    assert len(ledger) == 1


def test_concurrent_quota_operation_mutates_ledger_once(client, monkeypatch):
    account = {
        "user_id": 7,
        "status": "active",
        "free_total": 100,
        "free_used": 0,
        "free_reserved": 0,
        "balance_fen": 0,
        "balance_reserved_fen": 0,
        "created_at": None,
        "updated_at": None,
    }
    operations, ledger = _install_quota_operation_store(monkeypatch, account)
    payload = {
        "remainingCalls": 250,
        "note": "concurrent retry",
        "operationId": "quota-concurrent-retry-001",
    }

    def submit():
        with client.application.test_request_context(json=payload):
            return platform.admin_set_developer_quota(7)

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: submit(), range(2)))

    assert sorted(response.get_json()["idempotentReplay"] for response in responses) == [False, True]
    assert account["free_total"] == 250
    assert len(operations) == 1
    assert len(ledger) == 1


def test_quota_operation_id_rejects_reuse_for_different_target(client, monkeypatch):
    account = {
        "user_id": 7,
        "status": "active",
        "free_total": 100,
        "free_used": 0,
        "free_reserved": 0,
        "balance_fen": 0,
        "balance_reserved_fen": 0,
        "created_at": None,
        "updated_at": None,
    }
    _install_quota_operation_store(monkeypatch, account)
    operation_id = "quota-conflict-retry-001"

    with client.application.test_request_context(
        json={"remainingCalls": 250, "operationId": operation_id}
    ):
        first = platform.admin_set_developer_quota(7)
    with client.application.test_request_context(
        json={"remainingCalls": 300, "operationId": operation_id}
    ):
        conflict, status = platform.admin_set_developer_quota(7)

    assert first.get_json()["status"] == "success"
    assert status == 409
    assert account["free_total"] == 250


def test_request_rate_quota_uses_cas_idempotency_and_transactional_audit(client, monkeypatch):
    quota = {
        "daily_limit": 100,
        "rate_limit_per_minute": 10,
        "scopes": "",
        "notes": "old",
    }
    operations = []
    audits = []
    results = []

    class Cursor:
        current = None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params=None):
            normalized = " ".join(sql.split())
            if normalized.startswith("SELECT user_id FROM developer_api_keys"):
                self.current = {"user_id": 7}
            elif normalized.startswith("SELECT Userid FROM `user`"):
                self.current = {"Userid": 7}
            elif normalized.startswith("SELECT id, user_id FROM developer_api_keys"):
                self.current = {"id": 4, "user_id": 7}
            elif normalized.startswith("INSERT INTO developer_admin_operations"):
                operations.append(params)
            elif normalized.startswith("SELECT daily_limit, rate_limit_per_minute"):
                self.current = dict(quota)
            elif normalized.startswith("INSERT INTO developer_api_account_quotas"):
                quota.update({
                    "daily_limit": params[1],
                    "rate_limit_per_minute": params[2],
                    "scopes": params[3],
                    "notes": params[4],
                })
            elif normalized.startswith("INSERT INTO admin_audit_logs"):
                audits.append(params)
            elif normalized.startswith("INSERT INTO developer_admin_operation_results"):
                results.append(params)
            else:
                raise AssertionError(f"unexpected SQL: {normalized}")

        def fetchone(self):
            return dict(self.current) if self.current is not None else None

    class Connection:
        committed = False

        def begin(self):
            return None

        def cursor(self):
            return Cursor()

        def commit(self):
            self.committed = True

        def rollback(self):
            raise AssertionError("valid quota CAS must not roll back")

        def close(self):
            return None

    connection = Connection()
    _allow_financial_admin(monkeypatch)
    monkeypatch.setattr(platform, "_ensure_developer_platform_tables", lambda: True)
    monkeypatch.setattr(platform.admin_state, "ensure_api_key_quota_storage", lambda: True)
    monkeypatch.setattr(platform, "get_db_connection", lambda: connection)

    with client.application.test_request_context(json={
        "dailyLimit": 200,
        "rateLimitPerMinute": 20,
        "expectedDailyLimit": 100,
        "expectedRateLimitPerMinute": 10,
        "operationId": "request-quota-cas-001",
        "note": "capacity approved",
    }):
        response = platform.admin_update_request_quota(4)

    payload = response.get_json()
    assert payload["status"] == "success"
    assert payload["quota"]["dailyLimit"] == 200
    assert payload["quota"]["rateLimitPerMinute"] == 20
    assert connection.committed is True
    assert len(operations) == len(audits) == len(results) == 1
    assert operations[0][0] == "request-quota-cas-001"


def test_request_rate_quota_rejects_stale_expected_values(client, monkeypatch):
    class Cursor:
        current = None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params=None):
            normalized = " ".join(sql.split())
            if normalized.startswith("SELECT user_id FROM developer_api_keys"):
                self.current = {"user_id": 7}
            elif normalized.startswith("SELECT Userid FROM `user`"):
                self.current = {"Userid": 7}
            elif normalized.startswith("SELECT id, user_id FROM developer_api_keys"):
                self.current = {"id": 4, "user_id": 7}
            elif normalized.startswith("INSERT INTO developer_admin_operations"):
                return None
            elif normalized.startswith("SELECT daily_limit, rate_limit_per_minute"):
                self.current = {
                    "daily_limit": 150,
                    "rate_limit_per_minute": 10,
                    "scopes": "",
                    "notes": "changed",
                }
            else:
                raise AssertionError(f"unexpected SQL: {normalized}")

        def fetchone(self):
            return dict(self.current)

    class Connection:
        rolled_back = False

        def begin(self):
            return None

        def cursor(self):
            return Cursor()

        def commit(self):
            raise AssertionError("stale CAS must not commit")

        def rollback(self):
            self.rolled_back = True

        def close(self):
            return None

    connection = Connection()
    _allow_financial_admin(monkeypatch)
    monkeypatch.setattr(platform, "_ensure_developer_platform_tables", lambda: True)
    monkeypatch.setattr(platform.admin_state, "ensure_api_key_quota_storage", lambda: True)
    monkeypatch.setattr(platform, "get_db_connection", lambda: connection)

    with client.application.test_request_context(json={
        "dailyLimit": 200,
        "rateLimitPerMinute": 20,
        "expectedDailyLimit": 100,
        "expectedRateLimitPerMinute": 10,
        "operationId": "request-quota-stale-001",
    }):
        response, status = platform.admin_update_request_quota(4)

    assert status == 409
    assert response.get_json()["error"]["code"] == "quota_conflict"
    assert connection.rolled_back is True


def test_admin_account_adjustment_requires_idempotency_key(client, monkeypatch):
    _allow_financial_admin(monkeypatch)

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
    operation_results = []
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
            if normalized.startswith("SELECT user_id, status, free_total"):
                self.current = dict(account)
                return 1
            if normalized.startswith("UPDATE developer_accounts SET free_total"):
                account["free_total"], account["balance_fen"] = params[:2]
                return 1
            if normalized.startswith("INSERT INTO developer_billing_ledger"):
                ledger.append(params)
                return 1
            if normalized.startswith("INSERT INTO admin_audit_logs"):
                audits.append(params)
                return 1
            if normalized.startswith("INSERT INTO developer_admin_operation_results"):
                operation_results.append(params)
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

    _allow_financial_admin(monkeypatch)
    monkeypatch.setattr(platform, "_ensure_developer_account", lambda user_id: True)
    monkeypatch.setattr(platform, "_account_row", lambda user_id: dict(account))
    monkeypatch.setattr(platform, "get_db_connection", Connection)
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
    assert len(operation_results) == 1
    assert audits[0][3] == "developer.account.adjust"
    assert json.loads(audits[0][7])["operationId"] == payload["operationId"]


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
        if normalized.startswith("SELECT status_code, response_json"):
            return [{
                "status_code": 200,
                "response_json": json.dumps({
                    "status": "success",
                    "account": platform._account_payload(account),
                    "operationId": payload["operationId"],
                    "idempotentReplay": False,
                }),
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
    _allow_financial_admin(monkeypatch)
    monkeypatch.setattr(platform, "_ensure_developer_account", lambda user_id: True)
    monkeypatch.setattr(platform, "_account_row", lambda user_id: dict(account))
    monkeypatch.setattr(platform, "get_db_connection", Connection)
    monkeypatch.setattr(platform, "excute_sql", execute)

    with client.application.test_request_context(json=payload):
        response = platform.admin_adjust_developer_account(7)

    body = response.get_json()
    assert body["status"] == "success"
    assert body["idempotentReplay"] is True
    assert body["operationId"] == payload["operationId"]
    assert body["account"]["freeTotal"] == 110
    assert audits == []


def test_financial_admin_requires_recent_named_login(client, monkeypatch):
    now = int(time.time())
    monkeypatch.setattr(platform, "DEVELOPER_FINANCIAL_REAUTH_SECONDS", 300)
    monkeypatch.setattr(
        platform,
        "_admin_required",
        lambda permission: ({
            "Userid": "admin:7",
            "adminId": 7,
            "authType": "admin_account",
            "issuedAt": now - 301,
        }, None),
    )

    with client.application.test_request_context():
        actor, error = platform._financial_admin_required("billing.adjust")

    assert actor is None
    response, status = error
    assert status == 428
    assert response.get_json()["code"] == "reauthentication_required"


def test_pricing_rejects_string_boolean_before_database_write(client, monkeypatch):
    _allow_financial_admin(monkeypatch)
    monkeypatch.setattr(platform, "_ensure_developer_platform_tables", lambda: True)

    with client.application.test_request_context(json={
        "mode": "fast",
        "unitPriceFen": 5,
        "enabled": "false",
    }):
        response, status = platform.admin_update_developer_pricing()

    assert status == 400
    assert "布尔值" in response.get_json()["message"]


def test_financial_mutations_reject_coerced_integer_values(client, monkeypatch):
    _allow_financial_admin(monkeypatch)
    monkeypatch.setattr(platform, "_ensure_developer_platform_tables", lambda: True)
    monkeypatch.setattr(
        platform,
        "excute_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("invalid input reached database")),
    )

    for invalid in ("5", 5.5, True):
        with client.application.test_request_context(json={
            "mode": "fast",
            "unitPriceFen": invalid,
            "enabled": True,
        }):
            response, status = platform.admin_update_developer_pricing()
        assert status == 400
        assert "整数" in response.get_json()["message"]

        with client.application.test_request_context(json={
            "balanceDeltaFen": invalid,
            "operationId": "strict-adjustment-input",
        }):
            response, status = platform.admin_adjust_developer_account(7)
        assert status == 400
        assert "整数" in response.get_json()["message"]

        with client.application.test_request_context(json={
            "remainingCalls": invalid,
            "operationId": "strict-quota-input",
        }):
            response, status = platform.admin_set_developer_quota(7)
        assert status == 400
        assert "整数" in response.get_json()["message"]

    with client.application.test_request_context(json={
        "mode": "fast",
        "unitPriceFen": 5,
        "enabled": True,
        "expectedUnitPriceFen": 5.5,
        "expectedEnabled": True,
    }):
        response, status = platform.admin_update_developer_pricing()
    assert status == 400
    assert "expectedUnitPriceFen" in response.get_json()["message"]


def test_pricing_update_is_audited_and_snapshotted_in_one_transaction(client, monkeypatch):
    row = {
        "mode": "fast",
        "display_name": "快速检测",
        "unit_price_fen": 5,
        "enabled": 1,
        "updated_at": None,
    }
    operations = []
    audits = []
    results = []

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
            if normalized.startswith("SELECT mode, display_name"):
                self.current = dict(row)
                return 1
            if normalized.startswith("UPDATE developer_pricing"):
                row["unit_price_fen"], row["enabled"] = params[:2]
                return 1
            if normalized.startswith("INSERT INTO admin_audit_logs"):
                audits.append(params)
                return 1
            if normalized.startswith("INSERT INTO developer_admin_operation_results"):
                results.append(params)
                return 1
            raise AssertionError(f"unexpected SQL: {normalized}")

        def fetchone(self):
            return dict(self.current)

    class Connection:
        committed = False

        def begin(self):
            return None

        def cursor(self):
            return Cursor()

        def commit(self):
            self.committed = True

        def rollback(self):
            raise AssertionError("valid pricing update must not roll back")

        def close(self):
            return None

    connection = Connection()
    _allow_financial_admin(monkeypatch)
    monkeypatch.setattr(platform, "_ensure_developer_platform_tables", lambda: True)
    monkeypatch.setattr(platform, "get_db_connection", lambda: connection)

    payload = {
        "mode": "fast",
        "unitPriceFen": 8,
        "enabled": True,
        "expectedUnitPriceFen": 5,
        "expectedEnabled": True,
        "operationId": "pricing-update-0001",
    }
    with client.application.test_request_context(json=payload):
        response = platform.admin_update_developer_pricing()

    body = response.get_json()
    assert body["status"] == "success"
    assert body["idempotentReplay"] is False
    assert body["pricing"]["unitPriceFen"] == 8
    assert connection.committed is True
    assert len(operations) == 1
    assert operations[0][0] == payload["operationId"]
    assert len(operations[0][1]) == 64
    assert audits[0][3] == "developer.pricing.update"
    assert json.loads(audits[0][7])["operationId"] == payload["operationId"]
    assert json.loads(results[0][2])["pricing"]["unitPriceFen"] == 8


def test_pricing_update_rejects_stale_expected_values(client, monkeypatch):
    row = {
        "mode": "fast",
        "display_name": "快速检测",
        "unit_price_fen": 5,
        "enabled": 1,
        "updated_at": None,
    }
    updates = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, sql, params=None):
            normalized = " ".join(sql.split())
            if normalized.startswith("INSERT INTO developer_admin_operations"):
                return 1
            if normalized.startswith("SELECT mode, display_name"):
                return 1
            if normalized.startswith("UPDATE developer_pricing"):
                updates.append(params)
                return 1
            raise AssertionError(f"unexpected SQL: {normalized}")

        def fetchone(self):
            return dict(row)

    class Connection:
        rolled_back = False

        def begin(self):
            return None

        def cursor(self):
            return Cursor()

        def commit(self):
            raise AssertionError("stale pricing update must not commit")

        def rollback(self):
            self.rolled_back = True

        def close(self):
            return None

    connection = Connection()
    _allow_financial_admin(monkeypatch)
    monkeypatch.setattr(platform, "_ensure_developer_platform_tables", lambda: True)
    monkeypatch.setattr(platform, "get_db_connection", lambda: connection)

    with client.application.test_request_context(json={
        "mode": "fast",
        "unitPriceFen": 8,
        "enabled": True,
        "expectedUnitPriceFen": 4,
        "expectedEnabled": False,
        "operationId": "pricing-update-stale",
    }):
        response, status = platform.admin_update_developer_pricing()

    assert status == 409
    assert response.get_json()["code"] == "pricing_conflict"
    assert connection.rolled_back is True
    assert updates == []


def test_account_adjustment_rolls_back_when_audit_insert_fails(client, monkeypatch):
    account = {
        "user_id": 7,
        "status": "active",
        "free_total": 100,
        "free_used": 0,
        "free_reserved": 0,
        "balance_fen": 300,
        "balance_reserved_fen": 0,
        "created_at": None,
        "updated_at": None,
    }

    class Cursor:
        current = None

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, sql, params=None):
            normalized = " ".join(sql.split())
            if normalized.startswith("INSERT INTO developer_admin_operations"):
                return 1
            if normalized.startswith("SELECT user_id, status, free_total"):
                self.current = dict(account)
                return 1
            if normalized.startswith("UPDATE developer_accounts SET free_total"):
                account["free_total"], account["balance_fen"] = params[:2]
                return 1
            if normalized.startswith("INSERT INTO developer_billing_ledger"):
                return 1
            if normalized.startswith("INSERT INTO admin_audit_logs"):
                raise RuntimeError("internal audit database detail")
            raise AssertionError(f"unexpected SQL: {normalized}")

        def fetchone(self):
            return dict(self.current)

    class Connection:
        def __init__(self):
            self.snapshot = None
            self.committed = False

        def begin(self):
            self.snapshot = dict(account)

        def cursor(self):
            return Cursor()

        def commit(self):
            self.committed = True

        def rollback(self):
            account.clear()
            account.update(self.snapshot)

        def close(self):
            return None

    connection = Connection()
    _allow_financial_admin(monkeypatch)
    monkeypatch.setattr(platform, "_ensure_developer_account", lambda user_id: True)
    monkeypatch.setattr(platform, "get_db_connection", lambda: connection)
    monkeypatch.setattr(platform, "excute_sql", lambda *args, **kwargs: [{"Userid": 7}])

    with client.application.test_request_context(json={
        "balanceDeltaFen": 200,
        "operationId": "audit-failure-rollback-001",
    }):
        response, status = platform.admin_adjust_developer_account(7)

    assert status == 500
    assert response.get_json()["code"] == "financial_operation_failed"
    assert "internal audit" not in response.get_json()["message"]
    assert account["balance_fen"] == 300
    assert connection.committed is False


def test_task_payload_rewrites_browser_only_media_link(monkeypatch):
    monkeypatch.setattr(platform.admin_state, "get_detection_job", lambda task_id: None)
    monkeypatch.setattr(platform, "_reservation_payload", lambda task_id: {"status": "settled"})
    row = {
        "task_id": "task-media",
        "mode": "fast",
        "filename": "sample.png",
        "status": "success",
        "result_item_id": 42,
        "result_json": {
            "status": "success",
            "decisionStatus": "verdict",
            "billable": True,
            "result": {
                "itemid": 42,
                "image_url": "/api/media/image/42",
                "decisionStatus": "verdict",
                "billable": True,
            },
        },
    }

    payload = platform._task_payload(row)

    expected = "/api/openapi/v1/image-detections/task-media/media"
    assert payload["result"]["image_url"] == expected
    assert payload["links"]["media"] == expected


def test_task_payload_hides_result_while_settlement_is_pending(monkeypatch):
    monkeypatch.setattr(platform.admin_state, "get_detection_job", lambda task_id: None)
    monkeypatch.setattr(platform, "_reservation_payload", lambda task_id: {"status": "reserved"})
    row = {
        "task_id": "task-pending-settlement",
        "mode": "fast",
        "filename": "sample.png",
        "status": "success",
        "result_item_id": 42,
        "result_json": {"status": "success", "result": {"itemid": 42}},
    }

    payload = platform._task_payload(row)

    assert payload["status"] == "settlement_pending"
    assert payload["progress"] == 99
    assert payload["result"] is None
    assert payload["billing"]["status"] == "reserved"


def test_openapi_media_download_uses_api_key_owner_and_mode_scope(client, monkeypatch):
    actor = {"user_id": 7, "account_uuid": "00000000-0000-4000-8000-000000000007", "phone": "13800000007", "openid": "openid-7", "scopes": "image:fast"}
    row = {"task_id": "task-media", "mode": "fast", "status": "success", "result_item_id": 42}
    item = {"itemid": 42, "filename": "sample.png", "phone": "13800000007"}
    monkeypatch.setattr(platform, "_developer_key_required", lambda: (actor, None))
    monkeypatch.setattr(platform, "_task_row_for_user", lambda task_id, user_id, account_uuid: row)
    monkeypatch.setattr(platform, "_task_settlement_error", lambda task_id, *_args: None)
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
    monkeypatch.setattr(platform, "_queue_submission_guard", lambda *_: nullcontext())
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
        headers={"Idempotency-Key": "reliable-enqueue-request"},
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


def test_recovery_fails_closed_when_business_result_lacks_response_journal(monkeypatch):
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
    def execute(statement, params=None, fetch=True):
        normalized = " ".join(statement.split())
        if normalized.startswith("SELECT status, effect_item_id, effect_result_json"):
            return [{"status": "running", "effect_item_id": None, "effect_result_json": None}]
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

    with pytest.raises(platform.TaskRecoveryError, match="without a complete response journal"):
        platform._execute_or_recover_task(task, user_info, b"image")

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


def test_recovery_rejects_legacy_success_journal_without_explicit_outcome(monkeypatch):
    task = {"task_id": "job_legacy", "mode": "fast", "filename": "source.png"}
    journal = {
        "status": "success",
        "result": {"itemid": 93, "final_label": "AI生成图像"},
    }
    monkeypatch.setattr(
        platform,
        "excute_sql",
        lambda *_args, **_kwargs: [{
            "status": "running",
            "effect_item_id": 93,
            "effect_result_json": json.dumps(journal),
        }],
    )
    monkeypatch.setattr(
        platform,
        "_task_business_rows",
        lambda *_args, **_kwargs: [{"itemid": 93}],
    )

    with pytest.raises(platform.TaskRecoveryError, match="explicit decision outcome"):
        platform._recover_task_effect(task, {"Userid": 9})


def test_missing_task_outcome_fails_closed_to_review_only():
    outcome = platform._task_billing_outcome({
        "result_json": {
            "status": "success",
            "result": {"itemid": 94, "final_label": "真实图像"},
        }
    })

    assert outcome == ("review_only", False, "released")


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

    payload = {
        "status": "success",
        "result": {"itemid": 88, "decisionStatus": "verdict", "billable": True},
    }
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


def test_queue_capacity_keeps_developer_and_guest_pools_separate(monkeypatch):
    monkeypatch.setattr(platform, "DEVELOPER_TASK_MAX_PENDING", 3)
    monkeypatch.setattr(platform, "WEB_GUEST_TASK_MAX_PENDING", 5)
    monkeypatch.setattr(platform, "WEB_GUEST_OWNER_MAX_PENDING", 1)
    monkeypatch.setattr(platform, "DEVELOPER_SPOOL_MAX_BYTES", 1000)
    rows = {
        "developer_pending": 3,
        "web_pending": 0,
        "owner_pending": 0,
        "pending_bytes": 10,
    }
    monkeypatch.setattr(platform, "excute_sql", lambda *_args, **_kwargs: [rows])

    assert "队列已满" in platform._queue_capacity_error(1, "developer")
    assert platform._queue_capacity_error(1, "guest", "subject-a") is None

    rows.update(developer_pending=0, web_pending=4, owner_pending=1)
    assert "已有待处理任务" in platform._queue_capacity_error(1, "guest", "subject-a")
    assert platform._queue_capacity_error(1, "developer") is None


def test_queue_submission_guard_serializes_final_capacity_check(monkeypatch):
    admission_lock = threading.Lock()
    pending = {"count": 0}

    class Cursor:
        def __init__(self):
            self.row = None

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, sql, params=None):
            if "GET_LOCK" in sql:
                admission_lock.acquire()
                self.row = {"acquired": 1}
            elif "RELEASE_LOCK" in sql:
                admission_lock.release()
                self.row = {"released": 1}

        def fetchone(self):
            return self.row

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            return None

    monkeypatch.setattr(platform, "get_db_connection", Connection)
    monkeypatch.setattr(
        platform,
        "_queue_capacity_error",
        lambda _size: "检测队列已满，请稍后重试" if pending["count"] >= 3 else None,
    )
    barrier = threading.Barrier(10)

    def admit(_index):
        barrier.wait()
        try:
            with platform._queue_submission_guard(1):
                pending["count"] += 1
            return True
        except platform.QueueCapacityError:
            return False

    with ThreadPoolExecutor(max_workers=10) as executor:
        outcomes = list(executor.map(admit, range(10)))

    assert sum(outcomes) == 3
    assert pending["count"] == 3


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


def test_web_task_is_spooled_before_it_becomes_runnable(monkeypatch, tmp_path):
    monkeypatch.setattr(platform, "WEB_TASK_SPOOL_ROOT", tmp_path / "web-spool")
    monkeypatch.setattr(platform, "_ensure_developer_platform_tables", lambda: True)
    monkeypatch.setattr(platform, "_queue_submission_guard", lambda *_args: nullcontext())
    inserts = []

    def fake_sql(sql, params=None, fetch=True):
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT job_id, mode, request_sha256"):
            return []
        if "INSERT INTO web_detection_tasks" in normalized:
            inserts.append(params)
            assert (platform.WEB_TASK_SPOOL_ROOT / params[5]).read_bytes() == b"image-bytes"
            return 1
        raise AssertionError(sql)

    monkeypatch.setattr(platform, "excute_sql", fake_sql)
    job = {
        "id": "job_test123",
        "mode": "fast",
        "actor": {
            "id": 7,
            "account_uuid": "11111111-1111-4111-8111-111111111111",
            "username": "tester",
            "phone": "13800000000",
            "openid": "openid-7",
        },
    }
    user_info = {
        "Userid": 7,
        "account_uuid": "11111111-1111-4111-8111-111111111111",
        "username": "tester",
        "phone": "13800000000",
        "openid": "openid-7",
    }

    assert platform._enqueue_web_detection_task(
        job, b"image-bytes", "photo.png", "image/png", user_info, False, "", "web-task-spool-001"
    ) == ("job_test123", False)
    assert len(inserts) == 1
    assert inserts[0][0] == "job_test123"
    assert inserts[0][6] == len(b"image-bytes")


def test_web_task_replay_bypasses_current_queue_capacity(monkeypatch):
    monkeypatch.setattr(platform, "_ensure_developer_platform_tables", lambda: True)
    existing_sha256 = platform.hashlib.sha256(b"image-bytes").hexdigest()
    monkeypatch.setattr(
        platform,
        "excute_sql",
        lambda sql, *_args, **_kwargs: [{
            "job_id": "job_existing",
            "mode": "fast",
            "request_sha256": existing_sha256,
        }] if "SELECT job_id, mode, request_sha256" in sql else (_ for _ in ()).throw(AssertionError(sql)),
    )

    @contextmanager
    def full_queue(*_args):
        raise platform.QueueCapacityError("queue full")
        yield

    monkeypatch.setattr(platform, "_queue_submission_guard", full_queue)
    job = {
        "id": "job_retry",
        "mode": "fast",
        "actor": {"account_uuid": "11111111-1111-4111-8111-111111111111"},
    }

    assert platform._enqueue_web_detection_task(
        job,
        b"image-bytes",
        "photo.png",
        "image/png",
        {"Userid": 7},
        False,
        "",
        "web-replay-001",
    ) == ("job_existing", True)


def test_failed_web_task_retry_releases_old_idempotency_key(monkeypatch, tmp_path):
    monkeypatch.setattr(platform, "WEB_TASK_SPOOL_ROOT", tmp_path / "web-spool")
    monkeypatch.setattr(platform, "_ensure_developer_platform_tables", lambda: True)
    monkeypatch.setattr(platform, "_queue_submission_guard", lambda *_args: nullcontext())
    existing = {
        "job_id": "job_failed",
        "mode": "fast",
        "request_sha256": platform.hashlib.sha256(b"image-bytes").hexdigest(),
        "status": "failed",
    }
    inserted = []

    def fake_sql(sql, params=None, fetch=True):
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT job_id, mode, request_sha256, status"):
            return [dict(existing)] if existing else []
        if normalized.startswith("UPDATE web_detection_tasks SET idempotency_key = NULL"):
            existing.clear()
            return 1
        if normalized.startswith("INSERT INTO web_detection_tasks"):
            inserted.append(params)
            return 1
        raise AssertionError(sql)

    monkeypatch.setattr(platform, "excute_sql", fake_sql)
    job = {
        "id": "job_retry_new",
        "mode": "fast",
        "actor": {"account_uuid": "11111111-1111-4111-8111-111111111111"},
    }

    assert platform._enqueue_web_detection_task(
        job,
        b"image-bytes",
        "IMG_7956.jpeg",
        "image/jpeg",
        {"Userid": 7},
        False,
        "",
        "web-failed-retry-001",
    ) == ("job_retry_new", False)
    assert len(inserted) == 1


def test_web_task_schema_adds_idempotency_column_before_unique_index(monkeypatch):
    operations = []

    class Cursor:
        rows = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, sql, params=None):
            normalized = " ".join(sql.split())
            operations.append(normalized)
            if "INFORMATION_SCHEMA.COLUMNS" in normalized and "developer_detection_tasks" in normalized:
                self.rows = [{"COLUMN_NAME": name} for name in (
                    "account_uuid", "mime_type", "execution_filename", "spool_path",
                    "spool_size", "request_context_json", "idempotency_key", "lease_owner",
                    "next_attempt_at", "lease_expires_at", "attempt_count", "last_heartbeat_at",
                    "effect_item_id", "effect_result_json", "daily_quota_reserved",
                    "daily_quota_day", "prompt_tokens", "completion_tokens", "total_tokens",
                )]
            elif "uk_developer_task_idempotency" in normalized and normalized.startswith("SELECT COLUMN_NAME"):
                self.rows = [
                    {"COLUMN_NAME": "account_uuid"},
                    {"COLUMN_NAME": "idempotency_key"},
                ]
            elif "idx_developer_tasks_lease" in normalized and normalized.startswith("SELECT INDEX_NAME"):
                self.rows = [{"INDEX_NAME": "idx_developer_tasks_lease"}]
            elif "INFORMATION_SCHEMA.COLUMNS" in normalized and "web_detection_tasks" in normalized:
                self.rows = [{"COLUMN_NAME": name} for name in (
                    "owner_type", "owner_key", "next_attempt_at", "effect_item_id",
                    "effect_result_json",
                )]
            elif "idx_web_detection_tasks_owner" in normalized and normalized.startswith("SELECT INDEX_NAME"):
                self.rows = [{"INDEX_NAME": "idx_web_detection_tasks_owner"}]
            elif "uk_web_detection_tasks_idempotency" in normalized and normalized.startswith("SELECT INDEX_NAME"):
                self.rows = []
            else:
                self.rows = []

        def fetchall(self):
            return self.rows

        def fetchone(self):
            return self.rows[0] if self.rows else None

    class Connection:
        def cursor(self):
            return Cursor()

        def commit(self):
            return None

        def rollback(self):
            raise AssertionError("migration should not roll back")

        def close(self):
            return None

    monkeypatch.setattr(platform, "get_db_connection", Connection)

    assert platform._ensure_task_lease_schema() is True
    add_column = next(
        index for index, sql in enumerate(operations)
        if "ADD COLUMN idempotency_key" in sql and "web_detection_tasks" in sql
    )
    add_index = next(
        index for index, sql in enumerate(operations)
        if "ADD UNIQUE INDEX uk_web_detection_tasks_idempotency" in sql
    )
    assert add_column < add_index


def test_task_idempotency_index_builds_replacement_before_dropping_old_guard(monkeypatch):
    operations = []

    class Cursor:
        rows = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, sql, params=None):
            normalized = " ".join(sql.split())
            operations.append(normalized)
            if "INFORMATION_SCHEMA.COLUMNS" in normalized and "developer_detection_tasks" in normalized:
                self.rows = [{"COLUMN_NAME": name} for name in (
                    "account_uuid", "mime_type", "execution_filename", "spool_path",
                    "spool_size", "request_context_json", "idempotency_key", "lease_owner",
                    "next_attempt_at", "lease_expires_at", "attempt_count", "last_heartbeat_at",
                    "effect_item_id", "effect_result_json", "daily_quota_reserved",
                    "daily_quota_day", "prompt_tokens", "completion_tokens", "total_tokens",
                )]
            elif "INDEX_NAME = 'uk_developer_task_idempotency'" in normalized:
                self.rows = [{"COLUMN_NAME": "user_id"}, {"COLUMN_NAME": "idempotency_key"}]
            elif params == ("uk_developer_task_account_idempotency_v2",):
                self.rows = []
            elif "idx_developer_tasks_lease" in normalized and normalized.startswith("SELECT INDEX_NAME"):
                self.rows = [{"INDEX_NAME": "idx_developer_tasks_lease"}]
            elif "INFORMATION_SCHEMA.COLUMNS" in normalized and "web_detection_tasks" in normalized:
                self.rows = [{"COLUMN_NAME": name} for name in (
                    "owner_type", "owner_key", "idempotency_key", "next_attempt_at",
                    "effect_item_id", "effect_result_json",
                )]
            elif normalized.startswith("SELECT INDEX_NAME"):
                self.rows = [{"INDEX_NAME": "present"}]
            else:
                self.rows = []

        def fetchall(self):
            return self.rows

        def fetchone(self):
            return self.rows[0] if self.rows else None

    class Connection:
        def cursor(self):
            return Cursor()

        def commit(self):
            return None

        def rollback(self):
            raise AssertionError("safe index migration must not roll back")

        def close(self):
            return None

    monkeypatch.setattr(platform, "get_db_connection", Connection)

    assert platform._ensure_task_lease_schema() is True
    add_replacement = next(i for i, sql in enumerate(operations) if (
        "ADD UNIQUE INDEX uk_developer_task_account_idempotency_v2" in sql
    ))
    drop_old = next(i for i, sql in enumerate(operations) if (
        "DROP INDEX uk_developer_task_idempotency" in sql
    ))
    rename_replacement = next(i for i, sql in enumerate(operations) if (
        "RENAME INDEX uk_developer_task_account_idempotency_v2" in sql
    ))
    assert add_replacement < drop_old < rename_replacement


def test_financial_audit_is_also_appended_to_tamper_evident_chain(monkeypatch):
    sql_calls = []
    chain_calls = []

    class Cursor:
        def execute(self, sql, params=None):
            sql_calls.append((" ".join(sql.split()), params))

    monkeypatch.setattr(
        platform,
        "_append_security_audit",
        lambda *args, **kwargs: chain_calls.append((args, kwargs)) or "event-id",
    )
    actor = {"adminId": 9, "username": "finance", "phone": "13800000000"}

    platform._insert_transactional_admin_audit(
        Cursor(),
        actor,
        "developer.account.adjust",
        "7",
        before={"balanceFen": 100},
        after={"balanceFen": 150},
        meta={"operationId": "adjust-001"},
    )

    assert sql_calls[0][0].startswith("INSERT INTO admin_audit_logs")
    assert len(chain_calls) == 1
    args, _ = chain_calls[0]
    assert args[1:5] == ("admin", 9, "developer.account.adjust", "7")
    assert args[5]["before"] == {"balanceFen": 100}
    assert args[5]["after"] == {"balanceFen": 150}


def test_guest_web_task_daily_allowance_is_server_side_and_released_on_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(platform, "WEB_TASK_SPOOL_ROOT", tmp_path / "web-spool")
    monkeypatch.setattr(platform, "_ensure_developer_platform_tables", lambda: True)
    monkeypatch.setattr(platform, "_queue_submission_guard", lambda *_args: nullcontext())
    calls = []

    def fake_sql(sql, params=None, fetch=True):
        normalized = " ".join(sql.split())
        calls.append((normalized, params))
        if normalized.startswith("SELECT job_id, mode, request_sha256"):
            return []
        if normalized.startswith("SELECT COUNT(*) AS cnt FROM web_guest_daily_usage"):
            return [{"cnt": 0}]
        if normalized.startswith("INSERT IGNORE INTO web_guest_daily_usage"):
            return 1
        if normalized.startswith("INSERT INTO web_detection_tasks"):
            return 0
        if normalized.startswith("DELETE FROM web_guest_daily_usage"):
            return 1
        raise AssertionError(sql)

    monkeypatch.setattr(platform, "excute_sql", fake_sql)
    job = {
        "id": "job_guest1",
        "mode": "fast",
        "actor": {"id": None, "account_uuid": "", "phone": "", "openid": "guest-1"},
    }
    user_info = {"Userid": None, "account_uuid": "", "phone": "", "openid": "guest-1"}

    with pytest.raises(platform.TaskRecoveryError):
        platform._enqueue_web_detection_task(
            job, b"image-bytes", "photo.png", "image/png", user_info, True, "a" * 64, "guest-failure-001"
        )

    assert any(sql.startswith("DELETE FROM web_guest_daily_usage") for sql, _ in calls)
    assert not any(platform.WEB_TASK_SPOOL_ROOT.iterdir())


def test_guest_web_task_rejects_reused_daily_subject(monkeypatch, tmp_path):
    monkeypatch.setattr(platform, "WEB_TASK_SPOOL_ROOT", tmp_path / "web-spool")
    monkeypatch.setattr(platform, "_ensure_developer_platform_tables", lambda: True)
    monkeypatch.setattr(platform, "_queue_submission_guard", lambda *_args: nullcontext())
    monkeypatch.setattr(platform, "excute_sql", lambda *_args, **_kwargs: 0)
    job = {
        "id": "job_guest2",
        "mode": "fast",
        "actor": {"id": None, "account_uuid": "", "phone": "", "openid": "guest-2"},
    }
    user_info = {"Userid": None, "account_uuid": "", "phone": "", "openid": "guest-2"}

    with pytest.raises(platform.QueueCapacityError, match="免费检测次数"):
        platform._enqueue_web_detection_task(
            job, b"image-bytes", "photo.png", "image/png", user_info, True, "b" * 64, "guest-reuse-001"
        )


def test_guest_web_task_enforces_global_daily_budget(monkeypatch, tmp_path):
    monkeypatch.setattr(platform, "WEB_TASK_SPOOL_ROOT", tmp_path / "web-spool")
    monkeypatch.setattr(platform, "WEB_GUEST_DAILY_GLOBAL_LIMIT", 2)
    monkeypatch.setattr(platform, "_ensure_developer_platform_tables", lambda: True)
    monkeypatch.setattr(platform, "_queue_submission_guard", lambda *_args: nullcontext())
    monkeypatch.setattr(
        platform,
        "excute_sql",
        lambda sql, *_args, **_kwargs: (
            []
            if "SELECT job_id, mode, request_sha256" in sql
            else [{"cnt": 2}]
            if "COUNT(*) AS cnt FROM web_guest_daily_usage" in sql
            else (_ for _ in ()).throw(AssertionError(sql))
        ),
    )
    job = {
        "id": "job_guest_budget",
        "mode": "fast",
        "actor": {"id": None, "account_uuid": "", "phone": "", "openid": "guest"},
    }

    with pytest.raises(platform.QueueCapacityError, match="总额度"):
        platform._enqueue_web_detection_task(
            job,
            b"image-bytes",
            "photo.png",
            "image/png",
            {"Userid": None, "account_uuid": "", "phone": "", "openid": "guest"},
            True,
            "c" * 64,
            "guest-budget-001",
        )


def test_expired_web_task_fails_without_automatic_model_retry(monkeypatch):
    monkeypatch.setattr(platform, "_ensure_developer_platform_tables", lambda: True)
    updates = []

    def fake_sql(sql, params=None, fetch=True):
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT job_id, mode, filename"):
            return [{
                "job_id": "job_interrupted",
                "mode": "fast",
                "filename": "sample.png",
                "status": "running",
                "request_context_json": "{}",
            }]
        if normalized.startswith("SELECT status, effect_item_id, effect_result_json"):
            return [{"status": "running", "effect_item_id": None, "effect_result_json": None}]
        if normalized.startswith("UPDATE web_detection_tasks SET status = 'failed'"):
            updates.append(params)
            return 1
        raise AssertionError(sql)

    cache_updates = []
    monkeypatch.setattr(platform, "excute_sql", fake_sql)
    monkeypatch.setattr(
        platform.admin_state,
        "update_detection_job",
        lambda job_id, payload: cache_updates.append((job_id, payload)),
    )

    assert platform._reconcile_web_tasks() == 1
    assert "未自动重复推理" in updates[0][0]
    assert cache_updates[0][1]["status"] == "failed"


def test_expired_web_task_recovers_persisted_business_result(monkeypatch):
    monkeypatch.setattr(platform, "_ensure_developer_platform_tables", lambda: True)
    task = {
        "job_id": "job_0123456789abcdefabcd",
        "mode": "fast",
        "filename": "sample.png",
        "status": "running",
        "request_context_json": "context",
    }
    payload = {"status": "success", "result": {"itemid": 91, "filename": "sample.png"}}
    monkeypatch.setattr(
        platform,
        "_load_web_request_context",
        lambda _row: ({}, {"Userid": 7, "account_uuid": "owner"}, False),
    )
    monkeypatch.setattr(platform, "_recover_web_task_effect", lambda *_args: payload)
    updates = []

    def fake_sql(sql, params=None, fetch=True):
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT job_id, mode, filename"):
            return [task]
        if normalized.startswith("UPDATE web_detection_tasks SET status = 'success'"):
            updates.append(params)
            return 1
        raise AssertionError(sql)

    cache_updates = []
    monkeypatch.setattr(platform, "excute_sql", fake_sql)
    monkeypatch.setattr(
        platform.admin_state,
        "update_detection_job",
        lambda job_id, value: cache_updates.append((job_id, value)),
    )

    assert platform._reconcile_web_tasks() == 1
    assert json.loads(updates[0][0]) == payload
    assert cache_updates[0][1]["status"] == "success"
    assert "持久化结果恢复" in cache_updates[0][1]["summary"]


def test_web_recovery_rejects_primary_row_without_final_response_journal(monkeypatch):
    task = {"job_id": "job_0123456789abcdefabcd", "mode": "fast"}
    user_info = {"Userid": 7, "account_uuid": "00000000-0000-4000-8000-000000000007"}
    monkeypatch.setattr(
        platform,
        "excute_sql",
        lambda *_args, **_kwargs: [{
            "status": "running",
            "effect_item_id": None,
            "effect_result_json": None,
        }],
    )
    monkeypatch.setattr(
        platform,
        "_web_task_business_rows",
        lambda *_args, **_kwargs: [{"itemid": 91, "explantation": "raw model result"}],
    )

    with pytest.raises(platform.TaskRecoveryError, match="without a finalized response journal"):
        platform._recover_web_task_effect(task, user_info)


def test_web_recovery_normalizes_legacy_journal_to_review_only(monkeypatch):
    task = {"job_id": "job_0123456789abcdefabcd", "mode": "fast"}
    user_info = {"Userid": 7, "account_uuid": "00000000-0000-4000-8000-000000000007"}
    legacy = {"status": "success", "result": {"itemid": 91, "probability": 0.93}}
    monkeypatch.setattr(
        platform,
        "excute_sql",
        lambda *_args, **_kwargs: [{
            "status": "running",
            "effect_item_id": 91,
            "effect_result_json": json.dumps(legacy),
        }],
    )
    monkeypatch.setattr(
        platform,
        "_web_task_business_rows",
        lambda *_args, **_kwargs: [{"itemid": 91, "explantation": "legacy model result"}],
    )

    recovered = platform._recover_web_task_effect(task, user_info)

    assert recovered["decisionStatus"] == "review_only"
    assert recovered["billable"] is False
    assert recovered["result"]["decisionStatus"] == "review_only"


def test_explicit_gpu_overload_is_deferred_without_releasing_reservation(monkeypatch):
    updates = []

    def fake_sql(sql, params=None, fetch=True):
        updates.append((" ".join(sql.split()), params))
        return 1

    monkeypatch.setattr(platform, "excute_sql", fake_sql)
    task = {"task_id": "job_retry", "lease_owner": "worker-lease"}

    assert platform._defer_developer_task_after_overload(task, {"retryAfter": "7"}) is True
    assert "SET status = 'queued'" in updates[0][0]
    assert updates[0][1][0] == 7


def test_web_gpu_overload_is_deferred_only_below_attempt_limit(monkeypatch):
    captured = []
    monkeypatch.setattr(
        platform,
        "excute_sql",
        lambda sql, params=None, fetch=True: captured.append((sql, params)) or 1,
    )
    task = {"job_id": "job_retry", "lease_owner": "web-lease"}

    assert platform._defer_web_task_after_overload(task, {"retryAfter": "4"}) is True
    assert captured[0][1] == (4, "job_retry", "web-lease", platform.WEB_TASK_MAX_ATTEMPTS)
