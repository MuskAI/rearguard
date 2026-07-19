#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./deploy_common.sh
source "$SCRIPT_DIR/deploy_common.sh"

usage() {
  cat <<'EOF'
Usage: DEPLOY_SSH_KEY=/path/to/key ./scripts/deploy_v2.sh

Optional environment variables:
  DEPLOY_HOST   Default: 124.221.92.85
  DEPLOY_USER   Default: ubuntu
  DRY_RUN=1     Print commands without executing them

This script verifies the evidence service, builds the unified Agent frontend,
uploads both, restarts jianzhen-v2-backend.service, then runs health checks.
EOF
}

if [[ "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

require_ssh_key

ROOT_DIR="$(repo_root)"
BACKEND_DIR="$ROOT_DIR/v2-agent/backend"
FRONTEND_DIR="$ROOT_DIR/v2-agent/frontend"
SERVICE_UNIT="$ROOT_DIR/deploy/systemd/jianzhen-v2-backend.service"
ACTIVATE_SCRIPT="$ROOT_DIR/scripts/remote/activate_v2.sh"
DEPLOY_PATHS=(
  v2-agent/backend
  v2-agent/frontend
  deploy/systemd/jianzhen-v2-backend.service
  scripts/deploy_v2.sh
  scripts/remote/activate_v2.sh
  scripts/deploy_common.sh
)
assert_deploy_paths_clean "${DEPLOY_PATHS[@]}"
COMMIT_SHA="$(latest_commit_for_paths "${DEPLOY_PATHS[@]}")"
TMP_DIR="$(mktemp -d)"
ARCHIVE_PATH="$TMP_DIR/jianzhen-v2-backend.tgz"
FRONTEND_ARCHIVE_PATH="$TMP_DIR/jianzhen-v2-frontend.tgz"
MARKER_PATH="$TMP_DIR/jianzhen-v2.DEPLOYED_COMMIT"
REMOTE="$(remote_target)"
trap 'rm -rf "$TMP_DIR"' EXIT

log_step 1 "Verify V2 backend"
run_local python3 -m compileall "$BACKEND_DIR/app"
run_local bash -n "$ACTIVATE_SCRIPT"
(
  cd "$BACKEND_DIR"
  run_local .venv/bin/python -m pytest tests
)

log_step 2 "Build V2 frontend"
(
  cd "$FRONTEND_DIR"
  run_local npm run build
)

log_step 3 "Package V2 backend"
run_tar_create "$BACKEND_DIR" "$ARCHIVE_PATH" app pyproject.toml uv.lock requirements.lock
run_tar_create "$FRONTEND_DIR/dist" "$FRONTEND_ARCHIVE_PATH" .
write_commit_marker "$MARKER_PATH" "$COMMIT_SHA"

log_step 4 "Upload V2 release"
run_scp "$ARCHIVE_PATH" "$REMOTE:/tmp/jianzhen-v2-backend.tgz"
run_scp "$FRONTEND_ARCHIVE_PATH" "$REMOTE:/tmp/jianzhen-v2-frontend.tgz"
run_scp "$MARKER_PATH" "$REMOTE:/tmp/jianzhen-v2.DEPLOYED_COMMIT"
run_scp "$SERVICE_UNIT" "$REMOTE:/tmp/jianzhen-v2-backend.service"
run_scp "$ACTIVATE_SCRIPT" "$REMOTE:/tmp/jianzhen-activate-v2.sh"

log_step 5 "Create and verify a pre-migration backup"
run_remote "sudo bash -lc 'set -euo pipefail; test -x /usr/local/sbin/realguard-backup; set -a; for env_file in /etc/realguard/session.env /etc/realguard/realguard-backend.env /etc/realguard/detector-db.env /etc/realguard/jianzhen-v2.env /etc/realguard/backup.env; do [ ! -f \"\$env_file\" ] || . \"\$env_file\"; done; set +a; backup_output=\$(/usr/local/sbin/realguard-backup 2>&1); printf \"%s\\n\" \"\$backup_output\"; backup_dir=\$(printf \"%s\\n\" \"\$backup_output\" | sed -n \"s/^RealGuard backup completed: //p\" | tail -n 1); test -n \"\$backup_dir\"; test -d \"\$backup_dir\"; cd \"\$backup_dir\"; sha256sum -c SHA256SUMS'"

log_step 6 "Activate V2 release"
run_remote "bash /tmp/jianzhen-activate-v2.sh"

printf '\nV2 deployed from commit %s to %s\n' "$COMMIT_SHA" "$REMOTE"
