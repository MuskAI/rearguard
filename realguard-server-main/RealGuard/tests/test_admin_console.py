from pathlib import Path
import json
import sys
import time
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from imagedetection import creat_app  # noqa: E402
from imagedetection.views import admin  # noqa: E402
from imagedetection.views import developer_platform  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(admin, "_refresh_admin_session", lambda user: user)
    admin.model_registry.clear_health_cache()
    admin._clear_dashboard_metrics_cache()
    admin._clear_big_screen_cache()
    app = creat_app()
    app.config.update(TESTING=True)
    return app.test_client()


def _login_session(client, phone="13800000000", admin_role=None):
    with client.session_transaction() as sess:
        sess["user_info"] = {
            "Userid": 1,
            "username": "tester",
            "phone": phone,
            "openid": "openid-1",
        }
        if admin_role:
            sess[admin.ADMIN_SESSION_KEY] = {
                "Userid": "admin:1",
                "adminId": 1,
                "username": "root",
                "phone": phone,
                "role": admin_role,
                "authType": "admin_account",
                "sessionVersion": 1,
                "issuedAt": int(time.time()),
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
    monkeypatch.setattr(admin.traffic_geo, "traffic_summary", lambda: {
        "ready": True,
        "windowHours": 24,
        "homepage": {"pageViews": 23, "uniqueVisitors": 17},
        "site": {"pageViews": 81, "uniqueVisitors": 31},
        "onlineVisitors": 4,
        "onlineWindowMinutes": 5,
    })
    admin._clear_dashboard_metrics_cache()

    metrics = admin._dashboard_metrics()

    assert metrics["detections"]["today"] == 7
    assert metrics["detections"]["todayImages"] == 5
    assert metrics["detections"]["todayVideos"] == 2
    assert metrics["detections"]["yesterday"] == 5
    assert metrics["detections"]["last7Days"] == 23
    assert metrics["detections"]["lastImageAt"] == "2026-06-09 13:27:16"
    assert metrics["users"]["todayNew"] == 1
    assert metrics["traffic"] == {
        "ready": True,
        "windowHours": 24,
        "homepagePageViews": 23,
        "homepageUniqueVisitors": 17,
        "sitePageViews": 81,
        "siteUniqueVisitors": 31,
        "onlineVisitors": 4,
        "onlineWindowMinutes": 5,
    }
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


def test_admin_screen_renders_interactive_operations_controls(client, monkeypatch):
    monkeypatch.setenv("REALGUARD_ADMIN_USER_IDS", "1")
    monkeypatch.setattr(admin, "_admin_account_count", lambda: 0)
    _login_session(client)

    response = client.get("/admin/screen")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "慧鉴 AI 运营态势大屏" in html
    assert 'id="autoRefresh"' in html
    assert 'data-range="6"' in html
    assert 'data-distribution-mode="feedback"' in html
    assert 'data-primary-view="traffic"' in html
    assert 'id="trafficMap"' in html
    assert html.index('class="grid"') < html.index('id="runtime"')
    assert "算法服务器 · GPU 推理" in html
    assert "/static/js/echarts-6.1.0.min.js" in html
    assert 'id="inspector"' in html
    assert "['trend','routes','distribution'].forEach(setupCanvas)" in html


def test_admin_page_exposes_exact_developer_call_quota_control(client, monkeypatch):
    monkeypatch.setenv("REALGUARD_ADMIN_USER_IDS", "1")
    monkeypatch.setattr(admin, "_admin_account_count", lambda: 0)
    _login_session(client)

    response = client.get("/admin")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "设置 API 次数" in html
    assert "/api/admin/developer/accounts/" in html
    assert "remainingCalls" in html


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
    monkeypatch.setattr(admin, "_audit", lambda *args, **kwargs: None)

    response = client.post(
        "/admin/login",
        data={"identity": "ops", "password": "StrongPass123"},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 302
    with client.session_transaction() as sess:
        assert sess["admin_user"]["username"] == "ops"
        assert sess["admin_user"]["authType"] == "admin_account"
        assert sess[admin.ADMIN_CSRF_SESSION_KEY] != "test-csrf-token"


def test_admin_login_hides_account_inventory_and_sets_security_headers(client, monkeypatch):
    monkeypatch.setattr(admin, "_admin_account_count", lambda: (_ for _ in ()).throw(AssertionError("must not query")))

    response = client.get("/admin/login")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "已创建管理员账号" not in html
    assert "当前白名单管理员会话" not in html
    assert "no-store" in response.headers["Cache-Control"]
    assert "private" in response.headers["Cache-Control"]
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_admin_logout_requires_csrf_post_and_clears_session(client, monkeypatch):
    audit = []
    monkeypatch.setattr(admin, "_audit", lambda actor, action, target, **kwargs: audit.append((action, target)))
    with client.session_transaction() as sess:
        sess[admin.ADMIN_SESSION_KEY] = {
            "Userid": "admin:1",
            "adminId": 1,
            "username": "ops",
            "role": "admin",
        }
        sess[admin.ADMIN_SCREEN_SESSION_KEY] = "screen-claim"

    denied = client.get("/admin/logout")
    response = client.post("/admin/logout", headers=_csrf_headers(client))

    assert denied.status_code == 405
    assert response.status_code == 302
    with client.session_transaction() as sess:
        assert admin.ADMIN_SESSION_KEY not in sess
        assert admin.ADMIN_CSRF_SESSION_KEY not in sess
        assert admin.ADMIN_SCREEN_SESSION_KEY not in sess
    assert audit == [("admin.logout", "1")]


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
        sess["admin_user"] = {"Userid": "admin:1", "adminId": 1, "username": "root", "role": "super_admin"}
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
        assert sess["admin_user"]["adminId"] == 1


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
    _login_session(client, admin_role="admin")
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


def test_host_telemetry_reports_normalized_linux_resources(monkeypatch):
    monkeypatch.setattr(admin.os, "cpu_count", lambda: 4)
    monkeypatch.setattr(admin.os, "getloadavg", lambda: (1.0, 0.75, 0.5))
    monkeypatch.setattr(
        admin,
        "_read_proc_text",
        lambda path: "MemTotal: 1000 kB\nMemAvailable: 250 kB\n" if path == "/proc/meminfo" else "86461.0 0.0\n",
    )
    monkeypatch.setattr(admin.shutil, "disk_usage", lambda path: SimpleNamespace(total=1000, used=400, free=600))
    monkeypatch.setattr(admin, "_PROCESS_STARTED_MONOTONIC", 100.0)
    monkeypatch.setattr(admin.time, "monotonic", lambda: 160.0)

    payload = admin._host_telemetry()

    assert payload["status"] == "healthy"
    assert payload["cpu"]["cores"] == 4
    assert payload["cpu"]["loadPercent"] == 25.0
    assert payload["memory"]["usedPercent"] == 75.0
    assert payload["disk"]["usedPercent"] == 40.0
    assert payload["uptimeSeconds"] == 86461
    assert payload["processUptimeSeconds"] == 60


def test_screen_algorithm_server_payload_uses_primary_model_gpu_telemetry():
    payload = admin._screen_algorithm_server_payload(
        [{
            "id": "v1-onnx-mil",
            "name": "RealGuard 主模型",
            "enabled": True,
            "health": {
                # The Web proxy has no local model artifact, while the remote CUDA model is ready.
                "ok": False,
                "serviceOk": True,
                "latencyMs": 31,
                "telemetry": {
                    "inferenceMode": "remote-cuda",
                    "activeProvider": "CUDAExecutionProvider",
                    "cudaDeviceId": 0,
                    "remoteReady": True,
                    "remoteLatencyMs": 18.4,
                    "queueDepth": 2,
                },
            },
        }],
        {"imagePrimary": "v1-onnx-mil"},
    )

    assert payload == {
        "status": "healthy",
        "serviceReady": True,
        "modelReady": True,
        "modelId": "v1-onnx-mil",
        "modelName": "RealGuard 主模型",
        "inferenceMode": "remote-cuda",
        "provider": "CUDAExecutionProvider",
        "cudaDeviceId": 0,
        "latencyMs": 18.4,
        "queueDepth": 2,
    }


def test_big_screen_payload_excludes_internal_topology_and_assurance_details(monkeypatch):
    secret_endpoint = "http://10.1.20.66:15001/image"
    raw_model = {
        "id": "v1-onnx-mil",
        "name": "V1",
        "runtime": "flask",
        "enabled": True,
        "endpoint": secret_endpoint,
        "healthUrl": "http://127.0.0.1:15001/health",
        "artifactPath": "/srv/private/model.onnx",
    }
    monkeypatch.setattr(
        admin.model_registry,
        "load_registry",
        lambda: {
            "routing": {
                "imagePrimary": "v1-onnx-mil",
                "imageFallback": "v2-private",
                "fallbackEnabled": False,
                "notes": "private routing notes",
            },
            "models": [raw_model],
        },
    )
    monkeypatch.setattr(admin.model_registry, "artifact_status", lambda model: {"path": "/srv/private/model.onnx"})
    monkeypatch.setattr(
        admin.model_registry,
        "check_model_health",
        lambda model: {
            "ok": False,
            "serviceOk": True,
            "artifactReady": False,
            "latencyMs": 18,
            "message": "missing /srv/private/model.onnx",
        },
    )
    monkeypatch.setattr(admin, "_dashboard_metrics", lambda: {"detections": {}, "users": {}})
    monkeypatch.setattr(
        admin,
        "_v1_assurance",
        lambda **kwargs: {
            "online": False,
            "blockers": [f"private endpoint {secret_endpoint}"],
            "recommendations": ["private recommendation"],
            "primary": raw_model,
            "routing": {"fallbackEnabled": True},
            "alerts": {"webhookUrl": "https://private.example/hook"},
        },
    )
    monkeypatch.setattr(admin, "_host_telemetry", lambda: {"status": "healthy", "cpu": {}, "memory": {}, "disk": {}})
    monkeypatch.setattr(admin, "_hourly_detection_series", lambda hours=24: {"labels": [], "images": [], "videos": []})
    monkeypatch.setattr(admin, "_label_distribution", lambda: [])
    monkeypatch.setattr(admin, "_feedback_distribution", lambda: [])
    monkeypatch.setattr(admin, "_route_distribution", lambda: [])
    monkeypatch.setattr(admin, "_recent_detection_items", lambda limit=12: [])
    monkeypatch.setattr(
        admin.traffic_geo,
        "traffic_summary",
        lambda: {"ready": True, "uniqueVisitors": 3, "provinces": [], "privacy": {"rawIpsIncluded": False}},
    )

    payload = admin._big_screen_payload()
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["privacy"] == {
        "piiIncluded": False,
        "internalEndpointsIncluded": False,
        "rawIpsIncluded": False,
    }
    assert payload["traffic"]["privacy"]["rawIpsIncluded"] is False
    assert payload["routing"] == {
        "imagePrimary": "v1-onnx-mil",
        "imageFallback": "v2-private",
        "fallbackEnabled": False,
    }
    assert payload["models"][0]["health"]["message"] == "服务在线，模型能力未就绪"
    assert payload["assurance"] == {"online": False, "blockerCount": 1, "recommendationCount": 1}
    assert any(item["title"] == "自动兜底已开启" for item in payload["anomalies"])
    assert secret_endpoint not in serialized
    assert "/srv/private" not in serialized
    assert "private routing notes" not in serialized
    assert "private.example" not in serialized


def test_big_screen_recent_items_remove_user_and_file_identifiers(monkeypatch):
    queries = []

    def fake_detection_sql(sql, params=None):
        queries.append(sql)
        if sql.strip().upper().startswith("SHOW COLUMNS"):
            return [
                {"Field": "itemid"},
                {"Field": "createtime"},
                {"Field": "fake"},
                {"Field": "aigc"},
                {"Field": "clarity"},
                {"Field": "feedback"},
                {"Field": "filename"},
                {"Field": "phone"},
            ]
        return [{
            "itemid": 612,
            "createtime": "2026-06-09 13:27:16",
            "filename": "private-person.jpg",
            "phone": "13329825566",
            "aigc": "真实图像",
            "fake": 12.3,
            "clarity": "高",
            "feedback": "满意",
        }]

    monkeypatch.setattr(admin, "excute_detection_sql", fake_detection_sql)
    monkeypatch.setattr(
        admin.admin_state,
        "model_runs_by_itemids",
        lambda itemids: {"612": {
            "route": "primary",
            "status": "success",
            "model": {
                "id": "v1-onnx-mil",
                "name": "V1",
                "runtime": "flask",
                "version": "1.0",
                "endpoint": "http://127.0.0.1:15001/image",
            },
            "actor": {"phone": "13329825566", "username": "private-user"},
        }},
    )

    item = admin._recent_detection_items(1)[0]
    serialized = json.dumps(item, ensure_ascii=False)

    assert "filename" not in item
    assert "phone" not in item
    assert "filename" not in queries[-1].lower()
    assert "phone" not in queries[-1].lower()
    assert item["modelRoute"]["model"]["id"] == "v1-onnx-mil"
    assert "private-person.jpg" not in serialized
    assert "13329825566" not in serialized
    assert "127.0.0.1" not in serialized
    assert "private-user" not in serialized


def test_admin_users_supports_search_and_limit(client, monkeypatch):
    monkeypatch.setenv("REALGUARD_ADMIN_PHONES", "13800000000")
    _login_session(client, admin_role="admin")
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
    assert recorded["params"][-1] == 21
    assert response.get_json()["users"][0]["username"] == "muskai"


def test_admin_api_keys_returns_masked_keys(client, monkeypatch):
    monkeypatch.setenv("REALGUARD_ADMIN_PHONES", "13800000000")
    _login_session(client, admin_role="admin")

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


def test_api_key_owner_and_phone_search_are_redacted_without_pii_permission(client, monkeypatch):
    _login_session(client, admin_role="operator")
    recorded = {}

    def fake_sql(sql, params=None, fetch=True):
        recorded["sql"] = sql
        recorded["params"] = params
        return [{
            "id": 4,
            "user_id": 2,
            "name": "prod",
            "key_prefix": "rg_sk_",
            "key_last4": "9abc",
            "scopes": "detect",
            "status": "active",
            "phone": "13329825566",
            "username": "13329825566",
        }]

    monkeypatch.setattr(admin, "excute_sql", fake_sql)

    response = client.get("/api/admin/api-keys?q=13329825566")

    assert response.status_code == 200
    assert "u.phone LIKE" not in recorded["sql"]
    assert "u.username LIKE" not in recorded["sql"]
    key = response.get_json()["keys"][0]
    assert key["owner"] == "133****5566"
    assert key["phone"] == "133****5566"


def test_reviewer_cannot_export_users_without_user_view(client, monkeypatch):
    _login_session(client, admin_role="reviewer")
    monkeypatch.setattr(
        admin,
        "excute_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not query users")),
    )

    response = client.get("/api/admin/users/export")

    assert response.status_code == 403
    assert "user.view" in response.get_json()["message"]


