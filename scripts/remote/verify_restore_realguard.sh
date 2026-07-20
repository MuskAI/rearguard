#!/usr/bin/env bash
set -euo pipefail

umask 077

BACKUP_ROOT="${REALGUARD_BACKUP_ROOT:-/var/backups/realguard}"
SNAPSHOT_INPUT="${1:-$BACKUP_ROOT/latest}"
REPORT_ROOT="${REALGUARD_RESTORE_REPORT_ROOT:-$BACKUP_ROOT/restore-drills}"
STATUS_FILE="${REALGUARD_RESTORE_STATUS_FILE:-/opt/realguard-data/restore-drill-status.json}"
WORK_ROOT="${REALGUARD_RESTORE_WORK_ROOT:-/var/tmp}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
MYSQL_BIN="${MYSQL_BIN:-mysql}"
MYSQLCHECK_BIN="${MYSQLCHECK_BIN:-mysqlcheck}"
SYSTEM_DATABASE="${REALGUARD_DB_NAME:-system}"
DETECTION_DATABASE="${REALGUARD_DETECTION_DB_NAME:-image_detection}"
status_snapshot="$SNAPSHOT_INPUT"

write_status() {
  local state="$1"
  local error="${2:-}"
  local status_parent
  status_parent="$(dirname "$STATUS_FILE")"
  install -d -m 755 "$status_parent"
  RESTORE_STATUS_STATE="$state" \
  RESTORE_STATUS_SNAPSHOT="$status_snapshot" \
  RESTORE_STATUS_ERROR="$error" \
    "$PYTHON_BIN" - "$STATUS_FILE" <<'PY'
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timezone

target = Path(sys.argv[1])
previous = {}
try:
    if target.is_file() and not target.is_symlink() and target.stat().st_size <= 16384:
        previous = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(previous, dict):
            previous = {}
except (OSError, ValueError, TypeError):
    previous = {}

state = os.environ["RESTORE_STATUS_STATE"]
now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
payload = {
    "schemaVersion": 1,
    "state": state,
    "updatedAt": now,
    "lastSuccessAt": previous.get("lastSuccessAt", ""),
    "snapshot": os.environ.get("RESTORE_STATUS_SNAPSHOT", ""),
    "lastError": os.environ.get("RESTORE_STATUS_ERROR", "")[:1000],
}
if state == "passed":
    payload["lastSuccessAt"] = now
    payload["lastError"] = ""
temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
with temporary.open("w", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=True, separators=(",", ":"))
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.chmod(temporary, 0o644)
os.replace(temporary, target)
PY
}

if (( EUID != 0 )) && [[ "${REALGUARD_RESTORE_ALLOW_NON_ROOT:-0}" != "1" ]]; then
  echo "Restore verification must run as root." >&2
  write_status "failed" "root_privileges_required" || true
  exit 1
fi

if ! snapshot="$(readlink -f -- "$SNAPSHOT_INPUT")"; then
  write_status "failed" "backup_snapshot_not_found" || true
  exit 1
fi
status_snapshot="$snapshot"
[[ -d "$snapshot" ]] || {
  echo "Backup snapshot is not a directory: $SNAPSHOT_INPUT" >&2
  write_status "failed" "backup_snapshot_not_found" || true
  exit 1
}
[[ -f "$snapshot/SHA256SUMS" && -f "$snapshot/MANIFEST" ]] || {
  echo "Backup snapshot is missing SHA256SUMS or MANIFEST." >&2
  write_status "failed" "backup_manifest_missing" || true
  exit 1
}

for required in \
  mysql-system.sql.gz \
  mysql-detection.sql.gz \
  jianzhen-v2.sqlite3 \
  traffic-cumulative.sqlite3 \
  uploads.tgz \
  evidence-manifests.tgz \
  legacy-governance-evidence.tgz; do
  [[ -f "$snapshot/$required" ]] || {
    echo "Backup snapshot is incomplete: missing $required" >&2
    write_status "failed" "backup_snapshot_incomplete:$required" || true
    exit 1
  }
done

