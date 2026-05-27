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
    for module_name in ("app.storage", "app.main"):
        sys.modules.pop(module_name, None)
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
    assert "byDay" in payload
    assert "evidence" in payload
    assert payload["evidence"]["forensicsCompleted"] >= 1
    assert payload["evidence"]["provenanceCompleted"] >= 1
    assert payload["sourceVerdict"]
    assert "sources" in payload["byDay"][0]
