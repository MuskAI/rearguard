from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from imagedetection import creat_app  # noqa: E402
from imagedetection.views import api, detection, login, profile, utils  # noqa: E402


@pytest.fixture
def client():
    app = creat_app()
    app.config.update(TESTING=True)
    return app.test_client()


def _login_session(client, phone="13800000000"):
    with client.session_transaction() as sess:
        sess["user_info"] = {
            "Userid": 1,
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


def test_image_result_api_queries_with_user_phone(client, monkeypatch):
    calls = []

    def fake_detection_sql(sql, params=None, fetch=True):
        calls.append((sql, params))
        if sql == "SELECT * FROM data WHERE itemid = %s AND ((phone = %s) OR ((phone IS NULL OR phone = '') AND openid = %s)) LIMIT 1":
            assert params == ("7", "13800000000", "openid-1")
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
    assert any(params == ("7", "13800000000", "openid-1") for _, params in calls)


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
    assert where == "(phone = %s) OR ((phone IS NULL OR phone = '') AND openid = %s)"
    assert params == ("13800000007", "openid-7")


def test_profile_counts_use_stable_identity_fallbacks(client, monkeypatch):
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
        assert "(phone = %s)" in sql
        assert "(phone IS NULL OR phone = '') AND openid = %s" in sql
        assert params == ("13800000000", "openid-1")


def test_legacy_login_clears_previous_account_state(client, monkeypatch):
    monkeypatch.setattr(
        login,
        "_authenticate_password_user",
        lambda phone, secret: {"Userid": 9, "username": "next-user", "phone": phone, "openid": "openid-9"},
    )
    monkeypatch.setattr(login, "_record_terms_acceptance", lambda phone: True)
    monkeypatch.setattr(login, "_sync_detection_user", lambda *args, **kwargs: None)
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
    assert "phone = %s" in calls[0][0]
    assert calls[0][1] == ("88", "13800000000", "openid-1")


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


def test_send_login_code_requires_registered_phone(client, monkeypatch):
    monkeypatch.setattr(login, "excute_sql", lambda sql, params=None, fetch=True: [])
    monkeypatch.setattr(login, "_send_sms_code", lambda phone, scene: "123456")

    response = client.post("/sms/send_code", json={"phone": "13800000000", "scene": "login"})

    assert response.status_code == 400
    assert "尚未注册" in response.get_json()["message"]


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