started_epoch="$(date +%s)"
started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
suffix="$(date -u +%Y%m%d%H%M%S)_$$"
restore_system="rg_restore_system_$suffix"
restore_detection="rg_restore_detection_$suffix"
work_dir="$(mktemp -d "$WORK_ROOT/realguard-restore-drill.XXXXXX")"
event_scheduler_before=""

cleanup() {
  status=$?
  trap - EXIT INT TERM
  set +e
  "$MYSQL_BIN" --batch --execute="DROP DATABASE IF EXISTS \`$restore_system\`; DROP DATABASE IF EXISTS \`$restore_detection\`;" \
    >/dev/null 2>&1
  if [[ "$event_scheduler_before" == "ON" ]]; then
    "$MYSQL_BIN" --batch --execute="SET GLOBAL event_scheduler = ON" >/dev/null 2>&1
  fi
  rm -rf -- "$work_dir"
  if (( status != 0 )); then
    write_status "failed" "restore_verification_failed" || true
  fi
  exit "$status"
}
trap cleanup EXIT INT TERM
write_status "running"

(
  cd "$snapshot"
  sha256sum -c SHA256SUMS
)

database_exists() {
  local database="$1"
  "$MYSQL_BIN" --batch --skip-column-names \
    --execute="SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name = '$database'"
}

for database in "$restore_system" "$restore_detection"; do
  [[ "$(database_exists "$database")" == "0" ]] || {
    echo "Refusing to overwrite existing restore database: $database" >&2
    exit 1
  }
done

event_scheduler_before="$(
  "$MYSQL_BIN" --batch --skip-column-names --execute="SELECT @@GLOBAL.event_scheduler"
)"
if [[ "$event_scheduler_before" == "ON" ]]; then
  if [[ "${REALGUARD_RESTORE_ALLOW_EVENT_PAUSE:-0}" != "1" ]]; then
    echo "MySQL event_scheduler is ON; set REALGUARD_RESTORE_ALLOW_EVENT_PAUSE=1 for an isolated drill." >&2
    exit 1
  fi
  "$MYSQL_BIN" --batch --execute="SET GLOBAL event_scheduler = OFF"
fi

restore_mysql_dump() {
  local dump_path="$1" source_database="$2" target_database="$3"
  "$PYTHON_BIN" - "$dump_path" "$source_database" "$target_database" <<'PY' \
    | "$MYSQL_BIN" --binary-mode --default-character-set=utf8mb4
import gzip
import sys

dump_path, source_database, target_database = sys.argv[1:]
source_token = f"`{source_database}`"
target_token = f"`{target_database}`"
create_count = 0
use_count = 0

with gzip.open(dump_path, "rt", encoding="utf-8", errors="strict") as source:
    for line in source:
        stripped = line.lstrip()
        if stripped.startswith("DROP DATABASE"):
            raise SystemExit("dump contains a forbidden DROP DATABASE statement")
        if stripped.startswith("CREATE DATABASE"):
            if source_token not in line:
                raise SystemExit("dump CREATE DATABASE does not match the configured source database")
            line = line.replace(source_token, target_token, 1)
            create_count += 1
        elif stripped.startswith("USE "):
            if source_token not in line:
                raise SystemExit("dump USE does not match the configured source database")
            line = line.replace(source_token, target_token, 1)
            use_count += 1
        sys.stdout.write(line)

if create_count != 1 or use_count != 1:
    raise SystemExit(
        f"expected one CREATE DATABASE and USE statement, got create={create_count}, use={use_count}"
    )
PY
}

restore_mysql_dump "$snapshot/mysql-system.sql.gz" "$SYSTEM_DATABASE" "$restore_system"
restore_mysql_dump "$snapshot/mysql-detection.sql.gz" "$DETECTION_DATABASE" "$restore_detection"

"$MYSQLCHECK_BIN" --check --extended "$restore_system" "$restore_detection" >/dev/null
system_tables="$(
  "$MYSQL_BIN" --batch --skip-column-names \
    --execute="SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = '$restore_system'"
)"
detection_tables="$(
  "$MYSQL_BIN" --batch --skip-column-names \
    --execute="SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = '$restore_detection'"
)"
(( system_tables > 0 && detection_tables > 0 )) || {
  echo "Restored MySQL databases contain no tables." >&2
  exit 1
}

