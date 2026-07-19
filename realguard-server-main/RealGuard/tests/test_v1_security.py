from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import sys
import threading

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from imagedetection import creat_app  # noqa: E402
from imagedetection.views import api, detection, login, profile, utils  # noqa: E402

ACCOUNT_UUID = "11111111-1111-4111-8111-111111111111"


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
        if normalized.startswith("INSERT IGNORE INTO sms_send_limits"):
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


def test_login_sms_unknown_user_is_rejected_without_auto_create(client, monkeypatch):
    monkeypatch.setattr(api, "_verify_sms_code", lambda scene, phone, code: (True, ""))
    monkeypatch.setattr(api, "_find_user_by_phone", lambda phone: None)

    def fake_execute(sql, params=None, fetch=True):
        raise AssertionError("SMS login must not create users implicitly")

    monkeypatch.setattr(api, "excute_sql", fake_execute)

    response = client.post(
        "/api/login/sms",
        json={"phone": "13800000000", "sms_code": "123456", "accepted_terms": True},
    )

    assert response.status_code == 404
    assert "尚未注册" in response.get_json()["message"]


def test_register_requires_terms_acceptance(client, monkeypatch):
    monkeypatch.setattr(api, "_verify_sms_code", lambda scene, phone, code: (True, ""))

    response = client.post(
        "/api/register",
        json={
            "phone": "13800000000",
            "secret": "Password123",
            "username": "tester",
            "sms_code": "123456",
            "accepted_terms": False,
        },
    )

    assert response.status_code == 400
    assert "用户协议" in response.get_json()["message"]


def test_send_login_code_does_not_enumerate_registered_phone(client, monkeypatch):
    sent = []
    monkeypatch.setattr(login, "excute_sql", lambda sql, params=None, fetch=True: [])
    monkeypatch.setattr(login, "_reserve_sms_send", lambda scene, phone, client_ip: None)
    monkeypatch.setattr(login, "_send_sms_code", lambda phone, scene: sent.append((phone, scene)))

    response = client.post("/sms/send_code", json={"phone": "13800000000", "scene": "login"})

    assert response.status_code == 200
    assert response.get_json()["success"] is True
    assert "符合当前操作条件" in response.get_json()["message"]
    assert sent == []


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
            "username": "tester",
            "sms_code": "123456",
            "accepted_terms": True,
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
    monkeypatch.setattr(api.evidence_manifest, "delete_signed_image_manifest", lambda itemid: True)

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
