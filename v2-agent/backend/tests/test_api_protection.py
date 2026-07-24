from pathlib import Path
import binascii
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib
import io
import json
import sys
import struct
import time
import uuid
import zlib
import zipfile
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", binascii.crc32(kind + payload) & 0xFFFFFFFF)


def _png_with_itxt(keyword: str, text: str) -> bytes:
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    itxt = keyword.encode("utf-8") + b"\x00\x00\x00\x00\x00" + text.encode("utf-8")
    idat = zlib.compress(b"\x00\xff\xff\xff")
    return signature + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"iTXt", itxt) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")


def _vlm_analysis(
    *,
    file_type: str = "document",
    verdict: str = "real",
    source: str = "vlm",
    model_version: str = "pytest-vlm",
) -> dict:
    dimension_key = "aigc_image" if file_type == "image" else "aigc_text"
    return {
        "verdict": verdict,
        "confidence": 0.22,
        "dimensions": [
            {
                "key": dimension_key,
                "label": "真实模型检测",
                "score": 0.22,
                "result": "未见明显异常",
            }
        ],
        "regions": [],
        "explanation": "pytest 稳定真实模型结论",
        "modelVersion": model_version,
        "source": source,
        "synthid": {"detected": False},
        "visibleWatermark": {"detected": False, "provider": None},
    }


def _stable_vlm_analyze(file_type: str, _filename: str, _data: bytes) -> dict:
    return _vlm_analysis(file_type=file_type)


class ConsentTestClient(TestClient):
    def post(self, url, *args, **kwargs):
        if url == "/api/detect":
            data = dict(kwargs.pop("data", {}) or {})
            data.setdefault("upload_consent", "1")
            data.setdefault("consent_version", "2026-07-15+2026-07-20")
            data.setdefault(
                "terms_sha256",
                "09707ba3b915db9904cc6f8b4951b5c9bbfff7e768fd237c04eedf90fef89ff3",
            )
            data.setdefault(
                "privacy_sha256",
                "5c505aaf82abe1af5cac83fef81c60ec66e89a76377110fba6348ed0567d8935",
            )
            headers = dict(kwargs.pop("headers", {}) or {})
            headers.setdefault("Idempotency-Key", str(uuid.uuid4()))
            kwargs["data"] = data
            kwargs["headers"] = headers
        return super().post(url, *args, **kwargs)


def _forensic_analysis() -> dict:
    return {
        "verdict": "real",
        "confidence": 0.41,
        "summary": "pytest 取证判读",
        "items": [],
        "jpegPoints": [],
        "modelVersion": "pytest-forensics",
        "source": "vlm",
        "tokenUsage": {"promptTokens": 12, "completionTokens": 5, "totalTokens": 17},
    }


def test_public_detect_requires_server_verified_upload_consent(client):
    raw_client = TestClient(client.app, client=("127.0.0.1", 50001))

    response = raw_client.post(
        "/api/detect",
        files={"file": ("sample.txt", b"consent required", "text/plain")},
        headers={"Idempotency-Key": "missing-consent-001"},
    )

    assert response.status_code == 428
    assert "隐私政策" in response.json()["detail"]


def test_production_session_writes_require_csrf_and_same_origin(client, monkeypatch):
    import app.main as main  # noqa: WPS433

    monkeypatch.setattr(main, "RUNTIME_ENV", "production")

    def fake_session_user(request):
        if "session=csrf-user" in request.headers.get("cookie", ""):
            return {
                "mode": "session",
                "userId": 900,
                "accountUuid": "99999999-9999-4999-8999-999999999999",
            }
        return None

    monkeypatch.setattr(main, "_verify_session_user_sync", fake_session_user)
    client.cookies.set("session", "csrf-user")
    missing = client.post(
        "/api/detect",
        files={"file": ("csrf.txt", b"csrf", "text/plain")},
    )
    assert missing.status_code == 403
    assert "CSRF" in missing.json()["detail"]

    token = "csrf-token-for-production-test-1234567890"
    client.cookies.set(main.SESSION_CSRF_COOKIE, token)
    cross_site = client.post(
        "/api/detect",
        headers={
            "X-Huijian-CSRF": token,
            "Origin": "https://evil.example",
            "Sec-Fetch-Site": "cross-site",
        },
        files={"file": ("csrf.txt", b"csrf", "text/plain")},
    )
    assert cross_site.status_code == 403

    same_origin = client.post(
        "/api/detect",
        headers={
            "X-Huijian-CSRF": token,
            "Origin": "https://www.rrreal.cn",
            "Sec-Fetch-Site": "same-origin",
        },
        files={"file": ("csrf.txt", b"csrf", "text/plain")},
    )
    assert same_origin.status_code == 200


def test_public_detect_persists_pseudonymous_upload_consent(client):
    from app import storage

    response = client.post(
        "/api/detect",
        files={"file": ("sample.txt", b"consent recorded", "text/plain")},
        headers={"Idempotency-Key": "record-consent-001"},
    )

    assert response.status_code == 200
    with storage._connect() as conn:
        row = conn.execute(
            "SELECT subject_hash, upload_sha256, channel FROM guest_upload_consents"
        ).fetchone()
    assert len(row["subject_hash"]) == 64
    assert len(row["upload_sha256"]) == 64
    assert row["channel"] == "v2_public_detect"


