#!/usr/bin/env bash
set -euo pipefail

umask 077

BACKUP_ROOT="${REALGUARD_BACKUP_ROOT:-/var/backups/realguard}"
RETENTION_DAYS="${REALGUARD_BACKUP_RETENTION_DAYS:-14}"
OFFSITE_REMOTE="${REALGUARD_BACKUP_RCLONE_REMOTE:-}"
V2_DB="${JIANZHEN_DB_PATH:-/opt/jianzhen-v2/data/jianzhen-v2.sqlite3}"
TRAFFIC_DB="${REALGUARD_TRAFFIC_CUMULATIVE_DB:-/opt/realguard-data/traffic-cumulative.sqlite3}"
UPLOADS_DIR="${REALGUARD_UPLOADS_DIR:-/opt/realguard-server/RealGuard/imagedetection/static/uploads}"

[[ "$RETENTION_DAYS" =~ ^[0-9]+$ ]] && (( RETENTION_DAYS >= 1 ))
install -d -m 700 "$BACKUP_ROOT"

exec 9>"$BACKUP_ROOT/.backup.lock"
if ! flock -n 9; then
  echo "A RealGuard backup is already running." >&2
  exit 0
fi

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
hostname_safe="$(hostname | tr -cd 'A-Za-z0-9._-')"
staging="$(mktemp -d "$BACKUP_ROOT/.staging-${timestamp}.XXXXXX")"
final="$BACKUP_ROOT/$timestamp"
cleanup() {
  [[ ! -d "$staging" ]] || rm -rf -- "$staging"
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
  python3 - "$source" "$destination" <<'PY'
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

cat >"$staging/MANIFEST" <<EOF
created_at=$timestamp
hostname=$hostname_safe
v2_database=$V2_DB
traffic_database=$TRAFFIC_DB
uploads_directory=$UPLOADS_DIR
EOF
(
  cd "$staging"
  find . -type f ! -name SHA256SUMS -print0 \
    | sort -z \
    | xargs -0 sha256sum >SHA256SUMS
)

mv "$staging" "$final"
trap - EXIT
ln -sfn "$timestamp" "$BACKUP_ROOT/latest"

find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -name '20??????T??????Z' \
  -mtime "+$RETENTION_DAYS" -exec rm -rf -- {} +

if [[ -n "$OFFSITE_REMOTE" ]]; then
  command -v rclone >/dev/null
  rclone copy "$final" "${OFFSITE_REMOTE%/}/$hostname_safe/$timestamp" --immutable
else
  echo "Warning: REALGUARD_BACKUP_RCLONE_REMOTE is not configured; backup is local only." >&2
fi

echo "RealGuard backup completed: $final"
