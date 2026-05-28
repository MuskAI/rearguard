#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./deploy_common.sh
source "$SCRIPT_DIR/deploy_common.sh"

usage() {
  cat <<'EOF'
Usage: DEPLOY_SSH_KEY=/path/to/key ./scripts/check_deploy_status.sh [all|v1|v2]

Optional environment variables:
  DEPLOY_HOST     Default: 124.222.3.205
  DEPLOY_USER     Default: ubuntu
  EXPECT_COMMIT   Compare deployed commit against this commit SHA
  STRICT=1        Exit non-zero when service or health checks are unhealthy
  DRY_RUN=1       Print commands without executing them
EOF
}

if [[ "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

TARGET="${1:-all}"
STRICT="${STRICT:-0}"
ROOT_DIR="$(repo_root)"
LOCAL_HEAD="$(git -C "$ROOT_DIR" rev-parse --short HEAD)"
EXPECTED_COMMIT="${EXPECT_COMMIT:-}"

case "$TARGET" in
  all|v1|v2) ;;
  *)
    usage >&2
    exit 1
    ;;
esac

require_ssh_key

status=0

check_target() {
  local label="$1"
  local marker_path="$2"
  local service_name="$3"
  local internal_url="$4"
  local external_url="$5"
  local expected="$EXPECTED_COMMIT"
  local output deployed service_state internal_code external_code commit_state
  local remote_script

  if [[ -z "$expected" ]]; then
    expected="$LOCAL_HEAD"
  fi

  remote_script=$(cat <<EOF
deployed=\$(cat '$marker_path' 2>/dev/null || printf 'missing')
service=\$(systemctl is-active '$service_name' 2>/dev/null || printf 'unknown')
internal=\$(curl -sS -o /dev/null -w '%{http_code}' '$internal_url' || printf '000')
external=\$(curl -sS -o /dev/null -w '%{http_code}' '$external_url' || printf '000')
printf 'deployed=%s\nservice=%s\ninternal=%s\nexternal=%s\n' "\$deployed" "\$service" "\$internal" "\$external"
EOF
)

  output="$(run_remote_capture "$remote_script")"
  deployed="missing"
  service_state="unknown"
  internal_code="000"
  external_code="000"

  while IFS='=' read -r key value; do
    case "$key" in
      deployed) deployed="$value" ;;
      service) service_state="$value" ;;
      internal) internal_code="$value" ;;
      external) external_code="$value" ;;
    esac
  done <<< "$output"

  if [[ "$deployed" == "missing" ]]; then
    commit_state="missing"
  elif [[ "$deployed" == "$expected" ]]; then
    commit_state="match"
  else
    commit_state="mismatch"
  fi

  printf '\n[%s]\n' "$label"
  printf 'local_head:    %s\n' "$LOCAL_HEAD"
  printf 'expected:      %s\n' "$expected"
  printf 'deployed:      %s (%s)\n' "$deployed" "$commit_state"
  printf 'service:       %s\n' "$service_state"
  printf 'internal_http: %s\n' "$internal_code"
  printf 'external_http: %s\n' "$external_code"

  if [[ -n "$EXPECTED_COMMIT" && "$commit_state" != "match" ]]; then
    status=1
  fi
  if [[ "$STRICT" == "1" ]]; then
    if [[ "$service_state" != "active" || "$internal_code" != "200" || "$external_code" != "200" ]]; then
      status=1
    fi
  fi
}

if [[ "$TARGET" == "all" || "$TARGET" == "v1" ]]; then
  check_target "V1" \
    "/opt/realguard-server/DEPLOYED_COMMIT" \
    "realguard-backend.service" \
    "http://127.0.0.1:5000/api/history/image-detections" \
    "http://127.0.0.1/api/history/image-detections"
fi

if [[ "$TARGET" == "all" || "$TARGET" == "v2" ]]; then
  check_target "V2" \
    "/opt/jianzhen-v2/DEPLOYED_COMMIT" \
    "jianzhen-v2-backend.service" \
    "http://127.0.0.1:8848/api/health" \
    "http://127.0.0.1/v2-api/health"
fi

exit "$status"
