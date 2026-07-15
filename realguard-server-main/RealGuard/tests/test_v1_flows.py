from io import BytesIO
from pathlib import Path
import json
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from imagedetection import creat_app  # noqa: E402
from imagedetection.views import api, detection, reporting  # noqa: E402
import detector_backend  # noqa: E402

OWNER_WHERE = "(Userid = %s) OR (Userid IS NULL AND phone = %s) OR (Userid IS NULL AND (phone IS NULL OR phone = '') AND openid = %s)"
GUEST_OWNER_WHERE = "Userid IS NULL AND (phone IS NULL OR phone = '') AND openid = %s"
IMAGE_HISTORY_QUERY = f"SELECT * FROM data WHERE {OWNER_WHERE} ORDER BY createtime DESC"
VIDEO_HISTORY_QUERY = f"SELECT * FROM video_data WHERE {OWNER_WHERE} ORDER BY createtime DESC"


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


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


def test_api_password_login_sets_session(client, monkeypatch):
    monkeypatch.setattr(
        api,
        "_authenticate_password_user",
        lambda phone, secret: {
            "Userid": 7,
            "username": "tester",
            "phone": phone,
            "openid": "openid-1",
        },
    )
    monkeypatch.setattr(api, "_sync_detection_user", lambda *args, **kwargs: None)
    monkeypatch.setattr(api, "_record_terms_acceptance", lambda phone: True)

    response = client.post(
        "/api/login/password",
        json={"phone": "13800000000", "secret": "hashed-pass", "accepted_terms": True},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["user"]["Userid"] == 7
    with client.session_transaction() as sess:
        assert sess["user_info"]["phone"] == "13800000000"


def test_developer_api_key_lifecycle(client, monkeypatch):
    _login_session(client)
    monkeypatch.setattr(api, "_ensure_developer_api_key_table", lambda: True)
    monkeypatch.setattr(api, "DEVELOPER_AUTH_SECRET", "internal-secret")
    rows = []
    next_id = {"value": 0}

    def fake_lastid(sql, params=None):
        assert "INSERT INTO developer_api_keys" in sql
        next_id["value"] += 1
        user_id, name, key_hash, key_prefix, key_last4, scopes = params
        rows.append({
            "id": next_id["value"],
            "user_id": user_id,
            "name": name,
            "key_hash": key_hash,
            "key_prefix": key_prefix,
            "key_last4": key_last4,
            "scopes": scopes,
            "status": "active",
            "created_at": "2026-06-02 10:00:00",
            "last_used_at": None,
            "revoked_at": None,
            "phone": "13800000000",
            "username": "tester",
        })
        return next_id["value"]

    def fake_sql(sql, params=None, fetch=True):
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT COUNT(*) AS cnt FROM developer_api_keys"):
            user_id = params[0]
            return [{"cnt": sum(1 for row in rows if row["user_id"] == user_id and row["status"] == "active")}]
        if normalized.startswith("SELECT id, name, key_prefix"):
            if "WHERE id = %s AND user_id = %s" in normalized:
                key_id, user_id = params
                return [row for row in rows if row["id"] == key_id and row["user_id"] == user_id]
            user_id = params[0]
            return [row for row in rows if row["user_id"] == user_id]
        if normalized.startswith("UPDATE developer_api_keys SET status = 'revoked'"):
            key_id, user_id = params
            for row in rows:
                if row["id"] == key_id and row["user_id"] == user_id and row["status"] == "active":
                    row["status"] = "revoked"
                    row["revoked_at"] = "2026-06-02 10:01:00"
                    return 1
            return 0
        if normalized.startswith("SELECT k.id, k.user_id"):
            key_hash = params[0]
            return [row for row in rows if row["key_hash"] == key_hash]
        if normalized.startswith("UPDATE developer_api_keys SET last_used_at"):
            key_id = params[1]
            for row in rows:
                if row["id"] == key_id:
                    row["last_used_at"] = "2026-06-02 10:02:00"
                    return 1
            return 0
        raise AssertionError(f"unexpected SQL: {normalized}")

    monkeypatch.setattr(api, "excute_sql", fake_sql)
    monkeypatch.setattr(api, "excute_sql_lastid", fake_lastid)

    created = client.post("/api/developer/keys", json={"name": "Agent key"})
    payload = created.get_json()
    api_key = payload["apiKey"]

    assert created.status_code == 200
    assert api_key.startswith("rg_sk_")
    assert payload["key"]["preview"].endswith(api_key[-4:])
    assert "apiKey" not in payload["key"]

    listing = client.get("/api/developer/keys")
    assert listing.status_code == 200
    assert listing.get_json()["keys"][0]["preview"].endswith(api_key[-4:])
    assert api_key not in json.dumps(listing.get_json(), ensure_ascii=False)

    verified = client.post(
        "/api/developer/keys/verify",
        json={"api_key": api_key},
        headers={"X-RealGuard-Internal-Secret": "internal-secret"},
    )
    assert verified.status_code == 200
    assert verified.get_json()["valid"] is True
    assert verified.get_json()["userId"] == 1

    revoked = client.delete("/api/developer/keys/1")
    assert revoked.status_code == 200

    rejected = client.post(
        "/api/developer/keys/verify",
        json={"api_key": api_key},
        headers={"X-RealGuard-Internal-Secret": "internal-secret"},
    )
    assert rejected.status_code == 200
    assert rejected.get_json()["valid"] is False


def test_developer_token_usage_proxy_uses_current_user(client, monkeypatch):
    _login_session(client)
    monkeypatch.setattr(
        api,
        "_developer_usage_from_v1",
        lambda user_id, days: {
            "days": days,
            "summary": {
                "totalRequests": 3,
                "billableRequests": 3,
                "cacheHits": 0,
                "promptTokens": 0,
                "completionTokens": 0,
                "totalTokens": 0,
                "lastEventAt": "2026-06-02T10:00:00",
            },
            "byDay": [{"date": "2026-06-02", "requests": 3, "billableRequests": 3, "cacheHits": 0, "promptTokens": 0, "completionTokens": 0, "totalTokens": 0}],
            "byEndpoint": [{"endpoint": "/api/developer/v1/detect", "requests": 3, "totalTokens": 0}],
            "byModel": [{"modelVersion": "realguard-v1-image", "requests": 3, "totalTokens": 0}],
            "byKey": [],
        },
    )
    monkeypatch.setattr(
        api,
        "_developer_usage_from_v2",
        lambda user_id, days: {
            "days": days,
            "summary": {
                "totalRequests": 2,
                "billableRequests": 1,
                "cacheHits": 1,
                "promptTokens": 12,
                "completionTokens": 7,
                "totalTokens": 19,
                "lastEventAt": "2026-06-02T10:05:00+00:00",
            },
            "byDay": [{"date": "2026-06-02", "requests": 2, "billableRequests": 1, "cacheHits": 1, "promptTokens": 12, "completionTokens": 7, "totalTokens": 19}],
            "byEndpoint": [{"endpoint": "/api/detect", "requests": 2, "totalTokens": 19}],
            "byModel": [{"modelVersion": "qwen3-vl-flash", "requests": 2, "totalTokens": 19}],
            "byKey": [],
        },
    )

    response = client.get("/api/developer/usage?days=7")

    assert response.status_code == 200
    summary = response.get_json()["usage"]["summary"]
    assert summary["totalCalls"] == 5
    assert summary["v1Calls"] == 3
    assert summary["v2Calls"] == 2
    assert summary["totalTokens"] == 19


def test_developer_v1_detect_uses_api_key_and_records_call(client, monkeypatch):
    recorded = {}

    monkeypatch.setattr(
        api,
        "_developer_key_required",
        lambda: ({
            "id": 9,
            "user_id": 1,
            "username": "tester",
            "phone": "13800000000",
            "openid": "openid-1",
        }, None),
    )
    monkeypatch.setattr(
        api,
        "image_detect_for_actor",
        lambda user_info, is_guest=False: api.jsonify({
            "status": "success",
            "result": {
                "itemid": 22,
                "final_label": "AI生成图像",
                "probability": 0.73,
                "modelVersion": "realguard-v1-image",
            },
        }),
    )

    def fake_record(actor, **kwargs):
        recorded["actor"] = actor
        recorded["kwargs"] = kwargs
        return True

    monkeypatch.setattr(api, "_record_developer_usage_event", fake_record)

    response = client.post(
        "/api/developer/v1/detect",
        headers={"X-RealGuard-Key": "rg_sk_test"},
        data={"file": (BytesIO(b"fake-image"), "demo.png")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert response.get_json()["result"]["modelVersion"] == "realguard-v1-image"
    assert recorded["actor"]["id"] == 9
    assert recorded["kwargs"]["pipeline"] == "v1"
    assert recorded["kwargs"]["endpoint"] == "/api/developer/v1/detect"


def test_guest_image_detect_returns_rewritten_url_and_then_blocks(client, monkeypatch):
    monkeypatch.setattr(
        detection.model_registry,
        "get_routing",
        lambda: {"imagePrimary": "v1", "fallbackEnabled": False},
    )
    monkeypatch.setattr(
        detection.model_registry,
        "get_model",
        lambda model_id: {"id": "v1", "enabled": True, "endpoint": detection.IMAGE_DETECT_API, "timeoutSeconds": 180},
    )
    monkeypatch.setattr(detection, "_metadata_for_item", lambda itemid: {})
    monkeypatch.setattr(
        detection,
        "_backend_post",
        lambda url, **kwargs: _FakeResponse(
            {
                "code": 200,
                "data": {
                    "data_itemid": 11,
                    "fake_percentage": 64.0,
                    "final_label": "AI生成图像",
                    "confidence": "高",
                    "image_url": "http://10.1.20.66:5000/static/uploads/guest/image/demo.png",
                    "filename": "demo.png",
                    "file_size": "12KB",
                    "img_format": "png",
                    "resolution": "256x256",
                },
            }
        ),
    )

    first = client.post(
        "/image_upload/detect",
        data={"image": (BytesIO(b"fake-image"), "demo.png")},
        content_type="multipart/form-data",
    )

    assert first.status_code == 200
    payload = first.get_json()
    assert payload["result"]["image_url"] == "/api/media/image/11"
    with client.session_transaction() as sess:
        assert sess[detection.GUEST_DETECTION_SESSION_KEY] == 1

    second = client.post(
        "/image_upload/detect",
        data={"image": (BytesIO(b"fake-image"), "demo.png")},
        content_type="multipart/form-data",
    )

    assert second.status_code == 401
    assert "请登录后继续检测" in second.get_json()["message"]


def test_image_detect_falls_back_to_v2_when_v1_backend_is_down(client, monkeypatch):
    _login_session(client)
    monkeypatch.setattr(detection, "V2_DETECT_API", "http://v2.local/api/detect")
    monkeypatch.setattr(detection, "V2_INTERNAL_TOKEN", "internal-token")
    monkeypatch.setattr(detection, "IMAGE_DETECT_FALLBACK", "v2")
    monkeypatch.setattr(
        detection.model_registry,
        "get_routing",
        lambda: {
            "imagePrimary": "v1",
            "imageFallback": "v2",
            "fallbackEnabled": True,
        },
    )
    monkeypatch.setattr(
        detection.model_registry,
        "get_model",
        lambda model_id: {
            "v1": {"id": "v1", "enabled": True, "endpoint": detection.IMAGE_DETECT_API, "timeoutSeconds": 180},
            "v2": {"id": "v2", "enabled": True, "endpoint": "http://v2.local/api/detect", "timeoutSeconds": 180},
        }.get(model_id),
    )
    monkeypatch.setattr(detection, "_metadata_for_item", lambda itemid: {})
    monkeypatch.setattr(
        detection,
        "_save_local_upload",
        lambda image_bytes, folder, filename: ("stored-demo.png", "/tmp/stored-demo.png"),
    )
    monkeypatch.setattr(detection, "get_image_info", lambda path: ("PNG", "320x240"))
    monkeypatch.setattr(detection, "get_file_size_str", lambda path: "1KB")
    monkeypatch.setattr(detection, "excute_detection_sql_lastid", lambda sql, params=None: 88)

    def fake_backend_post(url, **kwargs):
        if url == detection.IMAGE_DETECT_API:
            raise detection.requests.ConnectionError("connection refused")
        assert url == "http://v2.local/api/detect"
        assert kwargs["headers"]["X-Jianzhen-Token"] == "internal-token"
        return _FakeResponse(
            {
                "taskId": "rj-20260603-0001",
                "reportId": "RJ-RPT-20260603-0001",
                "verdict": "suspected_fake",
                "confidence": 0.76,
                "modelVersion": "qwen3-vl-flash",
                "source": "vlm",
                "explanation": "V2 evidence summary",
                "dimensions": [
                    {"key": "aigc", "label": "AIGC生成检测", "score": 0.76, "result": "疑似生成"},
                ],
                "regions": [],
                "tokenUsage": {"promptTokens": 10, "completionTokens": 5, "totalTokens": 15},
                "fileMeta": {"size": "1KB", "resolution": "320x240"},
            }
        )

    monkeypatch.setattr(detection, "_backend_post", fake_backend_post)

    response = client.post(
        "/image_upload/detect",
        data={"image": (BytesIO(b"fake-image"), "demo.png")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["result"]["itemid"] == 88
    assert payload["result"]["final_label"] == "AI生成图像"
    assert payload["result"]["probability"] == pytest.approx(0.76)
    assert payload["result"]["image_url"] == "/api/media/image/88"


@pytest.mark.parametrize(
    "payload",
    [
        {"source": "mock", "verdict": "suspected_fake", "confidence": 0.8},
        {"source": "unknown", "verdict": "unknown", "confidence": 0.5},
        {"source": "vlm", "verdict": "unknown", "confidence": 0.5},
    ],
)
def test_v2_fallback_rejects_results_without_a_publishable_model_verdict(payload):
    with pytest.raises(RuntimeError, match="真实模型结论"):
        detection._assert_v2_publishable(payload)


def test_v2_probability_conversion_rejects_unknown_verdict():
    with pytest.raises(ValueError, match="明确判定"):
        detection._fake_percentage_from_v2({"verdict": "unknown", "confidence": 0.5})


def test_image_report_marks_borderline_result_for_human_review():
    html = reporting.image_report_content(
        {"itemid": 9, "fake": 66.1, "createtime": "2026-07-15 10:00:00"},
        {
            "final_label": "AI生成图像",
            "probability": 0.661,
            "confidence": "低",
            "filename": "sample.png",
            "visual_issues": [],
            "all_metadata": {},
        },
    )

    assert "需人工复核 · 66.1%" in html
    assert "缺失本身不代表伪造" in html
    assert reporting.image_report_filename(9) == "huijian-image-report-9.html"


def test_image_detect_can_use_aliyun_primary_and_records_backend_model(client, monkeypatch, tmp_path):
    _login_session(client)
    monkeypatch.setattr(detection.admin_state, "STATE_PATH", tmp_path / "admin_state.json")
    aliyun_model = {
        "id": "aliyun-aigc-pro",
        "name": "Aliyun AIGC Detector Pro",
        "enabled": True,
        "runtime": "aliyun-green",
        "endpoint": "internal://aliyun/aigcDetector_pro",
        "timeoutSeconds": 60,
        "version": "aigcDetector_pro",
    }
    monkeypatch.setattr(
        detection.model_registry,
        "get_routing",
        lambda: {
            "imagePrimary": "aliyun-aigc-pro",
            "imageFallback": "v2",
            "fallbackEnabled": False,
        },
    )
    monkeypatch.setattr(
        detection.model_registry,
        "get_model",
        lambda model_id: aliyun_model if model_id == "aliyun-aigc-pro" else None,
    )
    monkeypatch.setattr(detection, "_metadata_for_item", lambda itemid: {})
    monkeypatch.setattr(
        detection.aliyun_green,
        "detect_image_bytes",
        lambda service, image_bytes, filename: {
            "ok": True,
            "provider": "aliyun",
            "service": service,
            "latencyMs": 18,
            "normalized": {
                "finalLabel": "疑似AI生成",
                "riskScore": 0.82,
                "confidence": "高",
                "labels": ["aigc"],
                "descriptions": ["疑似生成式内容"],
            },
        },
    )
    monkeypatch.setattr(
        detection,
        "_save_local_upload",
        lambda image_bytes, folder, filename: ("stored-demo.png", "/tmp/stored-demo.png"),
    )
    monkeypatch.setattr(detection, "get_image_info", lambda path: ("PNG", "320x240"))
    monkeypatch.setattr(detection, "get_file_size_str", lambda path: "1KB")
    monkeypatch.setattr(detection, "excute_detection_sql_lastid", lambda sql, params=None: 123)

    response = client.post(
        "/image_upload/detect",
        data={"image": (BytesIO(b"fake-image"), "demo.png")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["result"]["itemid"] == 123
    assert payload["result"]["probability"] == pytest.approx(0.82)
    assert payload["result"]["agent_reasoning"] == ""
    runs = detection.admin_state.load_state()["modelRuns"]
    assert runs[0]["itemid"] == 123
    assert runs[0]["model"]["id"] == "aliyun-aigc-pro"
    assert runs[0]["meta"]["service"] == "aigcDetector_pro"


def test_public_agent_reasoning_hides_internal_model_fields():
    sanitized = detection._public_agent_reasoning(json.dumps({
        "fallback": "jianzhen-v2",
        "modelVersion": "qwen3-vl-flash",
        "source": "vlm",
        "taskId": "task-1",
        "reportId": "report-1",
    }, ensure_ascii=False))

    assert json.loads(sanitized) == {"taskId": "task-1", "reportId": "report-1"}


def test_swarm_detect_async_job_returns_expert_consensus(client, monkeypatch, tmp_path):
    _login_session(client)
    monkeypatch.setattr(detection.admin_state, "STATE_PATH", tmp_path / "admin_state.json")
    monkeypatch.setattr(detection, "V2_INTERNAL_TOKEN", "")
    monkeypatch.setattr(detection.aliyun_green, "configured", lambda: False)
    # Keep this test focused on the primary+metadata consensus path; skip the
    # provenance-style experts so the asserted score stays stable.
    monkeypatch.setattr(
        detection.swarm_c2pa_expert,
        "run_c2pa_expert",
        lambda *a, **kw: {"status": "skipped", "score": None, "verdict": "测试跳过",
                          "confidence": "", "evidence": [], "message": "test", "latencyMs": 0},
    )
    monkeypatch.setattr(
        detection.swarm_watermark_expert,
        "run_watermark_expert",
        lambda *a, **kw: {"status": "skipped", "score": None, "verdict": "测试跳过",
                          "confidence": "", "evidence": [], "message": "test", "latencyMs": 0},
    )

    class ImmediateThread:
        def __init__(self, target, args=(), kwargs=None, daemon=None):
            self.target = target
            self.args = args
            self.kwargs = kwargs or {}

        def start(self):
            self.target(*self.args, **self.kwargs)

    monkeypatch.setattr(detection.threading, "Thread", ImmediateThread)

    def fake_primary(image_bytes, filename, mimetype, user_info, *, is_guest=False, mark_guest=True):
        return {
            "status": "success",
            "result": {
                "itemid": 321,
                "final_label": "AI生成图像",
                "probability": 0.76,
                "detector_probability": 0.76,
                "confidence": "高",
                "explanation": "主检测认为疑似生成。",
                "visual_issues": ["纹理重复"],
                "image_url": "/static/uploads/openid-1/image/demo.png",
                "filename": "demo.png",
                "file_size": "1KB",
                "img_format": "PNG",
                "resolution": "320x240",
                "all_metadata": {},
                "feedback": None,
            },
        }, 200

    monkeypatch.setattr(detection, "_run_image_detection_payload", fake_primary)

    created = client.post(
        "/image_upload/detect_swarm",
        data={"image": (BytesIO(b"fake-image"), "demo.png")},
        content_type="multipart/form-data",
    )

    assert created.status_code == 202
    job_id = created.get_json()["job"]["id"]
    loaded = client.get(f"/image_upload/jobs/{job_id}")

    assert loaded.status_code == 200
    job = loaded.get_json()["job"]
    assert job["status"] == "success"
    assert job["mode"] == "swarm"
    assert job["progress"] == 100
    result = job["result"]["result"]
    assert result["itemid"] == 321
    assert result["probability"] == pytest.approx(0.7219)
    assert result["swarm"]["enabled"] is True
    assert result["swarm"]["effectiveExperts"] == 2
    assert any(expert["publicName"] == "主鉴伪专家" and expert["status"] == "success" for expert in result["swarm"]["experts"])
    assert any(expert["publicName"] == "元数据专家" and expert["status"] == "success" for expert in result["swarm"]["experts"])
    public_primary = next(expert for expert in result["swarm"]["experts"] if expert["publicName"] == "主鉴伪专家")
    assert public_primary["id"] == "expert-1"
    assert public_primary["publicId"] == "expert-1"
    assert public_primary["publicName"] == "主鉴伪专家"
    assert "publicMessage" in public_primary
    assert "name" not in public_primary
    assert "provider" not in public_primary
    assert "message" not in public_primary
    assert "verdict" not in public_primary
    assert "score" not in public_primary
    assert "confidence" not in public_primary
    assert "latencyMs" not in public_primary
    assert all(not expert["id"].startswith(("primary", "v2", "aliyun")) for expert in result["swarm"]["experts"])
    assert all("主路由" not in item for item in result["swarm"]["evidence"])
    assert all("风险评分" not in item for item in result["swarm"]["evidence"])


def test_swarm_detect_get_is_friendly(client):
    browser_response = client.get("/image_upload/detect_swarm")
    assert browser_response.status_code == 302
    assert browser_response.headers["Location"] == "/image_upload"

    api_response = client.get("/image_upload/detect_swarm", headers={"Accept": "application/json"})
    assert api_response.status_code == 200
    payload = api_response.get_json()
    assert payload["status"] == "success"
    assert "POST multipart/form-data" in payload["message"]


def test_swarm_aggregate_rejects_metadata_only():
    experts = detection._swarm_initial_experts()
    detection._swarm_set_expert(
        experts,
        "metadata",
        status="success",
        score=0.56,
        verdict="缺少元数据",
        evidence=["无 EXIF"],
    )

    result, error = detection._swarm_aggregate(experts, None, {"filename": "demo.png"})

    assert result is None
    assert "主检测与复核专家" in error


def test_swarm_detect_rejects_when_disabled(client, monkeypatch, tmp_path):
    _login_session(client)
    monkeypatch.setattr(detection.admin_state, "STATE_PATH", tmp_path / "admin_state.json")
    monkeypatch.setattr(
        detection.model_registry,
        "get_swarm_config",
        lambda: {"enabled": False, "minExperts": 2, "experts": []},
    )

    payload, status = detection._run_swarm_detection_payload(
        b"fake-image",
        "demo.png",
        "image/png",
        {"Userid": 1, "phone": "13800000000", "openid": "openid-1"},
    )

    assert status == 400
    assert "未在后台启用" in payload["message"]


def test_image_detect_does_not_fallback_when_admin_disables_it(client, monkeypatch):
    _login_session(client)
    monkeypatch.setattr(detection, "V2_INTERNAL_TOKEN", "internal-token")
    monkeypatch.setattr(
        detection.model_registry,
        "get_routing",
        lambda: {
            "imagePrimary": "v1",
            "imageFallback": "v2",
            "fallbackEnabled": False,
        },
    )
    monkeypatch.setattr(
        detection.model_registry,
        "get_model",
        lambda model_id: {
            "v1": {"id": "v1", "enabled": True, "endpoint": detection.IMAGE_DETECT_API, "timeoutSeconds": 180},
            "v2": {"id": "v2", "enabled": True, "endpoint": "http://v2.local/api/detect", "timeoutSeconds": 180},
        }.get(model_id),
    )

    def fake_backend_post(url, **kwargs):
        assert url == detection.IMAGE_DETECT_API
        raise detection.requests.ConnectionError("connection refused")

    monkeypatch.setattr(detection, "_backend_post", fake_backend_post)

    response = client.post(
        "/image_upload/detect",
        data={"image": (BytesIO(b"fake-image"), "demo.png")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 502
    assert "未启用 V2 兜底" in response.get_json()["message"]


def test_image_detect_blocks_v1_when_required_artifact_is_missing(client, monkeypatch):
    _login_session(client)
    monkeypatch.setattr(detection, "V2_INTERNAL_TOKEN", "")
    monkeypatch.setattr(
        detection.model_registry,
        "get_routing",
        lambda: {
            "imagePrimary": "v1-onnx-mil",
            "imageFallback": "v2",
            "fallbackEnabled": False,
        },
    )
    monkeypatch.setattr(
        detection.model_registry,
        "get_model",
        lambda model_id: {
            "v1-onnx-mil": {
                "id": "v1-onnx-mil",
                "enabled": True,
                "endpoint": detection.IMAGE_DETECT_API,
                "timeoutSeconds": 180,
            },
            "v2": {"id": "v2", "enabled": True, "endpoint": "http://v2.local/api/detect", "timeoutSeconds": 180},
        }.get(model_id),
    )
    monkeypatch.setattr(
        detection.model_registry,
        "model_artifact_ready",
        lambda model: (False, ["missing external ONNX weight file: model_deploy.onnx.data"], {}),
    )

    def fake_backend_post(url, **kwargs):
        raise AssertionError("primary backend should not be called when V1 artifacts are missing")

    monkeypatch.setattr(detection, "_backend_post", fake_backend_post)

    response = client.post(
        "/image_upload/detect",
        data={"image": (BytesIO(b"fake-image"), "demo.png")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 503
    message = response.get_json()["message"]
    assert "V1 主检测模型文件未就绪" in message
    assert "不会静默切换模型" in message


def test_detector_backend_image_endpoint_returns_v1_contract(monkeypatch):
    monkeypatch.setattr(detector_backend, "_ensure_capability_ready", lambda: None)
    monkeypatch.setattr(
        detector_backend,
        "_run_v1_detect",
        lambda image_path: {
            "final_label": "真实图像",
            "probability": 0.16,
            "detector_probability": 0.18,
            "confidence": "高",
            "explanation": "V1 evidence summary",
            "visual_issues": ["无明显视觉可疑点。"],
            "all_metadata": {"EXIF:Make": "Canon"},
            "metadata_signals": {"has_ai_signal": False, "has_real_signal": True},
            "agent_reasoning": "native-v1",
        },
    )
    monkeypatch.setattr(
        detector_backend,
        "_save_upload",
        lambda image_bytes, folder, filename: ("stored-demo.png", "/tmp/stored-demo.png"),
    )
    monkeypatch.setattr(detector_backend, "get_image_info", lambda path: ("PNG", "320x240"))
    monkeypatch.setattr(detector_backend, "get_file_size_str", lambda path: "1KB")
    monkeypatch.setattr(detector_backend, "excute_detection_sql_lastid", lambda sql, params=None: 91)
    app = detector_backend.create_app()
    app.config.update(TESTING=True)

    response = app.test_client().post(
        "/image",
        data={
            "image_file": (BytesIO(b"fake-image"), "demo.png"),
            "openid": "openid-1",
            "phone": "13800000000",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["code"] == 200
    assert payload["data"]["data_itemid"] == 91
    assert payload["data"]["fake_percentage"] == pytest.approx(16.0)
    assert payload["data"]["final_label"] == "真实图像"
    assert payload["data"]["image_url"].endswith("/static/uploads/openid-1/image/stored-demo.png")
    assert payload["data"]["agent_reasoning"] == "native-v1"


def test_video_detect_logged_in_builds_public_media_url(client, monkeypatch):
    _login_session(client)
    monkeypatch.setattr(
        detection,
        "_backend_post",
        lambda url, **kwargs: _FakeResponse(
            {
                "code": 200,
                "data": {
                    "data_itemid": 21,
                    "fake_percentage": 83.0,
                    "real_percentage": 17.0,
                    "confidence": 0.91,
                    "final_label": "fake",
                    "frame_count": 48,
                    "encoder": "h264",
                    "meta": {
                        "file_size": "2.5MB",
                        "duration": "8s",
                        "resolution": "1280x720",
                        "video_format": "mp4",
                    },
                },
            }
        ),
    )

    monkeypatch.setattr(
        detection,
        "excute_detection_sql",
        lambda sql, params=None, fetch=True: [{
            "itemid": 21,
            "filename": "video.mp4",
            "openid": "openid-1",
            "phone": "13800000000",
        }] if sql == f"SELECT * FROM video_data WHERE itemid = %s AND ({OWNER_WHERE}) LIMIT 1" else [],
    )

    response = client.post(
        "/video_upload/detect",
        data={"video_file": (BytesIO(b"fake-video"), "video.mp4"), "fast_mode": "1"},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    payload = response.get_json()["result"]
    assert payload["final_label"] == "AI生成视频"
    assert payload["confidence"] == "高"
    assert payload["video_url"] == "/api/media/video/21"


def test_guest_image_report_downloads_attachment(client, monkeypatch):
    with client.session_transaction() as sess:
        sess["guest_openid"] = "guest-123"

    def fake_detection_sql(sql, params=None, fetch=True):
        if sql == f"SELECT * FROM data WHERE itemid = %s AND {GUEST_OWNER_WHERE} LIMIT 1":
            assert params == ("31", "guest-123")
            return [{
                "itemid": 31,
                "filename": "guest.png",
                "fake": 61.0,
                "clarity": "中",
                "file_size": "8KB",
                "img_format": "png",
                "resolution": "320x320",
                "openid": "guest-123",
                "createtime": "2026-05-27 14:00:00",
            }]
        if sql == "SELECT all_metadata FROM exif WHERE data_itemid = %s LIMIT 1":
            return []
        raise AssertionError(f"unexpected SQL: {sql}")

    monkeypatch.setattr(detection, "excute_detection_sql", fake_detection_sql)

    response = client.get("/image_upload/report?itemid=31")

    assert response.status_code == 200
    assert "attachment;" in response.headers["Content-Disposition"]
    assert "图像鉴伪报告" in response.get_data(as_text=True)
    assert "guest.png" in response.get_data(as_text=True)


def test_guest_history_returns_guest_image_records(client, monkeypatch):
    with client.session_transaction() as sess:
        sess["guest_openid"] = "guest-abc"

    def fake_detection_sql(sql, params=None, fetch=True):
        if sql == f"SELECT * FROM data WHERE {GUEST_OWNER_WHERE} ORDER BY createtime DESC":
            assert params == ("guest-abc",)
            return [{
                "itemid": 35,
                "filename": "guest-history.png",
                "fake": 58.0,
                "clarity": "中",
                "openid": "guest-abc",
                "phone": "",
                "createtime": "2026-05-27 16:00:00",
                "explantation": "视觉可疑点\n- 边缘过度平滑",
            }]
        raise AssertionError(f"unexpected SQL: {sql}")

    monkeypatch.setattr(api, "excute_detection_sql", fake_detection_sql)
    monkeypatch.setattr(api, "_has_detection_metadata", lambda itemid: itemid == 35)

    response = client.get("/api/history/image-detections")

    assert response.status_code == 200
    record = response.get_json()["records"][0]
    assert record["itemid"] == 35
    assert record["is_guest_record"] is True
    assert record["report_url"] == "/image_upload/report?itemid=35"
    assert record["has_metadata"] is True
    assert record["has_visual_issues"] is True
    assert record["visual_issue_count"] == 1


def test_history_endpoints_return_empty_for_fresh_guest(client):
    image_response = client.get("/api/history/image-detections")
    video_response = client.get("/api/history/video-detections")

    assert image_response.status_code == 200
    assert image_response.get_json()["records"] == []
    assert video_response.status_code == 200
    assert video_response.get_json()["records"] == []


def test_guest_thumbnail_uses_guest_openid_lookup(client, monkeypatch, tmp_path):
    with client.session_transaction() as sess:
        sess["guest_openid"] = "guest-thumb"

    thumb_path = tmp_path / "thumb.webp"
    thumb_path.write_bytes(b"fake-webp")

    def fake_detection_sql(sql, params=None, fetch=True):
        if sql == f"SELECT * FROM data WHERE itemid = %s AND {GUEST_OWNER_WHERE} LIMIT 1":
            assert params == (91, "guest-thumb")
            return [{
                "itemid": 91,
                "filename": "guest-thumb.png",
                "openid": "guest-thumb",
                "phone": "",
                "createtime": "2026-05-27 16:05:00",
            }]
        raise AssertionError(f"unexpected SQL: {sql}")

    monkeypatch.setattr(api, "excute_detection_sql", fake_detection_sql)
    monkeypatch.setattr(api, "_thumbnail_cache_path", lambda item: thumb_path)

    response = client.get("/api/media/thumbnail/image/91")

    assert response.status_code == 200
    assert response.data == b"fake-webp"


def test_video_report_downloads_attachment_for_logged_user(client, monkeypatch):
    _login_session(client)

    def fake_detection_sql(sql, params=None, fetch=True):
        if sql == f"SELECT * FROM video_data WHERE itemid = %s AND ({OWNER_WHERE}) LIMIT 1":
            assert params == ("41", 1, "13800000000", "openid-1")
            return [{
                "itemid": 41,
                "filename": "clip.mp4",
                "fake": 78.5,
                "final_label": "AI生成视频",
                "confidence": "高",
                "explanation": "检测到明显生成痕迹。",
                "duration": "10s",
                "resolution": "1280x720",
                "file_size": "4MB",
                "encoder": "h264",
                "frame_count": 55,
                "openid": "openid-1",
                "phone": "13800000000",
                "createtime": "2026-05-27 14:05:00",
            }]
        raise AssertionError(f"unexpected SQL: {sql}")

    monkeypatch.setattr(detection, "excute_detection_sql", fake_detection_sql)

    response = client.get("/video_upload/report?itemid=41")

    assert response.status_code == 200
    assert "attachment;" in response.headers["Content-Disposition"]
    assert "视频鉴伪报告" in response.get_data(as_text=True)
    assert "clip.mp4" in response.get_data(as_text=True)


def test_history_detection_records_include_report_urls(client, monkeypatch):
    _login_session(client)

    def fake_detection_sql(sql, params=None, fetch=True):
        if sql == IMAGE_HISTORY_QUERY:
            assert params == (1, "13800000000", "openid-1")
            return [{
                "itemid": 51,
                "filename": "img.png",
                "fake": 51.5,
                "clarity": "中",
                "openid": "openid-1",
                "phone": "13800000000",
                "createtime": "2026-05-27 15:00:00",
                "explantation": "视觉可疑点\n- 背景纹理重复",
            }]
        if sql == VIDEO_HISTORY_QUERY:
            assert params == (1, "13800000000", "openid-1")
            return [{
                "itemid": 61,
                "filename": "vid.mp4",
                "fake": 78.2,
                "final_label": "AI生成视频",
                "confidence": "高",
                "openid": "openid-1",
                "phone": "13800000000",
                "createtime": "2026-05-27 15:02:00",
            }]
        raise AssertionError(f"unexpected SQL: {sql}")

    monkeypatch.setattr(api, "excute_detection_sql", fake_detection_sql)
    monkeypatch.setattr(api, "_has_detection_metadata", lambda itemid: itemid == 51)

    image_response = client.get("/api/history/image-detections")
    video_response = client.get("/api/history/video-detections")

    assert image_response.status_code == 200
    assert video_response.status_code == 200
    assert image_response.get_json()["records"][0]["report_url"] == "/image_upload/report?itemid=51"
    assert image_response.get_json()["records"][0]["has_metadata"] is True
    assert image_response.get_json()["records"][0]["has_visual_issues"] is True
    assert image_response.get_json()["records"][0]["visual_issue_count"] == 1
    assert video_response.get_json()["records"][0]["report_url"] == "/video_upload/report?itemid=61"


def test_history_uses_userid_for_legacy_bound_records(client, monkeypatch):
    _login_session(client)

    def fake_detection_sql(sql, params=None, fetch=True):
        if sql == IMAGE_HISTORY_QUERY:
            assert params == (1, "13800000000", "openid-1")
            return [{
                "itemid": 71,
                "Userid": 1,
                "filename": "legacy-openid-only.png",
                "fake": 63.0,
                "clarity": "中",
                "openid": "legacy-wechat-openid",
                "phone": "",
                "createtime": "2026-05-28 10:00:00",
                "explantation": "",
            }]
        raise AssertionError(f"unexpected SQL: {sql}")

    monkeypatch.setattr(api, "excute_detection_sql", fake_detection_sql)
    monkeypatch.setattr(api, "_has_detection_metadata", lambda itemid: False)

    response = client.get("/api/history/image-detections")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["total"] == 1
    assert payload["records"][0]["itemid"] == 71
    assert payload["records"][0]["is_guest_record"] is False


def test_history_endpoints_support_query_filter_and_limit(client, monkeypatch):
    _login_session(client)

    def fake_detection_sql(sql, params=None, fetch=True):
        if sql == IMAGE_HISTORY_QUERY:
            return [
                {
                    "itemid": 101,
                    "filename": "meta-hit.png",
                    "fake": 42.0,
                    "clarity": "高",
                    "openid": "openid-1",
                    "phone": "13800000000",
                    "createtime": "2026-05-27 18:00:00",
                    "explantation": "视觉可疑点\n- 边缘纹理断裂",
                },
                {
                    "itemid": 102,
                    "filename": "plain.png",
                    "fake": 12.0,
                    "clarity": "低",
                    "openid": "openid-1",
                    "phone": "13800000000",
                    "createtime": "2026-05-27 18:01:00",
                    "explantation": "",
                },
            ]
        if sql == VIDEO_HISTORY_QUERY:
            return [
                {
                    "itemid": 201,
                    "filename": "ai-video.mp4",
                    "fake": 88.0,
                    "final_label": "AI生成视频",
                    "confidence": "高",
                    "openid": "openid-1",
                    "phone": "13800000000",
                    "createtime": "2026-05-27 18:10:00",
                },
                {
                    "itemid": 202,
                    "filename": "real-video.mp4",
                    "fake": 18.0,
                    "final_label": "真实视频",
                    "confidence": "中",
                    "openid": "openid-1",
                    "phone": "13800000000",
                    "createtime": "2026-05-27 18:11:00",
                },
            ]
        raise AssertionError(f"unexpected SQL: {sql}")

    monkeypatch.setattr(api, "excute_detection_sql", fake_detection_sql)
    monkeypatch.setattr(api, "_has_detection_metadata", lambda itemid: itemid == 101)

    image_response = client.get("/api/history/image-detections?filter=metadata&query=元数据&limit=1")
    video_response = client.get("/api/history/video-detections?filter=ai&query=AI结论&limit=1")

    assert image_response.status_code == 200
    image_payload = image_response.get_json()
    assert image_payload["total"] == 1
    assert len(image_payload["records"]) == 1
    assert image_payload["records"][0]["itemid"] == 101

    assert video_response.status_code == 200
    video_payload = video_response.get_json()
    assert video_payload["total"] == 1
    assert len(video_payload["records"]) == 1
    assert video_payload["records"][0]["itemid"] == 201


def test_history_endpoints_support_offset_pagination(client, monkeypatch):
    _login_session(client)

    def fake_detection_sql(sql, params=None, fetch=True):
        if sql == IMAGE_HISTORY_QUERY:
            return [
                {
                    "itemid": 401,
                    "filename": "first.png",
                    "fake": 62.0,
                    "clarity": "高",
                    "openid": "openid-1",
                    "phone": "13800000000",
                    "createtime": "2026-05-27 19:00:00",
                    "explantation": "",
                },
                {
                    "itemid": 402,
                    "filename": "second.png",
                    "fake": 22.0,
                    "clarity": "中",
                    "openid": "openid-1",
                    "phone": "13800000000",
                    "createtime": "2026-05-27 19:01:00",
                    "explantation": "",
                },
            ]
        raise AssertionError(f"unexpected SQL: {sql}")

    monkeypatch.setattr(api, "excute_detection_sql", fake_detection_sql)
    monkeypatch.setattr(api, "_has_detection_metadata", lambda itemid: False)

    image_response = client.get("/api/history/image-detections?limit=1&offset=1")

    assert image_response.status_code == 200
    image_payload = image_response.get_json()
    assert image_payload["total"] == 2
    assert len(image_payload["records"]) == 1
    assert image_payload["records"][0]["itemid"] == 402


def test_history_endpoints_reject_invalid_filter_or_limit(client):
    bad_image_limit = client.get("/api/history/image-detections?limit=0")
    bad_image_offset = client.get("/api/history/image-detections?offset=-1")
    bad_image_filter = client.get("/api/history/image-detections?filter=bad")
    bad_video_filter = client.get("/api/history/video-detections?filter=oops")

    assert bad_image_limit.status_code == 400
    assert bad_image_offset.status_code == 400
    assert bad_image_filter.status_code == 400
    assert bad_video_filter.status_code == 400
