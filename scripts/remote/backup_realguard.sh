#!/usr/bin/env bash
set -euo pipefail

umask 077

BACKUP_ROOT="${REALGUARD_BACKUP_ROOT:-/var/backups/realguard}"
RETENTION_DAYS="${REALGUARD_BACKUP_RETENTION_DAYS:-14}"
OFFSITE_REMOTE="${REALGUARD_BACKUP_RCLONE_REMOTE:-}"
REQUIRE_OFFSITE="${REALGUARD_BACKUP_REQUIRE_OFFSITE:-0}"
REQUIRE_ALL_SOURCES="${REALGUARD_BACKUP_REQUIRE_ALL_SOURCES:-0}"
STATUS_FILE="${REALGUARD_BACKUP_STATUS_FILE:-/opt/realguard-data/backup-status.json}"
V2_DB="${JIANZHEN_DB_PATH:-/opt/jianzhen-v2/data/jianzhen-v2.sqlite3}"
TRAFFIC_DB="${REALGUARD_TRAFFIC_CUMULATIVE_DB:-/opt/realguard-data/traffic-cumulative.sqlite3}"
UPLOADS_DIR="${REALGUARD_UPLOADS_DIR:-/opt/realguard-server/RealGuard/imagedetection/static/uploads}"
EVIDENCE_MANIFEST_DIR="${REALGUARD_EVIDENCE_SNAPSHOT_ROOT:-/opt/realguard-data/evidence-manifests}"
LEGACY_GOVERNANCE_EVIDENCE_DIR="${REALGUARD_LEGACY_EVIDENCE_ROOT:-/opt/realguard-data/legacy-governance-evidence}"

[[ "$RETENTION_DAYS" =~ ^[0-9]+$ ]] && (( RETENTION_DAYS >= 1 ))
[[ "$REQUIRE_OFFSITE" =~ ^[01]$ ]]
[[ "$REQUIRE_ALL_SOURCES" =~ ^[01]$ ]]
install -d -m 700 "$BACKUP_ROOT"

run_started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
status_snapshot=""
status_offsite_verified="false"