verify_sqlite() {
  local database_path="$1"
  "$PYTHON_BIN" - "$database_path" <<'PY'
import json
import sqlite3
import sys

database_path = sys.argv[1]
with sqlite3.connect(f"file:{database_path}?mode=ro", uri=True) as connection:
    integrity = connection.execute("PRAGMA integrity_check").fetchall()
    if integrity != [("ok",)]:
        raise SystemExit(f"SQLite integrity check failed: {integrity[:5]}")
    tables = connection.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchone()[0]
print(json.dumps({"tables": tables, "integrity": "ok"}, separators=(",", ":")))
PY
}

v2_sqlite="$(verify_sqlite "$snapshot/jianzhen-v2.sqlite3")"
traffic_sqlite="$(verify_sqlite "$snapshot/traffic-cumulative.sqlite3")"

verify_archive() {
  local archive="$1" destination="$2"
  "$PYTHON_BIN" - "$archive" "$destination" <<'PY'
import json
from pathlib import Path, PurePosixPath
import tarfile
import sys

archive, destination = sys.argv[1:]
destination_path = Path(destination)
destination_path.mkdir(parents=True, exist_ok=True)
files = 0
total_bytes = 0
with tarfile.open(archive, "r:gz") as bundle:
    members = bundle.getmembers()
    for member in members:
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise SystemExit(f"unsafe archive path: {member.name}")
        if member.issym() or member.islnk() or member.isdev() or member.isfifo():
            raise SystemExit(f"unsafe archive member type: {member.name}")
        if member.isfile():
            files += 1
            total_bytes += member.size
    bundle.extractall(destination_path, filter="data")
print(json.dumps({"files": files, "bytes": total_bytes}, separators=(",", ":")))
PY
}

uploads_summary="$(verify_archive "$snapshot/uploads.tgz" "$work_dir/uploads")"
evidence_summary="$(
  verify_archive "$snapshot/evidence-manifests.tgz" "$work_dir/evidence-manifests"
)"
legacy_governance_summary="$(
  verify_archive "$snapshot/legacy-governance-evidence.tgz" "$work_dir/legacy-governance-evidence"
)"

finished_epoch="$(date +%s)"
finished_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
duration_seconds="$((finished_epoch - started_epoch))"
install -d -m 700 "$REPORT_ROOT"
report_path="$REPORT_ROOT/$(basename "$snapshot")-$suffix.json"
"$PYTHON_BIN" - \
  "$report_path" "$snapshot" "$started_at" "$finished_at" "$duration_seconds" \
  "$system_tables" "$detection_tables" "$v2_sqlite" "$traffic_sqlite" \
  "$uploads_summary" "$evidence_summary" "$legacy_governance_summary" <<'PY'
import json
from pathlib import Path
import sys

(
    report_path,
    snapshot,
    started_at,
    finished_at,
    duration_seconds,
    system_tables,
    detection_tables,
    v2_sqlite,
    traffic_sqlite,
    uploads,
    evidence,
    legacy_governance,
) = sys.argv[1:]
report = {
    "status": "passed",
    "snapshot": snapshot,
    "startedAt": started_at,
    "finishedAt": finished_at,
    "durationSeconds": int(duration_seconds),
    "checksums": "verified",
    "mysql": {
        "systemTables": int(system_tables),
        "detectionTables": int(detection_tables),
        "check": "extended-ok",
    },
    "sqlite": {
        "v2": json.loads(v2_sqlite),
        "traffic": json.loads(traffic_sqlite),
    },
    "archives": {
        "uploads": json.loads(uploads),
        "evidenceManifests": json.loads(evidence),
        "legacyGovernanceEvidence": json.loads(legacy_governance),
    },
}
Path(report_path).write_text(
    json.dumps(report, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
)
PY
chmod 600 "$report_path"
write_status "passed"

echo "RealGuard restore verification passed: $report_path"
