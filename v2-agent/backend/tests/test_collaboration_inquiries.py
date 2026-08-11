from __future__ import annotations

import importlib
from pathlib import Path
import sys

from fastapi.testclient import TestClient
import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def collaboration_client(monkeypatch, tmp_path):
    monkeypatch.setenv("JIANZHEN_ENV", "production")
    monkeypatch.setenv("JIANZHEN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("JIANZHEN_ADMIN_ACCESS_TOKEN", "collaboration-admin-token")
    monkeypatch.setenv("JIANZHEN_CONSENT_AUDIT_SALT", "collaboration-storage-test-secret-32")
    monkeypatch.setenv("JIANZHEN_REPORT_SHARE_SECRET", "collaboration-report-share-secret-32")
    for module_name in ("app.storage", "app.main", "storage", "main"):
        sys.modules.pop(module_name, None)
    import app.storage as storage  # noqa: WPS433
    importlib.reload(storage)
    import app.main as main  # noqa: WPS433
    importlib.reload(main)
    monkeypatch.setattr(main, "_session_access_granted", lambda _request: None)
    main._COLLABORATION_REQUESTS.clear()
    return TestClient(main.app, client=("127.0.0.1", 50000)), main, storage


def _headers(token: str, key: str = "cooperation-request-0001") -> dict[str, str]:
    return {
        "X-Huijian-CSRF": token,
        "Origin": "https://www.rrreal.cn",
        "Sec-Fetch-Site": "same-origin",
        "Idempotency-Key": key,
        "User-Agent": "Mozilla/5.0 collaboration-test",
    }


def _payload(**overrides) -> dict:
    return {
        "collaborationType": "research",
        "name": "测试伙伴",
        "organization": "内容真实性实验室",
        "contact": "partner@example.com",
        "message": "希望使用一批授权样本，共同验证模型在真实传播场景中的泛化能力。",
        "website": "",
        "privacyAccepted": True,
        **overrides,
    }


def test_collaboration_inquiry_is_csrf_protected_and_persisted_without_raw_ip(collaboration_client):
    client, main, storage = collaboration_client
    token = "collaboration-csrf-token-1234567890"
    client.cookies.set(main.SESSION_CSRF_COOKIE, token)

    missing_csrf = client.post("/api/collaboration-inquiries", json=_payload())
    cross_site = client.post(
        "/api/collaboration-inquiries",
        json=_payload(),
        headers={**_headers(token), "Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
    )
    accepted = client.post(
        "/api/collaboration-inquiries",
        json=_payload(),
        headers=_headers(token),
    )

    assert missing_csrf.status_code == 403
    assert cross_site.status_code == 403
    assert accepted.status_code == 201
    assert accepted.json()["inquiryId"].startswith("coop-")
    with storage._connect() as connection:
        row = connection.execute("SELECT * FROM collaboration_inquiries").fetchone()
    assert row["contact"] == "partner@example.com"
    assert len(row["source_hash"]) == 64
    assert "127.0.0.1" not in str(dict(row))


def test_collaboration_inquiry_is_idempotent_and_available_to_local_admin(collaboration_client):
    client, main, storage = collaboration_client
    token = "collaboration-csrf-token-1234567890"
    client.cookies.set(main.SESSION_CSRF_COOKIE, token)
    headers = _headers(token, "cooperation-idempotent-0001")

    first = client.post("/api/collaboration-inquiries", json=_payload(), headers=headers)
    repeated = client.post("/api/collaboration-inquiries", json=_payload(), headers=headers)
    conflict = client.post(
        "/api/collaboration-inquiries",
        json=_payload(message="这是另一份完全不同的合作说明，不能复用同一个提交标识。"),
        headers=headers,
    )
    admin = client.get(
        "/api/admin/collaboration-inquiries",
        headers={"Authorization": "Bearer collaboration-admin-token"},
    )

    assert first.status_code == 201
    assert repeated.status_code == 201
    assert repeated.json()["inquiryId"] == first.json()["inquiryId"]
    assert conflict.status_code == 409
    with storage._connect() as connection:
        count = connection.execute("SELECT COUNT(*) AS total FROM collaboration_inquiries").fetchone()["total"]
    assert count == 1
    assert admin.status_code == 200
    assert admin.json()["total"] == 1
    assert admin.json()["items"][0]["message"] == _payload()["message"]


@pytest.mark.parametrize(
    ("payload", "expected_status"),
    [
        (_payload(privacyAccepted=False), 428),
        (_payload(collaborationType="invalid"), 422),
        (_payload(message="太短"), 422),
    ],
)
def test_collaboration_inquiry_validates_required_fields(collaboration_client, payload, expected_status):
    client, main, _storage = collaboration_client
    token = "collaboration-csrf-token-1234567890"
    client.cookies.set(main.SESSION_CSRF_COOKIE, token)
    response = client.post(
        "/api/collaboration-inquiries",
        json=payload,
        headers=_headers(token, f"validation-{expected_status}-{payload.get('collaborationType')}"),
    )
    assert response.status_code == expected_status


def test_collaboration_honeypot_is_acknowledged_without_storage(collaboration_client):
    client, main, storage = collaboration_client
    token = "collaboration-csrf-token-1234567890"
    client.cookies.set(main.SESSION_CSRF_COOKIE, token)
    response = client.post(
        "/api/collaboration-inquiries",
        json=_payload(website="https://spam.example"),
        headers=_headers(token, "honeypot-request-0001"),
    )
    assert response.status_code == 201
    with storage._connect() as connection:
        count = connection.execute("SELECT COUNT(*) AS total FROM collaboration_inquiries").fetchone()["total"]
    assert count == 0


def test_collaboration_inquiry_rate_limit_returns_retry_guidance(collaboration_client):
    client, main, _storage = collaboration_client
    token = "collaboration-csrf-token-1234567890"
    client.cookies.set(main.SESSION_CSRF_COOKIE, token)
    for index in range(main.COLLABORATION_RATE_LIMIT):
        response = client.post(
            "/api/collaboration-inquiries",
            json=_payload(contact=f"partner-{index}@example.com"),
            headers=_headers(token, f"rate-limit-request-{index:04d}"),
        )
        assert response.status_code == 201
    blocked = client.post(
        "/api/collaboration-inquiries",
        json=_payload(contact="blocked@example.com"),
        headers=_headers(token, "rate-limit-request-blocked"),
    )
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1
