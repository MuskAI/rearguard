from __future__ import annotations

import gzip
import hashlib
import os
from pathlib import Path
import sqlite3
import subprocess


ROOT = Path(__file__).resolve().parents[3]
BACKUP_SCRIPT = ROOT / "scripts" / "remote" / "backup_realguard.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def test_backup_script_creates_consistent_local_snapshot(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
    _write_executable(fake_bin / "sort", "#!/bin/sh\ncat\n")
    _write_executable(fake_bin / "mysqldump", "#!/bin/sh\nprintf '%s\\n' '-- deterministic test dump'\n")

    v2_db = tmp_path / "v2.sqlite3"
    traffic_db = tmp_path / "traffic.sqlite3"
    for database, value in ((v2_db, "v2"), (traffic_db, "traffic")):
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE evidence (value TEXT NOT NULL)")
            connection.execute("INSERT INTO evidence (value) VALUES (?)", (value,))

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "sample.txt").write_text("upload evidence", encoding="utf-8")
    backup_root = tmp_path / "backups"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "REALGUARD_BACKUP_ROOT": str(backup_root),
        "REALGUARD_BACKUP_RETENTION_DAYS": "14",
        "REALGUARD_DB_USER": "backup-user",
        "REALGUARD_DB_PASSWORD": "test-only",
        "REALGUARD_DB_NAME": "system",
        "REALGUARD_DETECTION_DB_USER": "backup-user",
        "REALGUARD_DETECTION_DB_PASSWORD": "test-only",
        "REALGUARD_DETECTION_DB_NAME": "image_detection",
        "JIANZHEN_DB_PATH": str(v2_db),
        "REALGUARD_TRAFFIC_CUMULATIVE_DB": str(traffic_db),
        "REALGUARD_UPLOADS_DIR": str(uploads),
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

    for line in (snapshot / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, relative_path = line.split(maxsplit=1)
        target = snapshot / relative_path.lstrip("*./")
        assert hashlib.sha256(target.read_bytes()).hexdigest() == digest
