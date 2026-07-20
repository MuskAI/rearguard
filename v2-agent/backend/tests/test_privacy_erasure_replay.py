from __future__ import annotations

import importlib.util
from pathlib import Path
import sqlite3
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]
REPLAY_PATH = ROOT / "scripts" / "remote" / "replay_privacy_erasure_tombstones.py"


def _load_replay_module():
    spec = importlib.util.spec_from_file_location("privacy_erasure_replay", REPLAY_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_restore_replay_removes_v2_content_from_an_older_database(monkeypatch, tmp_path):
    from app import privacy_erasure_ledger, storage

    database = tmp_path / "restored-v2.sqlite3"
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "DB_PATH", database)
    monkeypatch.setattr(storage, "_INITIALIZED", False)
    task_id = "restored-private-task"
    report_id = "restored-private-report"
    result = {
        "taskId": task_id,
        "reportId": report_id,
        "createdAt": "2026-07-20T08:00:00+00:00",
        "fileMeta": {
            "name": "private.png",
            "type": "image",
            "size": "1KB",
            "resolution": "10x10",
        },
        "verdict": "unknown",
        "decisionStatus": "review_only",
        "decisionAuthority": "none",
        "reviewRequired": True,
        "confidence": 0.0,
        "modelVersion": "pytest",
        "source": "pytest",
    }
    storage.put_history(
        result,
        sha256="f" * 64,
        file_size=1024,
        thumbnail=None,
        actor={"userId": "user", "accountUuid": "tenant", "keyId": "key"},
    )
    storage.put_history_artifacts(task_id, forensics={"summary": "private"})
    privacy_erasure_ledger.record_tombstone(
        "jianzhen-v2",
        "history",
        task_id,
        report_id,
    )
    replay = _load_replay_module()
    rows = replay.read_tombstones(privacy_erasure_ledger.ledger_path())

    assert replay.replay_v2(rows, database) == 1
    assert storage.get_history(task_id) is None
    with storage._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM history_artifacts"
        ).fetchone()[0] == 0
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_live_ledger_must_cover_every_tombstone_in_snapshot(monkeypatch, tmp_path):
    from app import privacy_erasure_ledger

    replay = _load_replay_module()
    privacy_erasure_ledger.record_tombstone(
        "jianzhen-v2", "history", "task-old", "report-old"
    )
    live_path = privacy_erasure_ledger.ledger_path()
    snapshot_path = tmp_path / "snapshot-ledger.sqlite3"
    with sqlite3.connect(live_path) as source, sqlite3.connect(snapshot_path) as target:
        source.backup(target)
    privacy_erasure_ledger.record_tombstone(
        "jianzhen-v2", "history", "task-new", "report-new"
    )

    live = replay.read_tombstones(live_path)
    snapshot = replay.read_tombstones(snapshot_path)

    replay.ensure_snapshot_is_covered(live, snapshot)
    try:
        replay.ensure_snapshot_is_covered(snapshot, live)
    except RuntimeError as exc:
        assert "does not cover" in str(exc)
    else:
        raise AssertionError("an older ledger must not cover a newer snapshot")


def test_v2_replay_rejects_missing_report_id_without_touching_audit_rows(tmp_path):
    replay = _load_replay_module()
    database = tmp_path / "restored-v2.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE request_events (path TEXT, client_ip TEXT, user_agent TEXT)"
        )
        connection.execute(
            "INSERT INTO request_events VALUES ('/api/unrelated', '192.0.2.9', 'agent')"
        )
    malformed = [{
        "source_system": "jianzhen-v2",
        "resource_kind": "history",
        "primary_id": "task-without-report",
        "secondary_id": None,
        "tombstone_id": "malformed",
    }]

    with pytest.raises(RuntimeError, match="secondary id"):
        replay.replay_v2(malformed, database)

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT * FROM request_events").fetchone() == (
            "/api/unrelated",
            "192.0.2.9",
            "agent",
        )
