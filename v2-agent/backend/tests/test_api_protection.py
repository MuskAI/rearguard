from pathlib import Path
import importlib
import sys

from fastapi.testclient import TestClient
import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("JIANZHEN_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("JIANZHEN_DATA_DIR", str(tmp_path))
    for module_name in ("app.storage", "app.main", "storage", "main"):
        sys.modules.pop(module_name, None)
    import app.storage as storage  # noqa: WPS433
    importlib.reload(storage)
    import app.main as main  # noqa: WPS433

    importlib.reload(main)
    return TestClient(main.app)


def test_metrics_requires_token(client):
    unauth = client.get("/api/metrics")
    auth = client.get("/api/metrics", headers={"X-Jianzhen-Token": "test-token"})

    assert unauth.status_code == 401
    assert auth.status_code == 200


def test_health_exposes_access_protection(client):
    response = client.get("/api/health")
    payload = response.json()

    assert response.status_code == 200
    assert payload["accessProtectionEnabled"] is True
    assert payload["analysisCacheVersion"] == "v6-low-ela-weight"
    assert "/api/report/{report_id}/download" in payload["protectedEndpoints"]
    assert "/api/report/{report_id}/export" in payload["protectedEndpoints"]


def test_report_download_returns_attachment_html(client):
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
    assert report_id in response.text
    assert "鉴真 AI 鉴伪鉴定报告" in response.text


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
    assert "可解释性取证分析" in response.text
    assert "频域与光照存在可疑矛盾" in response.text
    assert "内容凭证验证（C2PA）" in response.text
    assert "OpenAI Images" in response.text


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
                "modelVersion": "mock",
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
    assert "可解释性取证分析" in download.text
    assert "内容凭证验证（C2PA）" in download.text


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
            "modelVersion": "mock",
            "source": "mock",
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
            "modelVersion": "mock",
            "source": "mock",
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
            "source": "unknown",
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
    client.post("/api/detect", files={"file": ("unknown.png", b"verdict-d", "image/png")})

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
    assert payload["filterCounts"]["unknownVerdict"] == 1
    assert len(real_only.json()["items"]) == 1
    assert real_only.json()["items"][0]["verdict"] == "real"
    assert len(suspected_only.json()["items"]) == 1
    assert suspected_only.json()["items"][0]["verdict"] == "suspected_fake"
    assert len(highly_only.json()["items"]) == 1
    assert highly_only.json()["items"][0]["verdict"] == "highly_suspected_fake"
    assert len(unknown_only.json()["items"]) == 1
    assert unknown_only.json()["items"][0]["verdict"] == "unknown"


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
    assert payload["summary"]["analysisCacheVersion"] == "v6-low-ela-weight"


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
            "modelVersion": "mock-model",
            "source": "mock",
            "synthid": {"detected": True},
            "visibleWatermark": {"detected": True, "provider": "gemini"},
        },
        {
            "verdict": "suspected_fake",
            "confidence": 0.66,
            "dimensions": [],
            "regions": [],
            "explanation": "仅证据图模式。",
            "modelVersion": "maps-only-model",
            "source": "maps-only",
            "synthid": {"detected": False},
            "visibleWatermark": {"detected": False, "provider": None},
        },
        {
            "verdict": "real",
            "confidence": 0.55,
            "dimensions": [],
            "regions": [],
            "explanation": "未知来源模式。",
            "modelVersion": "unknown-model",
            "source": "unknown",
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
    assert detect_a.json()["cacheVersion"] == "v6-low-ela-weight"
    assert detect_a_cached.json()["cacheHit"] is True
    assert detect_a_cached.json()["cacheVersion"] == "v6-low-ela-weight"

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
        f"/api/history?source=mock&query={detect_b.json()['reportId']}",
        headers={"X-Jianzhen-Token": "test-token"},
    )
    by_maps_only = client.get(
        "/api/history?source=maps-only&query=%E4%BB%85%E8%AF%81%E6%8D%AE%E5%9B%BE",
        headers={"X-Jianzhen-Token": "test-token"},
    )
    by_unknown = client.get(
        "/api/history?source=unknown&query=%E6%9C%AA%E7%9F%A5%E6%9D%A5%E6%BA%90",
        headers={"X-Jianzhen-Token": "test-token"},
    )
    by_model = client.get("/api/history?query=mock-model", headers={"X-Jianzhen-Token": "test-token"})
    by_cache_version = client.get("/api/history?query=%E7%BC%93%E5%AD%98%E7%89%88%E6%9C%AC%20v6-low-ela-weight", headers={"X-Jianzhen-Token": "test-token"})
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
    assert by_source.json()["items"][0]["source"] == "mock"
    assert by_source.json()["items"][0]["modelVersion"] == "mock-model"
    assert by_source.json()["items"][0]["reportId"] == detect_b.json()["reportId"]
    assert by_source.json()["filterCounts"]["maps-only"] == 0

    assert by_maps_only.status_code == 200
    assert by_maps_only.json()["total"] == 1
    assert by_maps_only.json()["filterCounts"]["maps-only"] == 1
    assert by_maps_only.json()["filterCounts"]["unknown"] == 0
    assert by_maps_only.json()["items"][0]["source"] == "maps-only"
    assert by_maps_only.json()["items"][0]["reportId"] == detect_c.json()["reportId"]

    assert by_unknown.status_code == 200
    assert by_unknown.json()["total"] == 1
    assert by_unknown.json()["filterCounts"]["unknown"] == 1
    assert by_unknown.json()["items"][0]["source"] == "unknown"
    assert by_unknown.json()["items"][0]["reportId"] == detect_d.json()["reportId"]

    assert by_model.status_code == 200
    assert by_model.json()["total"] == 1
    assert by_model.json()["items"][0]["modelVersion"] == "mock-model"
    assert by_model.json()["items"][0]["reportId"] == detect_b.json()["reportId"]

    assert by_cache_version.status_code == 200
    assert by_cache_version.json()["total"] >= 5
    assert {item["cacheVersion"] for item in by_cache_version.json()["items"]} == {"v6-low-ela-weight"}

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
