from io import BytesIO
from pathlib import Path
import base64
import json
import sys
import threading

import pytest
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from imagedetection import creat_app, legal_documents  # noqa: E402
from imagedetection.views import api, detection, evidence_manifest, historical_record, reporting  # noqa: E402
import detector_backend  # noqa: E402

ACCOUNT_UUID = "11111111-1111-4111-8111-111111111111"
OWNER_WHERE = "owner_account_uuid = %s"
GUEST_OWNER_WHERE = "Userid IS NULL AND (phone IS NULL OR phone = '') AND openid = %s"
IMAGE_HISTORY_QUERY = f"SELECT * FROM data WHERE {OWNER_WHERE} ORDER BY {api.HISTORY_ORDER_BY}"
VIDEO_HISTORY_QUERY = f"SELECT * FROM video_data WHERE {OWNER_WHERE} ORDER BY {api.HISTORY_ORDER_BY}"
VALID_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)
CALIBRATED_MODEL_DECISION = {
    "ready": True,
    "mode": "calibrated_verdict",
    "calibrationId": "calibration-2026-07",
    "manifestSha256": "b" * 64,
    "datasetSha256": "a" * 64,
    "modelSha256": "c" * 64,
    "preprocessingSha256": "d" * 64,
    "runtimeContractSha256": "e" * 64,
    "evaluationCodeRevision": "eval-commit-abc123",
    "expiresAt": "2099-12-31T23:59:59Z",
    "realSamples": 800,
    "fakeSamples": 700,
    "observedFpr": 0.03,
    "observedFnr": 0.08,
    "aiThreshold": 0.61,
    "gateReasons": [],
}


def _animated_gif_bytes():
    output = BytesIO()
    frames = [Image.new("RGB", (2, 2), color) for color in ("white", "black")]
    frames[0].save(output, format="GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
    return output.getvalue()


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FailedResponse(_FakeResponse):
    def __init__(self, payload, status_code, headers=None):
        super().__init__(payload)
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self):
        raise detection.requests.HTTPError(f"HTTP {self.status_code}", response=self)


@pytest.fixture
def client():
    app = creat_app()
    app.config.update(TESTING=True)
    return app.test_client()


@pytest.fixture(autouse=True)
def isolate_evidence_persistence(monkeypatch):
    """Flow tests do not require a live detection DB or evidence filesystem."""
    monkeypatch.setattr(
        detection,
        "_persist_and_freeze_completed_image_result",
        lambda itemid, result, **kwargs: True,
    )


def _login_session(client, phone="13800000000"):
    with client.session_transaction() as sess:
        sess["user_info"] = {
            "Userid": 1,
            "account_uuid": ACCOUNT_UUID,
            "username": "tester",
            "phone": phone,
            "openid": "openid-1",
        }


