from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
import sys
import threading

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from imagedetection import creat_app  # noqa: E402
from imagedetection.views import admin, admin_state, api, developer_platform  # noqa: E402


class _SharedQuotaDb:
    def __init__(self, *, daily_limit=None, minute_limit=None):
        self.lock = threading.Lock()
        self.now = datetime(2026, 7, 19, 10, 20, 30)
        self.key = {
            "id": 7,
            "user_id": 3,
            "name": "production",
            "scopes": "image:fast,image:swarm,reports",
            "status": "active",
            "expires_at": self.now + timedelta(days=30),
            "ip_allowlist": "",
            "phone": "13800000000",
            "username": "developer",
            "openid": "openid-3",
            "daily_limit": daily_limit,
            "rate_limit_per_minute": minute_limit,
        }
        self.usage = None
        self.connections = 0
        self.query_order = []

    def connect(self):
        self.connections += 1
        return _QuotaConnection(self)


class _QuotaCursor:
    def __init__(self, connection):
        self.connection = connection
        self.result = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        self.connection.db.query_order.append(normalized)
        if normalized.startswith("SELECT k.user_id FROM developer_api_keys"):
            self.result = {"user_id": self.connection.db.key["user_id"]}
            return 1
        if normalized.startswith("SELECT k.id, k.user_id"):
            self.result = deepcopy(self.connection.db.key)
            self.result["quota_now"] = self.connection.db.now
            return 1
        if normalized.startswith("SELECT Userid FROM `user`"):
            self.connection.acquire_key_lock()
            self.result = {"Userid": self.connection.db.key["user_id"]}
            return 1
        if normalized.startswith("SELECT u.Userid, q.daily_limit"):
            self.connection.acquire_key_lock()
            self.result = {
                "Userid": self.connection.db.key["user_id"],
                "daily_limit": self.connection.db.key["daily_limit"],
                "quota_now": self.connection.db.now,
            }
            return 1
        if normalized.startswith("SELECT day_bucket, daily_count"):
            self.result = deepcopy(self.connection.db.usage)
            return 1 if self.result else 0
        if normalized.startswith("INSERT INTO developer_api_account_quota_usage"):
            if len(params) == 6:
                user_id, day_bucket, daily_count, minute_bucket, next_daily_count, minute_count = params
                assert daily_count == next_daily_count
            else:
                user_id, day_bucket, daily_count, minute_bucket = params
                minute_count = int((self.connection.db.usage or {}).get("minute_count") or 0)
            self.connection.db.usage = {
                "user_id": user_id,
                "day_bucket": day_bucket,
                "daily_count": daily_count,
                "minute_bucket": minute_bucket,
                "minute_count": minute_count,
            }
            return 1
        if normalized.startswith("UPDATE developer_api_keys SET last_used_at"):
            self.connection.db.key["last_used_ip"] = params[0]
            return 1
        raise AssertionError(f"unexpected SQL: {normalized}")

    def fetchone(self):
        return deepcopy(self.result)


class _QuotaConnection:
    def __init__(self, db):
        self.db = db
        self.locked = False

    def begin(self):
        return None

    def cursor(self):
        return _QuotaCursor(self)

    def acquire_key_lock(self):
        self.db.lock.acquire()
        self.locked = True

    def _release(self):
        if self.locked:
            self.locked = False
            self.db.lock.release()

    def commit(self):
        self._release()

    def rollback(self):
        self._release()

    def close(self):
        self._release()


@pytest.fixture
def app():
    application = creat_app()
    application.config.update(TESTING=True)
    return application


def _install_quota_db(monkeypatch, db):
    monkeypatch.setattr(api, "_ensure_developer_api_key_table", lambda: True)
    monkeypatch.setattr(api, "get_db_connection", db.connect)


