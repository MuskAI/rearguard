#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./deploy_common.sh
source "$SCRIPT_DIR/deploy_common.sh"

usage() {
  cat <<'EOF'
Usage: DEPLOY_SSH_KEY=/path/to/key ./scripts/deploy_converge.sh [all|v1|v2|gpu]

Optional environment variables:
  DEPLOY_HOST   Default: 124.221.92.85
  DEPLOY_USER   Default: ubuntu
  DRY_RUN=1     Print commands without executing them

The script checks current deployment status first. A target is skipped only when
its commit, service and readiness checks all match; unhealthy targets are repaired.
EOF
}

if [[ "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

TARGET="${1:-all}"

case "$TARGET" in
  all|v1|v2|gpu) ;;
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
DEPLOY_GPU="$ROOT_DIR/scripts/deploy_detection_service.sh"

status_output="$(FORMAT=env DEPLOY_SSH_KEY="$DEPLOY_SSH_KEY" DEPLOY_HOST="$DEPLOY_HOST" DEPLOY_USER="$DEPLOY_USER" DRY_RUN="$DRY_RUN" bash "$STATUS_SCRIPT" "$TARGET")"
v1_expected="$(printf '%s\n' "$status_output" | sed -n 's/^v1_expected=//p' | tail -1)"
v2_expected="$(printf '%s\n' "$status_output" | sed -n 's/^v2_expected=//p' | tail -1)"
gpu_expected="$(printf '%s\n' "$status_output" | sed -n 's/^gpu_expected=//p' | tail -1)"
v1_repo_state="$(printf '%s\n' "$status_output" | sed -n 's/^v1_repo_state=//p' | tail -1)"
v2_repo_state="$(printf '%s\n' "$status_output" | sed -n 's/^v2_repo_state=//p' | tail -1)"
gpu_repo_state="$(printf '%s\n' "$status_output" | sed -n 's/^gpu_repo_state=//p' | tail -1)"
v1_service="$(printf '%s\n' "$status_output" | sed -n 's/^v1_service=//p' | tail -1)"
v1_internal="$(printf '%s\n' "$status_output" | sed -n 's/^v1_internal_http=//p' | tail -1)"
v1_external="$(printf '%s\n' "$status_output" | sed -n 's/^v1_external_http=//p' | tail -1)"
v2_service="$(printf '%s\n' "$status_output" | sed -n 's/^v2_service=//p' | tail -1)"
v2_internal="$(printf '%s\n' "$status_output" | sed -n 's/^v2_internal_http=//p' | tail -1)"
v2_external="$(printf '%s\n' "$status_output" | sed -n 's/^v2_external_http=//p' | tail -1)"
gpu_model_service="$(printf '%s\n' "$status_output" | sed -n 's/^gpu_model_service=//p' | tail -1)"
gpu_watermark_service="$(printf '%s\n' "$status_output" | sed -n 's/^gpu_watermark_service=//p' | tail -1)"
gpu_yolo_service="$(printf '%s\n' "$status_output" | sed -n 's/^gpu_yolo_service=//p' | tail -1)"
gpu_model_enabled="$(printf '%s\n' "$status_output" | sed -n 's/^gpu_model_enabled=//p' | tail -1)"
gpu_watermark_enabled="$(printf '%s\n' "$status_output" | sed -n 's/^gpu_watermark_enabled=//p' | tail -1)"
gpu_yolo_enabled="$(printf '%s\n' "$status_output" | sed -n 's/^gpu_yolo_enabled=//p' | tail -1)"
gpu_model_tunnel="$(printf '%s\n' "$status_output" | sed -n 's/^gpu_model_tunnel=//p' | tail -1)"
gpu_precheck_tunnel="$(printf '%s\n' "$status_output" | sed -n 's/^gpu_precheck_tunnel=//p' | tail -1)"
gpu_model_runtime="$(printf '%s\n' "$status_output" | sed -n 's/^gpu_model_runtime=//p' | tail -1)"
gpu_yolo_runtime="$(printf '%s\n' "$status_output" | sed -n 's/^gpu_yolo_runtime=//p' | tail -1)"
gpu_watermark_http="$(printf '%s\n' "$status_output" | sed -n 's/^gpu_watermark_http=//p' | tail -1)"
gpu_public_detector_service="$(printf '%s\n' "$status_output" | sed -n 's/^gpu_public_detector_service=//p' | tail -1)"
gpu_public_runtime="$(printf '%s\n' "$status_output" | sed -n 's/^gpu_public_runtime=//p' | tail -1)"
gpu_service="unhealthy"
gpu_internal="000"
gpu_external="000"
if [[ "$gpu_model_service" == "active" && "$gpu_watermark_service" == "active" \
    && "$gpu_yolo_service" == "active" && "$gpu_model_enabled" == "enabled" \
    && "$gpu_watermark_enabled" == "enabled" && "$gpu_yolo_enabled" == "enabled" \
    && "$gpu_model_tunnel" == "active" && "$gpu_precheck_tunnel" == "active" ]]; then
  gpu_service="active"
fi
if [[ "$gpu_model_runtime" == "ready" && "$gpu_yolo_runtime" == "ready" \
    && "$gpu_watermark_http" == "200" ]]; then
  gpu_internal="200"
fi
if [[ "$gpu_public_detector_service" == "active" && "$gpu_public_runtime" == "ready" ]]; then
  gpu_external="200"
fi
validate_repo_state() {
  case "$1" in
    match|mismatch|missing|dry-run) ;;
    *)
      echo "Deployment status output was incomplete or invalid" >&2
      exit 1
      ;;
  esac
}
if [[ "$TARGET" == "all" || "$TARGET" == "v1" ]]; then
  validate_repo_state "$v1_repo_state"
  [[ "$v1_expected" =~ ^[0-9a-f]{7,40}$ ]] || {
    echo "V1 expected commit was missing or invalid" >&2
    exit 1
  }
