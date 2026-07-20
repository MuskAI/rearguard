#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./deploy_common.sh
source "$SCRIPT_DIR/deploy_common.sh"

usage() {
  cat <<'EOF'
Usage: DEPLOY_SSH_KEY=/path/to/key ./scripts/check_deploy_status.sh [all|v1|v2|gpu]

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
  all|v1|v2|gpu) ;;
  *)
    usage >&2
    exit 1
    ;;
esac

if [[ "$TARGET" != "gpu" ]]; then
  require_ssh_key
fi

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
internal=\$(curl -sS --connect-timeout 3 --max-time 12 -o /dev/null -w '%{http_code}' '$internal_url' || printf '000')
external=\$(curl -sS --connect-timeout 3 --max-time 12 -o /dev/null -w '%{http_code}' '$external_url' || printf '000')
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
    "http://127.0.0.1:5000/api/ready" \
    "https://www.rrreal.cn/api/ready" \
    "realguard-server-main/RealGuard/run.py" \
    "realguard-server-main/RealGuard/detector_backend.py" \
    "realguard-server-main/RealGuard/model_decision_contract.py" \
    "realguard-server-main/RealGuard/requirements.txt" \
    "realguard-server-main/RealGuard/requirements.lock" \
    "realguard-server-main/RealGuard/imagedetection" \
    "realguard-server-main/frontend" \
    "realguard-server-main/deploy/nginx-realguard-frontend.conf" \
    "deploy/nginx/realguard.conf" \
    "deploy/nginx/snippets" \
    "deploy/systemd" \
    "scripts/deploy_v1.sh" \
    "scripts/remote/backup_realguard.sh" \
    "scripts/remote/verify_restore_realguard.sh" \
    "scripts/remote/activate_v1.sh" \
    "scripts/deploy_common.sh"
fi

if [[ "$TARGET" == "all" || "$TARGET" == "v2" ]]; then
  check_target "V2" \
    "/opt/jianzhen-v2/DEPLOYED_COMMIT" \
    "jianzhen-v2-backend.service" \
    "http://127.0.0.1:8848/api/ready" \
    "https://www.rrreal.cn/v2-api/ready" \
    "v2-agent/backend" \
    "v2-agent/frontend" \
    "deploy/systemd/jianzhen-v2-backend.service" \
    "scripts/deploy_v2.sh" \
    "scripts/remote/activate_v2.sh" \
    "scripts/deploy_common.sh"
fi

