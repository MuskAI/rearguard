#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./deploy_common.sh
source "$SCRIPT_DIR/deploy_common.sh"

usage() {
  cat <<'EOF'
Usage: DEPLOY_SSH_KEY=/path/to/key ./scripts/check_deploy_status.sh [all|v1|v2]

Optional environment variables:
  DEPLOY_HOST     Default: 124.221.92.85
  DEPLOY_USER     Default: ubuntu
  EXPECT_COMMIT   Override the auto-derived expected commit SHA
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
FORMAT="${FORMAT:-text}"
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
  shift 5
  local relevant_paths=("$@")
  local expected="$EXPECTED_COMMIT"
  local expected_source="override"
  local output deployed service_state internal_code external_code repo_state code_state
  local remote_script

  if [[ "$DRY_RUN" == "1" ]]; then
    deployed="dry-run"
    service_state="dry-run"
    internal_code="dry-run"
    external_code="dry-run"
    repo_state="dry-run"
    code_state="dry-run"
    if [[ -z "$expected" ]]; then
      expected="$(latest_commit_for_paths "${relevant_paths[@]}")"
      if [[ -n "$expected" ]]; then
        expected_source="target_paths"
      else
        expected="$LOCAL_HEAD"
        expected_source="repo_head_fallback"
      fi
    fi

    if [[ "$FORMAT" == "env" ]]; then
      local prefix
      prefix="$(printf '%s' "$label" | tr '[:upper:]' '[:lower:]')"
      printf '%s_local_head=%s\n' "$prefix" "$LOCAL_HEAD"
      printf '%s_expected=%s\n' "$prefix" "$expected"
      printf '%s_expected_from=%s\n' "$prefix" "$expected_source"
      printf '%s_deployed=%s\n' "$prefix" "$deployed"
      printf '%s_repo_state=%s\n' "$prefix" "$repo_state"
      printf '%s_code_state=%s\n' "$prefix" "$code_state"
      printf '%s_service=%s\n' "$prefix" "$service_state"
      printf '%s_internal_http=%s\n' "$prefix" "$internal_code"
      printf '%s_external_http=%s\n' "$prefix" "$external_code"
    else
      printf '\n[%s]\n' "$label"
      printf 'local_head:    %s\n' "$LOCAL_HEAD"
      printf 'expected:      %s\n' "$expected"
      printf 'expected_from: %s\n' "$expected_source"
      printf 'deployed:      %s\n' "$deployed"
      printf 'repo_state:    %s\n' "$repo_state"
      printf 'code_state:    %s\n' "$code_state"
      printf 'service:       %s\n' "$service_state"
      printf 'internal_http: %s\n' "$internal_code"
      printf 'external_http: %s\n' "$external_code"
    fi
    return 0
  fi

  if [[ -z "$expected" ]]; then
    expected="$(git -C "$ROOT_DIR" log -1 --format=%h -- "${relevant_paths[@]}")"
    if [[ -n "$expected" ]]; then
      expected_source="target_paths"
    else
      expected="$LOCAL_HEAD"
      expected_source="repo_head_fallback"
    fi
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
    repo_state="missing"
    code_state="missing"
  elif [[ "$deployed" == "$expected" ]]; then
    repo_state="match"
    code_state="current"
  elif ! git -C "$ROOT_DIR" rev-parse --verify "$deployed^{commit}" >/dev/null 2>&1; then
    repo_state="mismatch"
    code_state="unknown"
  elif git -C "$ROOT_DIR" diff --quiet "$deployed" "$expected" -- "${relevant_paths[@]}"; then
    repo_state="mismatch"
    code_state="current"
  else
    repo_state="mismatch"
    code_state="stale"
  fi

  if [[ "$FORMAT" == "env" ]]; then
    local prefix
    prefix="$(printf '%s' "$label" | tr '[:upper:]' '[:lower:]')"
    printf '%s_local_head=%s\n' "$prefix" "$LOCAL_HEAD"
    printf '%s_expected=%s\n' "$prefix" "$expected"
    printf '%s_expected_from=%s\n' "$prefix" "$expected_source"
    printf '%s_deployed=%s\n' "$prefix" "$deployed"
    printf '%s_repo_state=%s\n' "$prefix" "$repo_state"
    printf '%s_code_state=%s\n' "$prefix" "$code_state"
    printf '%s_service=%s\n' "$prefix" "$service_state"
    printf '%s_internal_http=%s\n' "$prefix" "$internal_code"
    printf '%s_external_http=%s\n' "$prefix" "$external_code"
  else
    printf '\n[%s]\n' "$label"
    printf 'local_head:    %s\n' "$LOCAL_HEAD"
    printf 'expected:      %s\n' "$expected"
    printf 'expected_from: %s\n' "$expected_source"
    printf 'deployed:      %s\n' "$deployed"
    printf 'repo_state:    %s\n' "$repo_state"
    printf 'code_state:    %s\n' "$code_state"
    printf 'service:       %s\n' "$service_state"
    printf 'internal_http: %s\n' "$internal_code"
    printf 'external_http: %s\n' "$external_code"
  fi

  if [[ -n "$EXPECTED_COMMIT" && "$repo_state" != "match" ]]; then
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
    "https://www.rrreal.cn/api/history/image-detections" \
    "realguard-server-main/RealGuard" \
    "realguard-server-main/frontend" \
    "realguard-server-main/deploy/nginx-realguard-frontend.conf" \
    "deploy/nginx/realguard.conf" \
    "scripts/deploy_v1.sh" \
    "scripts/deploy_common.sh"
fi

if [[ "$TARGET" == "all" || "$TARGET" == "v2" ]]; then
  check_target "V2" \
    "/opt/jianzhen-v2/DEPLOYED_COMMIT" \
    "jianzhen-v2-backend.service" \
    "http://127.0.0.1:8848/api/health" \
    "https://www.rrreal.cn/v2-api/health" \
    "v2-agent/backend" \
    "v2-agent/frontend" \
    "scripts/deploy_v2.sh" \
    "scripts/deploy_common.sh"
fi

exit "$status"
