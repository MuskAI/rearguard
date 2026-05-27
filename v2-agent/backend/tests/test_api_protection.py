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
