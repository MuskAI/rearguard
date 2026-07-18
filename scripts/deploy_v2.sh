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
COMMIT_SHA="$(latest_commit_for_paths v2-agent/backend v2-agent/frontend scripts/deploy_v2.sh scripts/deploy_common.sh)"
TMP_DIR="$(mktemp -d)"
ARCHIVE_PATH="$TMP_DIR/jianzhen-v2-backend.tgz"
FRONTEND_ARCHIVE_PATH="$TMP_DIR/jianzhen-v2-frontend.tgz"
MARKER_PATH="$TMP_DIR/jianzhen-v2.DEPLOYED_COMMIT"
REMOTE="$(remote_target)"
trap 'rm -rf "$TMP_DIR"' EXIT

log_step 1 "Verify V2 backend"
run_local python3 -m compileall "$BACKEND_DIR/app"
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
run_tar_create "$BACKEND_DIR" "$ARCHIVE_PATH" app pyproject.toml uv.lock
run_tar_create "$FRONTEND_DIR/dist" "$FRONTEND_ARCHIVE_PATH" .
write_commit_marker "$MARKER_PATH" "$COMMIT_SHA"

log_step 4 "Upload V2 release"
run_scp "$ARCHIVE_PATH" "$REMOTE:/tmp/jianzhen-v2-backend.tgz"
run_scp "$FRONTEND_ARCHIVE_PATH" "$REMOTE:/tmp/jianzhen-v2-frontend.tgz"
run_scp "$MARKER_PATH" "$REMOTE:/tmp/jianzhen-v2.DEPLOYED_COMMIT"

log_step 5 "Activate V2 release"
run_remote "sudo tar -xzf /tmp/jianzhen-v2-backend.tgz -C /opt/jianzhen-v2 && sudo install -m 644 /tmp/jianzhen-v2.DEPLOYED_COMMIT /opt/jianzhen-v2/DEPLOYED_COMMIT && sudo -u ubuntu /opt/jianzhen-v2/.venv/bin/python -m pip install --no-cache-dir --quiet --upgrade 'c2pa-python>=0.32.6' && sudo systemctl restart jianzhen-v2-backend.service && for _ in 1 2 3 4 5 6 7 8 9 10; do if curl -fsS http://127.0.0.1:8848/api/health >/dev/null; then break; fi; sleep 1; done && systemctl is-active jianzhen-v2-backend.service && curl -fsS http://127.0.0.1:8848/api/health >/dev/null && sudo rm -rf /var/www/v2.next && sudo mkdir -p /var/www/v2.next && sudo tar -xzf /tmp/jianzhen-v2-frontend.tgz -C /var/www/v2.next && sudo chown -R root:root /var/www/v2.next && sudo rm -rf /var/www/v2.previous && sudo mv /var/www/v2 /var/www/v2.previous && sudo mv /var/www/v2.next /var/www/v2 && sudo rm -rf /var/www/v2.previous && rm -f /tmp/jianzhen-v2-backend.tgz /tmp/jianzhen-v2-frontend.tgz /tmp/jianzhen-v2.DEPLOYED_COMMIT && curl -fsS -o /dev/null http://127.0.0.1/ && curl -fsS http://127.0.0.1/v2-api/health >/dev/null && cat /opt/jianzhen-v2/DEPLOYED_COMMIT"

printf '\nV2 deployed from commit %s to %s\n' "$COMMIT_SHA" "$REMOTE"