def test_admin_detections_tolerates_missing_optional_columns(client, monkeypatch):
    monkeypatch.setenv("REALGUARD_ADMIN_PHONES", "13800000000")
    _login_session(client, admin_role="admin")
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


def test_reviewer_detection_route_redacts_topology_actor_and_untrusted_meta(client, monkeypatch):
    _login_session(client, admin_role="reviewer")

    def fake_detection_sql(sql, params=None, fetch=True):
        if sql.strip().upper().startswith("SHOW COLUMNS"):
            return [
                {"Field": field}
                for field in ("itemid", "createtime", "filename", "fake", "phone", "aigc", "clarity", "feedback")
            ]
        return [{
            "itemid": 612,
            "createtime": "2026-06-09 13:27:16",
            "filename": "sample.jpg",
            "fake": 82.8,
            "phone": "13329825566",
            "aigc": "AI生成图像",
            "clarity": "中",
            "feedback": None,
        }]

    monkeypatch.setattr(admin, "excute_detection_sql", fake_detection_sql)
    monkeypatch.setattr(
        admin.admin_state,
        "model_runs_by_itemids",
        lambda itemids: {"612": {
            "id": "run-1",
            "route": "primary",
            "status": "success",
            "model": {
                "id": "v1-onnx-mil",
                "name": "V1",
                "runtime": "private-runtime",
                "endpoint": "https://10.1.20.66/private",
            },
            "actor": {"id": 7, "username": "private-user", "phone": "13329825566"},
            "meta": {"latencyMs": 15, "secretToken": "do-not-return"},
        }},
    )

    response = client.get("/api/admin/detections?limit=10")

    assert response.status_code == 200
    route = response.get_json()["detections"][0]["modelRoute"]
    assert route["model"] == {"id": "v1-onnx-mil", "name": "V1", "version": ""}
    assert route["actor"] == {"id": 7}
    assert route["meta"] == {"latencyMs": 15}
    serialized = json.dumps(route)
    assert "10.1.20.66" not in serialized
    assert "private-user" not in serialized
    assert "do-not-return" not in serialized


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
    _login_session(client, admin_role="admin")

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
    _login_session(client, admin_role="admin")

    response = client.delete("/api/admin/models/v1-onnx-mil", headers=_csrf_headers(client))

    assert response.status_code == 400
    assert "路由策略" in response.get_json()["message"]


