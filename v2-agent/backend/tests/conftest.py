from __future__ import annotations

import base64

import pytest


@pytest.fixture(autouse=True)
def explicit_test_evidence_signing_key(monkeypatch, tmp_path):
    """Tests opt in to an explicit deterministic Ed25519 seed."""
    seed = bytes(range(32))
    monkeypatch.setenv(
        "JIANZHEN_EVIDENCE_SIGNING_PRIVATE_KEY",
        "base64:" + base64.b64encode(seed).decode("ascii"),
    )
    monkeypatch.delenv("JIANZHEN_EVIDENCE_SIGNING_PRIVATE_KEY_FILE", raising=False)
    monkeypatch.setenv("JIANZHEN_EVIDENCE_SIGNING_KEY_ID", "pytest-evidence-2026-01")
    monkeypatch.setenv("JIANZHEN_EVIDENCE_VERIFY_PUBLIC_KEYS", "{}")
    monkeypatch.setenv(
        "REALGUARD_PRIVACY_ERASURE_LEDGER_PATH",
        str(tmp_path / "privacy-erasure" / "tombstones.sqlite3"),
    )
