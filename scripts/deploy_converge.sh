#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./deploy_common.sh
source "$SCRIPT_DIR/deploy_common.sh"

usage() {
  cat <<'EOF'
Usage: DEPLOY_SSH_KEY=/path/to/key ./scripts/deploy_converge.sh [all|v1|v2]

Optional environment variables:
  DEPLOY_HOST   Default: 124.222.3.205
  DEPLOY_USER   Default: ubuntu
  DRY_RUN=1     Print commands without executing them

The script checks current deployment status first. Targets with repo_state=match
are skipped; mismatched targets are republished via deploy_v1.sh / deploy_v2.sh.
EOF
}

if [[ "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

TARGET="${1:-all}"

case "$TARGET" in
  all|v1|v2) ;;
  *)
    usage >&2
    exit 1
    ;;
esac

require_ssh_key

ROOT_DIR="$(repo_root)"
STATUS_SCRIPT="$ROOT_DIR/scripts/check_deploy_status.sh"
DEPLOY_V1="$ROOT_DIR/scripts/deploy_v1.sh"
DEPLOY_V2="$ROOT_DIR/scripts/deploy_v2.sh"

status_output="$(FORMAT=env DEPLOY_SSH_KEY="$DEPLOY_SSH_KEY" DEPLOY_HOST="$DEPLOY_HOST" DEPLOY_USER="$DEPLOY_USER" DRY_RUN="$DRY_RUN" bash "$STATUS_SCRIPT" all)"
eval "$status_output"

deploy_if_needed() {
  local label="$1"
  local state="$2"
  local script_path="$3"

  if [[ "$state" == "match" ]]; then
    printf '%s already converged; skipping.\n' "$label"
    return 0
  fi

  printf '%s repo_state=%s; publishing target.\n' "$label" "$state"
  DEPLOY_SSH_KEY="$DEPLOY_SSH_KEY" DEPLOY_HOST="$DEPLOY_HOST" DEPLOY_USER="$DEPLOY_USER" DRY_RUN="$DRY_RUN" bash "$script_path"
}

if [[ "$TARGET" == "all" || "$TARGET" == "v1" ]]; then
  deploy_if_needed "V1" "$v1_repo_state" "$DEPLOY_V1"
fi

if [[ "$TARGET" == "all" || "$TARGET" == "v2" ]]; then
  deploy_if_needed "V2" "$v2_repo_state" "$DEPLOY_V2"
fi

printf '\nFinal status:\n'
DEPLOY_SSH_KEY="$DEPLOY_SSH_KEY" DEPLOY_HOST="$DEPLOY_HOST" DEPLOY_USER="$DEPLOY_USER" DRY_RUN="$DRY_RUN" bash "$STATUS_SCRIPT" "$TARGET"