def test_forensic_normalizer_hides_natural_language_verdicts():
    from app import detector

    normalized = detector.normalize_forensic_evidence({
        "items": [{
            "key": "spectrum",
            "status": "danger",
            "finding": "综合判定该图为AI生成，置信度95%",
        }],
    })

    assert normalized["decisionStatus"] == "review_only"
    assert normalized["items"][0]["status"] == "warn"
    assert "AI生成" not in normalized["items"][0]["finding"]
    assert "95" not in normalized["items"][0]["finding"]

    bypass = detector.normalize_forensic_evidence({
        "items": [{"key": "spectrum", "status": "danger", "finding": "画面源自扩散模型，机器合成特征明确"}],
    })
    assert "扩散模型" not in bypass["items"][0]["finding"]
    assert "机器合成" not in bypass["items"][0]["finding"]


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("JIANZHEN_ENV", "test")
    monkeypatch.setenv("JIANZHEN_ACCESS_TOKEN", "internal-token")
    monkeypatch.setenv("JIANZHEN_ADMIN_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("JIANZHEN_REPORT_SHARE_SECRET", "independent-report-share-secret-32")
    monkeypatch.setenv("JIANZHEN_ALLOW_LEGACY_REPORT_SHARES", "true")
    monkeypatch.setenv("JIANZHEN_CONSENT_AUDIT_SALT", "independent-consent-audit-secret-32")
    monkeypatch.setenv("JIANZHEN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-dashscope-key")
    monkeypatch.setenv("JIANZHEN_ALLOW_ANONYMOUS_DETECT", "true")
    for module_name in ("app.storage", "app.main", "storage", "main"):
        sys.modules.pop(module_name, None)
    import app.storage as storage  # noqa: WPS433
    importlib.reload(storage)
    import app.main as main  # noqa: WPS433

    importlib.reload(main)
    monkeypatch.setattr(main.detector, "API_KEY", "test-dashscope-key")
    monkeypatch.setattr(main.detector, "analyze", _stable_vlm_analyze)
    monkeypatch.setattr(main, "_session_auth_reachable", lambda: True)
    return ConsentTestClient(main.app, client=("127.0.0.1", 50000))


@pytest.fixture
def developer_key_client(monkeypatch, tmp_path):
    monkeypatch.setenv("JIANZHEN_ALLOW_LEGACY_REPORT_SHARES", "true")
    monkeypatch.setenv("JIANZHEN_ACCESS_TOKEN", "internal-token")
    monkeypatch.setenv("JIANZHEN_ADMIN_ACCESS_TOKEN", "admin-token")
    monkeypatch.setenv("JIANZHEN_REPORT_SHARE_SECRET", "independent-report-share-secret-32")
    monkeypatch.setenv("JIANZHEN_CONSENT_AUDIT_SALT", "independent-consent-audit-secret-32")
    monkeypatch.setenv("JIANZHEN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("JIANZHEN_ALLOW_DIRECT_DEVELOPER_KEYS", "true")
    monkeypatch.setenv("JIANZHEN_ENV", "test")
    monkeypatch.setenv("JIANZHEN_DEVELOPER_AUTH_URL", "http://realguard-v1.internal/api/developer/keys/verify")
    monkeypatch.setenv("REALGUARD_DEVELOPER_AUTH_SECRET", "internal-secret")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-dashscope-key")
    for module_name in ("app.storage", "app.main", "storage", "main"):
        sys.modules.pop(module_name, None)
    import app.storage as storage  # noqa: WPS433
    importlib.reload(storage)
    import app.main as main  # noqa: WPS433
    importlib.reload(main)
    monkeypatch.setattr(main.detector, "API_KEY", "test-dashscope-key")
    monkeypatch.setattr(main.detector, "analyze", _stable_vlm_analyze)

    def fake_verify(api_key, request):
        if api_key == "rg_sk_fast_only":
            return {
                "mode": "developer",
                "keyId": 303,
                "userId": 1,
                "accountUuid": "11111111-1111-4111-8111-111111111111",
                "scopes": ["image:fast"],
            }
        if api_key == "rg_sk_user1":
            return {
                "mode": "developer",
                "keyId": 101,
                "userId": 1,
                "accountUuid": "11111111-1111-4111-8111-111111111111",
                "scopes": ["detect", "reports"],
            }
        if api_key == "rg_sk_user2":
            return {
                "mode": "developer",
                "keyId": 202,
                "userId": 2,
                "accountUuid": "22222222-2222-4222-8222-222222222222",
                "scopes": ["detect", "reports"],
            }
        raise main.HTTPException(status_code=401, detail="API Key 缺失或无效")

    monkeypatch.setattr(main, "_verify_developer_key_sync", fake_verify)
    return TestClient(main.app, client=("127.0.0.1", 50000))


def test_fast_only_developer_key_cannot_read_history_or_reports(developer_key_client):
    detection = developer_key_client.post(
        "/api/detect",
        headers={"X-RealGuard-Key": "rg_sk_user1"},
        files={"file": ("sample.txt", b"scope isolation", "text/plain")},
    )
    assert detection.status_code == 200
    report_id = detection.json()["reportId"]
    headers = {"X-RealGuard-Key": "rg_sk_fast_only"}

    assert developer_key_client.get("/api/history", headers=headers).status_code == 403
    assert developer_key_client.get(f"/api/report/{report_id}", headers=headers).status_code == 403
    assert developer_key_client.get(f"/api/report/{report_id}/download", headers=headers).status_code == 403
    assert developer_key_client.post(f"/api/report/{report_id}/share", headers=headers).status_code == 403


def test_metrics_requires_token(client):
    unauth = client.get("/api/metrics")
    auth = client.get("/api/metrics", headers={"X-Jianzhen-Token": "test-token"})

    assert unauth.status_code == 401
    assert auth.status_code == 200


def test_static_admin_token_is_rejected_on_proxied_public_requests(client):
    headers = {
        "X-Jianzhen-Token": "test-token",
        "X-Forwarded-For": "203.0.113.10",
        "X-Real-IP": "203.0.113.10",
        "X-Forwarded-Proto": "https",
    }

    metrics = client.get("/api/metrics", headers=headers)
    history = client.get("/api/history", headers=headers)

    assert metrics.status_code == 403
    assert history.status_code == 401


def test_request_metric_storage_failure_does_not_replace_business_response(client, monkeypatch):
    import app.main as main  # noqa: WPS433

    monkeypatch.setattr(main.storage, "record_event", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")))

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readiness_requires_real_image_model_and_storage(client, monkeypatch):
    import app.main as main  # noqa: WPS433

    ready = client.get("/api/ready")
    monkeypatch.setattr(main.detector, "API_KEY", "")
    unavailable = client.get("/api/ready")

    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert ready.json()["checks"]["evidenceSigningConfigured"] is True
    assert ready.json()["evidenceSigning"]["algorithm"] == "Ed25519"
    assert ready.headers["cache-control"] == "no-store"
    assert unavailable.status_code == 503
    assert unavailable.json()["checks"]["imageModelConfigured"] is False

    monkeypatch.setattr(main.detector, "API_KEY", "test-dashscope-key")
    monkeypatch.setattr(main, "_session_auth_reachable", lambda: False)
    auth_unavailable = client.get("/api/ready")
    assert auth_unavailable.status_code == 503
    assert auth_unavailable.json()["checks"]["sessionAuthReachable"] is False


def test_readiness_fails_closed_without_evidence_signing_key(client, monkeypatch):
    monkeypatch.delenv("JIANZHEN_EVIDENCE_SIGNING_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("JIANZHEN_EVIDENCE_SIGNING_PRIVATE_KEY_FILE", raising=False)

    response = client.get("/api/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["checks"]["evidenceSigningConfigured"] is False
    assert response.json()["evidenceSigning"]["error"] == "evidence_signing_configuration_invalid"


def test_forensics_cache_is_scoped_by_user_and_does_not_leak_filename(developer_key_client, monkeypatch):
    import app.main as main  # noqa: WPS433

    calls = 0

    def fake_explainable(_data):
        nonlocal calls
        calls += 1
        return _forensic_analysis()

    monkeypatch.setattr(main.detector, "image_dimensions", lambda _data: (10, 10))
    monkeypatch.setattr(main.detector, "explainable", fake_explainable)
    monkeypatch.setattr(main.detector, "attach_forensic_images", lambda _data, report: dict(report))
    first = developer_key_client.post(
        "/api/forensics",
        headers={"X-RealGuard-Key": "rg_sk_user1"},
        files={"file": ("first-owner.png", b"same-image", "image/png")},
    )
    other_user = developer_key_client.post(
        "/api/forensics",
        headers={"X-RealGuard-Key": "rg_sk_user2"},
        files={"file": ("second-owner.png", b"same-image", "image/png")},
    )
    same_user = developer_key_client.post(
        "/api/forensics",
        headers={"X-RealGuard-Key": "rg_sk_user1"},
        files={"file": ("renamed-by-owner.png", b"same-image", "image/png")},
    )
    monkeypatch.setattr(main.detector, "VLM_MODEL", "pytest-forensics-next")
    new_model = developer_key_client.post(
        "/api/forensics",
        headers={"X-RealGuard-Key": "rg_sk_user1"},
        files={"file": ("new-model.png", b"same-image", "image/png")},
    )

    assert first.status_code == 200
    assert other_user.status_code == 200
    assert same_user.status_code == 200
    assert new_model.status_code == 200
    assert calls == 3
    for response in (first, other_user, same_user, new_model):
        assert response.json()["decisionStatus"] == "review_only"
        assert response.json()["decisionAuthority"] == "evidence_only"
        assert response.json()["reviewRequired"] is True
        assert "verdict" not in response.json()
        assert "confidence" not in response.json()
    assert "cacheHit" not in first.json()
    assert "cacheHit" not in other_user.json()
    assert "cacheHit" not in same_user.json()
    assert same_user.json()["fileMeta"]["name"] == "renamed-by-owner.png"
    assert same_user.json()["tokenUsage"] == {"promptTokens": 0, "completionTokens": 0, "totalTokens": 0}


def test_detection_cache_is_scoped_by_account(developer_key_client, monkeypatch):
    import app.main as main  # noqa: WPS433

    calls = 0

    def analyze(*args, **kwargs):
        nonlocal calls
        calls += 1
        return _stable_vlm_analyze(*args, **kwargs)

    monkeypatch.setattr(main.detector, "analyze", analyze)
    first = developer_key_client.post(
        "/api/detect",
        headers={"X-RealGuard-Key": "rg_sk_user1"},
        files={"file": ("first.txt", b"same-detection-bytes", "text/plain")},
    )
    other = developer_key_client.post(
        "/api/detect",
        headers={"X-RealGuard-Key": "rg_sk_user2"},
        files={"file": ("other.txt", b"same-detection-bytes", "text/plain")},
    )
    repeated = developer_key_client.post(
        "/api/detect",
        headers={"X-RealGuard-Key": "rg_sk_user1"},
        files={"file": ("renamed.txt", b"same-detection-bytes", "text/plain")},
    )

    assert first.status_code == 200
    assert other.status_code == 200
    assert repeated.status_code == 200
    assert first.json()["cacheHit"] is False
    assert other.json()["cacheHit"] is False
    assert repeated.json()["cacheHit"] is True
    assert calls == 2


def test_image_endpoints_reject_excessive_pixel_count(client, monkeypatch):
    import app.main as main  # noqa: WPS433

    monkeypatch.setattr(main.detector, "image_dimensions", lambda _data: (6000, 5000))
    monkeypatch.setattr(main.detector, "explainable", lambda _data: pytest.fail("oversized image reached forensics"))
    monkeypatch.setattr(main.detector, "analyze", lambda *_args: pytest.fail("oversized image reached detector"))

    forensics = client.post("/api/forensics", files={"file": ("oversized.png", b"header-only", "image/png")})
    detect = client.post("/api/detect", files={"file": ("oversized.png", b"header-only", "image/png")})

    assert forensics.status_code == 413
    assert detect.status_code == 413


def test_analysis_cache_can_enforce_max_age(client):
    import app.storage as storage  # noqa: WPS433

    cache_type = "image-forensics:ttl-test"
    storage.put_cached_analysis(cache_type, "ttl-sha", _forensic_analysis())

    assert storage.get_cached_analysis(cache_type, "ttl-sha", max_age_seconds=60) is not None
    assert storage.get_cached_analysis(cache_type, "ttl-sha", max_age_seconds=0) is None


def test_v1_session_unlocks_own_v2_history_but_not_admin_metrics(client, monkeypatch):
    import app.main as main  # noqa: WPS433

    def fake_session_user(request):
        if "session=valid" in request.headers.get("cookie", ""):
            return {
                "mode": "session",
                "userId": 7,
                "accountUuid": "77777777-7777-4777-8777-777777777777",
                "phone": "13800000000",
            }
        return None

    monkeypatch.setattr(main, "_verify_session_user_sync", fake_session_user)

    unauth = client.get("/api/history")
    client.cookies.set("session", "valid")
    auth = client.get("/api/history")
    metrics = client.get("/api/metrics")

    assert unauth.status_code == 401
    assert auth.status_code == 200
    assert metrics.status_code == 403


def test_session_history_is_strictly_isolated_by_user(client, monkeypatch):
    import app.main as main  # noqa: WPS433

    def fake_session_user(request):
        cookie = request.headers.get("cookie", "")
        if "session=user-a" in cookie:
            return {
                "mode": "session",
                "userId": 101,
                "accountUuid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "phone": "13800000101",
            }
        if "session=user-b" in cookie:
            return {
                "mode": "session",
                "userId": 101,
                "accountUuid": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                "phone": "13800000202",
            }
        return None

    monkeypatch.setattr(main, "_verify_session_user_sync", fake_session_user)

    client.cookies.set("session", "user-a")
    first = client.post(
        "/api/detect",
        files={"file": ("owner-a.txt", b"owner-a-private-content", "text/plain")},
    )
    first_payload = first.json()

    client.cookies.set("session", "user-b")
    second = client.post(
        "/api/detect",
        files={"file": ("owner-b.txt", b"owner-b-private-content", "text/plain")},
    )
    second_payload = second.json()
    listing = client.get("/api/history")
    foreign_detail = client.get(f"/api/history/{first_payload['taskId']}")
    foreign_artifacts = client.post(
        f"/api/history/{first_payload['taskId']}/artifacts",
        json={"forensics": {"summary": "tampered"}},
    )
    foreign_delete = client.delete(f"/api/history/{first_payload['taskId']}")
    foreign_report = client.get(f"/api/report/{first_payload['reportId']}/download")
    foreign_share = client.post(f"/api/report/{first_payload['reportId']}/share", json={})

    assert first.status_code == 200
    assert second.status_code == 200
    assert listing.status_code == 200
    assert [item["taskId"] for item in listing.json()["items"]] == [second_payload["taskId"]]
    assert foreign_detail.status_code == 404
    assert foreign_artifacts.status_code == 404
    assert foreign_delete.status_code == 404
    assert foreign_report.status_code == 404
    assert foreign_share.status_code == 404

    client.cookies.set("session", "user-a")
    owner_listing = client.get("/api/history")
    owner_detail = client.get(f"/api/history/{first_payload['taskId']}")
    other_detail = client.get(f"/api/history/{second_payload['taskId']}")
    owner_delete = client.delete(f"/api/history/{first_payload['taskId']}")

    assert [item["taskId"] for item in owner_listing.json()["items"]] == [first_payload["taskId"]]
    assert owner_detail.status_code == 200
    assert other_detail.status_code == 404
    assert owner_delete.status_code == 200


def test_unowned_guest_history_is_not_claimed_by_logged_in_user(client, monkeypatch):
    import app.main as main  # noqa: WPS433

    guest = client.post(
        "/api/detect",
        files={"file": ("guest.txt", b"guest-private-content", "text/plain")},
    )
    guest_payload = guest.json()

    monkeypatch.setattr(
        main,
        "_verify_session_user_sync",
        lambda request: {
            "mode": "session",
            "userId": 303,
            "accountUuid": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            "phone": "13800000303",
        },
    )
    client.cookies.set("session", "logged-in")
    listing = client.get("/api/history")
    detail = client.get(f"/api/history/{guest_payload['taskId']}")

    assert guest.status_code == 200
    assert listing.status_code == 200
    assert listing.json()["items"] == []
    assert detail.status_code == 404


def test_health_exposes_access_protection(client):
    response = client.get("/api/health")
    payload = response.json()

    assert response.status_code == 200
    assert payload["accessProtectionEnabled"] is True
    assert payload["unifiedLoginEnabled"] is True
    assert payload["sessionAuthEnabled"] is True
    assert payload["capabilities"]["image"] == "available"
    assert payload["capabilities"]["document"] == "limited"
    assert payload["capabilities"]["video"] == "unavailable"
    assert payload["capabilities"]["audio"] == "unavailable"
    assert "model" not in payload
    assert "version" not in payload
    assert "calibration" not in payload
    assert "storage" not in payload
    assert "repoPath" not in payload["synthid"]
    assert "developerKeyAuthEnabled" not in payload
    assert "developerKeyAuthConfigured" not in payload
    assert "analysisCacheVersion" not in payload
    assert "researchInterfaceVersion" not in payload
    assert "protectedEndpoints" not in payload
    assert "developerProtectedEndpoints" not in payload


def test_health_and_detect_are_unavailable_without_model_key(client, monkeypatch):
    import app.main as main  # noqa: WPS433

    monkeypatch.setattr(main.detector, "API_KEY", "")

    health = client.get("/api/health")
    detect = client.post(
        "/api/detect",
        files={"file": ("sample.txt", b"no model key", "text/plain")},
    )

    assert health.status_code == 200
    assert health.json()["capabilities"]["image"] == "unavailable"
    assert health.json()["capabilities"]["document"] == "unavailable"
    assert health.json()["capabilities"]["video"] == "unavailable"
    assert health.json()["capabilities"]["audio"] == "unavailable"
    assert detect.status_code == 503
    assert "真实模型服务未配置" in detect.json()["detail"]


@pytest.mark.parametrize(
    ("filename", "media_type"),
    [
        ("sample.mp4", "video/mp4"),
        ("sample.wav", "audio/wav"),
    ],
)
def test_detect_rejects_unsupported_media_without_model_call(client, monkeypatch, filename, media_type):
    import app.main as main  # noqa: WPS433

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("unsupported media must not call the model")

    monkeypatch.setattr(main.detector, "analyze", fail_if_called)
    response = client.post(
        "/api/detect",
        files={"file": (filename, b"unsupported-media", media_type)},
    )

    assert response.status_code == 422
    assert "尚未部署" in response.json()["detail"]
    assert "不会生成模拟结论" in response.json()["detail"]


def test_detect_rejects_declared_type_mismatch_and_unknown_extension(client, monkeypatch):
    import app.main as main  # noqa: WPS433

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("invalid upload must not call the model")

    monkeypatch.setattr(main.detector, "analyze", fail_if_called)
    mismatch = client.post(
        "/api/detect",
        files={"file": ("sample.mp4", b"video", "video/mp4")},
        data={"fileType": "image"},
    )
    unknown = client.post(
        "/api/detect",
        files={"file": ("sample.bin", b"binary", "application/octet-stream")},
        data={"fileType": "image"},
    )

    assert mismatch.status_code == 422
    assert "不一致" in mismatch.json()["detail"]
    assert unknown.status_code == 415
    assert "不支持" in unknown.json()["detail"]


@pytest.mark.parametrize(
    "analysis",
    [
        _vlm_analysis(source="mock"),
        _vlm_analysis(source="maps-only"),
        _vlm_analysis(source="unknown"),
    ],
    ids=["mock-source", "maps-only-source", "unknown-source"],
)
def test_detect_rejects_non_publishable_analysis(client, monkeypatch, analysis):
    import app.main as main  # noqa: WPS433

    monkeypatch.setattr(main.detector, "analyze", lambda *_args, **_kwargs: analysis)
    response = client.post(
        "/api/detect",
        files={"file": ("contract.txt", json.dumps(analysis).encode(), "text/plain")},
    )
    history = client.get("/api/history", headers={"X-Jianzhen-Token": "test-token"})

    assert response.status_code == 503
    assert response.json()["detail"] == "真实模型未返回可信的分析来源"
    assert history.status_code == 200
    assert history.json()["total"] == 0


def test_analysis_cache_only_accepts_explicit_decision_contracts(client):
    import app.storage as storage  # noqa: WPS433

    invalid_cases = [
        _vlm_analysis(source="mock"),
        _vlm_analysis(source="maps-only"),
        _vlm_analysis(source="unknown"),
        _vlm_analysis(verdict="unknown"),
    ]
    for index, analysis in enumerate(invalid_cases):
        sha256 = f"invalid-{index}"
        storage.put_cached_analysis("document", sha256, analysis)
        assert storage.get_cached_analysis("document", sha256) is None

    review_only = _vlm_analysis(verdict="unknown")
    review_only.update({
        "confidence": 0.0,
        "decisionStatus": "review_only",
        "decisionAuthority": "none",
        "reviewRequired": True,
    })
    storage.put_cached_analysis("document", "review-only", review_only)

    assert storage.get_cached_analysis("document", "review-only") == review_only


@pytest.mark.parametrize("raw_verdict", ["real", "unknown", ""])
def test_uncalibrated_vlm_is_published_only_as_review_required(client, monkeypatch, raw_verdict):
    import app.main as main  # noqa: WPS433

    monkeypatch.setattr(
        main.detector,
        "analyze",
        lambda *_args, **_kwargs: _vlm_analysis(verdict=raw_verdict),
    )

    response = client.post(
        "/api/detect",
        files={"file": (f"review-{raw_verdict or 'empty'}.txt", b"review payload", "text/plain")},
    )

    assert response.status_code == 200
    assert response.json()["verdict"] == "real"
    assert response.json()["decisionStatus"] == "review_only"
    assert response.json()["decisionAuthority"] == "none"
    assert response.json()["reviewRequired"] is True
    assert response.json()["aiProbability"] is None
    assert response.json()["dimensions"] == []
    assert response.json()["regions"] == []


def test_vlm_payload_cannot_self_authorize_a_verdict(client, monkeypatch):
    import app.main as main  # noqa: WPS433

    forged = _vlm_analysis(verdict="highly_suspected_fake")
    forged.update({
        "decisionStatus": "verdict",
        "decisionAuthority": "decisive_provenance",
        "reviewRequired": False,
    })
    monkeypatch.setattr(main.detector, "analyze", lambda *_args, **_kwargs: forged)

    response = client.post(
        "/api/detect",
        files={"file": ("forged.txt", b"forged model authority", "text/plain")},
    )

    assert response.status_code == 200
    assert response.json()["verdict"] == "highly_suspected_fake"
    assert response.json()["decisionStatus"] == "review_only"
    assert response.json()["decisionAuthority"] == "none"


def test_legacy_history_without_decision_authorization_fails_closed(client):
    import app.storage as storage  # noqa: WPS433

    detected = client.post(
        "/api/detect",
        files={"file": ("legacy.txt", b"legacy result", "text/plain")},
    ).json()
    legacy = dict(detected)
    legacy["taskId"] = "legacy-unsealed-task"
    legacy["reportId"] = "RJ-RPT-LEGACY-UNSEALED"
    legacy.update({"verdict": "real", "confidence": 0.97})
    for key in ("decisionStatus", "decisionAuthority", "reviewRequired"):
        legacy.pop(key, None)
    with storage._connect() as conn:
        conn.execute(
            """
            INSERT INTO history
                (task_id, report_id, created_at, sha256, file_type, file_name,
                 file_size, resolution, result_json, thumbnail)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                legacy["taskId"],
                legacy["reportId"],
                legacy["createdAt"],
                "e" * 64,
                "document",
                "legacy.txt",
                64,
                None,
                json.dumps(legacy, ensure_ascii=False),
                None,
            ),
        )
        conn.commit()

    listing = client.get("/api/history", headers={"X-Jianzhen-Token": "test-token"})
    detail = client.get(
        f"/api/history/{legacy['taskId']}",
        headers={"X-Jianzhen-Token": "test-token"},
    )

    item = next(entry for entry in listing.json()["items"] if entry["taskId"] == legacy["taskId"])
    assert item["verdict"] == "real"
    assert item["reviewRequired"] is True
    assert detail.json()["verdict"] == "real"
    assert detail.json()["decisionStatus"] == "review_only"


def test_admin_health_requires_token_and_exposes_diagnostics(client):
    unauth = client.get("/api/admin/health")
    auth = client.get("/api/admin/health", headers={"X-Jianzhen-Token": "test-token"})
    payload = auth.json()

    assert unauth.status_code == 401
    assert auth.status_code == 200
    assert payload["model"] == "qwen3-vl-flash"
    assert "calibration" in payload
    assert "storage" in payload
    assert "repoPath" in payload["synthid"]


def test_internal_detection_token_has_no_admin_or_history_access(client):
    headers = {"X-Jianzhen-Token": "internal-token"}

    health = client.get("/api/admin/health", headers=headers)
    history = client.get("/api/history", headers=headers)
    metrics = client.get("/api/metrics", headers=headers)

    assert health.status_code == 403
    assert history.status_code == 401
    assert metrics.status_code == 403


def test_internal_detection_token_still_allows_model_calls(developer_key_client):
    response = developer_key_client.post(
        "/api/detect",
        headers={"X-Jianzhen-Token": "internal-token"},
        files={"file": ("internal.txt", b"internal model request", "text/plain")},
        data={"fileType": "document"},
    )

    assert response.status_code == 200


def test_history_delete_removes_content_unlinks_usage_and_anonymous_cache_is_disabled(client):
    import app.storage as storage  # noqa: WPS433

    first = client.post(
        "/api/detect",
        files={"file": ("first.txt", b"same private content", "text/plain")},
    ).json()
    second = client.post(
        "/api/detect",
        files={"file": ("second.txt", b"same private content", "text/plain")},
    ).json()
    storage.put_history_artifacts(first["taskId"], forensics={"summary": "private evidence"})
    storage.record_token_usage(
        actor={"userId": 7, "keyId": 9},
        endpoint="/api/history-delete-privacy-test",
        file_type="document",
        result=first,
    )

    deleted_first = client.delete(
        f"/api/history/{first['taskId']}",
        headers={"X-Jianzhen-Token": "test-token"},
    )

    assert deleted_first.status_code == 200
    assert storage.get_history(first["taskId"]) is None
    with storage._connect() as conn:
        assert conn.execute(
            "SELECT 1 FROM history_artifacts WHERE task_id = ?",
            (first["taskId"],),
        ).fetchone() is None
        usage = conn.execute(
            """
            SELECT developer_user_id, developer_key_id, task_id, report_id
            FROM token_usage_events
            WHERE endpoint = '/api/history-delete-privacy-test'
            """,
        ).fetchone()
        assert dict(usage) == {
            "developer_user_id": None,
            "developer_key_id": None,
            "task_id": None,
            "report_id": None,
        }
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM analysis_cache WHERE sha256 = ?",
            (first["fileMeta"]["sha256"],),
        ).fetchone()["n"] == 0

    deleted_second = client.delete(
        f"/api/history/{second['taskId']}",
        headers={"X-Jianzhen-Token": "test-token"},
    )
    assert deleted_second.status_code == 200
    with storage._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM analysis_cache WHERE sha256 = ?",
            (second["fileMeta"]["sha256"],),
        ).fetchone()["n"] == 0


def test_developer_key_required_for_detect_when_enabled(developer_key_client):
    health = developer_key_client.get("/api/health")
    missing = developer_key_client.post(
        "/api/detect",
        files={"file": ("sample.txt", b"hello world from pytest", "text/plain")},
    )
    invalid = developer_key_client.post(
        "/api/detect",
        headers={"X-RealGuard-Key": "rg_sk_bad"},
        files={"file": ("sample.txt", b"hello world from pytest", "text/plain")},
    )
    valid = developer_key_client.post(
        "/api/detect",
        headers={"X-RealGuard-Key": "rg_sk_user1"},
        files={"file": ("sample.txt", b"hello world from pytest", "text/plain")},
    )

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert valid.status_code == 200
    assert "developerKeyAuthEnabled" not in health.json()
    assert "developerKeyAuthConfigured" not in health.json()
    assert "developerProtectedEndpoints" not in health.json()
    assert valid.json()["reportId"].startswith("RJ-RPT-")
    assert valid.json()["aiProbability"] is None
    assert valid.json()["riskScore"] is None
    assert valid.json()["riskVector"]["aiGenerated"] is None
    assert valid.json()["decisionStatus"] == "review_only"
    unified = valid.json()["unifiedForensics"]
    assert unified["interface_version"] == "aigc-forensics-unified-v0.1"
    assert unified["verdict"] == valid.json()["verdict"]
    assert unified["confidence"] == valid.json()["confidence"]
    assert "generator_attribution" in unified
    assert "open_set_score" in unified
    assert "evidence_regions" in unified
    assert "temporal_segments" in unified
    assert "provenance_signals" in unified
    assert unified["compute_cost"]["cache_hit"] is False


@pytest.mark.parametrize(
    ("risk", "expected_uncertainty"),
    [(0.05, 0.1), (0.5, 1.0), (0.95, 0.1)],
)
def test_unified_forensics_uncertainty_tracks_distance_from_boundary(risk, expected_uncertainty):
    from app import unified_forensics  # noqa: WPS433

    unified = unified_forensics.build({
        "verdict": "real" if risk < 0.5 else "highly_suspected_fake",
        "confidence": risk,
        "decisionStatus": "verdict",
        "decisionAuthority": "calibrated_model",
        "reviewRequired": False,
        "fileMeta": {"type": "image"},
        "dimensions": [],
        "regions": [],
    })

    assert unified["uncertainty"]["score"] == expected_uncertainty


@pytest.mark.parametrize(
    ("decision_status", "decision_authority"),
    [("review_only", "none"), ("review_only", "evidence_only"), ("verdict", "none")],
)
def test_unified_forensics_keeps_non_authoritative_results_uncertain(
    decision_status,
    decision_authority,
):
    from app import unified_forensics  # noqa: WPS433

    unified = unified_forensics.build({
        "verdict": "unknown",
        "confidence": 0.0,
        "decisionStatus": decision_status,
        "decisionAuthority": decision_authority,
        "fileMeta": {"type": "image"},
        "regions": [],
    })

    assert unified["uncertainty"]["score"] == 1.0
    assert "unauthorized_decision_contract" in unified["uncertainty"]["factors"]


def test_unified_forensics_distinguishes_valid_from_trusted_c2pa():
    from app import unified_forensics  # noqa: WPS433

    base = {
        "verdict": "unknown",
        "confidence": 0.5,
        "fileMeta": {"type": "image"},
        "regions": [],
        "provenance": {
            "hasCredentials": True,
            "isAiGenerated": True,
            "generator": "Example AI",
        },
    }
    valid = unified_forensics.build({
        **base,
        "provenance": {**base["provenance"], "validationState": "Valid"},
    })
    trusted = unified_forensics.build({
        **base,
        "provenance": {**base["provenance"], "validationState": "Trusted"},
    })

    assert valid["provenance_signals"]["c2pa"]["status"] == "credentials_untrusted"
    assert valid["provenance_signals"]["c2pa"]["credential_trusted"] is False
    assert valid["generator_attribution"]["status"] == "unverified_claim"
    assert valid["generator_attribution"]["confidence"] == 0.0
    assert trusted["provenance_signals"]["c2pa"]["status"] == "trusted"
    assert trusted["provenance_signals"]["c2pa"]["credential_trusted"] is True
    assert trusted["generator_attribution"]["confidence"] == 0.95


def test_direct_v2_developer_keys_are_retired_by_default(developer_key_client, monkeypatch):
    import app.main as main  # noqa: WPS433

    monkeypatch.setattr(main, "ALLOW_DIRECT_DEVELOPER_KEYS", False)
    response = developer_key_client.post(
        "/api/detect",
        headers={"X-RealGuard-Key": "rg_sk_user1"},
        files={"file": ("sample.txt", b"use the billed gateway", "text/plain")},
    )

    assert response.status_code == 410
    assert "/api/openapi/v1/image-detections" in response.json()["detail"]


def test_direct_v2_developer_keys_fail_closed_in_production(developer_key_client, monkeypatch):
    import app.main as main  # noqa: WPS433

    monkeypatch.setattr(main, "ALLOW_DIRECT_DEVELOPER_KEYS", True)
    monkeypatch.setattr(main, "RUNTIME_ENV", "production")
    response = developer_key_client.post(
        "/api/detect",
        headers={"X-RealGuard-Key": "rg_sk_user1"},
        files={"file": ("sample.txt", b"production direct key must fail", "text/plain")},
    )

    assert response.status_code == 503
    assert "统一计费网关" in response.json()["detail"]


def test_v2_detection_is_not_anonymous_by_default(client, monkeypatch):
    import app.main as main  # noqa: WPS433

    monkeypatch.setattr(main, "ALLOW_ANONYMOUS_DETECT", False)
    response = client.post(
        "/api/detect",
        files={"file": ("sample.txt", b"anonymous access must fail closed", "text/plain")},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "请先登录慧鉴 AI"


def test_v1_session_can_detect_when_developer_api_key_is_required(developer_key_client, monkeypatch):
    import app.main as main  # noqa: WPS433

    def fake_session_user(request):
        if "session=valid" in request.headers.get("cookie", ""):
            return {
                "mode": "session",
                "userId": 8,
                "accountUuid": "88888888-8888-4888-8888-888888888888",
                "phone": "13900000000",
            }
        return None

    monkeypatch.setattr(main, "_verify_session_user_sync", fake_session_user)

    missing = developer_key_client.post(
        "/api/detect",
        files={"file": ("sample.txt", b"session detect pytest", "text/plain")},
    )
    developer_key_client.cookies.set("session", "valid")
    valid = developer_key_client.post(
        "/api/detect",
        files={"file": ("sample.txt", b"session detect pytest", "text/plain")},
    )

    assert missing.status_code == 401
    assert valid.status_code == 200
    assert valid.json()["reportId"].startswith("RJ-RPT-")


def test_developer_token_usage_records_model_calls_and_cache_hits(developer_key_client, monkeypatch):
    import app.main as main  # noqa: WPS433

    monkeypatch.setattr(
        main.detector,
        "analyze",
        lambda *args, **kwargs: {
            "verdict": "real",
            "confidence": 0.22,
            "dimensions": [{"key": "aigc_text", "label": "AIGC文本检测", "score": 0.22, "result": "未见明显异常"}],
            "regions": [],
            "explanation": "pytest token usage",
            "modelVersion": "pytest-vlm",
            "source": "vlm",
            "tokenUsage": {"promptTokens": 12, "completionTokens": 7, "totalTokens": 19},
        },
    )
    files = {"file": ("usage.txt", b"token usage pytest unique", "text/plain")}
    first = developer_key_client.post(
        "/api/detect",
        headers={"X-RealGuard-Key": "rg_sk_user1"},
        files=files,
    )
    second = developer_key_client.post(
        "/api/detect",
        headers={"X-RealGuard-Key": "rg_sk_user1"},
        files={"file": ("usage.txt", b"token usage pytest unique", "text/plain")},
    )
    usage = developer_key_client.get(
        "/api/developer/token-usage?developerUserId=1&days=30",
        headers={"X-RealGuard-Internal-Secret": "internal-secret"},
    )
    other_user_usage = developer_key_client.get(
        "/api/developer/token-usage?developerUserId=2&days=30",
        headers={"X-RealGuard-Internal-Secret": "internal-secret"},
    )
    missing_secret = developer_key_client.get("/api/developer/token-usage?developerUserId=1&days=30")

    assert first.status_code == 200
    assert first.json()["tokenUsage"]["totalTokens"] == 19
    assert second.status_code == 200
    assert second.json()["cacheHit"] is True
    assert second.json()["tokenUsage"]["totalTokens"] == 0
    assert usage.status_code == 200
    assert usage.json()["summary"]["totalRequests"] == 2
    assert usage.json()["summary"]["billableRequests"] == 1
    assert usage.json()["summary"]["cacheHits"] == 1
    assert usage.json()["summary"]["totalTokens"] == 19
    assert usage.json()["byEndpoint"][0]["endpoint"] == "/api/detect"
    assert other_user_usage.status_code == 200
    assert other_user_usage.json()["summary"]["totalTokens"] == 0
    assert missing_secret.status_code == 403


def test_developer_key_report_access_is_scoped_to_owner(developer_key_client):
    detect = developer_key_client.post(
        "/api/detect",
        headers={"X-RealGuard-Key": "rg_sk_user1"},
        files={"file": ("sample.txt", b"owner scoped report", "text/plain")},
    )
    report_id = detect.json()["reportId"]

    owner = developer_key_client.get(
        f"/api/report/{report_id}/download",
        headers={"X-RealGuard-Key": "rg_sk_user1"},
    )
    owner_verify = developer_key_client.get(
        f"/api/report/{report_id}/verify",
        headers={"X-RealGuard-Key": "rg_sk_user1"},
    )
    other_user = developer_key_client.get(
        f"/api/report/{report_id}/download",
        headers={"X-RealGuard-Key": "rg_sk_user2"},
    )
    other_user_verify = developer_key_client.get(
        f"/api/report/{report_id}/verify",
        headers={"X-RealGuard-Key": "rg_sk_user2"},
    )
    admin = developer_key_client.get(
        f"/api/report/{report_id}/download",
        headers={"X-Jianzhen-Token": "admin-token"},
    )

    assert detect.status_code == 200
    assert owner.status_code == 200
    assert owner.headers["x-evidence-manifest-sha256"] == owner_verify.json()["manifest"]["sha256"]
    assert owner.headers["x-report-artifact-sha256"] == owner_verify.json()["artifact"]["downloadSha256"]
    assert owner_verify.status_code == 200
    assert owner_verify.json()["status"] == "valid"
    assert owner_verify.json()["packageIntegrityVerified"] is True
    assert owner_verify.json()["subjectVerified"] is False
    assert owner_verify.json()["complete"] is False
    assert other_user.status_code == 404
    assert other_user_verify.status_code == 404
    assert admin.status_code == 200


def test_developer_key_report_share_is_scoped_to_owner(developer_key_client):
    detect = developer_key_client.post(
        "/api/detect",
        headers={"X-RealGuard-Key": "rg_sk_user1"},
        files={"file": ("sample.txt", b"owner scoped public share", "text/plain")},
    )
    report_id = detect.json()["reportId"]

    owner = developer_key_client.post(
        f"/api/report/{report_id}/share",
        headers={"X-RealGuard-Key": "rg_sk_user1"},
        json={"expiresInSeconds": 3600},
    )
    other_user = developer_key_client.post(
        f"/api/report/{report_id}/share",
        headers={"X-RealGuard-Key": "rg_sk_user2"},
        json={"expiresInSeconds": 3600},
    )
    parsed = urlsplit(owner.json()["url"])
    public = developer_key_client.get(owner.json()["apiPath"])
    tampered = developer_key_client.get(owner.json()["apiPath"].replace("sig=", "sig=bad"))

    assert detect.status_code == 200
    assert owner.status_code == 200
    assert owner.json()["publicPath"].startswith("/api/report/")
    assert parsed.path == owner.json()["publicPath"].split("?", 1)[0]
    assert other_user.status_code == 404
    assert public.status_code == 200
    assert "text/html" in public.headers["content-type"]
    assert "content-disposition" not in public.headers
    assert "慧鉴 AI 数字内容鉴伪报告" in public.text
    assert tampered.status_code == 403


def test_report_share_is_persisted_and_public_access_is_audited(developer_key_client):
    from app import storage

    detect = developer_key_client.post(
        "/api/detect",
        headers={"X-RealGuard-Key": "rg_sk_user1"},
        files={"file": ("persisted.txt", b"persisted report share", "text/plain")},
    )
    report_id = detect.json()["reportId"]
    created = developer_key_client.post(
        f"/api/report/{report_id}/share",
        headers={"X-RealGuard-Key": "rg_sk_user1"},
        json={"expiresInSeconds": 3600},
    )
    payload = created.json()
    query = parse_qs(urlsplit(payload["apiPath"]).query)

    accessed = developer_key_client.get(
        payload["apiPath"],
        headers={"X-Forwarded-For": "203.0.113.18", "User-Agent": "share-audit-pytest"},
    )
    listed = developer_key_client.get(
        f"/api/report/{report_id}/shares",
        headers={"X-RealGuard-Key": "rg_sk_user1"},
    )
    foreign_list = developer_key_client.get(
        f"/api/report/{report_id}/shares",
        headers={"X-RealGuard-Key": "rg_sk_user2"},
    )
    with storage._connect() as conn:
        share = conn.execute(
            "SELECT * FROM report_shares WHERE share_id = ?",
            (payload["shareId"],),
        ).fetchone()
        audit = conn.execute(
            "SELECT * FROM report_share_access_events WHERE share_id = ? ORDER BY id DESC LIMIT 1",
            (payload["shareId"],),
        ).fetchone()

    assert created.status_code == 200
    assert payload["shareId"].startswith("rgs_")
    assert query["shareId"] == [payload["shareId"]]
    assert share["report_id"] == report_id
    assert share["created_by_user_id"] == "11111111-1111-4111-8111-111111111111"
    assert accessed.headers["cache-control"] == "private, no-store, max-age=0"
    assert listed.status_code == 200
    assert listed.json()["items"][0]["shareId"] == payload["shareId"]
    assert listed.json()["items"][0]["active"] is True
    assert foreign_list.status_code == 404
    assert share["created_by_key_id"] == "101"
    assert share["created_by_mode"] == "developer"
    assert share["revoked_at"] is None
    assert int(share["expires_at"]) > int(time.time())
    assert accessed.status_code == 200
    assert audit["report_id"] == report_id
    assert audit["client_ip"] == "203.0.113.18"
    assert audit["user_agent"] == "share-audit-pytest"
    assert audit["outcome"] == "granted"


def test_public_report_share_requires_its_database_record(developer_key_client):
    from app import storage

    detect = developer_key_client.post(
        "/api/detect",
        headers={"X-RealGuard-Key": "rg_sk_user1"},
        files={"file": ("database-gate.txt", b"database gated report share", "text/plain")},
    )
    report_id = detect.json()["reportId"]
    created = developer_key_client.post(
        f"/api/report/{report_id}/share",
        headers={"X-RealGuard-Key": "rg_sk_user1"},
        json={"expiresInSeconds": 3600},
    ).json()
    with storage._connect() as conn:
        conn.execute("DELETE FROM report_shares WHERE share_id = ?", (created["shareId"],))
        conn.commit()

    response = developer_key_client.get(created["apiPath"])

    assert response.status_code == 404
    assert response.json()["detail"] == "报告分享链接不存在"


def test_owner_can_revoke_one_share_without_affecting_another(developer_key_client):
    from app import storage

    detect = developer_key_client.post(
        "/api/detect",
        headers={"X-RealGuard-Key": "rg_sk_user1"},
        files={"file": ("revoke.txt", b"single report share revocation", "text/plain")},
    )
    report_id = detect.json()["reportId"]

    def create_share() -> dict:
        return developer_key_client.post(
            f"/api/report/{report_id}/share",
            headers={"X-RealGuard-Key": "rg_sk_user1"},
            json={"expiresInSeconds": 3600},
        ).json()

    first = create_share()
    second = create_share()
    foreign = developer_key_client.delete(
        f"/api/report/{report_id}/share/{first['shareId']}",
        headers={"X-RealGuard-Key": "rg_sk_user2"},
    )
    revoked = developer_key_client.delete(
        f"/api/report/{report_id}/share/{first['shareId']}",
        headers={"X-RealGuard-Key": "rg_sk_user1"},
    )
    first_access = developer_key_client.get(first["apiPath"])
    second_access = developer_key_client.get(second["apiPath"])
    with storage._connect() as conn:
        first_row = conn.execute(
            "SELECT revoked_at FROM report_shares WHERE share_id = ?",
            (first["shareId"],),
        ).fetchone()
        second_row = conn.execute(
            "SELECT revoked_at FROM report_shares WHERE share_id = ?",
            (second["shareId"],),
        ).fetchone()
        outcomes = [
            row["outcome"]
            for row in conn.execute(
                "SELECT outcome FROM report_share_access_events WHERE share_id = ? ORDER BY id",
                (first["shareId"],),
            ).fetchall()
        ]

    assert first["shareId"] != second["shareId"]
    assert foreign.status_code == 404
    assert revoked.status_code == 200
    assert revoked.json()["revokedAt"]
    assert first_access.status_code == 410
    assert first_access.json()["detail"] == "报告分享链接已撤销"
    assert second_access.status_code == 200
    assert first_row["revoked_at"]
    assert second_row["revoked_at"] is None
    assert outcomes == ["created", "revoked_by_owner", "revoked"]


def test_legacy_hmac_share_is_imported_audited_and_revocable(developer_key_client):
    from app import main, storage

    detect = developer_key_client.post(
        "/api/detect",
        headers={"X-RealGuard-Key": "rg_sk_user1"},
        files={"file": ("legacy.txt", b"legacy hmac report share", "text/plain")},
    )
    report_id = detect.json()["reportId"]
    expires = int(time.time()) + 3600
    signature = main._sign_report_share(report_id, expires)
    legacy_path = f"/api/report/{report_id}/public?expires={expires}&sig={signature}"

    first_access = developer_key_client.get(legacy_path)
    second_access = developer_key_client.get(legacy_path)
    with storage._connect() as conn:
        shares = conn.execute(
            "SELECT * FROM report_shares WHERE report_id = ? AND legacy = 1",
            (report_id,),
        ).fetchall()
        audit_count = conn.execute(
            "SELECT COUNT(*) AS n FROM report_share_access_events WHERE report_id = ?",
            (report_id,),
        ).fetchone()["n"]
    legacy_share_id = shares[0]["share_id"]
    revoked = developer_key_client.delete(
        f"/api/report/{report_id}/share/{legacy_share_id}",
        headers={"X-RealGuard-Key": "rg_sk_user1"},
    )
    after_revoke = developer_key_client.get(legacy_path)

    assert first_access.status_code == 200
    assert second_access.status_code == 200
    assert len(shares) == 1
    assert shares[0]["created_by_user_id"] == "1"
    assert shares[0]["created_by_mode"] == "legacy"
    assert audit_count == 2
    assert revoked.status_code == 200
    assert after_revoke.status_code == 410
    assert after_revoke.json()["detail"] == "报告分享链接已撤销"


@pytest.mark.parametrize("unsafe_secret", ["", "short-secret", "replace-with-a-secret"])
def test_report_share_requires_independent_signing_secret(client, monkeypatch, unsafe_secret):
    from app import main

    detect = client.post(
        "/api/detect",
        files={"file": ("sample.txt", b"independent share secret", "text/plain")},
    )
    monkeypatch.setattr(main, "REPORT_SHARE_SECRET", unsafe_secret)

    response = client.post(
        f"/api/report/{detect.json()['reportId']}/share",
        headers={"X-Jianzhen-Token": "test-token"},
        json={},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "报告分享签名密钥未安全配置"


def test_report_share_url_rejects_untrusted_forwarded_host(developer_key_client, monkeypatch):
    from app import main

    monkeypatch.setattr(main, "PUBLIC_BASE_URL", "")
    monkeypatch.setattr(main, "TRUSTED_PROXY_NETWORKS", ())
    detect = developer_key_client.post(
        "/api/detect",
        headers={"X-RealGuard-Key": "rg_sk_user1"},
        files={"file": ("host.txt", b"host header test", "text/plain")},
    )
    response = developer_key_client.post(
        f"/api/report/{detect.json()['reportId']}/share",
        headers={
            "X-RealGuard-Key": "rg_sk_user1",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "attacker.example",
        },
        json={},
    )

    assert response.status_code == 200
    assert response.json()["url"].startswith("https://www.rrreal.cn/")


def test_expired_report_share_retention_cannot_be_extended_by_recent_access(
    developer_key_client, monkeypatch
):
    from app import storage

    monkeypatch.setattr(storage, "REPORT_SHARE_RETENTION_DAYS", 30)
    share_id = "rgs_retention_test"
    storage.create_report_share(
        share_id=share_id,
        report_id="report-retention-test",
        expires_at=int(time.time()) - 31 * 24 * 60 * 60,
        created_by_user_id="1",
        created_by_key_id="101",
        created_by_mode="developer",
    )
    storage.record_report_share_access(
        share_id=share_id,
        report_id="report-retention-test",
        client_ip="203.0.113.22",
        user_agent="retention-test",
        outcome="expired",
    )

    deleted = storage.prune_telemetry()

    assert deleted["reportShares"] == 1
    assert storage.get_report_share(share_id) is None
    with storage._connect() as conn:
        event_count = conn.execute(
            "SELECT COUNT(*) AS n FROM report_share_access_events WHERE share_id = ?",
            (share_id,),
        ).fetchone()["n"]
    assert event_count == 1


def test_report_download_returns_attachment_pdf(client):
    detect = client.post(
        "/api/detect",
        files={"file": ("sample.txt", b"hello world from pytest", "text/plain")},
    )
    report_id = detect.json()["reportId"]

    response = client.get(
        f"/api/report/{report_id}/download",
        headers={"X-Jianzhen-Token": "test-token"},
    )

    assert detect.status_code == 200
    assert response.status_code == 200
    assert "attachment;" in response.headers["content-disposition"]
    assert response.headers["content-type"].startswith("application/pdf")
    assert ".pdf" in response.headers["content-disposition"]
    assert response.content.startswith(b"%PDF-")
    assert len(response.content) > 1500


def test_evidence_package_is_offline_verifiable_and_tenant_scoped(developer_key_client):
    from app import evidence_manifest_v2

    original = b"tenant-owned original for evidence package"
    detect = developer_key_client.post(
        "/api/detect",
        headers={"X-RealGuard-Key": "rg_sk_user1"},
        files={"file": ("evidence.txt", original, "text/plain")},
    )
    report_id = detect.json()["reportId"]
    package = developer_key_client.get(
        f"/api/report/{report_id}/evidence-package",
        headers={"X-RealGuard-Key": "rg_sk_user1"},
    )
    foreign = developer_key_client.get(
        f"/api/report/{report_id}/evidence-package",
        headers={"X-RealGuard-Key": "rg_sk_user2"},
    )

    assert detect.status_code == 200
    assert package.status_code == 200
    assert package.headers["content-type"].startswith("application/zip")
    assert package.headers["cache-control"] == "private, no-store, max-age=0"
    assert package.headers["x-evidence-package-sha256"] == hashlib.sha256(
        package.content
    ).hexdigest()
    assert foreign.status_code == 404

    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        assert set(archive.namelist()) == {
            "README.txt",
            "evidence-bundle.json",
            "key-registry.json",
            "report-payload.json",
            "report.pdf",
            "subject-metadata.json",
            "subject.sha256",
            "verify_evidence.py",
        }
        bundle = json.loads(archive.read("evidence-bundle.json"))
        report_payload = json.loads(archive.read("report-payload.json"))
        registry = json.loads(archive.read("key-registry.json"))
        pdf = archive.read("report.pdf")
        readme = archive.read("README.txt").decode("utf-8")
        subject_metadata = json.loads(archive.read("subject-metadata.json"))

    key_id = bundle["manifest"]["signature"]["keyId"]
    key = next(item for item in registry["keys"] if item["keyId"] == key_id)
    verification = evidence_manifest_v2.verify_bundle(
        bundle,
        artifact_bytes=pdf,
        report_payload=report_payload,
        subject_bytes=original,
        trusted_public_keys={key_id: __import__("base64").b64decode(key["publicKey"])},
    )
    assert verification["status"] == "valid"
    assert verification["subjectVerified"] is True
    assert subject_metadata["sha256"] == hashlib.sha256(original).hexdigest()
    assert registry["externallyAnchored"] is False
    assert "未接入 RFC 3161" in readme
    assert "独立发布渠道" in readme


def test_public_share_exposes_revocable_evidence_package(developer_key_client):
    detect = developer_key_client.post(
        "/api/detect",
        headers={"X-RealGuard-Key": "rg_sk_user1"},
        files={"file": ("public-package.txt", b"public evidence package", "text/plain")},
    )
    report_id = detect.json()["reportId"]
    created = developer_key_client.post(
        f"/api/report/{report_id}/share",
        headers={"X-RealGuard-Key": "rg_sk_user1"},
        json={"expiresInSeconds": 3600},
    ).json()

    public_html = developer_key_client.get(created["apiPath"])
    public_package = developer_key_client.get(created["evidencePackageApiPath"])
    tampered = developer_key_client.get(
        created["evidencePackageApiPath"].replace("sig=", "sig=bad")
    )
    revoked = developer_key_client.delete(
        f"/api/report/{report_id}/share/{created['shareId']}",
        headers={"X-RealGuard-Key": "rg_sk_user1"},
    )
    after_revoke = developer_key_client.get(created["evidencePackageApiPath"])

    assert public_html.status_code == 200
    assert "下载离线证据包" in public_html.text
    assert "/public/evidence-package?" in public_html.text
    assert public_package.status_code == 200
    assert public_package.headers["content-type"].startswith("application/zip")
    assert tampered.status_code == 403
    assert revoked.status_code == 200
    assert after_revoke.status_code == 410


def test_request_metrics_store_route_templates_not_report_identifiers(client):
    import app.storage as storage  # noqa: WPS433

    detect = client.post(
        "/api/detect",
        files={"file": ("route-template.txt", b"route privacy", "text/plain")},
    )
    report_id = detect.json()["reportId"]
    response = client.get(
        f"/api/report/{report_id}",
        headers={"X-Jianzhen-Token": "test-token"},
    )

    assert response.status_code == 200
    with storage._connect() as conn:
        paths = [row["path"] for row in conn.execute("SELECT path FROM request_events")]
    assert "/api/report/{report_id}" in paths
    assert all(report_id not in path for path in paths)


def test_report_verify_is_read_only_and_reports_missing_until_pdf_is_frozen(client):
    import app.storage as storage  # noqa: WPS433

    detect = client.post(
        "/api/detect",
        files={"file": ("verify-first.txt", b"read-only verification", "text/plain")},
    )
    report_id = detect.json()["reportId"]
    headers = {"X-Jianzhen-Token": "test-token"}

    before_download = client.get(f"/api/report/{report_id}/verify", headers=headers)

    assert before_download.status_code == 200
    assert before_download.json()["status"] == "missing"
    assert before_download.json()["manifest"]["status"] == "valid"
    assert before_download.json()["artifact"]["status"] == "missing"
    assert before_download.json()["artifact"]["downloadSha256"] is None
    assert storage.get_report_artifact(report_id) is None

    download = client.get(f"/api/report/{report_id}/download", headers=headers)
    after_download = client.get(f"/api/report/{report_id}/verify", headers=headers)

    assert download.status_code == 200
    assert after_download.status_code == 200
    assert after_download.json()["status"] == "valid"
    assert after_download.json()["packageIntegrityVerified"] is True
    assert after_download.json()["subjectVerified"] is False
    assert after_download.json()["complete"] is False
    assert after_download.json()["artifact"]["downloadSha256"] == download.headers[
        "x-report-artifact-sha256"
    ]


def test_provenance_reads_tc260_aigc_metadata_without_c2pa(client):
    xmp = """
    <x:xmpmeta xmlns:x="adobe:ns:meta/">
      <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
        <rdf:Description xmlns:TC260="http://www.tc260.org.cn/ns/AIGC/1.0/">
          <TC260:AIGC>{"Label":"1","ContentProducer":"001191110102MACQD9K64010000","ProduceID":"9ce377b782374e359a10b4f4c38bc557"}</TC260:AIGC>
        </rdf:Description>
      </rdf:RDF>
    </x:xmpmeta>
    """
    response = client.post(
        "/api/provenance",
        files={"file": ("tc260-aigc.png", _png_with_itxt("XML:com.adobe.xmp", xmp), "image/png")},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["hasCredentials"] is False
    assert payload["metadataAiGenerated"] is True
    assert payload["aiMetadata"]["score"] >= 70
    assert payload["metadataSummary"]["embeddedSectionCount"] >= 1
    assert any(signal["id"] == "tc260-aigc" for signal in payload["aiMetadata"]["signals"])
    assert any(chunk["keyword"] == "XML:com.adobe.xmp" for chunk in payload["metadata"]["png"]["textChunks"])


def test_detect_auto_persists_image_provenance_metadata(client):
    xmp = """
    <x:xmpmeta xmlns:x="adobe:ns:meta/">
      <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
        <rdf:Description xmlns:TC260="http://www.tc260.org.cn/ns/AIGC/1.0/">
          <TC260:AIGC>{"Label":"1","ContentProducer":"001191110102MACQD9K64010000","ProduceID":"9ce377b782374e359a10b4f4c38bc557"}</TC260:AIGC>
        </rdf:Description>
      </rdf:RDF>
    </x:xmpmeta>
    """
    detect = client.post(
        "/api/detect",
        files={"file": ("tc260-detect.png", _png_with_itxt("XML:com.adobe.xmp", xmp), "image/png")},
    )
    payload = detect.json()
    history = client.get(
        f"/api/history/{payload['taskId']}",
        headers={"X-Jianzhen-Token": "test-token"},
    )
    listing = client.get(
        "/api/history?hasProvenance=true",
        headers={"X-Jianzhen-Token": "test-token"},
    )

    assert detect.status_code == 200
    assert payload["provenance"]["metadataAiGenerated"] is True
    assert payload["unifiedForensics"]["provenance_signals"]["c2pa"]["status"] == "no_manifest"
    assert payload["unifiedForensics"]["provenance_signals"]["metadata_ai"]["detected"] is True
    assert payload["unifiedForensics"]["generator_attribution"]["status"] == "known_signal"
    assert history.status_code == 200
    assert history.json()["provenance"]["aiMetadata"]["score"] >= 70
    assert listing.status_code == 200
    assert any(item["taskId"] == payload["taskId"] for item in listing.json()["items"])


def test_history_detail_backfills_file_meta_for_legacy_rows(client):
    import app.storage as storage  # noqa: WPS433

    legacy_result = {
        "taskId": "legacy-task",
        "reportId": "RJ-RPT-LEGACY",
        "createdAt": "2026-06-19T00:00:00+00:00",
        "verdict": "unknown",
        "confidence": 0,
        "modelVersion": "legacy",
        "source": "legacy",
        "cacheVersion": "legacy",
        "elapsedMs": 0,
        "dimensions": [],
        "regions": [],
        "explanation": "legacy row without fileMeta",
        "disclaimer": "legacy",
    }
    with storage._connect() as conn:  # noqa: SLF001
        conn.execute(
            """
            INSERT INTO history
                (task_id, report_id, created_at, sha256, file_type, file_name, file_size,
                 resolution, result_json, thumbnail)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-task",
                "RJ-RPT-LEGACY",
                "2026-06-19T00:00:00+00:00",
                "abc123",
                "image",
                "legacy.png",
                2048,
                "1x1",
                json.dumps(legacy_result),
                "data:image/png;base64,abc",
            ),
        )
        conn.commit()

    response = client.get("/api/history/legacy-task", headers={"X-Jianzhen-Token": "test-token"})
    payload = response.json()

    assert response.status_code == 200
    assert payload["fileMeta"]["name"] == "legacy.png"
    assert payload["fileMeta"]["type"] == "image"
    assert payload["fileMeta"]["size"] == "2.0KB"
    assert payload["fileMeta"]["resolution"] == "1x1"
    assert payload["fileMeta"]["thumbnail"] == "data:image/png;base64,abc"


def test_report_export_ignores_client_supplied_evidence(client, monkeypatch):
    import app.main as main  # noqa: WPS433

    detect = client.post(
        "/api/detect",
        files={"file": ("sample.txt", b"hello world from pytest", "text/plain")},
    )
    report_id = detect.json()["reportId"]
    captured = {}

    def fake_pdf(result, *, forensics=None, provenance=None):
        captured["forensics"] = forensics
        captured["provenance"] = provenance
        return b"%PDF-authoritative"

    monkeypatch.setattr(main.reporting, "build_report_pdf", fake_pdf)

    response = client.post(
        f"/api/report/{report_id}/export",
        headers={"X-Jianzhen-Token": "test-token"},
        json={
            "forensics": {
                "verdict": "suspected_fake",
                "confidence": 0.71,
                "summary": "频域与光照存在可疑矛盾。",
                "items": [
                    {"key": "fft", "title": "频域分析", "status": "warn", "finding": "出现规则纹理"},
                ],
                "modelVersion": "qwen3-vl-flash",
                "source": "vlm",
            },
            "provenance": {
                "hasCredentials": True,
                "validationState": "valid",
                "generator": "OpenAI",
                "issuer": "Test Issuer",
                "signatureAlg": "ES256",
                "isAiGenerated": True,
                "actions": [{"action": "c2pa.created", "softwareAgent": "OpenAI Images"}],
                "ingredients": [{"title": "source.png", "relationship": "parentOf"}],
                "synthid": {"note": "未检测到 SynthID"},
            },
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content == b"%PDF-authoritative"
    assert captured == {"forensics": None, "provenance": None}


def test_history_artifacts_are_persisted_only_by_server_analysis(client, monkeypatch):
    import app.main as main  # noqa: WPS433

    image = _png_with_itxt("Comment", "pytest image")
    monkeypatch.setattr(main.provenance_precheck, "inspect", lambda *_args: {})
    monkeypatch.setattr(main.provenance_precheck, "build_analysis", lambda *_args: None)
    monkeypatch.setattr(main.detector, "explainable", lambda *_args: _forensic_analysis())
    monkeypatch.setattr(
        main.provenance,
        "read_provenance",
        lambda *_args: {
            "hasCredentials": False,
            "validationState": "none",
            "generator": None,
            "issuer": None,
            "signatureAlg": None,
            "isAiGenerated": None,
            "actions": [],
            "ingredients": [],
            "synthid": {"note": "未检测到 SynthID"},
            "error": None,
        },
    )
    detect = client.post(
        "/api/detect",
        files={"file": ("sample.png", image, "image/png")},
    )
    task_id = detect.json()["taskId"]
    report_id = detect.json()["reportId"]

    forged = client.post(
        f"/api/history/{task_id}/artifacts",
        headers={"X-Jianzhen-Token": "test-token"},
        json={"forensics": {"summary": "伪造证据"}},
    )
    forensics = client.post(
        "/api/forensics",
        headers={"X-Jianzhen-Token": "test-token"},
        data={"taskId": task_id},
        files={"file": ("sample.png", image, "image/png")},
    )
    provenance_result = client.post(
        "/api/provenance",
        headers={"X-Jianzhen-Token": "test-token"},
        data={"taskId": task_id},
        files={"file": ("sample.png", image, "image/png")},
    )
    listing = client.get("/api/history", headers={"X-Jianzhen-Token": "test-token"})
    history = client.get(f"/api/history/{task_id}", headers={"X-Jianzhen-Token": "test-token"})
    download = client.get(f"/api/report/{report_id}/download", headers={"X-Jianzhen-Token": "test-token"})

    assert forged.status_code == 410
    assert forensics.status_code == 200
    assert provenance_result.status_code == 200
    assert listing.status_code == 200
    assert listing.json()["items"][0]["hasForensics"] is True
    assert listing.json()["items"][0]["hasProvenance"] is True
    assert history.status_code == 200
    payload = history.json()
    assert payload["forensics"]["summary"].endswith("不形成内容真假的自动结论。")
    assert payload["forensics"]["decisionStatus"] == "review_only"
    assert payload["forensics"]["decisionAuthority"] == "evidence_only"
    assert "verdict" not in payload["forensics"]
    assert "confidence" not in payload["forensics"]
    assert payload["provenance"]["validationState"] == "none"
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("application/pdf")
    assert download.content.startswith(b"%PDF-")


def test_concurrent_server_artifact_updates_preserve_both_evidence_types(client):
    import app.storage as storage  # noqa: WPS433

    detect = client.post(
        "/api/detect",
        files={"file": ("sample.txt", b"concurrent artifact flow", "text/plain")},
    )
    task_id = detect.json()["taskId"]

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = []
        for index in range(12):
            futures.append(executor.submit(
                storage.put_history_artifacts,
                task_id,
                forensics={"summary": f"forensics-{index}"},
            ))
            futures.append(executor.submit(
                storage.put_history_artifacts,
                task_id,
                provenance={"validationState": f"provenance-{index}"},
            ))
        for future in futures:
            future.result()

    item = storage.get_history(task_id)
    assert item["forensics"]["summary"].startswith("forensics-")
    assert item["provenance"]["validationState"].startswith("provenance-")


def test_history_listing_exposes_source_and_watermark_summary(client, monkeypatch):
    import app.main as main  # noqa: WPS433

    monkeypatch.setattr(
        main.detector,
        "analyze",
        lambda *args, **kwargs: {
            "verdict": "highly_suspected_fake",
            "confidence": 0.92,
            "dimensions": [],
            "regions": [],
            "explanation": "命中多项水印证据。",
            "modelVersion": "qwen3-vl-flash",
            "source": "vlm",
            "synthid": {
                "detected": True,
                "supported": True,
                "confidence": 0.88,
                "phaseMatch": 0.91,
                "evidenceLevel": "strong",
                "note": "检测到高置信度 SynthID",
            },
            "visibleWatermark": {
                "detected": True,
                "provider": "gemini",
                "confidence": 0.95,
                "evidenceLevel": "strong",
                "hits": [],
                "temporal": {"sampledFrames": 1, "positiveFrames": 1, "moving": False},
                "note": "检测到 Gemini 可见水印",
            },
        },
    )

    detect = client.post(
        "/api/detect",
        files={"file": ("sample.png", b"fake-image", "image/png")},
    )
    listing = client.get("/api/history", headers={"X-Jianzhen-Token": "test-token"})

    assert detect.status_code == 200
    assert listing.status_code == 200
    item = listing.json()["items"][0]
    assert item["source"] == "vlm"
    assert item["hasVisibleWatermark"] is True
    assert item["visibleWatermarkProvider"] == "gemini"
    assert item["hasSynthid"] is True


def test_history_filters_and_counts_include_synthid(client, monkeypatch):
    import app.main as main  # noqa: WPS433

    responses = [
        {
            "verdict": "highly_suspected_fake",
            "confidence": 0.92,
            "dimensions": [],
            "regions": [],
            "explanation": "命中 SynthID。",
            "modelVersion": "qwen3-vl-flash",
            "source": "vlm",
            "synthid": {
                "detected": True,
                "supported": True,
                "confidence": 0.88,
                "phaseMatch": 0.91,
                "evidenceLevel": "strong",
                "note": "检测到高置信度 SynthID",
            },
            "visibleWatermark": {
                "detected": False,
                "provider": None,
                "confidence": 0.0,
                "evidenceLevel": "none",
                "hits": [],
                "temporal": {"sampledFrames": 1, "positiveFrames": 0, "moving": False},
                "note": "未检测到可见水印",
            },
        },
        {
            "verdict": "real",
            "confidence": 0.63,
            "dimensions": [],
            "regions": [],
            "explanation": "未命中 SynthID。",
            "modelVersion": "qwen3-vl-flash",
            "source": "vlm",
            "synthid": {
                "detected": False,
                "supported": True,
                "confidence": 0.02,
                "phaseMatch": 0.05,
                "evidenceLevel": "none",
                "note": "未检测到 SynthID",
            },
            "visibleWatermark": {
                "detected": False,
                "provider": None,
                "confidence": 0.0,
                "evidenceLevel": "none",
                "hits": [],
                "temporal": {"sampledFrames": 1, "positiveFrames": 0, "moving": False},
                "note": "未检测到可见水印",
            },
        },
    ]

    def fake_analyze(*args, **kwargs):
        return responses.pop(0)

    monkeypatch.setattr(main.detector, "analyze", fake_analyze)

    first = client.post(
        "/api/detect",
        files={"file": ("first.png", b"image-a", "image/png")},
    )
    second = client.post(
        "/api/detect",
        files={"file": ("second.png", b"image-b-different", "image/png")},
    )

    listing = client.get("/api/history", headers={"X-Jianzhen-Token": "test-token"})
    synthid_only = client.get("/api/history?hasSynthid=true", headers={"X-Jianzhen-Token": "test-token"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert listing.status_code == 200
    payload = listing.json()
    assert payload["filterCounts"]["synthid"] == 1
    assert payload["filterCounts"]["watermark"] == 0
    assert synthid_only.status_code == 200
    synthid_items = synthid_only.json()["items"]
    assert len(synthid_items) == 1
    assert synthid_items[0]["hasSynthid"] is True


def test_history_filters_fail_closed_for_uncalibrated_vlm_verdicts(client, monkeypatch):
    import app.main as main  # noqa: WPS433

    responses = [
        {
            "verdict": "real",
            "confidence": 0.61,
            "dimensions": [],
            "regions": [],
            "explanation": "真实内容。",
            "modelVersion": "qwen3-vl-flash",
            "source": "vlm",
            "synthid": {"detected": False, "supported": True, "confidence": 0.0, "phaseMatch": 0.0, "evidenceLevel": "none", "note": "未检测到 SynthID"},
            "visibleWatermark": {"detected": False, "provider": None, "confidence": 0.0, "evidenceLevel": "none", "hits": [], "temporal": {"sampledFrames": 1, "positiveFrames": 0, "moving": False}, "note": "未检测到可见水印"},
        },
        {
            "verdict": "suspected_fake",
            "confidence": 0.72,
            "dimensions": [],
            "regions": [],
            "explanation": "疑似伪造。",
            "modelVersion": "qwen3-vl-flash",
            "source": "vlm",
            "synthid": {"detected": False, "supported": True, "confidence": 0.0, "phaseMatch": 0.0, "evidenceLevel": "none", "note": "未检测到 SynthID"},
            "visibleWatermark": {"detected": False, "provider": None, "confidence": 0.0, "evidenceLevel": "none", "hits": [], "temporal": {"sampledFrames": 1, "positiveFrames": 0, "moving": False}, "note": "未检测到可见水印"},
        },
        {
            "verdict": "highly_suspected_fake",
            "confidence": 0.91,
            "dimensions": [],
            "regions": [],
            "explanation": "高度疑似伪造。",
            "modelVersion": "qwen3-vl-flash",
            "source": "vlm",
            "synthid": {"detected": False, "supported": True, "confidence": 0.0, "phaseMatch": 0.0, "evidenceLevel": "none", "note": "未检测到 SynthID"},
            "visibleWatermark": {"detected": False, "provider": None, "confidence": 0.0, "evidenceLevel": "none", "hits": [], "temporal": {"sampledFrames": 1, "positiveFrames": 0, "moving": False}, "note": "未检测到可见水印"},
        },
        {
            "verdict": "unknown",
            "confidence": 0.12,
            "dimensions": [],
            "regions": [],
            "explanation": "未知判定。",
            "modelVersion": "qwen3-vl-flash",
            "source": "vlm",
            "synthid": {"detected": False, "supported": True, "confidence": 0.0, "phaseMatch": 0.0, "evidenceLevel": "none", "note": "未检测到 SynthID"},
            "visibleWatermark": {"detected": False, "provider": None, "confidence": 0.0, "evidenceLevel": "none", "hits": [], "temporal": {"sampledFrames": 1, "positiveFrames": 0, "moving": False}, "note": "未检测到可见水印"},
        },
    ]

    def fake_analyze(*args, **kwargs):
        return responses.pop(0)

    monkeypatch.setattr(main.detector, "analyze", fake_analyze)

    client.post("/api/detect", files={"file": ("real.png", b"verdict-a", "image/png")})
    client.post("/api/detect", files={"file": ("suspected.png", b"verdict-b", "image/png")})
    client.post("/api/detect", files={"file": ("highly.png", b"verdict-c", "image/png")})
    unknown = client.post("/api/detect", files={"file": ("unknown.png", b"verdict-d", "image/png")})

    listing = client.get("/api/history", headers={"X-Jianzhen-Token": "test-token"})
    real_only = client.get("/api/history?verdict=real", headers={"X-Jianzhen-Token": "test-token"})
    suspected_only = client.get("/api/history?verdict=suspected_fake", headers={"X-Jianzhen-Token": "test-token"})
    highly_only = client.get("/api/history?verdict=highly_suspected_fake", headers={"X-Jianzhen-Token": "test-token"})
    unknown_only = client.get("/api/history?verdict=unknown", headers={"X-Jianzhen-Token": "test-token"})

    assert listing.status_code == 200
    payload = listing.json()
    assert payload["filterCounts"]["real"] == 2
    assert payload["filterCounts"]["suspected"] == 0
    assert payload["filterCounts"]["highly"] == 2
    assert payload["filterCounts"]["unknownVerdict"] == 0
    assert unknown.status_code == 200
    assert len(real_only.json()["items"]) == 2
    assert suspected_only.json()["items"] == []
    assert len(highly_only.json()["items"]) == 2
    assert unknown_only.json()["items"] == []
    assert all(item["reviewRequired"] is True for item in listing.json()["items"])


def test_metrics_include_source_and_evidence_breakdown(client):
    import app.storage as storage  # noqa: WPS433

    detect = client.post(
        "/api/detect",
        files={"file": ("sample.txt", b"metrics evidence flow", "text/plain")},
    )
    task_id = detect.json()["taskId"]
    storage.put_history_artifacts(
        task_id,
        forensics={"summary": "done"},
        provenance={"validationState": "valid"},
    )

    metrics = client.get("/api/metrics", headers={"X-Jianzhen-Token": "test-token"})

    assert metrics.status_code == 200
    payload = metrics.json()
    assert "bySource" in payload
    assert "sourceVerdict" in payload
    assert "sourceEvidence" in payload
    assert "byDay" in payload
    assert "evidence" in payload
    assert payload["evidence"]["forensicsCompleted"] >= 1
    assert payload["evidence"]["provenanceCompleted"] >= 1
    assert payload["sourceVerdict"]
    assert payload["sourceEvidence"]
    assert "sources" in payload["byDay"][0]
    assert "verdicts" in payload["byDay"][0]
    assert "evidence" in payload["byDay"][0]
    assert payload["summary"]["analysisCacheVersion"] == "v10-tenant-scoped"


def test_metrics_supports_window_sizes(client):
    metrics_7 = client.get("/api/metrics?days=7", headers={"X-Jianzhen-Token": "test-token"})
    metrics_30 = client.get("/api/metrics?days=30", headers={"X-Jianzhen-Token": "test-token"})
    metrics_bad = client.get("/api/metrics?days=9", headers={"X-Jianzhen-Token": "test-token"})

    assert metrics_7.status_code == 200
    assert metrics_30.status_code == 200
    assert len(metrics_7.json()["byDay"]) == 7
    assert len(metrics_30.json()["byDay"]) == 30
    assert metrics_bad.status_code == 400


def test_history_listing_supports_filters_query_and_limit(client, monkeypatch):
    import app.main as main  # noqa: WPS433
    import app.storage as storage  # noqa: WPS433

    baseline = client.get("/api/history", headers={"X-Jianzhen-Token": "test-token"})
    assert baseline.status_code == 200
    baseline_total = baseline.json()["total"]

    analyses = [
        {
            "verdict": "real",
            "confidence": 0.81,
            "dimensions": [],
            "regions": [],
            "explanation": "真实模型命中。",
            "modelVersion": "qwen3-vl-flash",
            "source": "vlm",
            "synthid": {"detected": False},
            "visibleWatermark": {"detected": False, "provider": None},
        },
        {
            "verdict": "real",
            "confidence": 0.81,
            "dimensions": [],
            "regions": [],
            "explanation": "同一匿名文件重新执行模型检测。",
            "modelVersion": "qwen3-vl-flash",
            "source": "vlm",
            "synthid": {"detected": False},
            "visibleWatermark": {"detected": False, "provider": None},
        },
        {
            "verdict": "highly_suspected_fake",
            "confidence": 0.97,
            "dimensions": [],
            "regions": [],
            "explanation": "命中 Gemini 水印。",
            "modelVersion": "qwen3-vl-flash-watermark",
            "source": "vlm",
            "synthid": {"detected": True},
            "visibleWatermark": {"detected": True, "provider": "gemini"},
        },
        {
            "verdict": "suspected_fake",
            "confidence": 0.66,
            "dimensions": [],
            "regions": [],
            "explanation": "真实模型给出疑似结论。",
            "modelVersion": "qwen3-vl-flash-evidence",
            "source": "vlm",
            "synthid": {"detected": False},
            "visibleWatermark": {"detected": False, "provider": None},
        },
        {
            "verdict": "real",
            "confidence": 0.55,
            "dimensions": [],
            "regions": [],
            "explanation": "真实模型未见明显异常。",
            "modelVersion": "qwen3-vl-flash",
            "source": "vlm",
            "synthid": {"detected": False},
            "visibleWatermark": {"detected": False, "provider": None},
        },
    ]

    monkeypatch.setattr(main.detector, "analyze", lambda *args, **kwargs: analyses.pop(0))

    detect_a = client.post("/api/detect", files={"file": ("alpha.txt", b"alpha", "text/plain")})
    detect_a_cached = client.post("/api/detect", files={"file": ("alpha.txt", b"alpha", "text/plain")})
    detect_b = client.post("/api/detect", files={"file": ("beta.txt", b"beta", "text/plain")})
    detect_c = client.post("/api/detect", files={"file": ("gamma.txt", b"gamma", "text/plain")})
    detect_d = client.post("/api/detect", files={"file": ("delta.txt", b"delta", "text/plain")})

    assert detect_a.status_code == 200
    assert detect_a_cached.status_code == 200
    assert detect_b.status_code == 200
    assert detect_c.status_code == 200
    assert detect_d.status_code == 200
    assert detect_a.json()["cacheVersion"] == "v10-tenant-scoped"
    assert detect_a_cached.json()["cacheHit"] is False
    assert detect_a_cached.json()["cacheVersion"] == "v10-tenant-scoped"

    task_a = detect_a.json()["taskId"]
    task_b = detect_b.json()["taskId"]

    storage.put_history_artifacts(task_a, forensics={"summary": "done"})
    storage.put_history_artifacts(task_b, provenance={"validationState": "valid"})

    limited = client.get("/api/history?limit=1", headers={"X-Jianzhen-Token": "test-token"})
    by_source = client.get(
        f"/api/history?source=vlm&query={detect_b.json()['reportId']}",
        headers={"X-Jianzhen-Token": "test-token"},
    )
    by_model = client.get("/api/history?query=qwen3-vl-flash-watermark", headers={"X-Jianzhen-Token": "test-token"})
    by_cache_version = client.get("/api/history?query=%E7%BC%93%E5%AD%98%E7%89%88%E6%9C%AC%20v10-tenant-scoped", headers={"X-Jianzhen-Token": "test-token"})
    by_query = client.get("/api/history?query=%E7%9C%9F%E5%AE%9E%E6%A8%A1%E5%9E%8B", headers={"X-Jianzhen-Token": "test-token"})
    by_evidence = client.get(
        "/api/history?hasWatermark=true&hasSynthid=true&query=gemini%20%E6%B0%B4%E5%8D%B0",
        headers={"X-Jianzhen-Token": "test-token"},
    )
    by_cache = client.get(
        f"/api/history?hasCache=true&query={detect_a_cached.json()['reportId']}",
        headers={"X-Jianzhen-Token": "test-token"},
    )
    by_forensics = client.get(
        f"/api/history?hasForensics=true&query={detect_a.json()['reportId']}",
        headers={"X-Jianzhen-Token": "test-token"},
    )

    assert limited.status_code == 200
    assert limited.json()["total"] == baseline_total + 5
    assert len(limited.json()["items"]) == 1

    assert by_source.status_code == 200
    assert by_source.json()["total"] == 1
    assert by_source.json()["items"][0]["source"] == "vlm"
    assert by_source.json()["items"][0]["modelVersion"] == "qwen3-vl-flash-watermark"
    assert by_source.json()["items"][0]["reportId"] == detect_b.json()["reportId"]
    assert by_source.json()["filterCounts"]["maps-only"] == 0

    assert by_model.status_code == 200
    assert by_model.json()["total"] == 1
    assert by_model.json()["items"][0]["modelVersion"] == "qwen3-vl-flash-watermark"
    assert by_model.json()["items"][0]["reportId"] == detect_b.json()["reportId"]

    assert by_cache_version.status_code == 200
    assert by_cache_version.json()["total"] >= 5
    assert {item["cacheVersion"] for item in by_cache_version.json()["items"]} == {"v10-tenant-scoped"}

    assert by_query.status_code == 200
    assert by_query.json()["total"] >= 1
    assert any(item["reportId"] == detect_a.json()["reportId"] for item in by_query.json()["items"])

    assert by_evidence.status_code == 200
    assert by_evidence.json()["total"] >= 1
    evidence_item = next(item for item in by_evidence.json()["items"] if item["reportId"] == detect_b.json()["reportId"])
    assert evidence_item["visibleWatermarkProvider"] == "gemini"
    assert evidence_item["hasSynthid"] is True

    assert by_cache.status_code == 200
    assert by_cache.json()["total"] == 0
    assert by_cache.json()["filterCounts"]["cache"] == 0

    assert by_forensics.status_code == 200
    assert by_forensics.json()["total"] == 1
    assert by_forensics.json()["items"][0]["taskId"] == task_a


def test_history_listing_rejects_invalid_filter_params(client):
    bad_limit = client.get("/api/history?limit=0", headers={"X-Jianzhen-Token": "test-token"})
    bad_offset = client.get("/api/history?offset=-1", headers={"X-Jianzhen-Token": "test-token"})
    bad_source = client.get("/api/history?source=bad-source", headers={"X-Jianzhen-Token": "test-token"})
    bad_verdict = client.get("/api/history?verdict=maybe", headers={"X-Jianzhen-Token": "test-token"})
    bad_bool = client.get("/api/history?hasForensics=maybe", headers={"X-Jianzhen-Token": "test-token"})
    bad_cache = client.get("/api/history?hasCache=maybe", headers={"X-Jianzhen-Token": "test-token"})

    assert bad_limit.status_code == 400
    assert bad_offset.status_code == 400
    assert bad_source.status_code == 400
    assert bad_verdict.status_code == 400
    assert bad_bool.status_code == 400
    assert bad_cache.status_code == 400


def test_history_listing_supports_offset_pagination(client, monkeypatch):
    import app.main as main  # noqa: WPS433

    analyses = [
        {
            "verdict": "real",
            "confidence": 0.71,
            "dimensions": [],
            "regions": [],
            "explanation": "one",
            "modelVersion": "qwen3-vl-flash",
            "source": "vlm",
            "synthid": {"detected": False},
            "visibleWatermark": {"detected": False, "provider": None},
        },
        {
            "verdict": "suspected_fake",
            "confidence": 0.81,
            "dimensions": [],
            "regions": [],
            "explanation": "two",
            "modelVersion": "qwen3-vl-flash",
            "source": "vlm",
            "synthid": {"detected": False},
            "visibleWatermark": {"detected": False, "provider": None},
        },
        {
            "verdict": "highly_suspected_fake",
            "confidence": 0.91,
            "dimensions": [],
            "regions": [],
            "explanation": "three",
            "modelVersion": "qwen3-vl-flash",
            "source": "vlm",
            "synthid": {"detected": False},
            "visibleWatermark": {"detected": False, "provider": None},
        },
    ]

    monkeypatch.setattr(main.detector, "analyze", lambda *args, **kwargs: analyses.pop(0))

    first = client.post("/api/detect", files={"file": ("one.txt", b"one", "text/plain")})
    second = client.post("/api/detect", files={"file": ("two.txt", b"two", "text/plain")})
    third = client.post("/api/detect", files={"file": ("three.txt", b"three", "text/plain")})

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 200

    page_one = client.get("/api/history?limit=2&offset=0", headers={"X-Jianzhen-Token": "test-token"})
    page_two = client.get("/api/history?limit=2&offset=2", headers={"X-Jianzhen-Token": "test-token"})

    assert page_one.status_code == 200
    assert page_two.status_code == 200
    assert page_one.json()["total"] >= 3
    assert len(page_one.json()["items"]) == 2
    assert len(page_two.json()["items"]) >= 1
    assert page_one.json()["items"][0]["reportId"] == third.json()["reportId"]
    assert page_two.json()["items"][0]["reportId"] == first.json()["reportId"]