write_status() {
  local state="$1"
  local status_parent
  status_parent="$(dirname "$STATUS_FILE")"
  install -d -m 755 "$status_parent"
  BACKUP_STATUS_STATE="$state" \
  BACKUP_STATUS_RUN_STARTED_AT="$run_started_at" \
  BACKUP_STATUS_SNAPSHOT="$status_snapshot" \
  BACKUP_STATUS_OFFSITE_CONFIGURED="$([[ -n "$OFFSITE_REMOTE" ]] && printf true || printf false)" \
  BACKUP_STATUS_OFFSITE_VERIFIED="$status_offsite_verified" \
    "${PYTHON_BIN:-python3}" - "$STATUS_FILE" <<'PY'
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

state = os.environ["BACKUP_STATUS_STATE"]
offsite_configured = os.environ["BACKUP_STATUS_OFFSITE_CONFIGURED"] == "true"
offsite_verified = os.environ["BACKUP_STATUS_OFFSITE_VERIFIED"] == "true"
now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
payload = {
    "schemaVersion": 1,
    "state": state,
    "runStartedAt": os.environ["BACKUP_STATUS_RUN_STARTED_AT"],
    "updatedAt": now,
    "snapshot": os.environ.get("BACKUP_STATUS_SNAPSHOT", ""),
    "offsiteConfigured": offsite_configured,
    "offsiteVerified": offsite_verified,
    "lastSuccessAt": previous.get("lastSuccessAt", ""),
    "lastOffsiteSuccessAt": previous.get("lastOffsiteSuccessAt", ""),
    "lastError": "backup_run_failed" if state == "failed" else "",
}
if state == "success":
    payload["lastSuccessAt"] = now
    if offsite_verified:
        payload["lastOffsiteSuccessAt"] = now

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

exec 9>"$BACKUP_ROOT/.backup.lock"
if ! flock -n 9; then
  echo "A RealGuard backup is already running." >&2
  exit 0
fi
write_status "running"

if [[ "$REQUIRE_OFFSITE" == "1" && -z "$OFFSITE_REMOTE" ]]; then
  echo "REALGUARD_BACKUP_REQUIRE_OFFSITE=1 but no rclone remote is configured." >&2
  write_status "failed"
  exit 1
fi
if [[ "$REQUIRE_ALL_SOURCES" == "1" ]]; then
  for required_file in "$V2_DB" "$TRAFFIC_DB"; do
    if [[ ! -f "$required_file" ]]; then
      echo "Required backup source is missing: $required_file" >&2
      write_status "failed"
      exit 1
    fi
  done
  for required_directory in "$UPLOADS_DIR" "$EVIDENCE_MANIFEST_DIR" "$LEGACY_GOVERNANCE_EVIDENCE_DIR"; do
    if [[ ! -d "$required_directory" ]]; then
      echo "Required backup source is missing: $required_directory" >&2
      write_status "failed"
      exit 1
    fi
  done
fi

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
hostname_safe="$(hostname | tr -cd 'A-Za-z0-9._-')"
staging="$(mktemp -d "$BACKUP_ROOT/.staging-${timestamp}.XXXXXX")"
final="$BACKUP_ROOT/$timestamp"
cleanup() {
  local exit_code=$?
  [[ ! -d "$staging" ]] || rm -rf -- "$staging"
  if (( exit_code != 0 )); then
    write_status "failed" || true
  fi
  exit "$exit_code"
}
trap cleanup EXIT

dump_mysql() {
  local label="$1" host="$2" port="$3" user="$4" password="$5" database="$6"
  if [[ -z "$user" || -z "$database" ]]; then
    echo "Missing credentials for MySQL backup: $label" >&2
    return 1
  fi
  MYSQL_PWD="$password" mysqldump \
    --host="$host" \
    --port="$port" \
    --user="$user" \
    --single-transaction \
    --no-tablespaces \
    --routines \
    --events \
    --triggers \
    --hex-blob \
    --databases "$database" \
    | gzip -9 >"$staging/mysql-${label}.sql.gz"
}

backup_sqlite() {
  local source="$1" destination="$2"
  [[ -f "$source" ]] || return 0
  "${PYTHON_BIN:-python3}" - "$source" "$destination" <<'PY'
import sqlite3
import sys

source, destination = sys.argv[1:3]
with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src:
    with sqlite3.connect(destination) as dst:
        src.backup(dst)
PY
}

dump_mysql \
  system \
  "${REALGUARD_DB_HOST:-127.0.0.1}" \
  "${REALGUARD_DB_PORT:-3306}" \
  "${REALGUARD_DB_USER:-}" \
  "${REALGUARD_DB_PASSWORD:-}" \
  "${REALGUARD_DB_NAME:-system}"
dump_mysql \
  detection \
  "${REALGUARD_DETECTION_DB_HOST:-127.0.0.1}" \
  "${REALGUARD_DETECTION_DB_PORT:-3306}" \
  "${REALGUARD_DETECTION_DB_USER:-}" \
  "${REALGUARD_DETECTION_DB_PASSWORD:-}" \
  "${REALGUARD_DETECTION_DB_NAME:-image_detection}"

backup_sqlite "$V2_DB" "$staging/jianzhen-v2.sqlite3"
backup_sqlite "$TRAFFIC_DB" "$staging/traffic-cumulative.sqlite3"

if [[ -d "$UPLOADS_DIR" ]]; then
  tar -C "$UPLOADS_DIR" -czf "$staging/uploads.tgz" .
fi
if [[ -d "$EVIDENCE_MANIFEST_DIR" ]]; then
  tar -C "$EVIDENCE_MANIFEST_DIR" -czf "$staging/evidence-manifests.tgz" .
fi
if [[ -d "$LEGACY_GOVERNANCE_EVIDENCE_DIR" ]]; then
  tar -C "$LEGACY_GOVERNANCE_EVIDENCE_DIR" -czf "$staging/legacy-governance-evidence.tgz" .
fi

cat >"$staging/MANIFEST" <<EOF
created_at=$timestamp
hostname=$hostname_safe
v2_database=$V2_DB
traffic_database=$TRAFFIC_DB
uploads_directory=$UPLOADS_DIR
evidence_manifest_directory=$EVIDENCE_MANIFEST_DIR
legacy_governance_evidence_directory=$LEGACY_GOVERNANCE_EVIDENCE_DIR
EOF
(
  cd "$staging"
  find . -type f ! -name SHA256SUMS -print0 \
    | sort -z \
    | xargs -0 sha256sum >SHA256SUMS
)

mv "$staging" "$final"
status_snapshot="$timestamp"

find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -name '20??????T??????Z' \
  -mtime "+$RETENTION_DAYS" -exec rm -rf -- {} +

if [[ -n "$OFFSITE_REMOTE" ]]; then
  command -v rclone >/dev/null
  offsite_destination="${OFFSITE_REMOTE%/}/$hostname_safe/$timestamp"
  rclone copy "$final" "$offsite_destination" --immutable
  rclone check "$final" "$offsite_destination" --one-way
  status_offsite_verified="true"
  echo "RealGuard offsite backup verified: $offsite_destination"
else
  echo "Warning: REALGUARD_BACKUP_RCLONE_REMOTE is not configured; backup is local only." >&2
fi

ln -sfn "$timestamp" "$BACKUP_ROOT/latest"
write_status "success"
trap - EXIT
echo "RealGuard backup completed: $final"
