from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from imagedetection import creat_app  # noqa: E402
from imagedetection.views import admin  # noqa: E402


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


def _csrf_headers(client):
    token = "test-csrf-token"
    with client.session_transaction() as sess:
        sess[admin.ADMIN_CSRF_SESSION_KEY] = token
    return {"X-CSRF-Token": token}


def test_admin_api_requires_admin_phone(client, monkeypatch):
    monkeypatch.delenv("REALGUARD_ADMIN_PHONES", raising=False)
    monkeypatch.delenv("REALGUARD_ADMIN_USER_IDS", raising=False)
    monkeypatch.delenv("REALGUARD_ADMIN_ALLOW_ANY_LOGIN", raising=False)
    _login_session(client)

    response = client.get("/api/admin/models")

    assert response.status_code == 403
    assert "无后台管理权限" in response.get_json()["message"]


def test_admin_overview_returns_models_and_metrics(client, monkeypatch):
    monkeypatch.setenv("REALGUARD_ADMIN_PHONES", "13800000000")
    _login_session(client)
    monkeypatch.setattr(
        admin.model_registry,
        "load_registry",
        lambda: {
            "routing": {"imagePrimary": "v1-onnx-mil", "fallbackEnabled": False},
            "models": [{
                "id": "v1-onnx-mil",
                "name": "V1",
                "role": "primary",
                "enabled": True,
                "artifactPath": "",
                "externalDataPath": "",
            }],
        },
    )
    monkeypatch.setattr(admin.model_registry, "artifact_status", lambda model: {"artifact": {}, "externalData": {}})
    monkeypatch.setattr(
        admin.model_registry,
        "check_model_health",
        lambda model: {"ok": False, "message": "missing external ONNX weight file"},
    )

    def fake_scalar(sql, params=None, detection=False, default=0):
        return 7 if "COUNT" in sql else default

    monkeypatch.setattr(admin, "_scalar", fake_scalar)

    response = client.get("/api/admin/overview")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["routing"]["fallbackEnabled"] is False
    assert payload["models"][0]["health"]["ok"] is False
    assert payload["metrics"]["users"]["total"] == 7


def test_dashboard_metrics_count_today_detections(monkeypatch):
    monkeypatch.setattr(admin, "_today_bounds", lambda: ("2026-06-09 00:00:00", "2026-06-10 00:00:00"))
    monkeypatch.setattr(admin, "_day_bounds", lambda offset=0: ("2026-06-08 00:00:00", "2026-06-09 00:00:00"))
    monkeypatch.setattr(admin, "_days_ago_start", lambda days: "2026-06-03 00:00:00")
    calls = []

    def fake_scalar(sql, params=None, detection=False, default=0):
        calls.append((sql, params, detection))
        if "ORDER BY itemid DESC LIMIT 1" in sql:
            return "2026-06-09 13:27:16"
        if "FROM data WHERE createtime" in sql and params == ("2026-06-09 00:00:00", "2026-06-10 00:00:00"):
            return 5
        if "FROM video_data WHERE createtime" in sql and params == ("2026-06-09 00:00:00", "2026-06-10 00:00:00"):
            return 2
        if "FROM data WHERE createtime" in sql and params == ("2026-06-08 00:00:00", "2026-06-09 00:00:00"):
            return 4
        if "FROM video_data WHERE createtime" in sql and params == ("2026-06-08 00:00:00", "2026-06-09 00:00:00"):
            return 1
        if "FROM data WHERE createtime" in sql and params == ("2026-06-03 00:00:00", "2026-06-10 00:00:00"):
            return 20
        if "FROM video_data WHERE createtime" in sql and params == ("2026-06-03 00:00:00", "2026-06-10 00:00:00"):
            return 3
        if "FROM user WHERE created_at" in sql:
            return 1
        return 10

    monkeypatch.setattr(admin, "_scalar", fake_scalar)

    metrics = admin._dashboard_metrics()

    assert metrics["detections"]["today"] == 7
    assert metrics["detections"]["todayImages"] == 5
    assert metrics["detections"]["todayVideos"] == 2
    assert metrics["detections"]["yesterday"] == 5
    assert metrics["detections"]["last7Days"] == 23
    assert metrics["detections"]["lastImageAt"] == "2026-06-09 13:27:16"
    assert metrics["users"]["todayNew"] == 1
    assert metrics["todayWindow"]["start"] == "2026-06-09 00:00:00"
    assert any(call[1] == ("2026-06-09 00:00:00", "2026-06-10 00:00:00") and call[2] for call in calls)


