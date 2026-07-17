from pathlib import Path
import binascii
import importlib
import json
import sys
import struct
import zlib
from urllib.parse import urlsplit

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


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("JIANZHEN_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("JIANZHEN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-dashscope-key")
    for module_name in ("app.storage", "app.main", "storage", "main"):
        sys.modules.pop(module_name, None)
    import app.storage as storage  # noqa: WPS433
    importlib.reload(storage)
    import app.main as main  # noqa: WPS433

    importlib.reload(main)
    monkeypatch.setattr(main.detector, "API_KEY", "test-dashscope-key")
    monkeypatch.setattr(main.detector, "analyze", _stable_vlm_analyze)
    return TestClient(main.app)


@pytest.fixture
def developer_key_client(monkeypatch, tmp_path):
    monkeypatch.setenv("JIANZHEN_ACCESS_TOKEN", "admin-token")
    monkeypatch.setenv("JIANZHEN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("JIANZHEN_REQUIRE_DEVELOPER_API_KEY", "true")
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
        if api_key == "rg_sk_user1":
            return {"mode": "developer", "keyId": 101, "userId": 1, "scopes": ["detect", "reports"]}
        if api_key == "rg_sk_user2":
            return {"mode": "developer", "keyId": 202, "userId": 2, "scopes": ["detect", "reports"]}
        raise main.HTTPException(status_code=401, detail="API Key 缺失或无效")

    monkeypatch.setattr(main, "_verify_developer_key_sync", fake_verify)
    return TestClient(main.app)


def test_metrics_requires_token(client):
    unauth = client.get("/api/metrics")
    auth = client.get("/api/metrics", headers={"X-Jianzhen-Token": "test-token"})

    assert unauth.status_code == 401
    assert auth.status_code == 200


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
    assert "cacheHit" not in first.json()
    assert "cacheHit" not in other_user.json()
    assert "cacheHit" not in same_user.json()
    assert same_user.json()["fileMeta"]["name"] == "renamed-by-owner.png"
    assert same_user.json()["tokenUsage"] == {"promptTokens": 0, "completionTokens": 0, "totalTokens": 0}


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

    storage.put_cached_analysis("ttl-test", "ttl-sha", _forensic_analysis())

    assert storage.get_cached_analysis("ttl-test", "ttl-sha", max_age_seconds=60) is not None
    assert storage.get_cached_analysis("ttl-test", "ttl-sha", max_age_seconds=0) is None


def test_v1_session_unlocks_own_v2_history_but_not_admin_metrics(client, monkeypatch):
    import app.main as main  # noqa: WPS433

    def fake_session_user(request):
        if "session=valid" in request.headers.get("cookie", ""):
            return {"mode": "session", "userId": 7, "phone": "13800000000"}
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
            return {"mode": "session", "userId": 101, "phone": "13800000101"}
        if "session=user-b" in cookie:
            return {"mode": "session", "userId": 202, "phone": "13800000202"}
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
        lambda request: {"mode": "session", "userId": 303, "phone": "13800000303"},
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
        _vlm_analysis(source="vlm", verdict="unknown"),
        _vlm_analysis(source="vlm", verdict=""),
    ],
    ids=["mock-source", "maps-only-source", "unknown-source", "unknown-verdict", "empty-verdict"],
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
    assert response.json()["detail"] == "真实模型未返回可发布的明确结论"
    assert history.status_code == 200
    assert history.json()["total"] == 0


def test_analysis_cache_only_accepts_publishable_vlm_results(client):
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

    valid = _vlm_analysis(verdict="suspected_fake")
    storage.put_cached_analysis("document", "valid-vlm", valid)

    assert storage.get_cached_analysis("document", "valid-vlm") == valid


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


def test_v1_session_can_detect_when_developer_api_key_is_required(developer_key_client, monkeypatch):
    import app.main as main  # noqa: WPS433

    def fake_session_user(request):
        if "session=valid" in request.headers.get("cookie", ""):
            return {"mode": "session", "userId": 8, "phone": "13900000000"}
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
    other_user = developer_key_client.get(
        f"/api/report/{report_id}/download",
        headers={"X-RealGuard-Key": "rg_sk_user2"},
    )
    admin = developer_key_client.get(
        f"/api/report/{report_id}/download",
        headers={"X-Jianzhen-Token": "admin-token"},
    )

    assert detect.status_code == 200
    assert owner.status_code == 200
    assert other_user.status_code == 404
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


def test_report_export_includes_forensics_and_provenance_sections(client):
    detect = client.post(
        "/api/detect",
        files={"file": ("sample.txt", b"hello world from pytest", "text/plain")},
    )
    report_id = detect.json()["reportId"]

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
    assert response.content.startswith(b"%PDF-")
    assert len(response.content) > 1500


def test_history_artifacts_persist_into_history_and_download(client):
    detect = client.post(
        "/api/detect",
        files={"file": ("sample.txt", b"persist artifact flow", "text/plain")},
    )
    task_id = detect.json()["taskId"]
    report_id = detect.json()["reportId"]

    save = client.post(
        f"/api/history/{task_id}/artifacts",
        headers={"X-Jianzhen-Token": "test-token"},
        json={
            "forensics": {
                "verdict": "real",
                "confidence": 0.33,
                "summary": "未见明显异常。",
                "items": [{"key": "ela", "title": "压缩对齐分析", "status": "ok", "finding": "未见明显异常"}],
                "modelVersion": "huijian-forensic-maps-v1",
                "source": "maps-only",
            },
            "provenance": {
                "hasCredentials": False,
                "validationState": "none",
                "generator": None,
                "issuer": None,
                "signatureAlg": None,
                "isAiGenerated": None,
                "actions": [],
                "ingredients": [],
                "synthid": {"note": "未检测到 SynthID"},
            },
        },
    )
    listing = client.get("/api/history", headers={"X-Jianzhen-Token": "test-token"})
    history = client.get(f"/api/history/{task_id}", headers={"X-Jianzhen-Token": "test-token"})
    download = client.get(f"/api/report/{report_id}/download", headers={"X-Jianzhen-Token": "test-token"})

    assert save.status_code == 200
    assert listing.status_code == 200
    assert listing.json()["items"][0]["hasForensics"] is True
    assert listing.json()["items"][0]["hasProvenance"] is True
    assert history.status_code == 200
    payload = history.json()
    assert payload["forensics"]["summary"] == "未见明显异常。"
    assert payload["provenance"]["validationState"] == "none"
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("application/pdf")
    assert download.content.startswith(b"%PDF-")


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


def test_history_filters_and_counts_include_verdict_breakdown(client, monkeypatch):
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
    assert payload["filterCounts"]["real"] == 1
    assert payload["filterCounts"]["suspected"] == 1
    assert payload["filterCounts"]["highly"] == 1
    assert payload["filterCounts"]["unknownVerdict"] == 0
    assert unknown.status_code == 503
    assert len(real_only.json()["items"]) == 1
    assert real_only.json()["items"][0]["verdict"] == "real"
    assert len(suspected_only.json()["items"]) == 1
    assert suspected_only.json()["items"][0]["verdict"] == "suspected_fake"
    assert len(highly_only.json()["items"]) == 1
    assert highly_only.json()["items"][0]["verdict"] == "highly_suspected_fake"
    assert unknown_only.json()["items"] == []


def test_metrics_include_source_and_evidence_breakdown(client):
    detect = client.post(
        "/api/detect",
        files={"file": ("sample.txt", b"metrics evidence flow", "text/plain")},
    )
    task_id = detect.json()["taskId"]
    client.post(
        f"/api/history/{task_id}/artifacts",
        headers={"X-Jianzhen-Token": "test-token"},
        json={
            "forensics": {"summary": "done"},
            "provenance": {"validationState": "valid"},
        },
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
    assert payload["summary"]["analysisCacheVersion"] == "v7-real-results-only"


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
    assert detect_a.json()["cacheVersion"] == "v7-real-results-only"
    assert detect_a_cached.json()["cacheHit"] is True
    assert detect_a_cached.json()["cacheVersion"] == "v7-real-results-only"

    task_a = detect_a.json()["taskId"]
    task_b = detect_b.json()["taskId"]

    client.post(
        f"/api/history/{task_a}/artifacts",
        headers={"X-Jianzhen-Token": "test-token"},
        json={"forensics": {"summary": "done"}},
    )
    client.post(
        f"/api/history/{task_b}/artifacts",
        headers={"X-Jianzhen-Token": "test-token"},
        json={"provenance": {"validationState": "valid"}},
    )

    limited = client.get("/api/history?limit=1", headers={"X-Jianzhen-Token": "test-token"})
    by_source = client.get(
        f"/api/history?source=vlm&query={detect_b.json()['reportId']}",
        headers={"X-Jianzhen-Token": "test-token"},
    )
    by_model = client.get("/api/history?query=qwen3-vl-flash-watermark", headers={"X-Jianzhen-Token": "test-token"})
    by_cache_version = client.get("/api/history?query=%E7%BC%93%E5%AD%98%E7%89%88%E6%9C%AC%20v7-real-results-only", headers={"X-Jianzhen-Token": "test-token"})
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
    assert {item["cacheVersion"] for item in by_cache_version.json()["items"]} == {"v7-real-results-only"}

    assert by_query.status_code == 200
    assert by_query.json()["total"] >= 1
    assert any(item["reportId"] == detect_a.json()["reportId"] for item in by_query.json()["items"])

    assert by_evidence.status_code == 200
    assert by_evidence.json()["total"] >= 1
    evidence_item = next(item for item in by_evidence.json()["items"] if item["reportId"] == detect_b.json()["reportId"])
    assert evidence_item["visibleWatermarkProvider"] == "gemini"
    assert evidence_item["hasSynthid"] is True

    assert by_cache.status_code == 200
    assert by_cache.json()["total"] == 1
    assert by_cache.json()["filterCounts"]["cache"] >= 1
    assert by_cache.json()["items"][0]["reportId"] == detect_a_cached.json()["reportId"]
    assert by_cache.json()["items"][0]["cacheHit"] is True

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
