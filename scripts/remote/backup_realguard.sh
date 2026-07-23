#!/usr/bin/env bash
set -euo pipefail

umask 077

BACKUP_ROOT="${REALGUARD_BACKUP_ROOT:-/var/backups/realguard}"
RETENTION_DAYS="${REALGUARD_BACKUP_RETENTION_DAYS:-14}"
MAX_SNAPSHOTS="${REALGUARD_BACKUP_MAX_SNAPSHOTS:-21}"
MAX_ROOT_BYTES="${REALGUARD_BACKUP_MAX_ROOT_BYTES:-17179869184}"
MIN_FREE_BYTES="${REALGUARD_BACKUP_MIN_FREE_BYTES:-8589934592}"
MIN_FREE_PERCENT="${REALGUARD_BACKUP_MIN_FREE_PERCENT:-15}"
MIN_STAGING_BYTES="${REALGUARD_BACKUP_MIN_STAGING_BYTES:-2147483648}"
OFFSITE_REMOTE="${REALGUARD_BACKUP_RCLONE_REMOTE:-}"
REQUIRE_OFFSITE="${REALGUARD_BACKUP_REQUIRE_OFFSITE:-0}"
REQUIRE_ALL_SOURCES="${REALGUARD_BACKUP_REQUIRE_ALL_SOURCES:-0}"
STATUS_FILE="${REALGUARD_BACKUP_STATUS_FILE:-/opt/realguard-data/backup-status.json}"
ADMIN_STATE_FILE="${REALGUARD_ADMIN_STATE_PATH:-/opt/realguard-data/admin_state.json}"
V2_DB="${JIANZHEN_DB_PATH:-/opt/jianzhen-v2/data/jianzhen-v2.sqlite3}"
TRAFFIC_DB="${REALGUARD_TRAFFIC_CUMULATIVE_DB:-/opt/realguard-data/traffic-cumulative.sqlite3}"
PRIVACY_ERASURE_LEDGER="${REALGUARD_PRIVACY_ERASURE_LEDGER_PATH:-/opt/realguard-data/privacy-erasure/privacy-erasure-tombstones.sqlite3}"
UPLOADS_DIR="${REALGUARD_UPLOADS_DIR:-/opt/realguard-server/RealGuard/imagedetection/static/uploads}"
EVIDENCE_MANIFEST_DIR="${REALGUARD_EVIDENCE_SNAPSHOT_ROOT:-/opt/realguard-data/evidence-manifests}"
LEGACY_GOVERNANCE_EVIDENCE_DIR="${REALGUARD_LEGACY_EVIDENCE_ROOT:-/opt/realguard-data/legacy-governance-evidence}"
INTERNAL_TEST_DIR="${REALGUARD_INTERNAL_TEST_ROOT:-/opt/realguard-data/internal-testing}"
INTERNAL_TEST_DB="${REALGUARD_INTERNAL_TEST_DB:-$INTERNAL_TEST_DIR/internal-testing.sqlite3}"

