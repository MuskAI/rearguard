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
ALERT_WORKER_SERVICE_UNIT="$ROOT_DIR/deploy/systemd/realguard-alert-worker.service"
ALERT_WATCHDOG_SERVICE_UNIT="$ROOT_DIR/deploy/systemd/realguard-alert-watchdog.service"
ALERT_WATCHDOG_TIMER_UNIT="$ROOT_DIR/deploy/systemd/realguard-alert-watchdog.timer"
BACKUP_SERVICE_UNIT="$ROOT_DIR/deploy/systemd/realguard-backup.service"
BACKUP_TIMER_UNIT="$ROOT_DIR/deploy/systemd/realguard-backup.timer"
RESTORE_DRILL_SERVICE_UNIT="$ROOT_DIR/deploy/systemd/realguard-restore-drill.service"
RESTORE_DRILL_TIMER_UNIT="$ROOT_DIR/deploy/systemd/realguard-restore-drill.timer"
SECURITY_AUDIT_SERVICE_UNIT="$ROOT_DIR/deploy/systemd/realguard-security-audit-verify.service"
SECURITY_AUDIT_TIMER_UNIT="$ROOT_DIR/deploy/systemd/realguard-security-audit-verify.timer"
BACKUP_SCRIPT="$ROOT_DIR/scripts/remote/backup_realguard.sh"
RESTORE_VERIFY_SCRIPT="$ROOT_DIR/scripts/remote/verify_restore_realguard.sh"
PRIVACY_REPLAY_SCRIPT="$ROOT_DIR/scripts/remote/replay_privacy_erasure_tombstones.py"
ACTIVATE_SCRIPT="$ROOT_DIR/scripts/remote/activate_v1.sh"
DEPLOY_PATHS=(
  realguard-server-main/RealGuard/run.py
  realguard-server-main/RealGuard/detector_backend.py
  realguard-server-main/RealGuard/model_decision_contract.py
  realguard-server-main/RealGuard/requirements.txt
  realguard-server-main/RealGuard/requirements.lock
  realguard-server-main/RealGuard/imagedetection
  realguard-server-main/frontend
  realguard-server-main/deploy/nginx-realguard-frontend.conf
  deploy/nginx
  deploy/systemd
  scripts/deploy_v1.sh
  scripts/remote/backup_realguard.sh
  scripts/remote/verify_restore_realguard.sh
  scripts/remote/replay_privacy_erasure_tombstones.py
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
REMOTE_STAGE=""
REMOTE_STAGE_ACTIVE=0

cleanup() {
  local status=$?
  trap - EXIT
  set +e
  if [[ "$REMOTE_STAGE_ACTIVE" == "1" && -n "$REMOTE_STAGE" && "$DRY_RUN" != "1" ]]; then
    if ! run_remote "rm -rf -- '$REMOTE_STAGE'" >/dev/null; then
      printf 'Warning: could not remove remote V1 staging directory %s\n' "$REMOTE_STAGE" >&2
    fi
  fi
  rm -rf "$TMP_DIR"
  exit "$status"
}
trap cleanup EXIT

log_step 1 "Verify V1 backend and deployment scripts"
run_local "$BACKEND_DIR/.venv-test/bin/python" -m compileall "$BACKEND_DIR/imagedetection"
run_local "$BACKEND_DIR/.venv-test/bin/python" -m py_compile "$BACKEND_DIR/detector_backend.py"
run_local bash -n "$ACTIVATE_SCRIPT"
run_local bash -n "$BACKUP_SCRIPT"
run_local bash -n "$RESTORE_VERIFY_SCRIPT"
run_local "$BACKEND_DIR/.venv-test/bin/python" -m py_compile "$PRIVACY_REPLAY_SCRIPT"
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
run_tar_create "$BACKEND_DIR" "$ARCHIVE_PATH" \
  run.py detector_backend.py model_decision_contract.py \
  requirements.txt requirements.lock imagedetection
run_tar_create "$FRONTEND_DIR/dist" "$FRONTEND_ARCHIVE_PATH" .
run_tar_create "$NGINX_SNIPPETS_DIR" "$NGINX_SNIPPETS_ARCHIVE_PATH" .
write_commit_marker "$MARKER_PATH" "$COMMIT_SHA"
run_local curl -fsSL \
  --retry 5 \
  --retry-all-errors \
  --retry-delay 2 \
  --connect-timeout 15 \
  --max-time 120 \
  "$IP2REGION_XDB_URL" -o "$IP2REGION_XDB_PATH"
if [[ "$DRY_RUN" != "1" ]]; then
  printf '%s  %s\n' "$IP2REGION_XDB_SHA256" "$IP2REGION_XDB_PATH" | shasum -a 256 -c -
fi

log_step 4 "Upload V1 release into an isolated remote staging directory"
if [[ "$DRY_RUN" == "1" ]]; then
  REMOTE_STAGE="/tmp/realguard-v1-${COMMIT_SHA}.dry-run-$$"
  run_remote "umask 077; mkdir -m 700 -- '$REMOTE_STAGE'"
else
  REMOTE_STAGE="$(run_remote_capture "umask 077; mktemp -d '/tmp/realguard-v1-${COMMIT_SHA}.XXXXXXXXXX'")"
  if [[ ! "$REMOTE_STAGE" =~ ^/tmp/realguard-v1-[0-9a-f]{7,40}\.[A-Za-z0-9]+$ ]]; then
    printf 'Remote V1 staging path is invalid: %s\n' "$REMOTE_STAGE" >&2
    exit 1
  fi
fi
REMOTE_STAGE_ACTIVE=1
run_scp "$ARCHIVE_PATH" "$REMOTE:$REMOTE_STAGE/realguard-v1-backend.tgz"
run_scp "$FRONTEND_ARCHIVE_PATH" "$REMOTE:$REMOTE_STAGE/realguard-v1-frontend.tgz"
run_scp "$NGINX_SNIPPETS_ARCHIVE_PATH" "$REMOTE:$REMOTE_STAGE/realguard-nginx-snippets.tgz"
run_scp "$MARKER_PATH" "$REMOTE:$REMOTE_STAGE/realguard-v1.DEPLOYED_COMMIT"
run_scp "$IP2REGION_XDB_PATH" "$REMOTE:$REMOTE_STAGE/realguard-ip2region-v4.xdb"
run_scp "$NGINX_CONFIG" "$REMOTE:$REMOTE_STAGE/realguard-frontend.nginx.conf"
run_scp "$HTTPS_NGINX_CONFIG" "$REMOTE:$REMOTE_STAGE/realguard-https.nginx.conf"
run_scp "$WEB_SERVICE_UNIT" "$REMOTE:$REMOTE_STAGE/realguard-backend.service"
run_scp "$DETECTOR_SERVICE_UNIT" "$REMOTE:$REMOTE_STAGE/realguard-detector-backend.service"
run_scp "$DEVELOPER_WORKER_SERVICE_UNIT" "$REMOTE:$REMOTE_STAGE/realguard-developer-worker.service"
run_scp "$ALERT_WORKER_SERVICE_UNIT" "$REMOTE:$REMOTE_STAGE/realguard-alert-worker.service"
run_scp "$ALERT_WATCHDOG_SERVICE_UNIT" "$REMOTE:$REMOTE_STAGE/realguard-alert-watchdog.service"
run_scp "$ALERT_WATCHDOG_TIMER_UNIT" "$REMOTE:$REMOTE_STAGE/realguard-alert-watchdog.timer"
run_scp "$BACKUP_SERVICE_UNIT" "$REMOTE:$REMOTE_STAGE/realguard-backup.service"
run_scp "$BACKUP_TIMER_UNIT" "$REMOTE:$REMOTE_STAGE/realguard-backup.timer"
run_scp "$RESTORE_DRILL_SERVICE_UNIT" "$REMOTE:$REMOTE_STAGE/realguard-restore-drill.service"
run_scp "$RESTORE_DRILL_TIMER_UNIT" "$REMOTE:$REMOTE_STAGE/realguard-restore-drill.timer"
run_scp "$SECURITY_AUDIT_SERVICE_UNIT" "$REMOTE:$REMOTE_STAGE/realguard-security-audit-verify.service"
run_scp "$SECURITY_AUDIT_TIMER_UNIT" "$REMOTE:$REMOTE_STAGE/realguard-security-audit-verify.timer"
run_scp "$BACKUP_SCRIPT" "$REMOTE:$REMOTE_STAGE/realguard-backup"
run_scp "$RESTORE_VERIFY_SCRIPT" "$REMOTE:$REMOTE_STAGE/realguard-restore-verify"
run_scp "$PRIVACY_REPLAY_SCRIPT" "$REMOTE:$REMOTE_STAGE/realguard-replay-privacy-erasures"
run_scp "$ACTIVATE_SCRIPT" "$REMOTE:$REMOTE_STAGE/realguard-activate-v1.sh"

log_step 5 "Promote and activate the staged V1 release under a release lock"
remote_activation="$(cat <<EOF
set -euo pipefail
stage='$REMOTE_STAGE'
promoted=0
fixed_files='realguard-v1-backend.tgz realguard-v1-frontend.tgz realguard-nginx-snippets.tgz realguard-v1.DEPLOYED_COMMIT realguard-ip2region-v4.xdb realguard-frontend.nginx.conf realguard-https.nginx.conf realguard-backend.service realguard-detector-backend.service realguard-developer-worker.service realguard-alert-worker.service realguard-alert-watchdog.service realguard-alert-watchdog.timer realguard-backup.service realguard-backup.timer realguard-restore-drill.service realguard-restore-drill.timer realguard-security-audit-verify.service realguard-security-audit-verify.timer realguard-backup realguard-restore-verify realguard-replay-privacy-erasures realguard-activate-v1.sh'
cleanup_stage() {
  if [[ "\$promoted" == "1" ]]; then
    for name in \$fixed_files; do
      rm -f -- "/tmp/\$name"
    done
  fi
  rm -rf -- "\$stage"
}
trap cleanup_stage EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
deploy_user=\$(id -un)
deploy_group=\$(id -gn)
sudo install -d -m 755 -o "\$deploy_user" -g "\$deploy_group" /opt/realguard-data/deploy-locks
exec 8>/opt/realguard-data/deploy-locks/v1-promotion.lock
flock -w 7200 8
promoted=1
cp -f --remove-destination -- "\$stage"/* /tmp/
IP2REGION_XDB_SHA256='$IP2REGION_XDB_SHA256' \
  REALGUARD_DETECTOR_PORT='$DETECTOR_PORT' \
  bash /tmp/realguard-activate-v1.sh
EOF
)"
run_remote "$remote_activation"
REMOTE_STAGE_ACTIVE=0

printf '\nV1 deployed from commit %s to %s\n' "$COMMIT_SHA" "$REMOTE"
