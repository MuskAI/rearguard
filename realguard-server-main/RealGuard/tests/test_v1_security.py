from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from imagedetection import creat_app  # noqa: E402
from imagedetection.views import api, detection, historical_record, login  # noqa: E402


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
        if sql == "SELECT * FROM data WHERE itemid = %s AND (phone = %s OR openid = %s) LIMIT 1":
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
    _login_session(client)

    response = client.get("/image_upload/result?itemid=7")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["result"]["itemid"] == 7
    assert any(params == ("7", "13800000000", "openid-1") for _, params in calls)


def test_retrieve_history_result_uses_user_phone_and_local_proxy(client, monkeypatch):
    seen = []

    def fake_execute(sql, params=None, fetch=True):
        seen.append((sql, params))
        assert sql == "SELECT * FROM retrieve_data WHERE itemid = %s AND phone = %s"
        assert params == ("9", "13800000000")
        return [{
            "itemid": 9,
            "phone": "13800000000",
            "filename": "query.png",
            "search_type": "image",
            "results_json": "[]",
            "result_count": 0,
            "top_k": 10,
            "file_size": "3KB",
            "createtime": "2026-05-27 12:00:00",
        }]

    monkeypatch.setattr(historical_record, "excute_sql", fake_execute)
    _login_session(client)

    response = client.get("/history_retrieve/result?itemid=9")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["base_url"] == "/retrieve/library-file/image/"
    assert payload["query_file_url"] == "/static/uploads/13800000000/retrieve/query.png"
    assert seen


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
