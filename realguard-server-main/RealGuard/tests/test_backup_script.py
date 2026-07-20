from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
BACKUP_SCRIPT = ROOT / "scripts" / "remote" / "backup_realguard.sh"
RESTORE_SCRIPT = ROOT / "scripts" / "remote" / "verify_restore_realguard.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def test_backup_script_creates_consistent_local_snapshot(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
    _write_executable(fake_bin / "sort", "#!/bin/sh\ncat\n")
    _write_executable(
        fake_bin / "mysqldump",
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >>\"$MYSQLDUMP_ARGS_LOG\"\nprintf '%s\\n' '-- deterministic test dump'\n",
    )
    _write_executable(
        fake_bin / "rclone",
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >>\"$RCLONE_ARGS_LOG\"\n",
    )

    v2_db = tmp_path / "v2.sqlite3"
    traffic_db = tmp_path / "traffic.sqlite3"
    for database, value in ((v2_db, "v2"), (traffic_db, "traffic")):
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE evidence (value TEXT NOT NULL)")
            connection.execute("INSERT INTO evidence (value) VALUES (?)", (value,))

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "sample.txt").write_text("upload evidence", encoding="utf-8")
    evidence_manifests = tmp_path / "evidence-manifests"
    evidence_manifests.mkdir()
    (evidence_manifests / "image-7.manifest.json").write_text(
        '{"manifest":{"record_id":"7"}}', encoding="utf-8"
    )
    governance_evidence = tmp_path / "legacy-governance-evidence"
    governance_evidence.mkdir()
    (governance_evidence / "case-7.json").write_text('{"case":7}', encoding="utf-8")
    backup_root = tmp_path / "backups"
    mysqldump_args_log = tmp_path / "mysqldump-args.log"
    rclone_args_log = tmp_path / "rclone-args.log"
    backup_status = tmp_path / "backup-status.json"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "REALGUARD_BACKUP_ROOT": str(backup_root),
        "REALGUARD_BACKUP_RETENTION_DAYS": "14",
        "REALGUARD_BACKUP_RCLONE_REMOTE": "archive:realguard",
        "REALGUARD_BACKUP_STATUS_FILE": str(backup_status),
        "REALGUARD_DB_USER": "backup-user",
        "REALGUARD_DB_PASSWORD": "test-only",
        "REALGUARD_DB_NAME": "system",
        "REALGUARD_DETECTION_DB_USER": "backup-user",
        "REALGUARD_DETECTION_DB_PASSWORD": "test-only",
        "REALGUARD_DETECTION_DB_NAME": "image_detection",
        "JIANZHEN_DB_PATH": str(v2_db),
        "REALGUARD_TRAFFIC_CUMULATIVE_DB": str(traffic_db),
        "REALGUARD_UPLOADS_DIR": str(uploads),
        "REALGUARD_EVIDENCE_SNAPSHOT_ROOT": str(evidence_manifests),
        "REALGUARD_LEGACY_EVIDENCE_ROOT": str(governance_evidence),
        "MYSQLDUMP_ARGS_LOG": str(mysqldump_args_log),
        "RCLONE_ARGS_LOG": str(rclone_args_log),
        "PYTHON_BIN": sys.executable,
    }

    completed = subprocess.run(
        ["bash", str(BACKUP_SCRIPT)],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    latest = backup_root / "latest"
    assert latest.is_symlink()
    snapshot = latest.resolve()
    assert gzip.decompress((snapshot / "mysql-system.sql.gz").read_bytes()).startswith(b"-- deterministic")
    assert gzip.decompress((snapshot / "mysql-detection.sql.gz").read_bytes()).startswith(b"-- deterministic")
    assert (snapshot / "jianzhen-v2.sqlite3").is_file()
    assert (snapshot / "traffic-cumulative.sqlite3").is_file()
    assert (snapshot / "uploads.tgz").is_file()
    assert (snapshot / "evidence-manifests.tgz").is_file()
    assert (snapshot / "legacy-governance-evidence.tgz").is_file()
    assert f"evidence_manifest_directory={evidence_manifests}" in (
        snapshot / "MANIFEST"
    ).read_text(encoding="utf-8")
    mysqldump_calls = mysqldump_args_log.read_text(encoding="utf-8").splitlines()
    assert len(mysqldump_calls) == 2
    assert all("--no-tablespaces" in call for call in mysqldump_calls)
    rclone_calls = rclone_args_log.read_text(encoding="utf-8").splitlines()
    assert len(rclone_calls) == 2
    assert rclone_calls[0].startswith("copy ") and rclone_calls[0].endswith(" --immutable")
    assert rclone_calls[1].startswith("check ") and rclone_calls[1].endswith(" --one-way")
    status = json.loads(backup_status.read_text(encoding="utf-8"))
    assert status["state"] == "success"
    assert status["snapshot"] == snapshot.name
    assert status["offsiteConfigured"] is True
    assert status["offsiteVerified"] is True
    assert status["lastSuccessAt"]
    assert status["lastOffsiteSuccessAt"]

    for line in (snapshot / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, relative_path = line.split(maxsplit=1)
        target = snapshot / relative_path.lstrip("*./")
        assert hashlib.sha256(target.read_bytes()).hexdigest() == digest


def test_backup_script_records_failed_run_without_erasing_last_success(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
    _write_executable(fake_bin / "mysqldump", "#!/bin/sh\nexit 9\n")
    backup_root = tmp_path / "backups"
    backup_status = tmp_path / "backup-status.json"
    backup_status.write_text(
        json.dumps({
            "schemaVersion": 1,
            "state": "success",
            "lastSuccessAt": "2026-07-19T00:00:00Z",
            "lastOffsiteSuccessAt": "2026-07-19T00:00:00Z",
        }),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "REALGUARD_BACKUP_ROOT": str(backup_root),
        "REALGUARD_BACKUP_STATUS_FILE": str(backup_status),
        "REALGUARD_DB_USER": "backup-user",
        "REALGUARD_DB_PASSWORD": "test-only",
        "REALGUARD_DETECTION_DB_USER": "backup-user",
        "REALGUARD_DETECTION_DB_PASSWORD": "test-only",
        "PYTHON_BIN": sys.executable,
    }

    completed = subprocess.run(
        ["bash", str(BACKUP_SCRIPT)],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    status = json.loads(backup_status.read_text(encoding="utf-8"))
    assert status["state"] == "failed"
    assert status["lastError"] == "backup_run_failed"
    assert status["lastSuccessAt"] == "2026-07-19T00:00:00Z"
    assert status["lastOffsiteSuccessAt"] == "2026-07-19T00:00:00Z"


def test_strict_offsite_mode_fails_before_changing_latest(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
    backup_root = tmp_path / "backups"
    old_snapshot = backup_root / "20260719T000000Z"
    old_snapshot.mkdir(parents=True)
    (backup_root / "latest").symlink_to(old_snapshot.name)
    backup_status = tmp_path / "backup-status.json"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "REALGUARD_BACKUP_ROOT": str(backup_root),
        "REALGUARD_BACKUP_STATUS_FILE": str(backup_status),
        "REALGUARD_BACKUP_REQUIRE_OFFSITE": "1",
        "REALGUARD_BACKUP_RCLONE_REMOTE": "",
        "PYTHON_BIN": sys.executable,
    }

    completed = subprocess.run(
        ["bash", str(BACKUP_SCRIPT)],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert (backup_root / "latest").resolve() == old_snapshot.resolve()
    status = json.loads(backup_status.read_text(encoding="utf-8"))
    assert status["state"] == "failed"
    assert status["offsiteConfigured"] is False


def test_restore_drill_records_early_snapshot_failure(tmp_path):
    status_path = tmp_path / "restore-status.json"
    missing_snapshot = tmp_path / "missing"
    completed = subprocess.run(
        ["bash", str(RESTORE_SCRIPT), str(missing_snapshot)],
        env={
            **os.environ,
            "REALGUARD_RESTORE_ALLOW_NON_ROOT": "1",
            "REALGUARD_RESTORE_STATUS_FILE": str(status_path),
            "PYTHON_BIN": sys.executable,
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["state"] == "failed"
    assert status["lastError"] == "backup_snapshot_not_found"