fi
if [[ "$TARGET" == "all" || "$TARGET" == "v2" ]]; then
  validate_repo_state "$v2_repo_state"
  [[ "$v2_expected" =~ ^[0-9a-f]{7,40}$ ]] || {
    echo "V2 expected commit was missing or invalid" >&2
    exit 1
  }
fi
if [[ "$TARGET" == "all" || "$TARGET" == "gpu" ]]; then
  validate_repo_state "$gpu_repo_state"
  [[ "$gpu_expected" =~ ^[0-9a-f]{7,40}$ ]] || {
    echo "GPU expected commit was missing or invalid" >&2
    exit 1
  }
fi

deploy_if_needed() {
  local label="$1"
  local state="$2"
  local script_path="$3"
  local service_state="$4"
  local internal_code="$5"
  local external_code="$6"

  if [[ "$state" == "match" && "$service_state" == "active" \
      && "$internal_code" == "200" && "$external_code" == "200" ]]; then
    printf '%s already converged; skipping.\n' "$label"
    return 0
  fi

  if [[ "$state" == "dry-run" ]]; then
    printf '%s status unavailable in DRY RUN; would publish target.\n' "$label"
    DEPLOY_SSH_KEY="$DEPLOY_SSH_KEY" DEPLOY_HOST="$DEPLOY_HOST" DEPLOY_USER="$DEPLOY_USER" DRY_RUN="$DRY_RUN" bash "$script_path"
    return 0
  fi

  printf '%s repo_state=%s; publishing target.\n' "$label" "$state"
  DEPLOY_SSH_KEY="$DEPLOY_SSH_KEY" DEPLOY_HOST="$DEPLOY_HOST" DEPLOY_USER="$DEPLOY_USER" DRY_RUN="$DRY_RUN" bash "$script_path"
}

if [[ "$TARGET" == "all" || "$TARGET" == "gpu" ]]; then
  deploy_if_needed "GPU" "$gpu_repo_state" "$DEPLOY_GPU" "$gpu_service" "$gpu_internal" "$gpu_external"
fi

if [[ "$TARGET" == "all" || "$TARGET" == "v1" ]]; then
  deploy_if_needed "V1" "$v1_repo_state" "$DEPLOY_V1" "$v1_service" "$v1_internal" "$v1_external"
fi

if [[ "$TARGET" == "all" || "$TARGET" == "v2" ]]; then
  deploy_if_needed "V2" "$v2_repo_state" "$DEPLOY_V2" "$v2_service" "$v2_internal" "$v2_external"
fi

final_status_check() {
  local label="$1"
  local expected="$2"
  local target="$3"
  printf '\nFinal %s status (expected commit %s):\n' "$label" "$expected"
  STRICT=1 EXPECT_COMMIT="$expected" \
    DEPLOY_SSH_KEY="$DEPLOY_SSH_KEY" \
    DEPLOY_HOST="$DEPLOY_HOST" \
    DEPLOY_USER="$DEPLOY_USER" \
    DRY_RUN="$DRY_RUN" \
    bash "$STATUS_SCRIPT" "$target"
}

if [[ "$TARGET" == "all" || "$TARGET" == "gpu" ]]; then
  final_status_check "GPU" "$gpu_expected" gpu
fi
if [[ "$TARGET" == "all" || "$TARGET" == "v1" ]]; then
  final_status_check "V1" "$v1_expected" v1
fi
if [[ "$TARGET" == "all" || "$TARGET" == "v2" ]]; then
  final_status_check "V2" "$v2_expected" v2
fi
