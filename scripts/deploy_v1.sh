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
  REALGUARD_DETECTOR_PORT
                Default: 15001
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
DETECTOR_PORT="${REALGUARD_DETECTOR_PORT:-15001}"
TMP_DIR="$(mktemp -d)"
ARCHIVE_PATH="$TMP_DIR/realguard-v1-backend.tgz"
MARKER_PATH="$TMP_DIR/realguard-v1.DEPLOYED_COMMIT"
REMOTE="$(remote_target)"
trap 'rm -rf "$TMP_DIR"' EXIT

log_step 1 "Verify V1 backend"
run_local "$BACKEND_DIR/.venv-test/bin/python" -m compileall "$BACKEND_DIR/imagedetection"
run_local "$BACKEND_DIR/.venv-test/bin/python" -m py_compile "$BACKEND_DIR/detector_backend.py"
run_local "$BACKEND_DIR/.venv-test/bin/pytest" "$BACKEND_DIR/tests"

log_step 2 "Build V1 frontend"
(
  cd "$FRONTEND_DIR"
  run_local npm run build
)

log_step 3 "Package V1 backend"
run_tar_create "$BACKEND_DIR" "$ARCHIVE_PATH" run.py detector_backend.py requirements.txt imagedetection
write_commit_marker "$MARKER_PATH" "$COMMIT_SHA"

log_step 4 "Upload V1 release"
run_scp "$ARCHIVE_PATH" "$REMOTE:/tmp/realguard-v1-backend.tgz"
run_scp "$MARKER_PATH" "$REMOTE:/tmp/realguard-v1.DEPLOYED_COMMIT"
run_scp -r "$FRONTEND_DIR/dist/." "$REMOTE:/var/www/realguard-frontend/"

log_step 5 "Activate V1 release"
run_remote "sudo tar -xzf /tmp/realguard-v1-backend.tgz -C /opt/realguard-server/RealGuard && sudo install -m 644 /tmp/realguard-v1.DEPLOYED_COMMIT /opt/realguard-server/DEPLOYED_COMMIT && sudo mkdir -p /opt/realguard-server/RealGuard/imagedetection/static/uploads/aliyun-probes && sudo chown -R ubuntu:ubuntu /opt/realguard-server/RealGuard/imagedetection/static/uploads && sudo -u ubuntu /opt/realguard-server/.venv/bin/python -m pip install --no-cache-dir --quiet --upgrade -r /opt/realguard-server/RealGuard/requirements.txt && sudo -u ubuntu /opt/realguard-server/.venv/bin/python -m pip install --no-cache-dir --quiet --no-deps --upgrade 'invisible-watermark>=0.2.0' && (sudo systemctl cat realguard-backend.service | sed -n 's/^Environment=//p' | tr ' ' '\n' | grep -E '^REALGUARD_(DETECTION_)?DB_' | sudo tee /etc/realguard/detector-db.env >/dev/null || true) && sudo chmod 600 /etc/realguard/detector-db.env && sudo tee /etc/systemd/system/realguard-detector-backend.service >/dev/null <<'UNIT'
[Unit]
Description=RealGuard V1 Detector Backend
After=network.target mysql.service jianzhen-v2-backend.service
Requires=mysql.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/realguard-server/RealGuard
Environment=REALGUARD_DETECTOR_HOST=127.0.0.1
Environment=REALGUARD_DETECTOR_PORT=$DETECTOR_PORT
EnvironmentFile=-/etc/realguard/realguard-backend.env
EnvironmentFile=-/etc/realguard/detector-db.env
EnvironmentFile=-/etc/realguard/agent.env
ExecStart=/opt/realguard-server/.venv/bin/python /opt/realguard-server/RealGuard/detector_backend.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
sudo mkdir -p /etc/systemd/system/realguard-backend.service.d && sudo tee /etc/systemd/system/realguard-backend.service.d/40-detector-backend-url.conf >/dev/null <<'UNIT'
[Service]
Environment=REALGUARD_DETECTION_BACKEND_URL=http://127.0.0.1:$DETECTOR_PORT
UNIT
sudo bash -lc 'set -a; [ ! -f /etc/realguard/realguard-backend.env ] || . /etc/realguard/realguard-backend.env; [ ! -f /etc/realguard/detector-db.env ] || . /etc/realguard/detector-db.env; set +a; cd /opt/realguard-server/RealGuard && /opt/realguard-server/.venv/bin/python -m flask --app run:app admin-db-upgrade' && sudo systemctl daemon-reload && sudo systemctl enable realguard-detector-backend.service >/dev/null && sudo systemctl restart realguard-detector-backend.service && sudo systemctl restart realguard-backend.service && rm -f /tmp/realguard-v1-backend.tgz /tmp/realguard-v1.DEPLOYED_COMMIT && health_ready=0 && for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do if curl -fsS http://127.0.0.1:$DETECTOR_PORT/health >/dev/null && curl -fsS http://127.0.0.1:5000/api/history/image-detections >/dev/null; then health_ready=1; break; fi; sleep 1; done && test \"\$health_ready\" = \"1\" && systemctl is-active realguard-detector-backend.service && systemctl is-active realguard-backend.service && curl -fsS http://127.0.0.1:$DETECTOR_PORT/health >/dev/null && curl -fsS http://127.0.0.1:5000/api/history/image-detections >/dev/null && curl -fsS -o /dev/null http://127.0.0.1/ && curl -fsS http://127.0.0.1/admin/login | grep -q 'RealGuard 管理员认证' && admin_register_code=\$(curl -sS -o /tmp/realguard-admin-register.html -w '%{http_code}' http://127.0.0.1/admin/register) && test \"\$admin_register_code\" = \"403\" && ! grep -q '注册管理员' /tmp/realguard-admin-register.html && big_screen_code=\$(curl -sS -o /tmp/realguard-big-screen.json -w '%{http_code}' http://127.0.0.1/api/admin/big-screen) && test \"\$big_screen_code\" = \"401\" && cat /opt/realguard-server/DEPLOYED_COMMIT"

printf '\nV1 deployed from commit %s to %s\n' "$COMMIT_SHA" "$REMOTE"
