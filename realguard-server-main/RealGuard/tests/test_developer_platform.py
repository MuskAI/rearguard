from concurrent.futures import ThreadPoolExecutor
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
    monkeypatch.setattr(platform, "_task_row_for_user", lambda task_id, user_id: rows.get((task_id, user_id)))
    monkeypatch.setattr(platform.admin_state, "get_detection_job", lambda task_id: None)
    monkeypatch.setattr(platform, "_reservation_payload", lambda task_id: {"source": "free", "amountFen": 0, "status": "settled"})

    owned = client.get("/api/openapi/v1/image-detections/job-owned", headers={"X-Test-Key": "owner"})
    foreign = client.get("/api/openapi/v1/image-detections/job-owned", headers={"X-Test-Key": "foreign"})

    assert owned.status_code == 200
    assert owned.get_json()["result"]["itemid"] == 12
    assert foreign.status_code == 404


def test_idempotency_key_rejects_different_content(client, monkeypatch):
    actor = {"id": 11, "user_id": 1, "scopes": ["image:fast"], "phone": "13800000000"}
    monkeypatch.setattr(platform, "_developer_key_required", lambda: (actor, None))
    monkeypatch.setattr(platform, "_ensure_developer_platform_tables", lambda: True)
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
    released = []
    usage = []
    sql = []
    monkeypatch.setattr(platform, "_settle_billing", lambda task_id: settled.append(task_id) or True)
    monkeypatch.setattr(platform, "_release_billing", lambda task_id, note="": released.append((task_id, note)) or True)
    monkeypatch.setattr(platform, "_record_developer_usage_event", lambda actor, **kwargs: usage.append((actor, kwargs)) or True)
    monkeypatch.setattr(platform, "excute_sql", lambda statement, params=None, fetch=True: sql.append((statement, params)) or 1)
    monkeypatch.setattr(platform, "_public_result_payload", lambda payload, mode: payload)

    actor = {"id": 9, "user_id": 1}
    assert platform._finish_task("ok", actor, "fast", {"status": "success", "result": {"itemid": 2}}, 200) is True
    assert platform._finish_task("bad", actor, "swarm", {"status": "error", "message": "model timeout"}, 503) is False

    assert settled == ["ok"]
    assert released == [("bad", "model timeout")]
    assert len(usage) == 1
    assert usage[0][1]["endpoint"].endswith(":fast")
    assert len(sql) == 2


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
