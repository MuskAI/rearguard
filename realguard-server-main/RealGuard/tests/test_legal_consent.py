from pathlib import Path

import pytest
from flask import Flask

from imagedetection import legal_documents
from imagedetection.views import detection, login


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LEGAL_ROOT = PROJECT_ROOT / "frontend" / "public" / "legal"


def test_legal_document_identities_match_published_files():
    assert legal_documents.identity_from_file(LEGAL_ROOT / "terms.html") == legal_documents.TERMS
    assert legal_documents.identity_from_file(LEGAL_ROOT / "privacy.html") == legal_documents.PRIVACY
    assert login.TERMS_VERSION == (
        f"{legal_documents.TERMS.version}+{legal_documents.PRIVACY.version}"
    )
    assert login.TERMS_SHA256 == legal_documents.TERMS.sha256
    assert login.PRIVACY_SHA256 == legal_documents.PRIVACY.sha256


def test_stale_deployment_override_is_rejected(monkeypatch):
    monkeypatch.setenv("REALGUARD_PRIVACY_SHA256", "0" * 64)

    with pytest.raises(RuntimeError, match="与已发布法律文档不一致"):
        legal_documents._assert_deployment_override(
            "REALGUARD_PRIVACY_SHA256",
            legal_documents.PRIVACY.sha256,
        )


def test_existing_consent_table_gets_per_document_version_columns(monkeypatch):
    statements = []

    def fake_sql(sql, params=None, fetch=True):
        normalized = " ".join(sql.split())
        statements.append((normalized, params, fetch))
        if normalized.startswith("SHOW COLUMNS"):
            return []
        return 1

    monkeypatch.setattr(login, "_CONSENT_STORAGE_READY", False)
    monkeypatch.setattr(login, "excute_sql", fake_sql)

    assert login._ensure_consent_event_storage() is True
    alters = [sql for sql, _params, _fetch in statements if sql.startswith("ALTER TABLE")]
    assert any("`terms_version`" in sql for sql in alters)
    assert any("`privacy_version`" in sql for sql in alters)


def test_record_terms_acceptance_persists_both_document_versions(monkeypatch):
    statements = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, sql, params=None):
            statements.append((" ".join(sql.split()), params))

        def fetchone(self):
            return {"Userid": 7}

    class Connection:
        def begin(self):
            return None

        def cursor(self):
            return Cursor()

        def commit(self):
            return None

        def rollback(self):
            raise AssertionError("consent transaction must not roll back")

        def close(self):
            return None

    monkeypatch.setattr(login, "_ensure_user_account_columns", lambda: True)
    monkeypatch.setattr(login, "_ensure_consent_event_storage", lambda: True)
    monkeypatch.setattr(login, "get_db_connection", Connection)

    assert login._record_terms_acceptance("13800000000", channel="test") is True

    insert_params = next(params for sql, params in statements if "INSERT INTO consent_events" in sql)
    assert insert_params[2:7] == (
        legal_documents.CONSENT_VERSION,
        legal_documents.TERMS.version,
        legal_documents.PRIVACY.version,
        legal_documents.TERMS.sha256,
        legal_documents.PRIVACY.sha256,
    )
    update_params = next(params for sql, params in statements if "UPDATE `user`" in sql)
    assert update_params == (legal_documents.CONSENT_VERSION, 7)


def test_guest_upload_consent_is_verified_and_pseudonymously_recorded(monkeypatch):
    captured = {}
    monkeypatch.setattr(login, "_ensure_consent_event_storage", lambda: True)
    monkeypatch.setattr(login, "_trusted_client_ip", lambda: "203.0.113.9")
    monkeypatch.setattr(
        detection,
        "excute_sql",
        lambda sql, params=None, fetch=True: captured.update(sql=sql, params=params) or 1,
    )
    flask_app = Flask(__name__)
    with flask_app.test_request_context(
        "/image_upload/detect_async",
        method="POST",
        data={
            "upload_consent": "1",
            "consent_version": legal_documents.CONSENT_VERSION,
            "terms_sha256": legal_documents.TERMS.sha256,
            "privacy_sha256": legal_documents.PRIVACY.sha256,
        },
        headers={"User-Agent": "consent-test"},
    ):
        error = detection._record_guest_upload_consent(
            {"openid": "guest-private-id"},
            b"uploaded-image",
            "guest-consent-001",
            "test",
        )

    assert error is None
    params = captured["params"]
    assert len(params[0]) == 64
    assert params[0] != "guest-private-id"
    assert params[1:6] == (
        legal_documents.CONSENT_VERSION,
        legal_documents.TERMS.version,
        legal_documents.PRIVACY.version,
        legal_documents.TERMS.sha256,
        legal_documents.PRIVACY.sha256,
    )
    assert len(params[7]) == 64
    assert len(params[8]) == 64
