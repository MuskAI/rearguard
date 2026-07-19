#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./deploy_common.sh
source "$SCRIPT_DIR/deploy_common.sh"

usage() {
  cat <<'EOF'
Usage: DEPLOY_SSH_KEY=/path/to/key ./scripts/deploy_v1.sh

Optional environment variables:
  DEPLOY_HOST   Default: 124.221.92.85
  DEPLOY_USER   Default: ubuntu
  REALGUARD_DETECTOR_PORT
                Default: 15001
  DRY_RUN=1     Print commands without executing them

This script verifies V1, builds the frontend, uploads a complete release,
activates hardened systemd/nginx configuration, and runs health checks.
EOF
}

if [[ "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

require_ssh_key

ROOT_DIR="$(repo_root)"
BACKEND_DIR="$ROOT_DIR/realguard-server-main/RealGuard"
FRONTEND_DIR="$ROOT_DIR/realguard-server-main/frontend"
NGINX_CONFIG="$ROOT_DIR/realguard-server-main/deploy/nginx-realguard-frontend.conf"
HTTPS_NGINX_CONFIG="$ROOT_DIR/deploy/nginx/realguard.conf"
NGINX_SNIPPETS_DIR="$ROOT_DIR/deploy/nginx/snippets"
WEB_SERVICE_UNIT="$ROOT_DIR/deploy/systemd/realguard-backend.service"
DETECTOR_SERVICE_UNIT="$ROOT_DIR/deploy/systemd/realguard-detector-backend.service"
DEVELOPER_WORKER_SERVICE_UNIT="$ROOT_DIR/deploy/systemd/realguard-developer-worker.service"
BACKUP_SERVICE_UNIT="$ROOT_DIR/deploy/systemd/realguard-backup.service"
BACKUP_TIMER_UNIT="$ROOT_DIR/deploy/systemd/realguard-backup.timer"
BACKUP_SCRIPT="$ROOT_DIR/scripts/remote/backup_realguard.sh"
RESTORE_VERIFY_SCRIPT="$ROOT_DIR/scripts/remote/verify_restore_realguard.sh"
ACTIVATE_SCRIPT="$ROOT_DIR/scripts/remote/activate_v1.sh"
DEPLOY_PATHS=(
  realguard-server-main/RealGuard
  realguard-server-main/frontend
  realguard-server-main/deploy/nginx-realguard-frontend.conf
  deploy/nginx
  deploy/systemd
  scripts/deploy_v1.sh
  scripts/remote/backup_realguard.sh
  scripts/remote/verify_restore_realguard.sh
  scripts/remote/activate_v1.sh
  scripts/deploy_common.sh
)
assert_deploy_paths_clean "${DEPLOY_PATHS[@]}"
COMMIT_SHA="$(latest_commit_for_paths \
  "${DEPLOY_PATHS[@]}")"
DETECTOR_PORT="${REALGUARD_DETECTOR_PORT:-15001}"
IP2REGION_XDB_URL="https://raw.githubusercontent.com/lionsoul2014/ip2region/cd40e3a1d532d645697999d646cf0e10481cef33/data/ip2region_v4.xdb"
IP2REGION_XDB_SHA256="6307a9696f5711f84bcb8b25f07894de68a64a0ed4a1cc7e990562dd3084f210"
TMP_DIR="$(mktemp -d)"
ARCHIVE_PATH="$TMP_DIR/realguard-v1-backend.tgz"
FRONTEND_ARCHIVE_PATH="$TMP_DIR/realguard-v1-frontend.tgz"
NGINX_SNIPPETS_ARCHIVE_PATH="$TMP_DIR/realguard-nginx-snippets.tgz"
MARKER_PATH="$TMP_DIR/realguard-v1.DEPLOYED_COMMIT"
IP2REGION_XDB_PATH="$TMP_DIR/ip2region-v4.xdb"
REMOTE="$(remote_target)"
trap 'rm -rf "$TMP_DIR"' EXIT

log_step 1 "Verify V1 backend and deployment scripts"
run_local "$BACKEND_DIR/.venv-test/bin/python" -m compileall "$BACKEND_DIR/imagedetection"
run_local "$BACKEND_DIR/.venv-test/bin/python" -m py_compile "$BACKEND_DIR/detector_backend.py"
run_local bash -n "$ACTIVATE_SCRIPT"
run_local bash -n "$BACKUP_SCRIPT"
run_local bash -n "$RESTORE_VERIFY_SCRIPT"
(
  cd "$BACKEND_DIR"
  run_local .venv-test/bin/python -m pytest tests
)

log_step 2 "Build V1 frontend"
(
  cd "$FRONTEND_DIR"
  run_local npm run build
)

log_step 3 "Package V1 release and pin IP geography data"
run_tar_create "$BACKEND_DIR" "$ARCHIVE_PATH" run.py detector_backend.py requirements.txt imagedetection
run_tar_create "$FRONTEND_DIR/dist" "$FRONTEND_ARCHIVE_PATH" .
run_tar_create "$NGINX_SNIPPETS_DIR" "$NGINX_SNIPPETS_ARCHIVE_PATH" .
write_commit_marker "$MARKER_PATH" "$COMMIT_SHA"
run_local curl -fsSL "$IP2REGION_XDB_URL" -o "$IP2REGION_XDB_PATH"
if [[ "$DRY_RUN" != "1" ]]; then
  printf '%s  %s\n' "$IP2REGION_XDB_SHA256" "$IP2REGION_XDB_PATH" | shasum -a 256 -c -
fi

log_step 4 "Upload V1 release"
run_scp "$ARCHIVE_PATH" "$REMOTE:/tmp/realguard-v1-backend.tgz"
run_scp "$FRONTEND_ARCHIVE_PATH" "$REMOTE:/tmp/realguard-v1-frontend.tgz"
run_scp "$NGINX_SNIPPETS_ARCHIVE_PATH" "$REMOTE:/tmp/realguard-nginx-snippets.tgz"
run_scp "$MARKER_PATH" "$REMOTE:/tmp/realguard-v1.DEPLOYED_COMMIT"
run_scp "$IP2REGION_XDB_PATH" "$REMOTE:/tmp/realguard-ip2region-v4.xdb"
run_scp "$NGINX_CONFIG" "$REMOTE:/tmp/realguard-frontend.nginx.conf"
run_scp "$HTTPS_NGINX_CONFIG" "$REMOTE:/tmp/realguard-https.nginx.conf"
run_scp "$WEB_SERVICE_UNIT" "$REMOTE:/tmp/realguard-backend.service"
run_scp "$DETECTOR_SERVICE_UNIT" "$REMOTE:/tmp/realguard-detector-backend.service"
run_scp "$DEVELOPER_WORKER_SERVICE_UNIT" "$REMOTE:/tmp/realguard-developer-worker.service"
run_scp "$BACKUP_SERVICE_UNIT" "$REMOTE:/tmp/realguard-backup.service"
run_scp "$BACKUP_TIMER_UNIT" "$REMOTE:/tmp/realguard-backup.timer"
run_scp "$BACKUP_SCRIPT" "$REMOTE:/tmp/realguard-backup"
run_scp "$RESTORE_VERIFY_SCRIPT" "$REMOTE:/tmp/realguard-restore-verify"
run_scp "$ACTIVATE_SCRIPT" "$REMOTE:/tmp/realguard-activate-v1.sh"

log_step 5 "Activate V1 release"
run_remote "IP2REGION_XDB_SHA256='$IP2REGION_XDB_SHA256' REALGUARD_DETECTOR_PORT='$DETECTOR_PORT' bash /tmp/realguard-activate-v1.sh"

printf '\nV1 deployed from commit %s to %s\n' "$COMMIT_SHA" "$REMOTE"