def test_admin_assurance_reports_v1_blockers(client, monkeypatch):
    monkeypatch.setenv("REALGUARD_ADMIN_PHONES", "13800000000")
    _login_session(client, admin_role="admin")
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
    monkeypatch.setattr(
        admin,
        "excute_sql",
        lambda sql, params=None, fetch=True: [{"id": 4}] if "SELECT id FROM developer_api_keys" in sql else 1,
    )
    _login_session(client, admin_role="admin")

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
    _login_session(client, admin_role="admin")
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
    assert response.get_json()["review"]["feedback"] == -1
    assert any(call[1] == (-1, 9) for call in calls)
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
    _login_session(client, admin_role="admin")
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
    _login_session(client, admin_role="admin")

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
            "role": "super_admin",
            "authType": "admin_account",
        }
    monkeypatch.setattr(admin, "_ensure_admin_account_table", lambda: True)
    monkeypatch.setattr(admin, "_audit", lambda *args, **kwargs: None)
    def fake_sql(sql, params=None, fetch=True):
        normalized = sql.strip().upper()
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
    monkeypatch.setattr(
        admin,
        "_update_admin_account_atomic",
        lambda admin_id, role, status, actor_admin_id=None: (
            {
                "id": admin_id,
                "username": "ops",
                "phone": "13300000000",
                "role": "readonly",
                "status": "active",
            },
            {
                "id": admin_id,
                "username": "ops",
                "phone": "13300000000",
                "role": role,
                "status": status,
                "session_version": 2,
            },
            "",
        ),
    )

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
            "role": "super_admin",
            "authType": "admin_account",
        }
    monkeypatch.setattr(admin, "_ensure_admin_account_table", lambda: True)
    monkeypatch.setattr(
        admin,
        "_update_admin_account_atomic",
        lambda *args, **kwargs: (
            {"id": 2, "username": "root", "role": "super_admin", "status": "active"},
            None,
            "self_downgrade",
        ),
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
    denied_query = client.get("/api/admin/big-screen?token=screen-secret")
    allowed = client.get("/api/admin/big-screen", headers={"X-RealGuard-Screen-Token": "screen-secret"})

    assert denied.status_code == 401
    assert denied_query.status_code == 401
    assert allowed.status_code == 200
    assert allowed.get_json()["metrics"]["detections"]["today"] == 11


def test_big_screen_query_token_is_exchanged_for_signed_session(client, monkeypatch):
    monkeypatch.setenv("REALGUARD_BIG_SCREEN_TOKEN", "screen-secret")
    admin._clear_big_screen_cache()
    monkeypatch.setattr(
        admin,
        "_big_screen_payload",
        lambda: {
            "generatedAt": "2026-06-09 18:00:00",
            "metrics": {"detections": {"today": 11}},
            "series": {"labels": [], "images": [], "videos": []},
            "models": [],
            "recent": [],
            "anomalies": [],
        },
    )

    exchanged = client.get("/admin/screen?token=screen-secret")

    assert exchanged.status_code == 302
    assert exchanged.headers["Location"].endswith("/admin/screen")
    assert "screen-secret" not in exchanged.headers["Location"]
    with client.session_transaction() as sess:
        assert sess[admin.ADMIN_SCREEN_SESSION_KEY] == admin.hashlib.sha256(b"screen-secret").hexdigest()
        assert sess[admin.ADMIN_SCREEN_SESSION_KEY] != "screen-secret"

    page = client.get(exchanged.headers["Location"])
    payload = client.get("/api/admin/big-screen")

    assert page.status_code == 200
    assert "screen-secret" not in page.get_data(as_text=True)
    assert payload.status_code == 200
    assert payload.get_json()["metrics"]["detections"]["today"] == 11


def test_admin_routing_update_can_rollback(client, monkeypatch, tmp_path):
    monkeypatch.setenv("REALGUARD_ADMIN_PHONES", "13800000000")
    monkeypatch.setattr(admin.model_registry, "REGISTRY_PATH", tmp_path / "registry.json")
    _login_session(client, admin_role="admin")
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
    _login_session(client, admin_role="admin")
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
    _login_session(client, admin_role="admin")

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


def test_admin_session_cookie_is_secure_by_default(client):
    response = client.get("/admin/login")

    cookie = response.headers.get("Set-Cookie", "")
    assert "Secure" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


def test_developer_admin_write_requires_global_csrf(client):
    with client.session_transaction() as sess:
        sess[admin.ADMIN_SESSION_KEY] = {
            "Userid": "admin:1",
            "adminId": 1,
            "username": "root",
            "role": "super_admin",
            "authType": "admin_account",
        }

    response = client.post("/api/admin/developer/pricing", json={"mode": "fast", "unitPriceFen": 5})

    assert response.status_code == 403
    assert "CSRF" in response.get_json()["message"]


def test_legacy_whitelist_is_readonly_for_sensitive_data(client, monkeypatch):
    monkeypatch.setenv("REALGUARD_ADMIN_PHONES", "13800000000")
    _login_session(client)

    response = client.get("/api/admin/users")

    assert response.status_code == 403
    assert "user.view" in response.get_json()["message"]


def test_admin_session_version_revokes_existing_cookie(monkeypatch):
    issued_at = int(time.time())
    cookie_user = {
        "Userid": "admin:7",
        "adminId": 7,
        "username": "ops",
        "role": "operator",
        "authType": "admin_account",
        "sessionVersion": 3,
        "issuedAt": issued_at,
    }
    monkeypatch.setattr(
        admin,
        "_find_admin_account_by_id",
        lambda admin_id: {
            "id": admin_id,
            "username": "ops",
            "phone": "",
            "role": "operator",
            "status": "active",
            "session_version": 4,
        },
    )

    assert admin._refresh_admin_session(cookie_user) is None


def test_health_rejects_authentication_error_as_service_ok(monkeypatch):
    class Response:
        status_code = 401
        text = "unauthorized"

        def json(self):
            return {"status": "error"}

    class Session:
        trust_env = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(admin.model_registry.requests, "Session", Session)

    health = admin.model_registry.check_model_health({"id": "candidate", "healthUrl": "https://model.test/health"})

    assert health["httpStatus"] == 401
    assert health["serviceOk"] is False
    assert health["ok"] is False


def test_webhook_delivery_pins_validated_public_address_and_rejects_redirect(monkeypatch):
    calls = []
    monkeypatch.setattr(admin, "_resolve_public_webhook_addresses", lambda host, port: (["8.8.8.8"], ""))
    monkeypatch.setattr(
        admin,
        "_post_webhook_payload",
        lambda url, payload, address, timeout=5: calls.append((url, address)) or (302, "redirect"),
    )
    monkeypatch.setattr(admin.time, "sleep", lambda *_args: None)
    monkeypatch.setattr(
        admin.admin_state,
        "record_alert_delivery",
        lambda claim, ok, **kwargs: {"ok": ok, **kwargs},
    )

    result = admin._deliver_alert_claim(
        {"eventId": "probeFailed", "kind": "alert", "title": "test"},
        "https://hooks.example.test/notify",
    )

    assert result["ok"] is False
    assert result["status_code"] == 302
    assert result["attempts"] == 3
    assert calls == [
        ("https://hooks.example.test/notify", "8.8.8.8"),
        ("https://hooks.example.test/notify", "8.8.8.8"),
        ("https://hooks.example.test/notify", "8.8.8.8"),
    ]


def test_webhook_validation_rejects_domain_resolving_to_loopback(monkeypatch):
    monkeypatch.setattr(
        admin.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(admin.socket.AF_INET, admin.socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
    )

    url, error = admin._validate_webhook_url("https://hooks.example.test/notify", resolve=True)

    assert url == ""
    assert "内网" in error or "本机" in error


def test_last_super_admin_update_uses_serializing_row_locks(monkeypatch):
    queries = []

    class Cursor:
        current = ""

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params=None):
            normalized = " ".join(sql.split())
            queries.append((normalized, params))
            if "WHERE role = 'super_admin'" in normalized:
                self.current = "super_admins"
            elif "FOR UPDATE" in normalized:
                self.current = "target"
            elif normalized.startswith("UPDATE admin_accounts"):
                self.current = "update"
            else:
                self.current = "after"

        def fetchall(self):
            return [{"id": 1}] if self.current == "super_admins" else []

        def fetchone(self):
            if self.current == "target":
                return {
                    "id": 1,
                    "username": "root",
                    "role": "super_admin",
                    "status": "active",
                    "session_version": 1,
                }
            return None

    class Connection:
        rolled_back = False
        committed = False

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
    monkeypatch.setattr(admin, "get_db_connection", lambda: connection)

    before, after, error = admin._update_admin_account_atomic(1, "readonly", "active", actor_admin_id=2)

    assert before["role"] == "super_admin"
    assert after is None
    assert error == "last_super_admin"
    assert connection.rolled_back is True
    assert connection.committed is False
    assert "ORDER BY id FOR UPDATE" in queries[0][0]
    assert not any(query.startswith("UPDATE admin_accounts") for query, _ in queries)


def test_admin_security_sql_upgrades_existing_tables_idempotently():
    sql = (ROOT / "sql" / "patch_admin_security.sql").read_text(encoding="utf-8")

    assert "information_schema.COLUMNS" in sql
    assert "ALTER TABLE admin_accounts ADD COLUMN session_version" in sql
    assert "CREATE TABLE IF NOT EXISTS admin_login_attempts" in sql
    assert "idx_admin_accounts_role_status" in sql


def test_routing_rejects_unknown_primary(monkeypatch, tmp_path):
    monkeypatch.setattr(admin.model_registry, "REGISTRY_PATH", tmp_path / "registry.json")

    with pytest.raises(ValueError, match="主模型不存在"):
        admin.model_registry.update_routing({"imagePrimary": "missing-model"})


def test_csv_neutralizes_spreadsheet_formulas():
    app = creat_app()
    with app.test_request_context("/"):
        response = admin._csv_response("audit.csv", ["value"], [["=HYPERLINK(\"https://evil.test\")"]])

    assert "'=HYPERLINK" in response.get_data(as_text=True)