def test_admin_page_renders_workspace_for_allowed_user(client, monkeypatch):
    monkeypatch.setenv("REALGUARD_ADMIN_USER_IDS", "1")
    monkeypatch.setattr(admin, "_admin_account_count", lambda: 0)
    _login_session(client)

    response = client.get("/admin")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "慧鉴 AI 管理控制台" in html
    assert "模型管理" in html
    assert "线上主模型快速切换" in html
    assert "运营大屏" in html


def test_admin_account_login_sets_admin_session(client, monkeypatch):
    password_hash = admin.generate_password_hash("StrongPass123")
    monkeypatch.setattr(admin, "_admin_account_count", lambda: 1)
    monkeypatch.setattr(
        admin,
        "_find_admin_account",
        lambda identity: {
            "id": 3,
            "username": "ops",
            "phone": "13329825566",
            "password_hash": password_hash,
            "role": "admin",
            "status": "active",
        } if identity == "ops" else None,
    )
    monkeypatch.setattr(admin, "_update_admin_login", lambda account: None)

    response = client.post(
        "/admin/login",
        data={"identity": "ops", "password": "StrongPass123"},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 302
    with client.session_transaction() as sess:
        assert sess["admin_user"]["username"] == "ops"
        assert sess["admin_user"]["authType"] == "admin_account"


def test_admin_register_requires_existing_admin_session(client, monkeypatch):
    monkeypatch.setattr(admin, "_admin_account_count", lambda: 0)
    monkeypatch.setattr(admin, "_audit", lambda *args, **kwargs: None)

    created = {}

    def fake_create(username, phone, password, role="admin"):
        created.update({"username": username, "phone": phone, "password": password, "role": role})
        return True, ""

    monkeypatch.setattr(admin, "_create_admin_account", fake_create)
    monkeypatch.setattr(
        admin,
        "_find_admin_account",
        lambda identity: {
            "id": 4,
            "username": "ops",
            "phone": "13329825566",
            "password_hash": "unused",
            "role": "admin",
            "status": "active",
        } if identity == "ops" else None,
    )

    get_denied = client.get("/admin/register")
    denied = client.post("/admin/register", data={
        "username": "ops",
        "phone": "13329825566",
        "password": "StrongPass123",
        "password_confirm": "StrongPass123",
    })
    with client.session_transaction() as sess:
        sess["admin_user"] = {"Userid": "admin:1", "adminId": 1, "username": "root", "role": "admin"}
    allowed = client.post("/admin/register", data={
        "username": "ops",
        "phone": "13329825566",
        "password": "StrongPass123",
        "password_confirm": "StrongPass123",
    }, headers=_csrf_headers(client))

    assert get_denied.status_code == 403
    assert denied.status_code == 403
    assert allowed.status_code == 302
    assert created["username"] == "ops"
    with client.session_transaction() as sess:
        assert sess["admin_user"]["adminId"] == 4


def test_admin_big_screen_endpoint_uses_admin_session(client, monkeypatch):
    with client.session_transaction() as sess:
        sess["admin_user"] = {"Userid": "admin:1", "adminId": 1, "username": "ops", "role": "admin"}
    monkeypatch.setattr(
        admin,
        "_big_screen_payload",
        lambda: {
            "generatedAt": "2026-06-09 16:00:00",
            "metrics": {"detections": {"today": 7}},
            "series": {"labels": ["15:00"], "images": [5], "videos": [2]},
            "models": [],
            "recent": [],
        },
    )

    response = client.get("/api/admin/big-screen")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["metrics"]["detections"]["today"] == 7
    assert payload["series"]["images"] == [5]


def test_admin_system_returns_services(client, monkeypatch):
    monkeypatch.setenv("REALGUARD_ADMIN_PHONES", "13800000000")
    _login_session(client)
    monkeypatch.setattr(
        admin.model_registry,
        "load_registry",
        lambda: {
            "routing": {"imagePrimary": "v1-onnx-mil"},
            "models": [{
                "id": "v1-onnx-mil",
                "name": "V1",
                "runtime": "flask",
                "endpoint": "http://127.0.0.1:15001/image",
                "healthUrl": "http://127.0.0.1:15001/health",
            }],
        },
    )
    monkeypatch.setattr(admin.model_registry, "artifact_status", lambda model: {"artifact": {}, "externalData": {}})
    monkeypatch.setattr(
        admin.model_registry,
        "check_model_health",
        lambda model: {"ok": True, "artifactReady": True, "message": "ok", "warnings": []},
    )

    response = client.get("/api/admin/system")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["services"][0]["id"] == "v1-onnx-mil"
    assert payload["services"][0]["health"]["ok"] is True


def test_admin_users_supports_search_and_limit(client, monkeypatch):
    monkeypatch.setenv("REALGUARD_ADMIN_PHONES", "13800000000")
    _login_session(client)
    recorded = {}

    def fake_sql(sql, params=None, fetch=True):
        recorded["sql"] = sql
        recorded["params"] = params
        return [{
            "Userid": 2,
            "phone": "13329825566",
            "username": "muskai",
            "openid": "openid-2",
            "created_at": "2026-06-03 19:13:40",
            "terms_version": "2026-06-03",
            "terms_accepted_at": "2026-06-03 19:13:40",
        }]

    monkeypatch.setattr(admin, "excute_sql", fake_sql)

    response = client.get("/api/admin/users?q=musk&limit=20")

    assert response.status_code == 200
    assert "phone LIKE" in recorded["sql"]
    assert recorded["params"][-1] == 20
    assert response.get_json()["users"][0]["username"] == "muskai"


def test_admin_api_keys_returns_masked_keys(client, monkeypatch):
    monkeypatch.setenv("REALGUARD_ADMIN_PHONES", "13800000000")
    _login_session(client)

    def fake_sql(sql, params=None, fetch=True):
        assert "developer_api_keys" in sql
        return [{
            "id": 4,
            "user_id": 2,
            "name": "prod",
            "key_prefix": "rg_sk_",
            "key_last4": "9abc",
            "scopes": "detect",
            "status": "active",
            "created_at": "2026-06-03 19:13:40",
            "last_used_at": None,
            "revoked_at": None,
            "last_used_ip": "",
            "phone": "13329825566",
            "username": "muskai",
        }]

    monkeypatch.setattr(admin, "excute_sql", fake_sql)

    response = client.get("/api/admin/api-keys?q=prod")

    assert response.status_code == 200
    key = response.get_json()["keys"][0]
    assert key["masked"] == "rg_sk_...9abc"
    assert key["owner"] == "muskai"


def test_admin_detections_tolerates_missing_optional_columns(client, monkeypatch):
    monkeypatch.setenv("REALGUARD_ADMIN_PHONES", "13800000000")
    _login_session(client)
    recorded = {}

    def fake_detection_sql(sql, params=None, fetch=True):
        normalized = sql.strip().upper()
        if normalized.startswith("SHOW COLUMNS"):
            return [
                {"Field": "itemid"},
                {"Field": "createtime"},
                {"Field": "filename"},
                {"Field": "fake"},
                {"Field": "phone"},
                {"Field": "aigc"},
                {"Field": "clarity"},
                {"Field": "feedback"},
            ]
        recorded["sql"] = sql
        recorded["params"] = params
        return [{
            "itemid": 612,
            "createtime": "2026-06-09 13:27:16",
            "filename": "sample.jpg",
            "fake": 82.8,
            "detector_probability": None,
            "phone": "13329825566",
            "aigc": "AI生成图像",
            "clarity": "中",
            "feedback": None,
        }]

    monkeypatch.setattr(admin, "excute_detection_sql", fake_detection_sql)
    monkeypatch.setattr(
        admin.admin_state,
        "model_runs_by_itemids",
        lambda itemids: {"612": {"model": {"id": "aliyun-aigc-pro"}}},
    )

    response = client.get("/api/admin/detections?limit=10")

    assert response.status_code == 200
    assert "NULL AS detector_probability" in recorded["sql"]
    payload = response.get_json()
    assert payload["detections"][0]["id"] == 612
    assert payload["detections"][0]["detectorProbability"] is None
    assert payload["detections"][0]["modelRoute"]["model"]["id"] == "aliyun-aigc-pro"


def test_v1_health_marks_capability_unready_when_external_data_missing(monkeypatch, tmp_path):
    onnx_path = tmp_path / "model_deploy.onnx"
    external_path = tmp_path / "model_deploy.onnx.data"
    onnx_path.write_bytes(b"onnx")

    class Response:
        status_code = 200
        text = "ok"

    class Session:
        trust_env = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(admin.model_registry.requests, "Session", Session)

    health = admin.model_registry.check_model_health({
        "id": "v1-onnx-mil",
        "healthUrl": "http://127.0.0.1:15001/health",
        "artifactPath": str(onnx_path),
        "externalDataPath": str(external_path),
    })

    assert health["serviceOk"] is True
    assert health["ok"] is False
    assert health["artifactReady"] is False
    assert health["capabilityReady"] is False
    assert "model_deploy.onnx.data" in health["warnings"][0]


def test_admin_can_create_probe_and_delete_candidate_model(client, monkeypatch, tmp_path):
    monkeypatch.setenv("REALGUARD_ADMIN_PHONES", "13800000000")
    monkeypatch.setattr(admin.model_registry, "REGISTRY_PATH", tmp_path / "registry.json")
    monkeypatch.setattr(admin.admin_state, "STATE_PATH", tmp_path / "admin_state.json")
    monkeypatch.setattr(admin, "_probe_model", lambda model: {"ok": True, "message": "probe ok"})
    _login_session(client)

    headers = _csrf_headers(client)
    created = client.post("/api/admin/models", json={
        "id": "v3-test",
        "name": "V3 Test",
        "endpoint": "http://127.0.0.1:19000/image",
        "healthUrl": "http://127.0.0.1:19000/health",
    }, headers=headers)
    assert created.status_code == 201
    assert created.get_json()["model"]["id"] == "v3-test"

    probe = client.post("/api/admin/models/v3-test/probe", headers=headers)
    assert probe.status_code == 200
    assert probe.get_json()["probe"]["ok"] is True

    deleted = client.delete("/api/admin/models/v3-test", headers=headers)
    assert deleted.status_code == 200
    audit = client.get("/api/admin/audit").get_json()["audit"]
    assert [item["action"] for item in audit[:3]] == ["model.delete", "model.probe", "model.create"]


def test_admin_rejects_deleting_routed_model(client, monkeypatch, tmp_path):
    monkeypatch.setenv("REALGUARD_ADMIN_PHONES", "13800000000")
    monkeypatch.setattr(admin.model_registry, "REGISTRY_PATH", tmp_path / "registry.json")
    _login_session(client)

    response = client.delete("/api/admin/models/v1-onnx-mil", headers=_csrf_headers(client))

    assert response.status_code == 400
    assert "路由策略" in response.get_json()["message"]


def test_admin_assurance_reports_v1_blockers(client, monkeypatch):
    monkeypatch.setenv("REALGUARD_ADMIN_PHONES", "13800000000")
    _login_session(client)
    monkeypatch.setattr(
        admin.model_registry,
        "load_registry",
        lambda: {
            "routing": {"imagePrimary": "v1-legacy-tunnel", "fallbackEnabled": False},
            "models": [{
                "id": "v1-legacy-tunnel",
                "name": "Legacy",
                "enabled": True,
                "endpoint": "http://127.0.0.1:15000/image",
                "healthUrl": "http://127.0.0.1:15000/image",
            }],
        },
    )
    monkeypatch.setattr(admin.model_registry, "artifact_status", lambda model: {"artifact": {}, "externalData": {}})
    monkeypatch.setattr(
        admin.model_registry,
        "check_model_health",
        lambda model: {"ok": False, "serviceOk": False, "message": "connection refused", "warnings": []},
    )

    response = client.get("/api/admin/assurance")

    assert response.status_code == 200
    payload = response.get_json()["assurance"]
    assert payload["online"] is False
    assert "v1-legacy-tunnel" in payload["blockers"][0]


def test_admin_alerts_and_api_key_quota_are_persisted(client, monkeypatch, tmp_path):
    monkeypatch.setenv("REALGUARD_ADMIN_PHONES", "13800000000")
    monkeypatch.setattr(admin.admin_state, "STATE_PATH", tmp_path / "admin_state.json")
    _login_session(client)

    headers = _csrf_headers(client)
    alerts = client.post("/api/admin/alerts", json={
        "enabled": True,
        "webhookUrl": "https://example.test/hook",
        "rules": {"v1Offline": True, "fallbackEnabled": False},
    }, headers=headers)
    quota = client.post("/api/admin/api-keys/4/quota", json={"dailyLimit": 200, "rateLimitPerMinute": 20}, headers=headers)

    assert alerts.status_code == 200
    assert alerts.get_json()["alerts"]["enabled"] is True
    assert quota.status_code == 200
    assert quota.get_json()["quota"]["dailyLimit"] == 200
    audit_actions = [item["action"] for item in client.get("/api/admin/audit").get_json()["audit"]]
    assert "alerts.update" in audit_actions
    assert "api_key.quota.update" in audit_actions


def test_admin_detection_review_writes_feedback_and_audit(client, monkeypatch, tmp_path):
    monkeypatch.setenv("REALGUARD_ADMIN_PHONES", "13800000000")
    monkeypatch.setattr(admin.admin_state, "STATE_PATH", tmp_path / "admin_state.json")
    _login_session(client)
    calls = []

    def fake_detection_sql(sql, params=None, fetch=True):
        calls.append((sql, params, fetch))
        if sql.strip().upper().startswith("SELECT"):
            return [{"itemid": 9, "feedback": None}]
        if sql.strip().upper().startswith("UPDATE"):
            return 1
        return []

    monkeypatch.setattr(admin, "excute_detection_sql", fake_detection_sql)

    response = client.post("/api/admin/detections/9/review", json={"feedback": -1}, headers=_csrf_headers(client))

    assert response.status_code == 200
    assert response.get_json()["review"]["feedback"] == "不满意"
    assert any(call[1] == ("不满意", 9) for call in calls)
    assert client.get("/api/admin/audit").get_json()["audit"][0]["action"] == "detection.review"


def test_aliyun_green_health_requires_credentials(monkeypatch):
    monkeypatch.delenv("ALIBABA_CLOUD_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", raising=False)
    monkeypatch.delenv("REALGUARD_PUBLIC_BASE_URL", raising=False)

    health = admin.model_registry.check_model_health({
        "id": "aliyun-aigc-pro",
        "runtime": "aliyun-green",
        "endpoint": "internal://aliyun/aigcDetector_pro",
    })

    assert health["ok"] is False
    assert health["capabilityReady"] is False
    assert "missing ALIBABA_CLOUD_ACCESS_KEY_ID" in health["warnings"]
    assert "missing ALIBABA_CLOUD_ACCESS_KEY_SECRET" in health["warnings"]


def test_admin_probe_uses_aliyun_adapter(client, monkeypatch, tmp_path):
    monkeypatch.setenv("REALGUARD_ADMIN_PHONES", "13800000000")
    monkeypatch.setattr(admin.admin_state, "STATE_PATH", tmp_path / "admin_state.json")
    _login_session(client)
    monkeypatch.setattr(
        admin.model_registry,
        "get_model",
        lambda model_id: {
            "id": model_id,
            "runtime": "aliyun-green",
            "endpoint": "internal://aliyun/aigcDetector_pro",
        },
    )
    monkeypatch.setattr(
        admin.aliyun_green,
        "detect_image_bytes",
        lambda service, image_bytes, filename: {"ok": True, "service": service, "latencyMs": 12},
    )

    response = client.post("/api/admin/models/aliyun-aigc-pro/probe", headers=_csrf_headers(client))

    assert response.status_code == 200
    assert response.get_json()["probe"]["ok"] is True


def test_admin_write_requires_csrf_token(client, monkeypatch):
    monkeypatch.setenv("REALGUARD_ADMIN_PHONES", "13800000000")
    _login_session(client)

    response = client.post("/api/admin/models", json={"id": "v3-no-csrf"})

    assert response.status_code == 403
    assert "CSRF" in response.get_json()["message"]


def test_readonly_admin_cannot_create_model(client, monkeypatch):
    with client.session_transaction() as sess:
        sess["admin_user"] = {
            "Userid": "admin:9",
            "adminId": 9,
            "username": "viewer",
            "role": "readonly",
            "authType": "admin_account",
        }

    response = client.post(
        "/api/admin/models",
        json={"id": "v3-readonly", "endpoint": "http://127.0.0.1:19000/image"},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 403
    assert "model.manage" in response.get_json()["message"]


def test_admin_accounts_can_be_listed_and_updated(client, monkeypatch):
    with client.session_transaction() as sess:
        sess["admin_user"] = {
            "Userid": "admin:1",
            "adminId": 1,
            "username": "root",
            "role": "admin",
            "authType": "admin_account",
        }
    monkeypatch.setattr(admin, "_ensure_admin_account_table", lambda: True)
    monkeypatch.setattr(admin, "_audit", lambda *args, **kwargs: None)
    calls = []

    def fake_sql(sql, params=None, fetch=True):
        calls.append((sql, params, fetch))
        normalized = sql.strip().upper()
        if normalized.startswith("UPDATE ADMIN_ACCOUNTS"):
            return 1
        if "FROM ADMIN_ACCOUNTS" in normalized and "WHERE ID" in normalized:
            role = "operator" if any(call[0].strip().upper().startswith("UPDATE ADMIN_ACCOUNTS") for call in calls) else "readonly"
            return [{
                "id": 2,
                "username": "ops",
                "phone": "13300000000",
                "role": role,
                "status": "active",
                "created_at": "2026-06-10 10:00:00",
                "last_login_at": None,
                "last_login_ip": "",
            }]
        if "FROM ADMIN_ACCOUNTS" in normalized:
            return [{
                "id": 2,
                "username": "ops",
                "phone": "13300000000",
                "role": "readonly",
                "status": "active",
                "created_at": "2026-06-10 10:00:00",
                "last_login_at": None,
                "last_login_ip": "",
            }]
        return []

    monkeypatch.setattr(admin, "excute_sql", fake_sql)

    listed = client.get("/api/admin/admins?limit=20")
    updated = client.post(
        "/api/admin/admins/2",
        json={"role": "operator", "status": "active"},
        headers=_csrf_headers(client),
    )

    assert listed.status_code == 200
    assert listed.get_json()["admins"][0]["username"] == "ops"
    assert updated.status_code == 200
    assert updated.get_json()["admin"]["role"] == "operator"


def test_admin_cannot_disable_self(client, monkeypatch):
    with client.session_transaction() as sess:
        sess["admin_user"] = {
            "Userid": "admin:2",
            "adminId": 2,
            "username": "root",
            "role": "admin",
            "authType": "admin_account",
        }
    monkeypatch.setattr(admin, "_ensure_admin_account_table", lambda: True)
    monkeypatch.setattr(
        admin,
        "excute_sql",
        lambda sql, params=None, fetch=True: [{
            "id": 2,
            "username": "root",
            "phone": "",
            "role": "admin",
            "status": "active",
            "created_at": "2026-06-10 10:00:00",
            "last_login_at": None,
            "last_login_ip": "",
        }],
    )

    response = client.post(
        "/api/admin/admins/2",
        json={"role": "readonly", "status": "disabled"},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 400
    assert "当前登录管理员" in response.get_json()["message"]


def test_big_screen_readonly_token_allows_unauthenticated_fetch(client, monkeypatch):
    monkeypatch.setenv("REALGUARD_BIG_SCREEN_TOKEN", "screen-secret")
    admin._clear_big_screen_cache()
    monkeypatch.setattr(
        admin,
        "_big_screen_payload",
        lambda: {
            "generatedAt": "2026-06-09 18:00:00",
            "metrics": {"detections": {"today": 11}},
            "series": {"labels": ["18:00"], "images": [11], "videos": [0]},
            "models": [],
            "recent": [],
            "anomalies": [],
        },
    )

    denied = client.get("/api/admin/big-screen")
    allowed = client.get("/api/admin/big-screen", headers={"X-RealGuard-Screen-Token": "screen-secret"})

    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert allowed.get_json()["metrics"]["detections"]["today"] == 11


def test_admin_routing_update_can_rollback(client, monkeypatch, tmp_path):
    monkeypatch.setenv("REALGUARD_ADMIN_PHONES", "13800000000")
    monkeypatch.setattr(admin.model_registry, "REGISTRY_PATH", tmp_path / "registry.json")
    _login_session(client)
    headers = _csrf_headers(client)

    updated = client.post(
        "/api/admin/routing",
        json={"imagePrimary": "v1-legacy-tunnel", "imageFallback": "v2-qwen-vlm", "fallbackEnabled": False},
        headers=headers,
    )
    rolled_back = client.post("/api/admin/routing/rollback", json={}, headers=headers)

    assert updated.status_code == 200
    assert updated.get_json()["routing"]["imagePrimary"] == "v1-legacy-tunnel"
    assert rolled_back.status_code == 200
    assert rolled_back.get_json()["routing"]["imagePrimary"] == "v1-onnx-mil"


def test_admin_swarm_config_can_be_updated(client, monkeypatch, tmp_path):
    monkeypatch.setenv("REALGUARD_ADMIN_PHONES", "13800000000")
    monkeypatch.setattr(admin.model_registry, "REGISTRY_PATH", tmp_path / "registry.json")
    _login_session(client)
    headers = _csrf_headers(client)

    loaded = client.get("/api/admin/swarm")
    updated = client.post(
        "/api/admin/swarm",
        json={
            "enabled": True,
            "minExperts": 3,
            "consensusThreshold": 0.72,
            "experts": [
                {"id": "primary", "enabled": True, "weight": 0.5},
                {"id": "metadata", "enabled": False, "weight": 0.0},
            ],
        },
        headers=headers,
    )

    assert loaded.status_code == 200
    assert any(item["id"] == "aliyun_ps" for item in loaded.get_json()["swarm"]["experts"])
    assert updated.status_code == 200
    swarm = updated.get_json()["swarm"]
    assert swarm["minExperts"] == 3
    assert swarm["consensusThreshold"] == 0.72
    assert next(item for item in swarm["experts"] if item["id"] == "metadata")["enabled"] is False


def test_admin_detection_detail_returns_model_chain(client, monkeypatch):
    monkeypatch.setenv("REALGUARD_ADMIN_PHONES", "13800000000")
    _login_session(client)

    def fake_detection_sql(sql, params=None, fetch=True):
        normalized = sql.strip().upper()
        if normalized.startswith("SHOW COLUMNS"):
            return [
                {"Field": "itemid"},
                {"Field": "createtime"},
                {"Field": "filename"},
                {"Field": "fake"},
                {"Field": "detector_probability"},
                {"Field": "phone"},
                {"Field": "aigc"},
                {"Field": "clarity"},
                {"Field": "feedback"},
            ]
        if "FROM EXIF" in normalized:
            return [{"all_metadata": '{"Make":"Canon"}'}]
        return [{
            "itemid": 612,
            "Userid": 2,
            "openid": "openid-2",
            "createtime": "2026-06-09 13:27:16",
            "filename": "sample.jpg",
            "fake": 82.8,
            "detector_probability": 0.828,
            "phone": "13329825566",
            "aigc": "AI生成图像",
            "clarity": "中",
            "feedback": None,
            "explantation": "检测说明",
        }]

    monkeypatch.setattr(admin, "excute_detection_sql", fake_detection_sql)
    monkeypatch.setattr(
        admin.admin_state,
        "model_runs_by_itemids",
        lambda itemids: {"612": {"route": "primary", "model": {"id": "aliyun-aigc-pro"}, "meta": {"latencyMs": 15}}},
    )

    response = client.get("/api/admin/detections/612")

    assert response.status_code == 200
    detection = response.get_json()["detection"]
    assert detection["metadata"]["Make"] == "Canon"
    assert detection["modelRoute"]["model"]["id"] == "aliyun-aigc-pro"


def test_create_admin_cli_uses_explicit_initializer(monkeypatch):
    app = creat_app()
    created = {}

    monkeypatch.setattr(admin, "apply_admin_schema", lambda: (True, ["admin_accounts: ready"]))
    monkeypatch.setattr(
        admin,
        "_create_admin_account",
        lambda username, phone, password, role="admin": created.update({
            "username": username,
            "phone": phone,
            "password": password,
            "role": role,
        }) or (True, ""),
    )

    result = app.test_cli_runner().invoke(
        args=[
            "create-admin",
            "--username", "root",
            "--password", "StrongPass123",
            "--phone", "13329825566",
            "--role", "super_admin",
            "--migrate",
        ]
    )

    assert result.exit_code == 0
    assert created["username"] == "root"
    assert created["role"] == "super_admin"
