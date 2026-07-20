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
REMOTE_STAGE=""
REMOTE_STAGE_ACTIVE=0

cleanup() {
  local status=$?
  trap - EXIT
  set +e
  if [[ "$REMOTE_STAGE_ACTIVE" == "1" && -n "$REMOTE_STAGE" && "$DRY_RUN" != "1" ]]; then
    if ! run_remote "rm -rf -- '$REMOTE_STAGE'" >/dev/null; then
      printf 'Warning: could not remove remote V2 staging directory %s\n' "$REMOTE_STAGE" >&2
    fi
  fi
  rm -rf "$TMP_DIR"
  exit "$status"
}
trap cleanup EXIT

log_step 1 "Verify V2 backend"
run_local "$BACKEND_DIR/.venv/bin/python" -m compileall "$BACKEND_DIR/app"
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

log_step 4 "Upload V2 release into an isolated remote staging directory"
if [[ "$DRY_RUN" == "1" ]]; then
  REMOTE_STAGE="/tmp/jianzhen-v2-${COMMIT_SHA}.dry-run-$$"
  run_remote "umask 077; mkdir -m 700 -- '$REMOTE_STAGE'"
else
  REMOTE_STAGE="$(run_remote_capture "umask 077; mktemp -d '/tmp/jianzhen-v2-${COMMIT_SHA}.XXXXXXXXXX'")"
  if [[ ! "$REMOTE_STAGE" =~ ^/tmp/jianzhen-v2-[0-9a-f]{7,40}\.[A-Za-z0-9]+$ ]]; then
    printf 'Remote V2 staging path is invalid: %s\n' "$REMOTE_STAGE" >&2
    exit 1
  fi
fi
REMOTE_STAGE_ACTIVE=1
run_scp "$ARCHIVE_PATH" "$REMOTE:$REMOTE_STAGE/jianzhen-v2-backend.tgz"
run_scp "$FRONTEND_ARCHIVE_PATH" "$REMOTE:$REMOTE_STAGE/jianzhen-v2-frontend.tgz"
run_scp "$MARKER_PATH" "$REMOTE:$REMOTE_STAGE/jianzhen-v2.DEPLOYED_COMMIT"
run_scp "$SERVICE_UNIT" "$REMOTE:$REMOTE_STAGE/jianzhen-v2-backend.service"
run_scp "$ACTIVATE_SCRIPT" "$REMOTE:$REMOTE_STAGE/jianzhen-activate-v2.sh"

log_step 5 "Create and verify a pre-migration backup"
run_remote "sudo bash -lc 'set -euo pipefail; test -x /usr/local/sbin/realguard-backup; set -a; for env_file in /etc/realguard/session.env /etc/realguard/realguard-backend.env /etc/realguard/detector-db.env /etc/realguard/jianzhen-v2.env /etc/realguard/backup.env; do [ ! -f \"\$env_file\" ] || . \"\$env_file\"; done; set +a; backup_output=\$(/usr/local/sbin/realguard-backup 2>&1); printf \"%s\\n\" \"\$backup_output\"; backup_dir=\$(printf \"%s\\n\" \"\$backup_output\" | sed -n \"s/^RealGuard backup completed: //p\" | tail -n 1); test -n \"\$backup_dir\"; test -d \"\$backup_dir\"; cd \"\$backup_dir\"; sha256sum -c SHA256SUMS'"

log_step 6 "Promote and activate the staged V2 release under a release lock"
remote_activation="$(cat <<EOF
set -euo pipefail
stage='$REMOTE_STAGE'
promoted=0
fixed_files='jianzhen-v2-backend.tgz jianzhen-v2-frontend.tgz jianzhen-v2.DEPLOYED_COMMIT jianzhen-v2-backend.service jianzhen-activate-v2.sh'
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
exec 8>/opt/realguard-data/deploy-locks/v2-promotion.lock
flock -w 7200 8
promoted=1
cp -f --remove-destination -- "\$stage"/* /tmp/
bash /tmp/jianzhen-activate-v2.sh
EOF
)"
run_remote "$remote_activation"
REMOTE_STAGE_ACTIVE=0

printf '\nV2 deployed from commit %s to %s\n' "$COMMIT_SHA" "$REMOTE"