require_uint() {
  local name="$1" value="$2" minimum="$3" maximum="${4:-9223372036854775807}"
  if [[ ! "$value" =~ ^(0|[1-9][0-9]*)$ ]] \
    || (( ${#value} > ${#maximum} )) \
    || { (( ${#value} == ${#maximum} )) && [[ "$value" > "$maximum" ]]; } \
    || (( 10#$value < minimum )); then
    echo "$name must be an integer between $minimum and $maximum; got: $value" >&2
    exit 64
  fi
}

require_uint REALGUARD_BACKUP_RETENTION_DAYS "$RETENTION_DAYS" 1
require_uint REALGUARD_BACKUP_MAX_SNAPSHOTS "$MAX_SNAPSHOTS" 1
require_uint REALGUARD_BACKUP_MAX_ROOT_BYTES "$MAX_ROOT_BYTES" 1
require_uint REALGUARD_BACKUP_MIN_FREE_BYTES "$MIN_FREE_BYTES" 0
require_uint REALGUARD_BACKUP_MIN_FREE_PERCENT "$MIN_FREE_PERCENT" 0 99
require_uint REALGUARD_BACKUP_MIN_STAGING_BYTES "$MIN_STAGING_BYTES" 0
require_uint REALGUARD_BACKUP_REQUIRE_OFFSITE "$REQUIRE_OFFSITE" 0 1
require_uint REALGUARD_BACKUP_REQUIRE_ALL_SOURCES "$REQUIRE_ALL_SOURCES" 0 1
install -d -m 700 "$BACKUP_ROOT"
BACKUP_ROOT="$(cd "$BACKUP_ROOT" && pwd -P)"

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
  echo "RealGuard backup lock is busy; another backup remains authoritative. status_file=$STATUS_FILE" >&2
  exit 75
fi
write_status "running"

directory_bytes() {
  local path="$1" kib
  [[ -e "$path" ]] || {
    printf '0\n'
    return
  }
  read -r kib _ < <(du -sk -- "$path")
  [[ "$kib" =~ ^[0-9]+$ ]] || return 1
  printf '%s\n' "$((10#$kib * 1024))"
}

refresh_snapshots() {
  local candidate name
  snapshots=()
  snapshot_count=0
  shopt -s nullglob
  for candidate in "$BACKUP_ROOT"/20??????T??????Z; do
    name="${candidate##*/}"
    if [[ "$name" =~ ^20[0-9]{6}T[0-9]{6}Z$ && -d "$candidate" && ! -L "$candidate" ]]; then
      snapshots+=("$candidate")
      snapshot_count=$((snapshot_count + 1))
    fi
  done
  shopt -u nullglob
}

latest_snapshot_path() {
  local resolved=""
  if [[ -L "$BACKUP_ROOT/latest" ]]; then
    resolved="$(readlink -f "$BACKUP_ROOT/latest" 2>/dev/null || true)"
    if [[ "$resolved" == "$BACKUP_ROOT"/20??????T??????Z && -d "$resolved" && ! -L "$resolved" ]]; then
      printf '%s\n' "$resolved"
    fi
  fi
}

remove_snapshot() {
  local candidate="$1" name latest
  name="${candidate##*/}"
  latest="$(latest_snapshot_path)"
  if [[ "$candidate" != "$BACKUP_ROOT/$name" || ! "$name" =~ ^20[0-9]{6}T[0-9]{6}Z$ ]]; then
    echo "Refusing to remove non-canonical backup path: $candidate" >&2
    return 1
  fi
  if [[ "$candidate" == "$latest" || -L "$candidate" || ! -d "$candidate" ]]; then
    return 1
  fi
  rm -rf -- "$candidate"
  echo "Removed old RealGuard snapshot: $name" >&2
}

cleanup_retention_snapshots() {
  local cutoff candidate name latest
  if ! cutoff="$("${PYTHON_BIN:-python3}" - "$RETENTION_DAYS" <<'PY'
from datetime import datetime, timedelta, timezone
import sys

days = int(sys.argv[1])
print((datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y%m%dT%H%M%SZ"))
PY
  )"; then
    echo "Unable to calculate backup retention cutoff." >&2
    return 1
  fi
  latest="$(latest_snapshot_path)"
  refresh_snapshots
  if (( snapshot_count > 0 )); then
    for candidate in "${snapshots[@]}"; do
      name="${candidate##*/}"
      if [[ "$name" < "$cutoff" && "$candidate" != "$latest" ]]; then
        remove_snapshot "$candidate"
      fi
    done
  fi
}

refresh_capacity() {
  local filesystem blocks used available capacity mount
  backup_root_bytes="$(directory_bytes "$BACKUP_ROOT")"
  read -r filesystem blocks used available capacity mount < <(df -Pk "$BACKUP_ROOT" | tail -n 1)
  if [[ ! "$blocks" =~ ^[0-9]+$ || ! "$available" =~ ^[0-9]+$ ]]; then
    echo "Unable to read backup filesystem capacity." >&2
    return 1
  fi
  filesystem_bytes="$((10#$blocks * 1024))"
  available_bytes="$((10#$available * 1024))"
  refresh_snapshots
}

capacity_is_sufficient() {
  local reserve_bytes="$1" incoming_snapshots="$2" projected_available projected_snapshots latest
  refresh_capacity || return 1
  latest="$(latest_snapshot_path)"
  projected_snapshots=$((snapshot_count + incoming_snapshots))
  if (( incoming_snapshots > 0 )) && [[ -n "$latest" ]]; then
    projected_snapshots=$((projected_snapshots - 1))
  fi
  (( projected_snapshots <= MAX_SNAPSHOTS )) || return 1
  (( backup_root_bytes + reserve_bytes <= MAX_ROOT_BYTES )) || return 1
  (( available_bytes >= reserve_bytes + MIN_FREE_BYTES )) || return 1
  projected_available=$((available_bytes - reserve_bytes))
  (( filesystem_bytes > 0 )) || return 1
  (( projected_available * 100 / filesystem_bytes >= MIN_FREE_PERCENT ))
}

enforce_capacity() {
  local reserve_bytes="$1" incoming_snapshots="$2" candidate latest removed
  cleanup_retention_snapshots || return 1
  while ! capacity_is_sufficient "$reserve_bytes" "$incoming_snapshots"; do
    latest="$(latest_snapshot_path)"
    removed=0
    if (( snapshot_count > 0 )); then
      for candidate in "${snapshots[@]}"; do
        if [[ "$candidate" != "$latest" ]] && remove_snapshot "$candidate"; then
          removed=1
          break
        fi
      done
    fi
    if (( removed == 0 )); then
      refresh_capacity || true
      echo "Backup capacity preflight failed: snapshots=${snapshot_count:-unknown}/$MAX_SNAPSHOTS root_bytes=${backup_root_bytes:-unknown}/$MAX_ROOT_BYTES available_bytes=${available_bytes:-unknown} reserve_bytes=$reserve_bytes min_free_bytes=$MIN_FREE_BYTES min_free_percent=$MIN_FREE_PERCENT" >&2
      return 1
    fi
  done
}

estimate_staging_bytes() {
  local estimate="$MIN_STAGING_BYTES" source size latest
  local source_total=0
  for source in "$ADMIN_STATE_FILE" "$V2_DB" "$TRAFFIC_DB" "$PRIVACY_ERASURE_LEDGER" "$UPLOADS_DIR" "$EVIDENCE_MANIFEST_DIR" "$LEGACY_GOVERNANCE_EVIDENCE_DIR" "$INTERNAL_TEST_DIR"; do
    if [[ -e "$source" ]]; then
      size="$(directory_bytes "$source")"
      source_total=$((source_total + size))
    fi
  done
  (( source_total <= estimate )) || estimate="$source_total"
  latest="$(latest_snapshot_path)"
  if [[ -n "$latest" ]]; then
    size="$(directory_bytes "$latest")"
    (( size <= estimate )) || estimate="$size"
  fi
  printf '%s\n' "$estimate"
}

snapshot_is_independently_viable() {
  local snapshot="$1" snapshot_bytes canonical_bytes=0 candidate candidate_bytes
  local non_snapshot_bytes reclaimable_bytes projected_root_bytes projected_available_bytes
  [[ -d "$snapshot" && ! -L "$snapshot" ]] || return 1
  snapshot_bytes="$(directory_bytes "$snapshot")" || return 1
  refresh_capacity || return 1
  for candidate in "${snapshots[@]}"; do
    candidate_bytes="$(directory_bytes "$candidate")" || return 1
    canonical_bytes=$((canonical_bytes + candidate_bytes))
  done
  (( canonical_bytes >= snapshot_bytes )) || return 1
  non_snapshot_bytes=$((backup_root_bytes - canonical_bytes))
  (( non_snapshot_bytes >= 0 )) || return 1
  reclaimable_bytes=$((canonical_bytes - snapshot_bytes))
  projected_root_bytes=$((non_snapshot_bytes + snapshot_bytes))
  projected_available_bytes=$((available_bytes + reclaimable_bytes))
  (( projected_root_bytes <= MAX_ROOT_BYTES )) || return 1
  (( projected_available_bytes >= MIN_FREE_BYTES )) || return 1
  (( filesystem_bytes > 0 )) || return 1
  (( projected_available_bytes * 100 / filesystem_bytes >= MIN_FREE_PERCENT ))
}

if [[ "$REQUIRE_OFFSITE" == "1" && -z "$OFFSITE_REMOTE" ]]; then
  echo "REALGUARD_BACKUP_REQUIRE_OFFSITE=1 but no rclone remote is configured." >&2
  write_status "failed"
  exit 1
fi
if [[ ! -f "$PRIVACY_ERASURE_LEDGER" || -L "$PRIVACY_ERASURE_LEDGER" ]]; then
  echo "Required privacy erasure ledger is missing or unsafe: $PRIVACY_ERASURE_LEDGER" >&2
  write_status "failed"
  exit 1
fi
if [[ "$REQUIRE_ALL_SOURCES" == "1" ]]; then
  for required_file in "$V2_DB" "$TRAFFIC_DB" "$PRIVACY_ERASURE_LEDGER"; do
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

if ! estimated_staging_bytes="$(estimate_staging_bytes)"; then
  echo "Unable to estimate backup staging capacity." >&2
  write_status "failed"
  exit 1
fi
if ! enforce_capacity "$estimated_staging_bytes" 1; then
  write_status "failed"
  exit 1
fi
echo "Backup capacity preflight passed: reserved_bytes=$estimated_staging_bytes" >&2

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
hostname_safe="$(hostname | tr -cd 'A-Za-z0-9._-')"
staging="$(mktemp -d "$BACKUP_ROOT/.staging-${timestamp}.XXXXXX")"
final="$BACKUP_ROOT/$timestamp"
if [[ -e "$final" || -L "$final" ]]; then
  echo "Refusing to overwrite existing backup snapshot: $final" >&2
  rm -rf -- "$staging"
  write_status "failed"
  exit 1
fi
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
backup_sqlite "$PRIVACY_ERASURE_LEDGER" "$staging/privacy-erasure-tombstones.sqlite3"
backup_sqlite "$INTERNAL_TEST_DB" "$staging/internal-testing.sqlite3"

admin_state_backed_up=false
if [[ -f "$ADMIN_STATE_FILE" && ! -L "$ADMIN_STATE_FILE" ]]; then
  cp -p "$ADMIN_STATE_FILE" "$staging/admin_state.json"
  admin_state_backed_up=true
fi

if [[ -d "$UPLOADS_DIR" ]]; then
  tar --exclude='*.deleting-*' -C "$UPLOADS_DIR" -czf "$staging/uploads.tgz" .
fi
if [[ -d "$EVIDENCE_MANIFEST_DIR" ]]; then
  tar --exclude='*.deleting-*' -C "$EVIDENCE_MANIFEST_DIR" -czf "$staging/evidence-manifests.tgz" .
fi
if [[ -d "$LEGACY_GOVERNANCE_EVIDENCE_DIR" ]]; then
  tar -C "$LEGACY_GOVERNANCE_EVIDENCE_DIR" -czf "$staging/legacy-governance-evidence.tgz" .
fi
if [[ -d "$INTERNAL_TEST_DIR" ]]; then
  tar \
    --exclude="$(basename "$INTERNAL_TEST_DB")" \
    --exclude="$(basename "$INTERNAL_TEST_DB")-shm" \
    --exclude="$(basename "$INTERNAL_TEST_DB")-wal" \
    -C "$INTERNAL_TEST_DIR" -czf "$staging/internal-testing-files.tgz" .
fi

cat >"$staging/MANIFEST" <<EOF
created_at=$timestamp
hostname=$hostname_safe
v2_database=$V2_DB
traffic_database=$TRAFFIC_DB
privacy_erasure_ledger=$PRIVACY_ERASURE_LEDGER
admin_state_file=$ADMIN_STATE_FILE
admin_state_backed_up=$admin_state_backed_up
uploads_directory=$UPLOADS_DIR
evidence_manifest_directory=$EVIDENCE_MANIFEST_DIR
legacy_governance_evidence_directory=$LEGACY_GOVERNANCE_EVIDENCE_DIR
internal_testing_directory=$INTERNAL_TEST_DIR
internal_testing_database=$INTERNAL_TEST_DB
EOF
(
  cd "$staging"
  find . -type f ! -name SHA256SUMS -print0 \
    | sort -z \
    | xargs -0 sha256sum >SHA256SUMS
)

mv "$staging" "$final"
status_snapshot="$timestamp"
if ! snapshot_is_independently_viable "$final"; then
  echo "Completed snapshot cannot satisfy configured capacity limits even after old snapshots are reclaimed." >&2
  rm -rf -- "$final"
  status_snapshot=""
  write_status "failed"
  exit 1
fi
ln -sfn "$timestamp" "$BACKUP_ROOT/latest"
if ! enforce_capacity 0 0; then
  echo "Completed snapshot violates configured backup capacity limits." >&2
  exit 1
fi

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

write_status "success"
trap - EXIT
echo "RealGuard backup completed: $final"