def _run_fast_payload(client, *, is_guest=False):
    user_info = {
        "Userid": None if is_guest else 1,
        "account_uuid": "" if is_guest else ACCOUNT_UUID,
        "username": "访客" if is_guest else "tester",
        "phone": "" if is_guest else "13800000000",
        "openid": "guest-test" if is_guest else "openid-1",
    }
    with client.application.test_request_context("/image_upload/detect", method="POST"):
        return detection._run_image_detection_payload(
            VALID_PNG_BYTES,
            "demo.png",
            "image/png",
            user_info,
            is_guest=is_guest,
            mark_guest=False,
        )


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
    monkeypatch.setattr(api, "_reserve_password_login_attempt", lambda phone: None)
    monkeypatch.setattr(api, "_clear_password_phone_attempts", lambda phone: None)

    response = client.post(
        "/api/login/password",
        json={"phone": "13800000000", "secret": "hashed-pass", "accepted_terms": True},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["user"]["Userid"] == 7
    with client.session_transaction() as sess:
        assert sess["user_info"]["phone"] == "13800000000"


def test_api_me_returns_anonymous_state_without_session(client):
    response = client.get("/api/me")

    assert response.status_code == 200
    assert response.get_json() == {
        "status": "success",
        "authenticated": False,
        "user": None,
        "counters": {"image_detect": 0, "video_detect": 0},
    }


def test_developer_api_key_lifecycle(client, monkeypatch):
    _login_session(client)
    monkeypatch.setattr(api, "_ensure_developer_api_key_table", lambda: True)
    monkeypatch.setattr(api, "DEVELOPER_AUTH_SECRET", "internal-secret")
    monkeypatch.setattr(api, "DEVELOPER_IDEMPOTENCY_SECRET", "test-idempotency-secret")
    rows = []
    next_id = {"value": 0}

    def fake_lastid(sql, params=None):
        assert "INSERT INTO developer_api_keys" in sql
        next_id["value"] += 1
        user_id, name, key_hash, key_prefix, key_last4, scopes, expires_at, ip_allowlist = params
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
            "expires_at": expires_at,
            "ip_allowlist": ip_allowlist,
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
    def fake_create_with_limit(user_id, options):
        api_key = f"{api.DEVELOPER_API_KEY_PREFIX}created-test-secret"
        row_id = fake_lastid(
            "INSERT INTO developer_api_keys",
            (
                user_id,
                options["name"],
                api._developer_key_hash(api_key),
                api.DEVELOPER_API_KEY_PREFIX,
                api_key[-4:],
                options["scopes"],
                options["expires_at"],
                options["ip_allowlist"],
            ),
        )
        return api_key, next(row for row in rows if row["id"] == row_id), None

    monkeypatch.setattr(api, "_create_developer_key_with_limit", fake_create_with_limit)

    def fake_revoke_atomic(user_id, key_id):
        for row in rows:
            if row["id"] == key_id and row["user_id"] == user_id and row["status"] == "active":
                row["status"] = "revoked"
                row["revoked_at"] = "2026-06-02 10:03:00"
                return True, None, 200
        return False, "API Key 不存在或已撤销", 404

    monkeypatch.setattr(api, "_revoke_developer_key_atomic", fake_revoke_atomic)

    created = client.post(
        "/api/developer/keys",
        json={"name": "Agent key"},
        headers={"Idempotency-Key": "create-agent-key-001"},
    )
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


def test_developer_api_key_creation_requires_idempotency_key(client, monkeypatch):
    _login_session(client)
    monkeypatch.setattr(api, "_ensure_developer_api_key_table", lambda: True)

    response = client.post("/api/developer/keys", json={"name": "Agent key"})

    assert response.status_code == 400
    assert response.get_json()["code"] == "idempotency_key_required"


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


def test_developer_v1_detect_is_retired_after_key_validation(client, monkeypatch):
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
    response = client.post(
        "/api/developer/v1/detect",
        headers={"X-RealGuard-Key": "rg_sk_test"},
        data={"file": (BytesIO(VALID_PNG_BYTES), "demo.png")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 410
    assert response.get_json()["migration"] == "/api/developer/openapi.json"


def test_legacy_guest_image_detect_uses_durable_queue_and_then_blocks(client, monkeypatch, tmp_path):
    monkeypatch.setattr(detection.admin_state, "STATE_PATH", tmp_path / "admin_state.json")
    monkeypatch.setattr(detection, "_record_guest_upload_consent", lambda *_args, **_kwargs: None)
    queued = []
    monkeypatch.setattr(
        detection,
        "_enqueue_persistent_web_job",
        lambda job, *args, **kwargs: queued.append(args) or (True, "", "", job["id"], False),
    )

    first = client.post(
        "/image_upload/detect",
        data={
            "image": (BytesIO(VALID_PNG_BYTES), "demo.png"),
            "upload_consent": "1",
            "consent_version": legal_documents.CONSENT_VERSION,
            "terms_sha256": legal_documents.TERMS.sha256,
            "privacy_sha256": legal_documents.PRIVACY.sha256,
        },
        content_type="multipart/form-data",
        headers={"Idempotency-Key": "guest-detection-001"},
    )

    assert first.status_code == 202
    assert first.get_json()["job"]["status"] == "queued"
    assert len(queued) == 1
    with client.session_transaction() as sess:
        assert sess[detection.GUEST_DETECTION_SESSION_KEY] == 1

    second = client.post(
        "/image_upload/detect",
        data={"image": (BytesIO(VALID_PNG_BYTES), "demo.png")},
        content_type="multipart/form-data",
        headers={"Idempotency-Key": "guest-detection-002"},
    )

    assert second.status_code == 401
    assert "请登录后继续检测" in second.get_json()["message"]


def test_guest_image_detection_requires_current_upload_consent(client, monkeypatch, tmp_path):
    monkeypatch.setattr(detection.admin_state, "STATE_PATH", tmp_path / "admin_state.json")
    queued = []
    monkeypatch.setattr(
        detection,
        "_enqueue_persistent_web_job",
        lambda *args, **kwargs: queued.append(args) or (True, "", "", "unexpected", False),
    )

    response = client.post(
        "/image_upload/detect_async",
        data={"image": (BytesIO(VALID_PNG_BYTES), "demo.png")},
        content_type="multipart/form-data",
        headers={"Idempotency-Key": "guest-no-consent-001"},
    )

    assert response.status_code == 428
    assert response.get_json()["code"] == "upload_consent_required"
    assert queued == []


def test_remote_primary_result_is_imported_into_local_user_history(monkeypatch):
    inserted = {}
    api_json = {
        "code": 200,
        "data": {
            "data_itemid": 713,
            "filename": "remote-result.png",
            "fake_percentage": 66.1,
            "detector_probability": 0.61,
            "final_label": "AI生成图像",
            "confidence": "中",
            "explanation": "远端模型完成检测。",
            "visual_issues": ["局部纹理异常"],
        },
    }
    monkeypatch.setattr(detection, "_local_detection_record", lambda itemid: None)
    monkeypatch.setattr(
        detection,
        "_save_local_upload",
        lambda image_bytes, folder, filename: ("local-result.png", "/tmp/local-result.png"),
    )
    monkeypatch.setattr(detection, "get_image_info", lambda path: ("PNG", "640x480"))
    monkeypatch.setattr(detection, "get_file_size_str", lambda path: "12KB")
    monkeypatch.setattr(detection, "_detection_database_user_id", lambda phone, openid: 42)

    def fake_lastid(sql, params=None):
        inserted["sql"] = " ".join(sql.split())
        inserted["params"] = params
        return 648

    monkeypatch.setattr(detection, "excute_detection_sql_lastid", fake_lastid)

    itemid = detection._ensure_local_primary_record(
        api_json,
        b"image-bytes",
        "upload.png",
        "openid-1",
        "13800000000",
        {"Userid": 1},
    )

    assert itemid == 648
    assert "INSERT INTO data" in inserted["sql"]
    assert inserted["params"][-3] == 42
    assert inserted["params"][-1] is None
    assert api_json["data"]["data_itemid"] == 648
    assert api_json["data"]["filename"] == "local-result.png"
    assert api_json["data"]["image_url"] == "/api/media/image/648"


def test_existing_guest_primary_record_materializes_source_without_duplication(monkeypatch):
    updates = []
    api_json = {
        "code": 200,
        "data": {
            "data_itemid": 22,
            "filename": "stored.png",
        },
    }
    monkeypatch.setattr(
        detection,
        "_local_detection_record",
        lambda itemid: {
            "itemid": itemid,
            "filename": "stored.png",
            "Userid": None,
            "owner_account_uuid": None,
            "phone": "",
            "openid": "guest-existing",
        },
    )
    monkeypatch.setattr(detection.os.path, "isfile", lambda path: False)
    monkeypatch.setattr(
        detection,
        "_save_local_upload",
        lambda image_bytes, folder, filename: ("local-stored.png", "/tmp/local-stored.png"),
    )
    monkeypatch.setattr(detection, "get_image_info", lambda path: ("PNG", "640x480"))
    monkeypatch.setattr(detection, "get_file_size_str", lambda path: "12KB")
    monkeypatch.setattr(
        detection,
        "excute_detection_sql",
        lambda sql, params=None, fetch=True: updates.append((sql, params, fetch)) or 1,
    )
    monkeypatch.setattr(
        detection,
        "_insert_local_detection_record",
        lambda *args, **kwargs: pytest.fail("matching local records must not be duplicated"),
    )

    itemid = detection._ensure_local_primary_record(
        api_json,
        b"image-bytes",
        "upload.png",
        "guest-existing",
        "",
        {"Userid": None, "openid": "guest-existing"},
    )

    assert itemid == 22
    assert len(updates) == 1
    assert "UPDATE data" in updates[0][0]
    assert updates[0][1][-1] == "guest-existing"
    assert api_json["data"]["filename"] == "local-stored.png"


def test_swarm_final_result_updates_the_same_history_record(monkeypatch):
    updates = []
    final_result = {
        "itemid": 648,
        "filename": "local-result.png",
        "probability": 0.7612,
        "detector_probability": 0.61,
        "final_label": "AI生成图像",
        "confidence": "中",
        "explanation": "多路证据融合完成。",
    }
    monkeypatch.setattr(
        detection,
        "_local_detection_record",
        lambda itemid: {
            "itemid": itemid,
            "filename": "local-result.png",
            "Userid": 1,
            "phone": "13800000000",
            "openid": "openid-1",
        },
    )
    monkeypatch.setattr(
        detection,
        "excute_detection_sql",
        lambda sql, params=None, fetch=True: updates.append((" ".join(sql.split()), params, fetch)) or 1,
    )

    itemid = detection._persist_swarm_history_result(
        final_result,
        b"image-bytes",
        "upload.png",
        "openid-1",
        "13800000000",
        {"Userid": 1},
    )

    assert itemid == 648
    assert updates[0][0].startswith("UPDATE data SET fake")
    assert updates[0][1][0] == pytest.approx(76.12)
    assert updates[0][1][-1] == 648
    assert updates[0][2] is False


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
                "source": "provenance",
                "decisionStatus": "verdict",
                "decisionAuthority": "decisive_provenance",
                "reviewRequired": False,
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

    payload, status_code = _run_fast_payload(client)

    assert status_code == 200
    assert payload["result"]["itemid"] == 88
    assert payload["result"]["final_label"] == "需人工复核"
    assert payload["result"]["probability"] is None
    assert payload["result"]["detector_probability"] is None
    assert payload["result"]["confidence"] == "不适用"
    assert payload["result"]["scorePublished"] is False
    assert payload["result"]["modelDecisionReady"] is False
    assert payload["result"]["reviewRequired"] is True
    assert payload["result"]["image_url"] == "/api/media/image/88"


@pytest.mark.parametrize(
    "payload",
    [
        {"source": "mock", "verdict": "suspected_fake", "confidence": 0.8},
        {"source": "unknown", "verdict": "unknown", "confidence": 0.5},
        {"source": "vlm", "verdict": "unknown", "confidence": 0.5},
        {
            "source": "provenance",
            "verdict": "suspected_fake",
            "decisionStatus": "review_only",
            "decisionAuthority": "none",
            "reviewRequired": True,
        },
        {
            "source": "provenance",
            "verdict": "suspected_fake",
            "decisionStatus": "verdict",
            "decisionAuthority": "none",
            "reviewRequired": False,
        },
        {
            "source": "vlm",
            "verdict": "suspected_fake",
            "decisionStatus": "verdict",
            "decisionAuthority": "decisive_provenance",
            "reviewRequired": False,
        },
    ],
)
def test_v2_fallback_rejects_results_without_a_publishable_model_verdict(payload):
    with pytest.raises(RuntimeError, match="真实模型结论"):
        detection._assert_v2_publishable(payload)


def test_v2_probability_conversion_rejects_unknown_verdict():
    with pytest.raises(ValueError, match="明确判定"):
        detection._fake_percentage_from_v2({"verdict": "unknown", "confidence": 0.5})


@pytest.mark.parametrize(
    ("verdict", "confidence", "expected"),
    [
        ("real", 0.2, 20.0),
        ("suspected_fake", 0.66, 66.0),
        ("highly_suspected_fake", 95, 95.0),
    ],
)
def test_v2_probability_conversion_preserves_ai_risk_semantics(verdict, confidence, expected):
    assert detection._fake_percentage_from_v2({
        "verdict": verdict,
        "confidence": confidence,
    }) == expected


def test_v2_probability_conversion_prefers_explicit_aigc_probability():
    assert detection._fake_percentage_from_v2({
        "verdict": "suspected_fake",
        "confidence": 0.94,
        "riskScore": 0.94,
        "aiProbability": 0.2,
        "riskVector": {"aiGenerated": 0.2, "tampered": 0.94, "deepfake": 0.1},
    }) == 20.0


def test_v2_publishable_contract_accepts_verified_provenance_source():
    detection._assert_v2_publishable({
        "source": "provenance",
        "verdict": "highly_suspected_fake",
        "confidence": 0.99,
        "decisionStatus": "verdict",
        "decisionAuthority": "decisive_provenance",
        "reviewRequired": False,
    })


def test_image_report_marks_borderline_result_for_human_review(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence_manifest, "load_recorded_metadata", lambda record_id: {})
    source = tmp_path / "sample.png"
    source.write_bytes(b"sample-image")
    html = reporting.image_report_content(
        {
            "itemid": 9,
            "filename": "sample.png",
            "fake": 66.1,
            "aigc": "AI生成图像",
            "clarity": "低",
            "explantation": "服务端证据摘要",
            "createtime": "2026-07-15 10:00:00",
        },
        {
            "final_label": "客户端伪造标签",
            "probability": 0.01,
        },
        source_path=source,
        model_run={},
        generated_at="2026-07-15T02:00:00Z",
        signing_key="test-evidence-key-0123456789abcdef",
        snapshot_root=tmp_path / "snapshots",
    )

    assert "需人工复核 · 未发布自动风险分数" in html
    assert "缺失本身不代表伪造" in html
    assert reporting.image_report_filename(9) == "huijian-image-report-9.pdf"


def test_video_review_only_report_never_fabricates_zero_probability():
    html = reporting.video_report_content(
        {"itemid": 17, "createtime": "2026-07-20 10:00:00"},
        {
            "filename": "sample.mp4",
            "decisionStatus": "review_only",
            "decisionAuthority": "none",
            "reviewRequired": True,
            "fake_percentage": None,
            "real_percentage": None,
            "confidence": "低",
            "final_label": "需人工复核",
            "explanation": "模型尚未获得自动结论授权。",
        },
    )

    assert "需人工复核 · 未发布自动概率" in html
    assert "<td class=\"right\">未发布</td>" in html
    assert "AI 概率 0.0%" not in html
    assert "真实概率 0.0%" not in html


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

    payload, status_code = _run_fast_payload(client)

    assert status_code == 200
    assert payload["result"]["itemid"] == 123
    assert payload["result"]["probability"] is None
    assert payload["result"]["detector_probability"] is None
    assert payload["result"]["confidence"] == "不适用"
    assert payload["result"]["scorePublished"] is False
    assert payload["result"]["final_label"] == "需人工复核"
    assert payload["result"]["reviewRequired"] is True
    assert payload["result"]["agent_reasoning"] == ""
    runs = detection.admin_state.load_state()["modelRuns"]
    assert runs[0]["itemid"] == 123
    assert runs[0]["model"]["id"] == "aliyun-aigc-pro"
    assert runs[0]["meta"]["service"] == "aigcDetector_pro"


def test_fast_image_detect_exposes_parallel_visible_watermark(client, monkeypatch):
    _login_session(client)
    precheck = {
        "status": "ok",
        "coordinateSpace": "display_normalized_v1",
        "displaySize": {"width": 640, "height": 480},
        "elapsedMs": 24,
        "engineVersion": "test-registry",
        "genericVisibleWatermark": {
            "available": True,
            "detected": True,
            "count": 1,
            "model": "test-yolo",
        },
        "visibleHits": [{
            "provider": "yolo11x_watermark",
            "label": "可见水印（平台待确认）",
            "confidence": 0.91,
            "bbox": {"x": 0.72, "y": 0.81, "w": 0.18, "h": 0.09},
        }],
    }
    monkeypatch.setattr(detection, "_primary_image_model", lambda: {"id": "fast-test"})
    monkeypatch.setattr(
        detection,
        "_primary_image_endpoint",
        lambda: ("http://detector.test/image", 30, ""),
    )
    monkeypatch.setattr(
        detection,
        "_backend_post",
        lambda *args, **kwargs: _FakeResponse({
            "code": 200,
            "data": {
                "data_itemid": 456,
                "fake_percentage": 21.0,
                "final_label": "真实图像",
                "confidence": "中",
                "filename": "demo.png",
                "remote_evidence": {"visibleWatermarkPrecheck": precheck},
            },
        }),
    )
    monkeypatch.setattr(detection, "_ensure_local_primary_record", lambda *args, **kwargs: None)
    monkeypatch.setattr(detection, "_metadata_for_item", lambda itemid: {})
    monkeypatch.setattr(detection, "_record_model_run", lambda *args, **kwargs: None)

    payload, status_code = _run_fast_payload(client)

    assert status_code == 200
    result = payload["result"]
    assert result["visibleWatermark"]["detected"] is True
    assert result["visibleWatermark"]["hits"][0]["confidence"] == pytest.approx(0.91)
    assert result["visibleWatermark"]["elapsedMs"] == 24
    assert result["final_label"] == "需人工复核"
    assert result["probability"] is None
    assert result["detector_probability"] is None
    assert result["confidence"] == "不适用"
    assert result["scorePublished"] is False
    assert result["reviewRequired"] is True
    assert result.get("watermark_verdict_override") is None
    assert "不单独影响 AI 生成结论" in result["visibleWatermark"]["note"]
    assert "_remote_evidence" not in result


def test_fast_image_detect_marks_failed_watermark_evidence_as_incomplete(client, monkeypatch):
    _login_session(client)
    monkeypatch.setattr(detection, "_primary_image_endpoint", lambda: ("http://detector.test/image", 30, ""))
    monkeypatch.setattr(
        detection,
        "_backend_post",
        lambda *args, **kwargs: _FakeResponse({
            "code": 200,
            "data": {
                "data_itemid": 457,
                "fake_percentage": 21.0,
                "final_label": "真实图像",
                "confidence": "中",
                "filename": "demo.png",
                "remote_evidence": {
                    "visibleWatermarkPrecheck": {
                        "status": "failed",
                        "errorCode": "visible_watermark_unavailable",
                        "message": "可见水印检测暂不可用，本次证据不完整。",
                        "elapsedMs": 12000,
                    },
                },
            },
        }),
    )
    monkeypatch.setattr(detection, "_ensure_local_primary_record", lambda *args, **kwargs: None)
    monkeypatch.setattr(detection, "_metadata_for_item", lambda itemid: {})
    monkeypatch.setattr(detection, "_record_model_run", lambda *args, **kwargs: None)

    payload, status_code = _run_fast_payload(client)

    assert status_code == 200
    result = payload["result"]
    assert result["visibleWatermark"]["supported"] is False
    assert result["visibleWatermark"]["detected"] is False
    assert "不可用" in result["visibleWatermark"]["note"]
    assert result["evidenceCompleteness"] is False
    assert "不能据此判断图片未含水印" in result["evidenceWarnings"][0]


def test_fast_image_detect_does_not_publish_uncalibrated_model_score(client, monkeypatch):
    _login_session(client)
    precheck = {
        "status": "ok",
        "coordinateSpace": "display_normalized_v1",
        "displaySize": {"width": 640, "height": 480},
        "genericVisibleWatermark": {
            "available": True,
            "detected": False,
            "count": 0,
        },
        "visibleHits": [],
    }
    monkeypatch.setattr(detection, "_primary_image_endpoint", lambda: ("http://detector.test/image", 30, ""))
    monkeypatch.setattr(
        detection,
        "_backend_post",
        lambda *args, **kwargs: _FakeResponse({
            "code": 200,
            "data": {
                "data_itemid": 458,
                "fake_percentage": 99.9,
                "detector_probability": 0.999,
                "final_label": "AI生成图像",
                "confidence": "高",
                "explanation": "未校准模型原始输出。",
                "filename": "demo.png",
                "remote_evidence": {
                    "visibleWatermarkPrecheck": precheck,
                    "modelDecision": {
                        "ready": False,
                        "mode": "review_only",
                        "rawModelScore": 0.999,
                    },
                },
            },
        }),
    )
    monkeypatch.setattr(detection, "_ensure_local_primary_record", lambda *args, **kwargs: 458)
    monkeypatch.setattr(detection, "_metadata_for_item", lambda itemid: {})
    monkeypatch.setattr(detection, "_record_model_run", lambda *args, **kwargs: None)

    payload, status_code = _run_fast_payload(client)

    assert status_code == 200
    result = payload["result"]
    assert result["final_label"] == "需人工复核"
    assert result["probability"] is None
    assert result["detector_probability"] is None
    assert result["confidence"] == "不适用"
    assert result["scorePublished"] is False
    assert result["modelDecisionReady"] is False
    assert result["reviewRequired"] is True
    assert result["evidenceCompleteness"] is False
    assert any("独立校准契约" in warning for warning in result["evidenceWarnings"])


def test_fast_image_detect_uses_rich_native_capture_chain_for_borderline_risk(client, monkeypatch):
    _login_session(client)
    monkeypatch.setattr(detection, "validate_model_decision", lambda _decision: True)
    monkeypatch.setattr(detection, "validate_inference_audit", lambda _audit, _decision: True)
    camera_metadata = {
        "EXIF:Make": "Apple",
        "EXIF:Model": "iPhone 16 Pro",
        "EXIF:LensModel": "iPhone 16 Pro back triple camera",
        "EXIF:ExposureTime": "1/120",
        "EXIF:FNumber": "1.8",
        "EXIF:ISO": "80",
        "EXIF:FocalLength": "6.8 mm",
        "EXIF:DateTimeOriginal": "2026:07:20 10:21:33",
        "EXIF:OffsetTimeOriginal": "+08:00",
        "EXIF:MakerNote": {"HDR": "On"},
        "EXIF:WhiteBalance": "Auto",
        "EXIF:ColorSpace": "sRGB",
        "EXIF:SceneType": "Directly photographed",
    }
    precheck = {
        "status": "ok",
        "coordinateSpace": "display_normalized_v1",
        "displaySize": {"width": 4032, "height": 3024},
        "genericVisibleWatermark": {"available": True, "detected": False, "count": 0},
        "visibleHits": [],
    }
    monkeypatch.setattr(detection, "_primary_image_endpoint", lambda: ("http://detector.test/image", 30, ""))
    monkeypatch.setattr(
        detection,
        "_backend_post",
        lambda *args, **kwargs: _FakeResponse({
            "code": 200,
            "data": {
                "fake_percentage": 70.0,
                "detector_probability": 0.70,
                "final_label": "AI生成图像",
                "confidence": "中",
                "explanation": "主模型处于边界偏高区间。",
                "filename": "iphone-photo.jpg",
                "full_exif_info": camera_metadata,
                "remote_evidence": {
                    "visibleWatermarkPrecheck": precheck,
                    "modelDecision": {
                        **CALIBRATED_MODEL_DECISION,
                        "publishedProbability": 0.70,
                        "finalLabel": "AI生成图像",
                    },
                    "modelRun": {"schema": "test-audit"},
                },
            },
        }),
    )
    monkeypatch.setattr(detection, "_ensure_local_primary_record", lambda *args, **kwargs: None)
    monkeypatch.setattr(detection, "_record_model_run", lambda *args, **kwargs: None)

    payload, status_code = _run_fast_payload(client)

    assert status_code == 200
    result = payload["result"]
    assert result["detector_probability"] == pytest.approx(0.70)
    assert result["probability"] < 0.62
    assert result["final_label"] == "真实图像"
    assert result["decisionAuthority"] == "calibrated_model_with_capture_evidence"
    assert result["capture_evidence"]["profile"] == "native_capture_chain"
    assert result["probabilityModel"]["captureGuardrail"]["applied"] is True
    assert "原始主模型 AI 风险为 70.00%" in result["explanation"]


def test_model_decision_contract_requires_complete_calibration_evidence():
    calibrated = dict(CALIBRATED_MODEL_DECISION)

    assert detection._model_decision_is_publishable(calibrated) is False
    assert detection._model_decision_is_publishable({**calibrated, "datasetSha256": ""}) is False
    assert detection._model_decision_is_publishable({**calibrated, "observedFpr": 0.08}) is False


def test_fast_detection_preserves_gpu_queue_overload_without_model_fallback(monkeypatch):
    fallback_calls = []
    monkeypatch.setattr(detection, "_primary_image_endpoint", lambda: ("http://detector/image", 30, ""))
    monkeypatch.setattr(
        detection,
        "_backend_post",
        lambda *args, **kwargs: _FailedResponse(
            {"code": 429, "errorCode": "gpu_queue_full", "msg": "GPU busy"},
            429,
            {"Retry-After": "6"},
        ),
    )
    monkeypatch.setattr(
        detection,
        "_detect_with_v2_fallback",
        lambda *args, **kwargs: fallback_calls.append(True),
    )

    payload, status = detection._run_image_detection_payload(
        VALID_PNG_BYTES,
        "demo.png",
        "image/png",
        {"Userid": 1, "account_uuid": ACCOUNT_UUID, "openid": "openid-1"},
    )

    assert status == 429
    assert payload["code"] == "gpu_queue_full"
    assert payload["retryAfter"] == "6"
    assert fallback_calls == []


def test_partial_watermark_scan_cannot_claim_complete_negative_evidence():
    visible = detection.swarm_visible_watermark_expert._visible_result({
        "status": "ok",
        "elapsedMs": 20,
        "visibleHits": [],
        "genericVisibleWatermark": {
            "available": False,
            "error": "ConnectionError",
            "detected": False,
            "count": 0,
        },
    })

    assert visible["registrySupported"] is True
    assert visible["genericVisibleSupported"] is False
    assert visible["supported"] is False
    assert visible["detected"] is False


def test_v1_watermark_policy_keeps_confirmed_platform_marks_non_decisive():
    result = {
        "final_label": "真实图像",
        "probability": 0.21,
        "detector_probability": 0.21,
        "confidence": "中",
        "explanation": "主模型偏向真实。",
    }
    visible = {
        "supported": True,
        "coordinateSpace": "display_normalized_v1",
        "displaySize": {"width": 1280, "height": 720},
        "detected": True,
        "hits": [{
            "provider": "gemini",
            "label": "Google Gemini",
            "confidence": 0.91,
            "bbox": {"x": 0.72, "y": 0.81, "w": 0.18, "h": 0.09},
            "method": "remove_ai_watermarks_registry",
            "decisive": True,
            "localizationConfirmed": True,
        }],
    }

    assert detection.watermark_verdict.apply_to_result(result, visible) is False
    assert result["final_label"] == "真实图像"
    assert result["probability"] == pytest.approx(0.21)
    assert "watermark_verdict_override" not in result
    explanation = detection.watermark_verdict.build_explanation(result, visible)
    assert "Google Gemini" in explanation
    assert "不单独决定真伪" in explanation


def test_v1_registry_visual_attribution_remains_non_decisive_without_yolo():
    result = {
        "final_label": "真实图像",
        "probability": 0.12,
        "detector_probability": 0.12,
        "confidence": "中",
        "explanation": "主模型偏向真实。",
    }
    visible = {
        "supported": False,
        "positiveEvidenceSupported": True,
        "registrySupported": True,
        "genericVisibleSupported": False,
        "coordinateSpace": "display_normalized_v1",
        "displaySize": {"width": 1280, "height": 720},
        "detected": True,
        "hits": [{
            "provider": "gemini",
            "label": "Google Gemini",
            "confidence": 0.91,
            "bbox": {"x": 0.72, "y": 0.81, "w": 0.18, "h": 0.09},
            "method": "remove_ai_watermarks_registry",
            "decisive": True,
            "localizationConfirmed": False,
        }],
    }

    assert detection.watermark_verdict.apply_to_result(result, visible) is False
    assert result["probability"] == pytest.approx(0.12)


@pytest.mark.parametrize(
    "bbox",
    [
        {"x": -0.01, "y": 0.2, "w": 0.2, "h": 0.2},
        {"x": 0.9, "y": 0.2, "w": 0.2, "h": 0.2},
        {"x": float("nan"), "y": 0.2, "w": 0.2, "h": 0.2},
        {"x": 0.2, "y": 0.2, "w": float("inf"), "h": 0.2},
    ],
)
def test_v1_watermark_policy_rejects_invalid_bbox_without_clamping(bbox):
    result = {
        "final_label": "真实图像",
        "probability": 0.12,
        "detector_probability": 0.12,
        "confidence": "中",
        "explanation": "主模型偏向真实。",
    }
    visible = {
        "supported": True,
        "coordinateSpace": "display_normalized_v1",
        "displaySize": {"width": 1280, "height": 720},
        "detected": True,
        "hits": [{
            "provider": "gemini",
            "confidence": 0.99,
            "bbox": bbox,
            "method": "remove_ai_watermarks_registry",
            "decisive": True,
        }],
    }

    assert detection.watermark_verdict.apply_to_result(result, visible) is False
    assert result["probability"] == pytest.approx(0.12)


def test_v1_watermark_policy_rejects_unconfirmed_tiny_low_confidence_hit():
    result = {
        "final_label": "真实图像",
        "probability": 0.21,
        "detector_probability": 0.21,
        "confidence": "中",
        "explanation": "主模型偏向真实。",
    }
    visible = {
        "supported": True,
        "coordinateSpace": "display_normalized_v1",
        "displaySize": {"width": 1280, "height": 720},
        "detected": True,
        "hits": [{
            "provider": "gemini",
            "confidence": 0.01,
            "bbox": {"x": 0.8, "y": 0.8, "w": 0.001, "h": 0.001},
            "method": "remove_ai_watermarks_registry",
            "decisive": True,
            "localizationConfirmed": True,
        }],
    }

    assert detection.watermark_verdict.apply_to_result(result, visible) is False
    assert result["final_label"] == "真实图像"
    assert result["probability"] == pytest.approx(0.21)


def test_v1_watermark_policy_rejects_decisive_hit_with_invalid_coordinate_protocol():
    result = {
        "final_label": "真实图像",
        "probability": 0.1,
        "detector_probability": 0.1,
        "confidence": "高",
        "explanation": "主模型偏向真实。",
    }
    visible = {
        "supported": False,
        "coordinateSpace": "unknown",
        "displaySize": {},
        "detected": True,
        "hits": [{
            "provider": "gemini",
            "confidence": 0.99,
            "bbox": {"x": 0.7, "y": 0.8, "w": 0.2, "h": 0.1},
            "method": "remove_ai_watermarks_registry",
            "decisive": True,
            "localizationConfirmed": True,
        }],
    }

    assert detection.watermark_verdict.apply_to_result(result, visible) is False
    assert result["final_label"] == "真实图像"
    assert result["probability"] == pytest.approx(0.1)


def test_visible_watermark_is_persisted_in_model_run_meta(monkeypatch):
    captured = {}
    precheck = {
        "status": "ok",
        "coordinateSpace": "display_normalized_v1",
        "displaySize": {"width": 640, "height": 480},
        "elapsedMs": 18,
        "genericVisibleWatermark": {"available": True, "detected": False, "count": 0},
        "visibleHits": [],
    }
    data = {
        "_route_model_id": "fast-test",
        "_route_role": "primary",
        "_route_provider": "internal",
        "_route_latency_ms": 41,
        "remote_evidence": {"visibleWatermarkPrecheck": precheck},
    }
    monkeypatch.setattr(detection.model_registry, "get_model", lambda model_id: {"id": model_id})

    def capture_model_run(*args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(detection.admin_state, "append_model_run", capture_model_run)
    detection._record_model_run(987, data, {"Userid": 1})

    visible = captured["meta"]["visibleWatermark"]
    assert visible["supported"] is True
    assert visible["detected"] is False
    assert "remote_evidence" not in captured["meta"]

    monkeypatch.setattr(
        detection.admin_state,
        "model_runs_by_itemids",
        lambda itemids: {"987": {"meta": {"visibleWatermark": visible}}},
    )
    assert detection._stored_visible_watermark_for_item(987) == visible


def test_image_result_restores_persisted_visible_watermark(client, monkeypatch):
    visible = {
        "enabled": True,
        "supported": True,
        "detected": False,
        "provider": None,
        "confidence": 0.0,
        "evidenceLevel": "none",
        "hits": [],
        "temporal": {"sampledFrames": 1, "positiveFrames": 0, "moving": False},
        "note": "可见水印扫描完成，本次未检出。",
        "elapsedMs": 19,
    }
    monkeypatch.setattr(
        detection,
        "_load_detection_record",
        lambda table, itemid: {
            "itemid": 987,
            "filename": "demo.png",
            "fake": 21.0,
            "clarity": "中",
            "feedback": None,
        },
    )
    monkeypatch.setattr(detection, "_metadata_for_item", lambda itemid: {})
    monkeypatch.setattr(detection, "_backend_static_url", lambda kind, item: "/api/media/image/987")
    monkeypatch.setattr(
        detection.admin_state,
        "model_runs_by_itemids",
        lambda itemids: {"987": {"meta": {"visibleWatermark": visible}}},
    )
    _login_session(client)

    response = client.get("/image_upload/result?itemid=987")

    assert response.status_code == 200
    assert response.get_json()["result"]["visibleWatermark"] == visible


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

    monkeypatch.setattr(detection, "_persist_swarm_history_result", lambda *args, **kwargs: 321)
    frozen_results = []
    monkeypatch.setattr(
        detection,
        "_persist_and_freeze_completed_image_result",
        lambda itemid, result, **kwargs: frozen_results.append((itemid, dict(result))) or True,
    )
    primary_freeze_flags = []

    def fake_primary(
        image_bytes,
        filename,
        mimetype,
        user_info,
        *,
        is_guest=False,
        mark_guest=True,
            include_internal_evidence=False,
            freeze_evidence=True,
            source_task_id="",
        ):
        primary_freeze_flags.append(freeze_evidence)
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
                "modelDecisionReady": True,
                "reviewRequired": False,
                "decisionStatus": "verdict",
                "decisionAuthority": "calibrated_model",
            },
        }, 200

    monkeypatch.setattr(detection, "_run_image_detection_payload", fake_primary)

    def execute_persisted_job(job, image_bytes, filename, mimetype, user_info, is_guest, idempotency_key):
        detection._run_swarm_image_job(
            job["id"], image_bytes, filename, mimetype, user_info, is_guest
        )
        return True, "", "", job["id"], False

    monkeypatch.setattr(detection, "_enqueue_persistent_web_job", execute_persisted_job)
    monkeypatch.setattr(
        detection,
        "_load_persistent_web_job",
        lambda job_id: detection.admin_state.get_detection_job(job_id),
    )

    created = client.post(
        "/image_upload/detect_swarm",
        data={"image": (BytesIO(VALID_PNG_BYTES), "demo.png")},
        content_type="multipart/form-data",
        headers={"Idempotency-Key": "swarm-consensus-001"},
    )

    assert created.status_code == 202
    job_id = created.get_json()["job"]["id"]
    assert detection.admin_state.get_detection_job(job_id)["actor"]["account_uuid"] == ACCOUNT_UUID
    loaded = client.get(f"/image_upload/jobs/{job_id}")

    assert loaded.status_code == 200
    job = loaded.get_json()["job"]
    assert job["status"] == "success"
    assert job["mode"] == "swarm"
    assert job["progress"] == 100
    result = job["result"]["result"]
    assert result["itemid"] == 321
    assert result["probability"] == pytest.approx(0.76)
    assert primary_freeze_flags == [False]
    assert len(frozen_results) == 1
    assert frozen_results[0][0] == 321
    assert frozen_results[0][1]["swarm"]["enabled"] is True
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


def test_guest_swarm_detection_requires_login(client, monkeypatch):
    queued = []
    monkeypatch.setattr(
        detection,
        "_enqueue_persistent_web_job",
        lambda *args, **kwargs: queued.append(args) or (True, "", ""),
    )

    response = client.post(
        "/image_upload/detect_swarm",
        data={"image": (BytesIO(VALID_PNG_BYTES), "demo.png")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 401
    assert response.get_json()["code"] == "authentication_required"
    assert queued == []


def test_async_image_upload_rejects_file_above_application_limit(client, monkeypatch):
    _login_session(client)
    monkeypatch.setattr(detection, "MAX_IMAGE_UPLOAD_BYTES", 4)

    response = client.post(
        "/image_upload/detect_async",
        data={"image": (BytesIO(b"12345"), "large.png")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 413
    assert response.get_json()["code"] == "image_too_large"


def test_image_upload_requires_content_length_before_parsing(client):
    with client.application.test_request_context("/image_upload/detect_async", method="POST"):
        response, status = detection._reject_oversized_upload_requests()

    assert status == 411
    assert response.get_json()["code"] == "length_required"


def test_video_upload_rejects_file_above_application_limit(client, monkeypatch):
    _login_session(client)
    monkeypatch.setattr(detection, "MAX_VIDEO_UPLOAD_BYTES", 4)

    response = client.post(
        "/video_upload/detect",
        data={"video_file": (BytesIO(b"12345"), "large.mp4")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 413
    assert response.get_json()["code"] == "video_too_large"


def test_async_image_upload_returns_429_when_job_capacity_is_full(client, monkeypatch, tmp_path):
    _login_session(client)
    monkeypatch.setattr(detection.admin_state, "STATE_PATH", tmp_path / "admin_state.json")

    monkeypatch.setattr(
        detection,
        "_enqueue_persistent_web_job",
        lambda *args, **kwargs: (False, "server_busy", "检测队列已满，请稍后重试", None, False),
    )

    response = client.post(
        "/image_upload/detect_async",
        data={"image": (BytesIO(VALID_PNG_BYTES), "sample.png")},
        content_type="multipart/form-data",
        headers={"Idempotency-Key": "capacity-full-001"},
    )

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "5"
    assert response.get_json()["code"] == "server_busy"


def test_async_image_replay_does_not_leave_orphan_progress_job(client, monkeypatch, tmp_path):
    _login_session(client)
    monkeypatch.setattr(detection.admin_state, "STATE_PATH", tmp_path / "admin_state.json")
    existing = {
        "id": "job_existing",
        "kind": "image",
        "filename": "sample.png",
        "status": "running",
        "actor": {"account_uuid": ACCOUNT_UUID},
        "progress": 50,
    }
    monkeypatch.setattr(
        detection,
        "_enqueue_persistent_web_job",
        lambda *_args, **_kwargs: (True, "", "", "job_existing", True),
    )
    monkeypatch.setattr(detection, "_load_persistent_web_job", lambda _job_id: existing)

    response = client.post(
        "/image_upload/detect_async",
        data={"image": (BytesIO(VALID_PNG_BYTES), "sample.png")},
        content_type="multipart/form-data",
        headers={"Idempotency-Key": "replay-cleanup-001"},
    )

    assert response.status_code == 200
    assert response.get_json()["job"]["id"] == "job_existing"
    assert detection.admin_state.list_detection_jobs() == []


def test_legacy_image_upload_alias_never_uses_process_local_slots(client, monkeypatch, tmp_path):
    _login_session(client)
    monkeypatch.setattr(detection.admin_state, "STATE_PATH", tmp_path / "admin_state.json")

    class CountingSlots:
        acquired = 0
        released = 0

        def acquire(self, blocking=False):
            self.acquired += 1
            return True

        def release(self):
            self.released += 1

    slots = CountingSlots()
    monkeypatch.setattr(detection, "BACKGROUND_JOB_SLOTS", slots)
    monkeypatch.setattr(
        detection,
        "_enqueue_persistent_web_job",
        lambda job, *args, **kwargs: (True, "", "", job["id"], False),
    )

    response = client.post(
        "/image_upload/detect",
        data={"image": (BytesIO(VALID_PNG_BYTES), "sample.png")},
        content_type="multipart/form-data",
        headers={"Idempotency-Key": "legacy-alias-001"},
    )

    assert response.status_code == 202
    assert slots.acquired == 0
    assert slots.released == 0


def test_image_upload_rejects_invalid_image_before_queueing(client):
    _login_session(client)

    response = client.post(
        "/image_upload/detect_async",
        data={"image": (BytesIO(b"not-an-image"), "sample.png")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert response.get_json()["code"] == "invalid_image"


def test_image_upload_rejects_animated_image_instead_of_analyzing_first_frame(client):
    _login_session(client)

    response = client.post(
        "/image_upload/detect_async",
        data={"image": (BytesIO(_animated_gif_bytes()), "sample.gif")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 415
    assert response.get_json()["code"] == "unsupported_animated_image"


def test_image_upload_rejects_pixel_bomb_before_queueing(client, monkeypatch):
    _login_session(client)
    monkeypatch.setattr(detection, "MAX_IMAGE_SOURCE_PIXELS", 0)

    response = client.post(
        "/image_upload/detect_async",
        data={"image": (BytesIO(VALID_PNG_BYTES), "sample.png")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 413
    payload = response.get_json()
    assert payload["code"] == "image_pixel_limit_exceeded"
    assert payload["details"]["width"] == 1
    assert payload["details"]["height"] == 1


def test_swarm_aggregate_keeps_tamper_risk_separate_from_aigc(monkeypatch):
    monkeypatch.setattr(detection, "_swarm_config", lambda: {
        "minExperts": 2,
        "consensusThreshold": 0.65,
        "disagreementThreshold": 0.35,
    })
    experts = [
        {"id": "primary", "name": "主模型", "status": "success", "score": 0.2, "weight": 0.7},
        {"id": "aliyun_ps", "name": "篡改模型", "status": "success", "score": 0.94, "weight": 0.3},
    ]
    primary = {
        "itemid": 1,
        "final_label": "真实图像",
        "probability": 0.2,
        "detector_probability": 0.2,
        "confidence": "高",
        "all_metadata": {},
        "modelDecisionReady": True,
        "reviewRequired": False,
        "decisionStatus": "verdict",
        "decisionAuthority": "calibrated_model",
    }

    result, error = detection._swarm_aggregate(experts, primary, {})

    assert error == ""
    assert result["final_label"] == "真实图像"
    assert result["probability"] == pytest.approx(0.2)
    assert result["aigc_probability"] == pytest.approx(0.2)
    assert result["tamper_probability"] == pytest.approx(0.94)
    assert result["probabilityModel"]["decisionContribution"] == "diagnostic_only"


def test_history_summary_preserves_specialized_tamper_label(monkeypatch):
    monkeypatch.setattr(api, "_has_detection_metadata", lambda itemid: False)
    monkeypatch.setattr(evidence_manifest, "validate_model_decision", lambda _decision: True)
    monkeypatch.setattr(evidence_manifest, "validate_inference_audit", lambda _audit, _decision: True)

    record = api._image_history_record(
        {
            "itemid": 73,
            "filename": "edited-camera.jpg",
            "fake": 94,
            "aigc": "疑似篡改图像",
            "clarity": "高",
            "createtime": "2026-07-19 12:00:00",
        },
        {
            "meta": {
                "modelDecision": dict(CALIBRATED_MODEL_DECISION),
            }
        },
    )

    assert record["final_label"] == "疑似篡改图像"
    assert record["fake_prob"] == 94.0


def test_history_summary_suppresses_unverified_legacy_score(monkeypatch):
    monkeypatch.setattr(api, "_has_detection_metadata", lambda itemid: False)

    record = api._image_history_record(
        {
            "itemid": 74,
            "filename": "legacy-review.jpg",
            "fake": 50,
            "aigc": "AI生成图像",
            "clarity": "低",
            "createtime": "2026-07-19 12:00:00",
        },
        {},
    )

    assert record["final_label"] == "需人工复核"
    assert record["fake_prob"] is None
    assert record["real_prob"] is None
    assert record["confidence"] == "不适用"
    assert record["review_required"] is True
    assert record["decision_status"] == "review_only"
    assert record["report_url"] == ""


def test_legacy_history_page_suppresses_review_score_and_escapes_filename(client, monkeypatch):
    _login_session(client)
    monkeypatch.setattr(
        historical_record,
        "excute_detection_sql",
        lambda *args, **kwargs: [{
            "itemid": 75,
            "filename": '<img src=x onerror="alert(1)">.jpg',
            "fake": 50,
            "aigc": "AI生成图像",
            "clarity": "低",
            "createtime": "2026-07-19 12:00:00",
        }],
    )
    monkeypatch.setattr(historical_record, "detection_record_is_publishable", lambda item: True)
    monkeypatch.setattr(historical_record.admin_state, "model_runs_by_itemids", lambda itemids: {})
    monkeypatch.setattr(
        historical_record.evidence_manifest,
        "_decision_authorization",
        lambda run, visible: {"status": "review_only"},
    )

    response = client.get("/history_photo")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert '"fake_prob": null' in html
    assert '"real_prob": null' in html
    assert '"decision_status": "review_only"' in html
    assert '<img src=x onerror="alert(1)">' not in html


def test_image_pdf_uses_persisted_specialized_label_without_refusion(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        detection,
        "_load_detection_record",
        lambda table, itemid: {
            "itemid": 73,
            "filename": "edited-camera.jpg",
            "fake": 94,
            "detector_probability": 0.2,
            "aigc": "疑似篡改图像",
            "clarity": "高",
            "createtime": "2026-07-19 12:00:00",
        },
    )
    monkeypatch.setattr(detection, "_metadata_for_item", lambda itemid: {"Software": "Photo Editor"})
    monkeypatch.setattr(detection, "_runtime_visible_watermark_for_item", lambda itemid: None)
    monkeypatch.setattr(
        detection,
        "_stored_decision_authorization_for_item",
        lambda itemid: {"status": "verdict", "authority": "calibrated_model"},
    )

    def fake_pdf(item, result):
        captured.update(result)
        return b"%PDF-test"

    monkeypatch.setattr(detection.reporting, "image_report_pdf", fake_pdf)

    response = client.get("/image_upload/report?itemid=73")

    assert response.status_code == 200
    assert captured["final_label"] == "疑似篡改图像"
    assert captured["probability"] == pytest.approx(0.94)
    assert captured["detector_probability"] == pytest.approx(0.2)


def test_image_history_preserves_specialized_swarm_label(client, monkeypatch):
    _login_session(client)
    monkeypatch.setattr(detection, "_load_detection_record", lambda *args, **kwargs: {
        "itemid": 77,
        "filename": "tampered.png",
        "fake": 94.0,
        "detector_probability": 0.2,
        "aigc": "疑似篡改图像",
        "clarity": "高",
        "explantation": "篡改风险独立命中。",
        "feedback": None,
    })
    monkeypatch.setattr(detection, "_metadata_for_item", lambda *_args: {})
    monkeypatch.setattr(detection, "_backend_static_url", lambda *_args: "/api/media/image/77")
    monkeypatch.setattr(
        detection,
        "_stored_decision_authorization_for_item",
        lambda itemid: {"status": "verdict", "authority": "calibrated_model"},
    )

    response = client.get("/image_upload/result?itemid=77")

    assert response.status_code == 200
    result = response.get_json()["result"]
    assert result["final_label"] == "疑似篡改图像"
    assert result["probability"] == pytest.approx(0.94)
    assert result["detector_probability"] == pytest.approx(0.2)


def test_explicit_review_only_authorization_cannot_be_overridden_by_calibrated_model(monkeypatch):
    monkeypatch.setattr(
        detection.admin_state,
        "model_runs_by_itemids",
        lambda _itemids: {
            "73": {
                "meta": {
                    "decisionAuthorization": {"status": "review_only", "authority": "none"},
                    "modelDecision": dict(CALIBRATED_MODEL_DECISION),
                }
            }
        },
    )
    monkeypatch.setattr(detection, "_runtime_visible_watermark_for_item", lambda _itemid: None)

    authorization = detection._stored_decision_authorization_for_item(73)

    assert authorization == {"status": "review_only", "authority": "none"}


def test_swarm_defers_network_experts_until_primary_finishes(monkeypatch):
    primary_finished = threading.Event()
    v2_started = threading.Event()
    ordering = []
    specs = [
        {'id': 'primary', 'name': '主检测', 'role': '主检测', 'provider': 'internal', 'weight': 0.7},
        {'id': 'metadata', 'name': '元数据', 'role': '元数据', 'provider': 'local', 'weight': 0.1},
        {'id': 'v2', 'name': '语义复核', 'role': '语义复核', 'provider': 'internal', 'weight': 0.2},
    ]
    monkeypatch.setattr(detection, '_swarm_specs', lambda include_disabled=False: specs)
    monkeypatch.setattr(detection, '_swarm_config', lambda: {'enabled': True, 'minExperts': 2})
    monkeypatch.setattr(detection, '_swarm_v2_stagger_seconds', lambda image_bytes: 60.0)
    monkeypatch.setattr(detection, '_persist_swarm_history_result', lambda *args, **kwargs: 123)

    primary_result = {
        'itemid': 123,
        'filename': 'parallel.png',
        'probability': 0.8,
        'detector_probability': 0.8,
        'final_label': 'AI生成图像',
        'confidence': '高',
        'all_metadata': {},
    }

    def fake_primary(*args, **kwargs):
        ordering.append(('primary_saw_v2', v2_started.is_set()))
        primary_finished.set()
        return primary_result, {
            'status': 'success', 'score': 0.8, 'verdict': 'AI生成图像',
            'confidence': '高', 'evidence': [], 'message': '完成', 'latencyMs': 1,
        }

    def fake_v2(*args, **kwargs):
        v2_started.set()
        ordering.append(('v2_saw_primary_finished', primary_finished.is_set()))
        return {
            'status': 'success', 'score': 0.75, 'verdict': '疑似伪造',
            'confidence': '高', 'evidence': [], 'message': '完成', 'latencyMs': 1,
        }

    monkeypatch.setattr(detection, '_swarm_primary_expert', fake_primary)
    monkeypatch.setattr(detection, '_swarm_v2_expert', fake_v2)

    payload, status = detection._run_swarm_detection_payload(
        b'image-bytes',
        'parallel.png',
        'image/png',
        {'Userid': 1, 'openid': 'parallel-test'},
    )

    assert status == 200
    assert payload['status'] == 'success'
    assert ordering == [
        ('primary_saw_v2', False),
        ('v2_saw_primary_finished', True),
    ]


def test_swarm_v2_stagger_scales_with_upload_size(monkeypatch):
    monkeypatch.setattr(detection, 'SWARM_V2_STAGGER_BYTES_PER_SECOND', 800_000)
    monkeypatch.setattr(detection, 'SWARM_V2_MAX_STAGGER_SECONDS', 8.0)

    assert detection._swarm_v2_stagger_seconds(b'x' * 80_000) == pytest.approx(0.1)
    assert detection._swarm_v2_stagger_seconds(b'x' * 4_800_000) == pytest.approx(6.0)
    assert detection._swarm_v2_stagger_seconds(b'x' * 20_000_000) == pytest.approx(8.0)


def test_swarm_reuses_primary_visible_precheck(monkeypatch):
    specs = [
        {'id': 'primary', 'name': '主检测', 'role': '主检测', 'provider': 'internal', 'weight': 0.8},
        {'id': 'metadata', 'name': '元数据', 'role': '元数据', 'provider': 'local', 'weight': 0.2},
        {
            'id': 'visible_watermark', 'name': '平台水印', 'role': '平台水印复核',
            'provider': 'hybrid', 'weight': 0.0,
        },
    ]
    monkeypatch.setattr(detection, '_swarm_specs', lambda include_disabled=False: specs)
    monkeypatch.setattr(detection, '_swarm_config', lambda: {'enabled': True, 'minExperts': 2})
    monkeypatch.setattr(detection, '_persist_swarm_history_result', lambda *args, **kwargs: 123)
    monkeypatch.setattr(
        detection.swarm_visible_watermark_expert,
        'run_visible_watermark_expert',
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('must not upload the image twice')),
    )
    monkeypatch.setattr(
        detection,
        '_swarm_primary_expert',
        lambda *args, **kwargs: (
            {
                'itemid': 123, 'filename': 'shared.png', 'probability': 0.8,
                'detector_probability': 0.8, 'final_label': 'AI生成图像',
                'confidence': '高', 'all_metadata': {},
            },
            {
                'status': 'success', 'score': 0.8, 'verdict': 'AI生成图像',
                'confidence': '高', 'evidence': [], 'message': '完成', 'latencyMs': 1,
                'remoteEvidence': {
                    'visibleWatermarkPrecheck': {
                        'status': 'ok',
                        'elapsedMs': 12,
                        'visibleHits': [],
                        'genericVisibleWatermark': {'available': True, 'elapsedMs': 12},
                    },
                },
            },
        ),
    )

    payload, status = detection._run_swarm_detection_payload(
        b'image-bytes',
        'shared.png',
        'image/png',
        {'Userid': 1, 'openid': 'shared-test'},
    )

    assert status == 200
    assert payload['status'] == 'success'
    visible = next(
        expert for expert in payload['result']['swarm']['experts']
        if expert.get('id') == 'visible_watermark'
    )
    assert visible['status'] == 'success'
    assert visible['message'].endswith('source=shared-upload')


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

    assert error == ""
    assert result["final_label"] == "需人工复核"
    assert result["decisionStatus"] == "review_only"


def test_swarm_aggregate_returns_review_state_for_uncalibrated_primary():
    experts = [
        {
            "id": "primary",
            "status": "success",
            "score": None,
            "weight": 1.0,
            "verdict": "需人工复核",
        },
        {
            "id": "metadata",
            "status": "success",
            "score": 0.5,
            "weight": 0.08,
            "verdict": "无明确来源信号",
        },
    ]
    primary = {
        "filename": "demo.png",
        "probability": 0.5,
        "detector_probability": 0.999,
        "modelDecisionReady": False,
        "reviewRequired": True,
    }

    result, error = detection._swarm_aggregate(experts, primary, {})

    assert error == ""
    assert result["final_label"] == "需人工复核"
    assert result["probability"] == 0.5
    assert result["reviewRequired"] is True
    assert result["swarm"]["enabled"] is True


def test_uncalibrated_secondary_models_cannot_publish_swarm_verdict():
    experts = [
        {"id": "primary", "status": "success", "score": None, "weight": 1.0},
        {"id": "v2", "status": "success", "score": 0.99, "weight": 0.6},
        {"id": "aliyun_pro", "status": "success", "score": 0.98, "weight": 0.4},
    ]
    primary = {
        "filename": "demo.png",
        "probability": 0.5,
        "detector_probability": 0.999,
        "modelDecisionReady": False,
        "reviewRequired": True,
        "decisionStatus": "review_only",
    }

    result, error = detection._swarm_aggregate(experts, primary, {})

    assert error == ""
    assert result["final_label"] == "需人工复核"
    assert result["probability"] == 0.5
    assert result["detector_probability"] == 0.5
    assert result["decisionStatus"] == "review_only"
    assert result["decisionAuthority"] == "none"


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

    payload, status_code = _run_fast_payload(client)

    assert status_code == 502
    assert "未启用 V2 兜底" in payload["message"]


def test_image_detect_uses_detector_backend_when_web_proxy_has_no_local_artifact(client, monkeypatch):
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
        lambda model: (_ for _ in ()).throw(
            AssertionError("the Web proxy must not require a local model artifact")
        ),
    )
    monkeypatch.setattr(detection, "_metadata_for_item", lambda itemid: {})
    monkeypatch.setattr(detection, "_ensure_local_primary_record", lambda *args, **kwargs: 45)

    def fake_backend_post(url, **kwargs):
        assert url == detection.IMAGE_DETECT_API
        assert kwargs["data"]["account_uuid"] == ACCOUNT_UUID
        return _FakeResponse({
            "code": 200,
            "data": {
                "data_itemid": 45,
                "fake_percentage": 12.0,
                "final_label": "真实图像",
                "confidence": "高",
                "filename": "demo.png",
                "file_size": "1KB",
                "img_format": "png",
                "resolution": "64x64",
            },
        })

    monkeypatch.setattr(detection, "_backend_post", fake_backend_post)

    payload, status_code = _run_fast_payload(client)

    assert status_code == 200
    assert payload["result"]["itemid"] == 45


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
    inserted = []

    def fake_insert(sql, params=None):
        inserted.append((sql, params))
        return 91

    monkeypatch.setattr(detector_backend, "excute_detection_sql_lastid", fake_insert)
    monkeypatch.setattr(
        detector_backend,
        "_consume_remote_inference_evidence",
        lambda: {"visibleWatermarkPrecheck": {"status": "ok", "visibleHits": []}},
    )
    monkeypatch.setattr(detector_backend, "DETECTOR_INTERNAL_TOKEN", "detector-test-token")
    app = detector_backend.create_app()
    app.config.update(TESTING=True)

    response = app.test_client().post(
        "/image",
        headers={"X-RealGuard-Detector-Token": "detector-test-token"},
        data={
            "image_file": (BytesIO(VALID_PNG_BYTES), "demo.png"),
            "openid": "openid-1",
            "phone": "13800000000",
            "account_uuid": ACCOUNT_UUID,
            "source_task_id": "job_0123456789abcdefabcd",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["code"] == 200
    assert payload["data"]["data_itemid"] == 91
    assert payload["data"]["fake_percentage"] == pytest.approx(50.0)
    assert payload["data"]["final_label"] == "需人工复核"
    assert payload["data"]["remote_evidence"]["modelDecision"]["ready"] is False
    assert payload["data"]["image_url"].endswith("/static/uploads/openid-1/image/stored-demo.png")
    assert payload["data"]["agent_reasoning"] == "native-v1"
    assert payload["data"]["remote_evidence"]["visibleWatermarkPrecheck"]["status"] == "ok"
    assert "owner_account_uuid" in inserted[0][0]
    assert inserted[0][1][-2] == ACCOUNT_UUID
    assert inserted[0][1][-1] == "job_0123456789abcdefabcd"


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
        "claim_detection_record_owner",
        lambda table, itemid, account_uuid, *args: account_uuid == ACCOUNT_UUID,
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
    assert payload["final_label"] == "需人工复核"
    assert payload["confidence"] == "不适用"
    assert payload["fake_percentage"] is None
    assert payload["decisionStatus"] == "review_only"
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
    monkeypatch.setattr(detection.reporting, "image_report_pdf", lambda item, result: b"%PDF-test")
    monkeypatch.setattr(
        detection,
        "_stored_decision_authorization_for_item",
        lambda itemid: {"status": "verdict", "authority": "calibrated_model"},
    )

    response = client.get("/image_upload/report?itemid=31")

    assert response.status_code == 200
    assert "attachment;" in response.headers["Content-Disposition"]
    assert response.mimetype == "application/pdf"
    assert ".pdf" in response.headers["Content-Disposition"]
    assert response.data.startswith(b"%PDF-")


def test_guest_history_returns_guest_image_records(client, monkeypatch):
    with client.session_transaction() as sess:
        sess["guest_openid"] = "guest-abc"

    def fake_detection_sql(sql, params=None, fetch=True):
        if sql == f"SELECT * FROM data WHERE {GUEST_OWNER_WHERE} ORDER BY {api.HISTORY_ORDER_BY}":
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
    monkeypatch.setattr(evidence_manifest, "validate_model_decision", lambda _decision: True)
    monkeypatch.setattr(evidence_manifest, "validate_inference_audit", lambda _audit, _decision: True)
    monkeypatch.setattr(api, "_has_detection_metadata", lambda itemid: itemid == 35)
    monkeypatch.setattr(
        api.admin_state,
        "model_runs_by_itemids",
        lambda itemids: {
            str(itemid): {
                "meta": {
                    "modelDecision": dict(CALIBRATED_MODEL_DECISION)
                }
            }
            for itemid in itemids
        },
    )

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
            assert params == ("41", ACCOUNT_UUID)
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
    assert response.mimetype == "application/pdf"
    assert ".pdf" in response.headers["Content-Disposition"]
    assert response.data.startswith(b"%PDF-")


def test_history_detection_records_include_report_urls(client, monkeypatch):
    _login_session(client)

    def fake_detection_sql(sql, params=None, fetch=True):
        if sql == IMAGE_HISTORY_QUERY:
            assert params == (ACCOUNT_UUID,)
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
            assert params == (ACCOUNT_UUID,)
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
    monkeypatch.setattr(evidence_manifest, "validate_model_decision", lambda _decision: True)
    monkeypatch.setattr(evidence_manifest, "validate_inference_audit", lambda _audit, _decision: True)
    monkeypatch.setattr(api, "_has_detection_metadata", lambda itemid: itemid == 51)
    monkeypatch.setattr(
        api.admin_state,
        "model_runs_by_itemids",
        lambda itemids: {
            str(itemid): {
                "meta": {
                    "modelDecision": dict(CALIBRATED_MODEL_DECISION)
                }
            }
            for itemid in itemids
        },
    )

    image_response = client.get("/api/history/image-detections")
    video_response = client.get("/api/history/video-detections")

    assert image_response.status_code == 200
    assert video_response.status_code == 200
    assert image_response.get_json()["records"][0]["report_url"] == "/image_upload/report?itemid=51"
    assert image_response.get_json()["records"][0]["has_metadata"] is True
    assert image_response.get_json()["records"][0]["has_visual_issues"] is True
    assert image_response.get_json()["records"][0]["visual_issue_count"] == 1
    assert video_response.get_json()["records"][0]["report_url"] == "/video_upload/report?itemid=61"


def test_history_uses_immutable_uuid_when_detection_userid_differs(client, monkeypatch):
    _login_session(client)

    def fake_detection_sql(sql, params=None, fetch=True):
        if sql == IMAGE_HISTORY_QUERY:
            assert params == (ACCOUNT_UUID,)
            return [{
                "itemid": 71,
                "Userid": 999,
                "filename": "detection-db-owner.png",
                "fake": 63.0,
                "clarity": "中",
                "openid": "openid-1",
                "phone": "13800000000",
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
    video_response = client.get("/api/history/video-detections?filter=review&query=人工复核&limit=1")

    assert image_response.status_code == 200
    image_payload = image_response.get_json()
    assert image_payload["total"] == 1
    assert len(image_payload["records"]) == 1
    assert image_payload["records"][0]["itemid"] == 101

    assert video_response.status_code == 200
    video_payload = video_response.get_json()
    assert video_payload["total"] == 2
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
