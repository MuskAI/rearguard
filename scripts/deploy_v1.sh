#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./deploy_common.sh
source "$SCRIPT_DIR/deploy_common.sh"

usage() {
  cat <<'EOF'
Usage: DEPLOY_SSH_KEY=/path/to/key ./scripts/deploy_v1.sh

Optional environment variables:
  DEPLOY_HOST   Default: 124.222.3.205
  DEPLOY_USER   Default: ubuntu
  DRY_RUN=1     Print commands without executing them

This script verifies V1, builds the frontend, uploads the backend package and
frontend dist assets, restarts realguard-backend.service, then runs health checks.
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
COMMIT_SHA="$(latest_commit_for_paths realguard-server-main/RealGuard realguard-server-main/frontend)"
TMP_DIR="$(mktemp -d)"
ARCHIVE_PATH="$TMP_DIR/realguard-v1-backend.tgz"
MARKER_PATH="$TMP_DIR/realguard-v1.DEPLOYED_COMMIT"
REMOTE="$(remote_target)"
trap 'rm -rf "$TMP_DIR"' EXIT

log_step 1 "Verify V1 backend"
run_local python3 -m compileall "$BACKEND_DIR/imagedetection"
run_local "$BACKEND_DIR/.venv-test/bin/pytest" "$BACKEND_DIR/tests"

log_step 2 "Build V1 frontend"
(
  cd "$FRONTEND_DIR"
  run_local npm run build
)

log_step 3 "Package V1 backend"
run_tar_create "$BACKEND_DIR" "$ARCHIVE_PATH" run.py requirements.txt imagedetection
write_commit_marker "$MARKER_PATH" "$COMMIT_SHA"

log_step 4 "Upload V1 release"
run_scp "$ARCHIVE_PATH" "$REMOTE:/tmp/realguard-v1-backend.tgz"
run_scp "$MARKER_PATH" "$REMOTE:/tmp/realguard-v1.DEPLOYED_COMMIT"
run_scp -r "$FRONTEND_DIR/dist/." "$REMOTE:/var/www/realguard-frontend/"

log_step 5 "Activate V1 release"
run_remote "sudo tar -xzf /tmp/realguard-v1-backend.tgz -C /opt/realguard-server/RealGuard && sudo install -m 644 /tmp/realguard-v1.DEPLOYED_COMMIT /opt/realguard-server/DEPLOYED_COMMIT && sudo mkdir -p /opt/realguard-server/RealGuard/imagedetection/static/uploads && sudo chown -R ubuntu:ubuntu /opt/realguard-server/RealGuard/imagedetection/static/uploads && rm -f /tmp/realguard-v1-backend.tgz /tmp/realguard-v1.DEPLOYED_COMMIT && sudo systemctl restart realguard-backend.service && for _ in 1 2 3 4 5 6 7 8 9 10; do if curl -fsS http://127.0.0.1:5000/api/history/image-detections >/dev/null; then break; fi; sleep 1; done && systemctl is-active realguard-backend.service && curl -fsS http://127.0.0.1:5000/api/history/image-detections >/dev/null && curl -fsS -o /dev/null http://127.0.0.1/ && cat /opt/realguard-server/DEPLOYED_COMMIT"

printf '\nV1 deployed from commit %s to %s\n' "$COMMIT_SHA" "$REMOTE"