if [[ "$TARGET" == "all" || "$TARGET" == "gpu" ]]; then
  gpu_host="${GPU_DEPLOY_HOST:-10.1.20.66}"
  gpu_user="${GPU_DEPLOY_USER:-ymk}"
  gpu_port="${GPU_DEPLOY_PORT:-22}"
  gpu_key="${GPU_DEPLOY_SSH_KEY:-}"
  gpu_paths=(
    services/realguard-detection
    services/watermark-precheck
    services/yolo-watermark
    scripts/deploy_detection_service.sh
    scripts/remote/activate_detection_service.sh
    scripts/remote/activate_public_gpu_deploy.sh
    scripts/remote/rollback_detection_service.sh
    scripts/remote/rollback_public_gpu_deploy.sh
  )
  gpu_expected="${EXPECTED_COMMIT:-$(git -C "$ROOT_DIR" log -1 --format=%h -- "${gpu_paths[@]}")}"
  gpu_known_hosts="${GPU_DEPLOY_KNOWN_HOSTS_FILE:-${HOME:+$HOME/.ssh/known_hosts}}"
  if [[ -z "$gpu_known_hosts" || ! -f "$gpu_known_hosts" || ! -r "$gpu_known_hosts" ]]; then
    printf 'GPU known-hosts file is missing or unreadable: %s\n' "$gpu_known_hosts" >&2
    exit 2
  fi
  gpu_ssh=(-p "$gpu_port" -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$gpu_known_hosts")
  if [[ -n "$gpu_key" ]]; then
    gpu_ssh+=(-i "$gpu_key" -o IdentitiesOnly=yes)
  fi
  if [[ "$DRY_RUN" == "1" ]]; then
    gpu_output=$'deployed=dry-run\nmodel=dry-run\nwatermark=dry-run\nyolo=dry-run\nmodel_enabled=dry-run\nwatermark_enabled=dry-run\nyolo_enabled=dry-run\nmodel_tunnel=dry-run\nprecheck_tunnel=dry-run\nmodel_runtime=dry-run\nyolo_runtime=dry-run\nwatermark_http=dry-run\n'
    public_gpu_output=$'config_commit=dry-run\ndetector_service=dry-run\npublic_runtime=dry-run\n'
  else
    gpu_output="$(ssh "${gpu_ssh[@]}" "$gpu_user@$gpu_host" '
      deployed=$(cat /home/ymk/realguard-detection-releases/current/DEPLOYED_COMMIT 2>/dev/null || printf missing)
      model=$(systemctl is-active realguard-detection.service 2>/dev/null || printf unknown)
      watermark=$(systemctl is-active realguard-watermark-precheck.service 2>/dev/null || printf unknown)
      yolo=$(systemctl is-active realguard-yolo-watermark.service 2>/dev/null || printf unknown)
      model_enabled=$(systemctl is-enabled realguard-detection.service 2>/dev/null || printf unknown)
      watermark_enabled=$(systemctl is-enabled realguard-watermark-precheck.service 2>/dev/null || printf unknown)
      yolo_enabled=$(systemctl is-enabled realguard-yolo-watermark.service 2>/dev/null || printf unknown)
      model_tunnel=$(systemctl is-active realguard-web-tunnel.service 2>/dev/null || printf unknown)
      precheck_tunnel=$(systemctl is-active realguard-v2-precheck-tunnel.service 2>/dev/null || printf unknown)
      model_pid=$(systemctl show realguard-detection.service -p MainPID --value 2>/dev/null || printf 0)
      if [[ "$model_pid" =~ ^[1-9][0-9]*$ && -r "/proc/$model_pid/environ" ]]; then
        model_token=$(tr '\''\0'\'' '\''\n'\'' < "/proc/$model_pid/environ" | sed -n '\''s/^REALGUARD_MODEL_INTERNAL_TOKEN=//p'\'' | head -1)
        response_key_id=$(tr '\''\0'\'' '\''\n'\'' < "/proc/$model_pid/environ" | sed -n '\''s/^REALGUARD_MODEL_RESPONSE_HMAC_KEY_ID=//p'\'' | head -1)
      else
        model_token=""
        response_key_id=""
      fi
      response_key_id=${response_key_id:-v1}
      expected_model_revision=$(sed -n '\''s/^Environment=REALGUARD_V2_MODEL_REVISION=//p'\'' /etc/systemd/system/realguard-detection.service.d/gpu.conf | tail -1)
      expected_model_sha256=$(sed -n '\''s/^Environment=REALGUARD_V2_MODEL_SHA256=//p'\'' /etc/systemd/system/realguard-detection.service.d/gpu.conf | tail -1)
      model_runtime=$(curl -fsS -H "X-RealGuard-Internal-Token: $model_token" http://127.0.0.1:5000/internal/model/health | /home/ymk/miniconda3/envs/realguard/bin/python -c '\''
import json, sys
p=json.load(sys.stdin)
d=p.get("data") or {}
assert p.get("code") == 200
assert d.get("activeProvider") == "CUDAExecutionProvider"
assert d.get("modelRevision") == sys.argv[1]
assert d.get("modelSha256") == sys.argv[2]
assert d.get("deploymentCommit") == sys.argv[3]
assert d.get("responseIntegrityReady") is True
assert d.get("responseIntegrityKeyId") == sys.argv[4]
print("ready")
'\'' "$expected_model_revision" "$expected_model_sha256" "$deployed" "$response_key_id" 2>/dev/null || printf invalid)
      yolo_runtime=$(curl -fsS http://127.0.0.1:5067/health | /home/ymk/miniconda3/envs/realguard/bin/python -c '\''
import json, sys
p=json.load(sys.stdin)
assert p.get("status") == "ok"
assert p.get("cudaReady") is True and p.get("device") != "cpu" and p.get("gpu")
assert p.get("modelRevision") == "796a3b58a1121f20c5976d59314baea3db659a66"
assert p.get("modelSha256") == "6ac71b6ab8db27ec7928b5176e60a359c65e1579a5c1d58cf2f98df30cf3085e"
print("ready")
'\'' 2>/dev/null || printf invalid)
      watermark_http=$(curl -sS -o /dev/null -w "%{http_code}" http://127.0.0.1:5066/health || printf 000)
      printf "deployed=%s\nmodel=%s\nwatermark=%s\nyolo=%s\nmodel_enabled=%s\nwatermark_enabled=%s\nyolo_enabled=%s\nmodel_tunnel=%s\nprecheck_tunnel=%s\nmodel_runtime=%s\nyolo_runtime=%s\nwatermark_http=%s\n" \
        "$deployed" "$model" "$watermark" "$yolo" "$model_enabled" "$watermark_enabled" \
        "$yolo_enabled" "$model_tunnel" "$precheck_tunnel" "$model_runtime" "$yolo_runtime" "$watermark_http"
    ')"
    public_gpu_output="$(run_remote_capture '
      config_commit=$(cat /opt/realguard-data/public-detector-remote.DEPLOYED_COMMIT 2>/dev/null || printf missing)
      detector_service=$(systemctl is-active realguard-detector-backend.service 2>/dev/null || printf unknown)
      public_runtime=$(curl -fsS http://127.0.0.1:15001/health | python3 -c '\''
import json, sys
p=json.load(sys.stdin)
r=p.get("remoteInference") or {}
assert p.get("capabilityReady") is True
assert r.get("deploymentCommit") == sys.argv[1]
print("ready")
'\'' "$config_commit" 2>/dev/null || printf invalid)
      printf "config_commit=%s\ndetector_service=%s\npublic_runtime=%s\n" "$config_commit" "$detector_service" "$public_runtime"
    ')"
  fi
  gpu_deployed="$(printf '%s\n' "$gpu_output" | sed -n 's/^deployed=//p')"
  gpu_public_config_commit="$(printf '%s\n' "$public_gpu_output" | sed -n 's/^config_commit=//p')"
  gpu_public_detector_service="$(printf '%s\n' "$public_gpu_output" | sed -n 's/^detector_service=//p')"
  gpu_public_runtime="$(printf '%s\n' "$public_gpu_output" | sed -n 's/^public_runtime=//p')"
  if [[ "$gpu_deployed" == "dry-run" ]]; then
    gpu_repo_state="dry-run"
  elif [[ "$gpu_deployed" == "$gpu_expected" && "$gpu_public_config_commit" == "$gpu_expected" ]]; then
    gpu_repo_state="match"
  elif [[ "$gpu_deployed" == "missing" || -z "$gpu_deployed" ]]; then
    gpu_repo_state="missing"
  else
    gpu_repo_state="mismatch"
  fi
  gpu_model="$(printf '%s\n' "$gpu_output" | sed -n 's/^model=//p')"
  gpu_watermark="$(printf '%s\n' "$gpu_output" | sed -n 's/^watermark=//p')"
  gpu_yolo="$(printf '%s\n' "$gpu_output" | sed -n 's/^yolo=//p')"
  gpu_model_enabled="$(printf '%s\n' "$gpu_output" | sed -n 's/^model_enabled=//p')"
  gpu_watermark_enabled="$(printf '%s\n' "$gpu_output" | sed -n 's/^watermark_enabled=//p')"
  gpu_yolo_enabled="$(printf '%s\n' "$gpu_output" | sed -n 's/^yolo_enabled=//p')"
  gpu_model_tunnel="$(printf '%s\n' "$gpu_output" | sed -n 's/^model_tunnel=//p')"
  gpu_precheck_tunnel="$(printf '%s\n' "$gpu_output" | sed -n 's/^precheck_tunnel=//p')"
  gpu_model_runtime="$(printf '%s\n' "$gpu_output" | sed -n 's/^model_runtime=//p')"
  gpu_yolo_runtime="$(printf '%s\n' "$gpu_output" | sed -n 's/^yolo_runtime=//p')"
  gpu_watermark_http="$(printf '%s\n' "$gpu_output" | sed -n 's/^watermark_http=//p')"
  if [[ "$FORMAT" == "env" ]]; then
    printf 'gpu_expected=%s\n' "$gpu_expected"
    printf 'gpu_deployed=%s\n' "$gpu_deployed"
    printf 'gpu_repo_state=%s\n' "$gpu_repo_state"
    printf 'gpu_public_config_commit=%s\n' "$gpu_public_config_commit"
    printf 'gpu_public_detector_service=%s\n' "$gpu_public_detector_service"
    printf 'gpu_public_runtime=%s\n' "$gpu_public_runtime"
    printf 'gpu_model_service=%s\n' "$gpu_model"
    printf 'gpu_watermark_service=%s\n' "$gpu_watermark"
    printf 'gpu_yolo_service=%s\n' "$gpu_yolo"
    printf 'gpu_model_enabled=%s\n' "$gpu_model_enabled"
    printf 'gpu_watermark_enabled=%s\n' "$gpu_watermark_enabled"
    printf 'gpu_yolo_enabled=%s\n' "$gpu_yolo_enabled"
    printf 'gpu_model_tunnel=%s\n' "$gpu_model_tunnel"
    printf 'gpu_precheck_tunnel=%s\n' "$gpu_precheck_tunnel"
    printf 'gpu_model_runtime=%s\n' "$gpu_model_runtime"
    printf 'gpu_yolo_runtime=%s\n' "$gpu_yolo_runtime"
    printf 'gpu_watermark_http=%s\n' "$gpu_watermark_http"
  else
    printf '\n[GPU]\n'
    printf 'expected:       %s\n' "$gpu_expected"
    printf 'deployed:       %s\n' "$gpu_deployed"
    printf 'repo_state:     %s\n' "$gpu_repo_state"
    printf 'public_config:  %s\n' "$gpu_public_config_commit"
    printf 'public_detector:%s\n' "$gpu_public_detector_service"
    printf 'public_runtime: %s\n' "$gpu_public_runtime"
    printf 'model_service:  %s\n' "$gpu_model"
    printf 'watermark:      %s\n' "$gpu_watermark"
    printf 'yolo:           %s\n' "$gpu_yolo"
    printf 'enabled:        model=%s watermark=%s yolo=%s\n' \
      "$gpu_model_enabled" "$gpu_watermark_enabled" "$gpu_yolo_enabled"
    printf 'tunnels:        model=%s precheck=%s\n' "$gpu_model_tunnel" "$gpu_precheck_tunnel"
    printf 'model_runtime:  %s\n' "$gpu_model_runtime"
    printf 'yolo_runtime:   %s\n' "$gpu_yolo_runtime"
    printf 'watermark_http: %s\n' "$gpu_watermark_http"
  fi
  if [[ "$STRICT" == "1" ]]; then
    if [[ "$gpu_deployed" != "$gpu_expected" || "$gpu_model" != "active" \
      || "$gpu_watermark" != "active" || "$gpu_yolo" != "active" \
      || "$gpu_model_enabled" != "enabled" || "$gpu_watermark_enabled" != "enabled" \
      || "$gpu_yolo_enabled" != "enabled" || "$gpu_model_tunnel" != "active" \
      || "$gpu_precheck_tunnel" != "active" || "$gpu_model_runtime" != "ready" \
      || "$gpu_yolo_runtime" != "ready" \
      || "$gpu_watermark_http" != "200" \
      || "$gpu_public_config_commit" != "$gpu_expected" \
      || "$gpu_public_detector_service" != "active" \
      || "$gpu_public_runtime" != "ready" ]]; then
      status=1
    fi
  fi
fi

exit "$status"