def _consume(
    app,
    api_key="rg_sk_test",
    remote_addr="127.0.0.1",
    path="/api/openapi/v1/image-detections",
    method="POST",
):
    with app.test_request_context(
        path,
        method=method,
        headers={"X-RealGuard-Key": api_key},
        environ_base={"REMOTE_ADDR": remote_addr},
    ):
        return api._consume_developer_key_request(api_key)


def _require_key(app, *, path, method="GET"):
    with app.test_request_context(
        path,
        method=method,
        headers={"X-RealGuard-Key": "rg_sk_test"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    ):
        return api._developer_key_required()


def _reserve_daily(actor):
    return api._change_developer_daily_detection_quota(actor, 1)


def test_daily_limit_is_atomic_across_independent_connections(app, monkeypatch):
    db = _SharedQuotaDb(daily_limit=3)
    _install_quota_db(monkeypatch, db)
    barrier = threading.Barrier(8)
    results = []
    results_lock = threading.Lock()

    def request_once():
        barrier.wait()
        actor, _, error_code, _ = _consume(app)
        result = (None, error_code, None) if error_code else _reserve_daily(actor)
        with results_lock:
            results.append(result)

    threads = [threading.Thread(target=request_once) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert sum(error_code is None for _, error_code, _ in results) == 3
    assert sum(error_code == "daily_limit_exceeded" for _, error_code, _ in results) == 5
    assert db.usage["daily_count"] == 3
    assert db.connections == 16


def test_key_consumption_locks_account_before_key(app, monkeypatch):
    db = _SharedQuotaDb(daily_limit=3)
    _install_quota_db(monkeypatch, db)

    actor, error, error_code, _ = _consume(app)

    assert actor["id"] == 7
    assert error is None
    assert error_code is None
    account_lock = next(
        index for index, query in enumerate(db.query_order)
        if query.startswith("SELECT Userid FROM `user`")
    )
    key_lock = next(
        index for index, query in enumerate(db.query_order)
        if query.startswith("SELECT k.id, k.user_id")
    )
    assert account_lock < key_lock


def test_rate_limit_returns_429_retry_after_and_resets_next_minute(app, monkeypatch):
    db = _SharedQuotaDb(daily_limit=10, minute_limit=1)
    _install_quota_db(monkeypatch, db)
    client = app.test_client()

    accepted = client.post("/api/developer/v1/detect", headers={"X-RealGuard-Key": "rg_sk_test"})
    limited = client.post("/api/developer/v1/detect", headers={"X-RealGuard-Key": "rg_sk_test"})

    assert accepted.status_code == 410
    assert limited.status_code == 429
    assert limited.get_json()["code"] == "rate_limit_exceeded"
    assert 1 <= int(limited.headers["Retry-After"]) <= 60

    db.now += timedelta(minutes=1)
    reset = client.post("/api/developer/v1/detect", headers={"X-RealGuard-Key": "rg_sk_test"})
    assert reset.status_code == 410
    assert db.usage["daily_count"] == 0
    assert db.usage["minute_count"] == 1


def test_read_only_polling_does_not_consume_submission_rate_limit(app, monkeypatch):
    db = _SharedQuotaDb(daily_limit=10, minute_limit=1)
    _install_quota_db(monkeypatch, db)

    first_poll = _consume(app, method="GET")
    second_poll = _consume(app, method="GET")
    first_submit = _consume(app, method="POST")
    poll_after_submit = _consume(app, method="GET")
    limited_submit = _consume(app, method="POST")

    assert first_poll[2] is None
    assert second_poll[2] is None
    assert first_submit[2] is None
    assert poll_after_submit[2] is None
    assert limited_submit[2] == "rate_limit_exceeded"
    assert db.usage["minute_count"] == 1


def test_daily_limit_returns_429_with_next_day_retry_after(app, monkeypatch):
    db = _SharedQuotaDb(daily_limit=1)
    _install_quota_db(monkeypatch, db)

    actor, _, error_code, _ = _consume(app)
    assert error_code is None
    assert _reserve_daily(actor)[1] is None
    with app.test_request_context("/api/openapi/v1/image-detections", method="POST"):
        response, status_code = api._reserve_developer_daily_detection(actor)

    assert status_code == 429
    assert response.get_json()["code"] == "daily_limit_exceeded"
    assert int(response.headers["Retry-After"]) > 60
    assert db.usage["daily_count"] == 1
    assert db.usage["minute_count"] == 1


def test_rotated_key_shares_account_daily_limit(app, monkeypatch):
    db = _SharedQuotaDb(daily_limit=1)
    _install_quota_db(monkeypatch, db)

    actor, _, error_code, _ = _consume(app, api_key="rg_sk_first")
    assert error_code is None
    assert _reserve_daily(actor)[1] is None
    db.key["id"] = 8
    rotated, _, error_code, _ = _consume(app, api_key="rg_sk_rotated")
    assert error_code is None
    _, error_code, _ = _reserve_daily(rotated)

    assert error_code == "daily_limit_exceeded"
    assert db.usage["user_id"] == 3
    assert db.usage["daily_count"] == 1


def test_polling_and_report_do_not_consume_detection_quotas(app, monkeypatch):
    db = _SharedQuotaDb(daily_limit=1, minute_limit=3)
    _install_quota_db(monkeypatch, db)

    actor, _, error_code, _ = _consume(app)
    assert error_code is None
    assert _reserve_daily(actor)[1] is None
    assert _consume(app, path="/api/openapi/v1/image-detections/task-1", method="GET")[2] is None
    assert _consume(app, path="/api/openapi/v1/image-detections/task-1/report", method="GET")[2] is None
    assert db.usage["daily_count"] == 1
    assert db.usage["minute_count"] == 1

    actor, error = _require_key(app, path="/api/openapi/v1/image-detections/task-1")
    assert actor["id"] == 7
    assert error is None
    assert db.usage["daily_count"] == 1
    assert db.usage["minute_count"] == 1


@pytest.mark.parametrize(
    ("key_updates", "remote_addr", "expected_code"),
    [
        ({"status": "revoked"}, "127.0.0.1", None),
        ({"expires_at": datetime(2026, 7, 19, 10, 20, 29)}, "127.0.0.1", None),
        ({"ip_allowlist": "10.0.0.0/8"}, "203.0.113.8", "ip_not_allowed"),
    ],
)
def test_rejected_keys_do_not_consume_quota(app, monkeypatch, key_updates, remote_addr, expected_code):
    db = _SharedQuotaDb(daily_limit=10, minute_limit=10)
    db.key.update(key_updates)
    _install_quota_db(monkeypatch, db)

    row, error, error_code, retry_after = _consume(app, remote_addr=remote_addr)

    assert error_code == expected_code
    assert retry_after is None
    assert db.usage is None
    if expected_code == "ip_not_allowed":
        assert row is None
        assert "IP" in error
    else:
        assert row is None
        assert error is None


def test_legacy_admin_quota_is_migrated_then_db_becomes_authoritative(monkeypatch, tmp_path):
    monkeypatch.setattr(admin_state, "STATE_PATH", tmp_path / "admin-state.json")
    monkeypatch.setattr(admin_state, "_API_KEY_QUOTA_TABLES_READY", False)
    admin_state.save_state({
        "apiKeyQuotas": {
            "7": {"dailyLimit": 20, "rateLimitPerMinute": 4, "notes": "legacy"},
        },
    })
    legacy_stored = {}
    account_stored = {}

    def fake_system_sql(sql, params=None, fetch=True):
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT CONSTRAINT_NAME FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS"):
            return [{"CONSTRAINT_NAME": params[1]}]
        if normalized.startswith("CREATE TABLE"):
            return 0
        if normalized.startswith("SELECT user_id FROM developer_api_keys"):
            return [{"user_id": 3}]
        if normalized.startswith("SELECT daily_limit"):
            source = account_stored if "developer_api_account_quotas" in normalized else legacy_stored
            value = source.get(int(params[0]))
            return [deepcopy(value)] if value else []
        if normalized.startswith("INSERT INTO developer_api_key_quotas"):
            key_id, daily_limit, minute_limit, scopes, notes, existing_key_id = params
            assert key_id == existing_key_id
            insert_only = "key_id = VALUES(key_id)" in normalized
            if key_id not in legacy_stored or not insert_only:
                legacy_stored[key_id] = {
                    "daily_limit": daily_limit,
                    "rate_limit_per_minute": minute_limit,
                    "scopes": scopes,
                    "notes": notes,
                }
            return 1
        if normalized.startswith("INSERT INTO developer_api_account_quotas"):
            user_id, daily_limit, minute_limit, scopes, notes, existing_user_id = params
            assert user_id == existing_user_id == 3
            insert_only = "user_id = VALUES(user_id)" in normalized
            if user_id not in account_stored or not insert_only:
                account_stored[user_id] = {
                    "daily_limit": daily_limit,
                    "rate_limit_per_minute": minute_limit,
                    "scopes": scopes,
                    "notes": notes,
                }
            return 1
        raise AssertionError(f"unexpected SQL: {normalized}")

    monkeypatch.setattr(admin_state, "_system_sql", fake_system_sql)

    migrated = admin_state.get_api_key_quota(7)
    assert migrated["dailyLimit"] == 20
    assert migrated["rateLimitPerMinute"] == 4

    updated = admin_state.set_api_key_quota(7, {"dailyLimit": 50})
    assert updated["dailyLimit"] == 50
    assert account_stored[3]["daily_limit"] == 50

    state = admin_state.load_state()
    state["apiKeyQuotas"]["7"]["dailyLimit"] = 999
    admin_state.save_state(state)
    assert admin_state.get_api_key_quota(7)["dailyLimit"] == 50


def test_db_authoritative_quota_write_failure_returns_admin_503(app, monkeypatch):
    monkeypatch.setattr(
        developer_platform,
        "admin_update_request_quota",
        lambda key_id: (
            admin.jsonify({
                "error": {
                    "code": "quota_storage_unavailable",
                    "message": "API 配额存储初始化失败",
                }
            }),
            503,
        ),
    )
    client = app.test_client()
    with client.session_transaction() as session:
        session[admin.ADMIN_CSRF_SESSION_KEY] = "csrf-token"

    response = client.post(
        "/api/admin/api-keys/7/quota",
        json={
            "dailyLimit": 20,
            "rateLimitPerMinute": None,
            "expectedDailyLimit": 10,
            "expectedRateLimitPerMinute": None,
            "operationId": "quota-write-failure-001",
        },
        headers={"X-CSRF-Token": "csrf-token"},
    )

    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "quota_storage_unavailable"


def test_db_authoritative_quota_write_failure_does_not_update_json(monkeypatch, tmp_path):
    monkeypatch.setattr(admin_state, "STATE_PATH", tmp_path / "admin-state.json")
    admin_state.save_state({
        "apiKeyQuotas": {"7": {"dailyLimit": 10, "rateLimitPerMinute": 2}},
    })
    monkeypatch.setattr(admin_state, "_API_KEY_QUOTA_TABLES_READY", True)

    def failing_system_sql(sql, params=None, fetch=True):
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT user_id FROM developer_api_keys"):
            return [{"user_id": 3}]
        if normalized.startswith("SELECT daily_limit"):
            return [{
                "daily_limit": 10,
                "rate_limit_per_minute": 2,
                "scopes": "",
                "notes": "",
            }]
        if normalized.startswith("INSERT INTO developer_api_account_quotas"):
            return None
        raise AssertionError(f"unexpected SQL: {normalized}")

    monkeypatch.setattr(admin_state, "_system_sql", failing_system_sql)

    result = admin_state.set_api_key_quota(7, {"dailyLimit": 20})

    assert result is None
    assert admin_state.load_state()["apiKeyQuotas"]["7"]["dailyLimit"] == 10
