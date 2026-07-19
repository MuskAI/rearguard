from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile


ROOT = Path(__file__).resolve().parents[3]
RESTORE_SCRIPT = ROOT / "scripts" / "remote" / "verify_restore_realguard.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _write_sqlite(path: Path, value: str) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE sample (value TEXT NOT NULL)")
        connection.execute("INSERT INTO sample (value) VALUES (?)", (value,))


def _write_archive(path: Path, source: Path) -> None:
    with tarfile.open(path, "w:gz") as archive:
        archive.add(source, arcname=".")


def test_restore_script_rehydrates_isolated_snapshot(tmp_path):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "MANIFEST").write_text("created_at=test\n", encoding="utf-8")
    for label, database in (("system", "system"), ("detection", "image_detection")):
        sql = (
            f"CREATE DATABASE /*!32312 IF NOT EXISTS*/ `{database}`;\n"
            f"USE `{database}`;\n"
            "CREATE TABLE sample (id INT PRIMARY KEY);\n"
            "INSERT INTO sample VALUES (1);\n"
        )
        with gzip.open(snapshot / f"mysql-{label}.sql.gz", "wt", encoding="utf-8") as output:
            output.write(sql)

    _write_sqlite(snapshot / "jianzhen-v2.sqlite3", "v2")
    _write_sqlite(snapshot / "traffic-cumulative.sqlite3", "traffic")
    upload_source = tmp_path / "upload-source"
    upload_source.mkdir()
    (upload_source / "sample.png").write_bytes(b"image")
    evidence_source = tmp_path / "evidence-source"
    evidence_source.mkdir()
    (evidence_source / "image-1.manifest.json").write_text("{}", encoding="utf-8")
    _write_archive(snapshot / "uploads.tgz", upload_source)
    _write_archive(snapshot / "evidence-manifests.tgz", evidence_source)

    checksum_lines = []
    for path in sorted(snapshot.iterdir()):
        if path.name == "SHA256SUMS":
            continue
        checksum_lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  ./{path.name}")
    (snapshot / "SHA256SUMS").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    mysql_stdin = tmp_path / "mysql-stdin.sql"
    _write_executable(
        fake_bin / "mysql",
        """#!/bin/sh
case "$*" in
  *information_schema.schemata*) printf '0\\n';;
  *@@GLOBAL.event_scheduler*) printf 'OFF\\n';;
  *information_schema.tables*) printf '3\\n';;
  *) cat >>"$MYSQL_STDIN_LOG";;
esac
""",
    )
    _write_executable(fake_bin / "mysqlcheck", "#!/bin/sh\nexit 0\n")
    report_root = tmp_path / "reports"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "MYSQL_BIN": str(fake_bin / "mysql"),
        "MYSQLCHECK_BIN": str(fake_bin / "mysqlcheck"),
        "MYSQL_STDIN_LOG": str(mysql_stdin),
        "PYTHON_BIN": sys.executable,
        "REALGUARD_RESTORE_ALLOW_NON_ROOT": "1",
        "REALGUARD_RESTORE_REPORT_ROOT": str(report_root),
        "REALGUARD_RESTORE_WORK_ROOT": str(tmp_path),
    }

    completed = subprocess.run(
        ["bash", str(RESTORE_SCRIPT), str(snapshot)],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    reports = list(report_root.glob("*.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["status"] == "passed"
    assert report["mysql"]["systemTables"] == 3
    assert report["sqlite"]["v2"]["integrity"] == "ok"
    assert report["archives"]["uploads"]["files"] == 1
    restored_sql = mysql_stdin.read_text(encoding="utf-8")
    assert "CREATE DATABASE /*!32312 IF NOT EXISTS*/ `rg_restore_system_" in restored_sql
    assert "CREATE DATABASE /*!32312 IF NOT EXISTS*/ `rg_restore_detection_" in restored_sql
    assert "CREATE DATABASE /*!32312 IF NOT EXISTS*/ `system`;" not in restored_sql
