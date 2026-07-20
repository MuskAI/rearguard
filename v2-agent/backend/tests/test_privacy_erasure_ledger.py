from __future__ import annotations

import os
import sqlite3
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_tombstone_is_idempotent_append_only_and_private():
    from app import privacy_erasure_ledger

    first = privacy_erasure_ledger.record_tombstone(
        "jianzhen-v2",
        "history",
        "task-private-1",
        "report-private-1",
    )
    replay = privacy_erasure_ledger.record_tombstone(
        "jianzhen-v2",
        "history",
        "task-private-1",
        "report-private-1",
    )

    assert replay == first
    path = privacy_erasure_ledger.ledger_path()
    assert path.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM privacy_erasure_tombstones"
        ).fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE privacy_erasure_tombstones SET primary_id = 'changed'"
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("DELETE FROM privacy_erasure_tombstones")


def test_ledger_rejects_symlink_target(monkeypatch, tmp_path):
    from app import privacy_erasure_ledger

    target = tmp_path / "outside.sqlite3"
    target.write_bytes(b"outside")
    link = tmp_path / "ledger.sqlite3"
    link.symlink_to(target)
    monkeypatch.setenv("REALGUARD_PRIVACY_ERASURE_LEDGER_PATH", str(link))

    with pytest.raises(
        privacy_erasure_ledger.PrivacyErasureLedgerError,
        match="symlink",
    ):
        privacy_erasure_ledger.record_tombstone(
            "jianzhen-v2",
            "history",
            "task-private-2",
        )
    assert target.read_bytes() == b"outside"


def test_tombstone_identifier_contains_no_raw_filename_or_user_identity():
    from app import privacy_erasure_ledger

    tombstone = privacy_erasure_ledger.record_tombstone(
        "realguard-v1",
        "image-history",
        42,
    )

    assert tombstone["primaryId"] == "42"
    assert tombstone["secondaryId"] is None
    assert "filename" not in str(tombstone).lower()
    assert "owner" not in str(tombstone).lower()
