#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SOURCE_DIR="$ROOT_DIR/services/realguard-detection"
WATERMARK_DIR="$ROOT_DIR/services/watermark-precheck"
YOLO_DIR="$ROOT_DIR/services/yolo-watermark"
ACTIVATE_SCRIPT="$ROOT_DIR/scripts/remote/activate_detection_service.sh"
GPU_ROLLBACK_SOURCE="$ROOT_DIR/scripts/remote/rollback_detection_service.sh"
PUBLIC_ACTIVATE_SOURCE="$ROOT_DIR/scripts/remote/activate_public_gpu_deploy.sh"
PUBLIC_ROLLBACK_SOURCE="$ROOT_DIR/scripts/remote/rollback_public_gpu_deploy.sh"
GPU_HOST="${GPU_DEPLOY_HOST:-10.1.20.66}"
GPU_USER="${GPU_DEPLOY_USER:-ymk}"
GPU_PORT="${GPU_DEPLOY_PORT:-22}"
GPU_KEY="${GPU_DEPLOY_SSH_KEY:-}"
DRY_RUN="${DRY_RUN:-0}"
TMP_DIR="$(mktemp -d)"
ARCHIVE="$TMP_DIR/realguard-detection-release.tgz"
MARKER="$TMP_DIR/realguard-detection.DEPLOYED_COMMIT"
STAGE_DIR="$TMP_DIR/stage"
WEB_DRAIN_HOST="${GPU_WEB_DRAIN_HOST:-124.221.92.85}"
WEB_DRAIN_USER="${GPU_WEB_DRAIN_USER:-ubuntu}"
WEB_DRAIN_KEY="${GPU_WEB_DRAIN_SSH_KEY:-${DEPLOY_SSH_KEY:-}}"
WEB_DRAIN_ENABLED="${GPU_DRAIN_WEB_WORKER:-1}"
web_worker_drain_attempted=0
public_config_switched=0
gpu_activation_succeeded=0
deployment_committed=0
PUBLIC_CONFIG_TARGET="/etc/systemd/system/realguard-detector-backend.service.d/remote.conf"
PUBLIC_CONFIG_MARKER="/opt/realguard-data/public-detector-remote.DEPLOYED_COMMIT"
PUBLIC_BACKUP_ROOT=""
PUBLIC_RECOVERY_UNIT=""
PUBLIC_CONFIG_TMP=""
PUBLIC_ACTIVATE_TMP=""
PUBLIC_ROLLBACK_TMP=""
PUBLIC_ROLLBACK_SCRIPT=""

public_ssh() {
  ssh -i "$WEB_DRAIN_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
    "$WEB_DRAIN_USER@$WEB_DRAIN_HOST" "$@"
}

recover_public_worker() {
  public_ssh \
    "state=\$(sudo cat '$PUBLIC_BACKUP_ROOT/realguard-developer-worker.service.active'); \
     case \"\$state\" in \
       active) sudo systemctl start realguard-developer-worker.service ;; \
       inactive) sudo systemctl stop realguard-developer-worker.service ;; \
       *) exit 2 ;; \
     esac; \
     test \"\$(sudo systemctl is-active realguard-developer-worker.service 2>/dev/null || true)\" = \"\$state\""
}

restore_public_config() {
  public_ssh \
    "sudo '$PUBLIC_ROLLBACK_SCRIPT' '$PUBLIC_BACKUP_ROOT' '$PUBLIC_CONFIG_TARGET' \
       '$PUBLIC_CONFIG_MARKER' realguard-detector-backend.service \
       realguard-developer-worker.service '$commit_sha' force"
}

rollback_gpu_release() {
  ssh -tt "${ssh_options[@]}" "$GPU_USER@$GPU_HOST" \
    'current=$(readlink -f /home/ymk/realguard-detection-releases/current 2>/dev/null || true); \
     if [[ -z "$current" || ! -f "$current/DEPLOYED_COMMIT" ]]; then exit 0; fi; \
     test "$(tr -d "[:space:]" < "$current/DEPLOYED_COMMIT")" = "'"$commit_sha"'" || exit 0; \
     rollback_script=$(cat "$current/rollback_script_path"); \
     sudo "$rollback_script" "$current" force'
}

cleanup() {
  if [[ "$gpu_activation_succeeded" == "1" && "$deployment_committed" != "1" ]]; then
    if ! rollback_gpu_release; then
      echo "WARNING: GPU release rollback failed; its remote TTL watchdog remains armed" >&2
    fi
  fi
  if [[ "$public_config_switched" == "1" && -n "$WEB_DRAIN_KEY" ]]; then
    if restore_public_config; then
      public_config_switched=0
      web_worker_drain_attempted=0
    else
      echo "WARNING: public detector configuration rollback failed" >&2
    fi
  fi
  if [[ "$web_worker_drain_attempted" == "1" && -n "$WEB_DRAIN_KEY" ]]; then
    if ! recover_public_worker; then
      echo "WARNING: immediate public worker recovery failed; the remote TTL watchdog remains armed" >&2
    fi
  fi
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

paths=(
  services/realguard-detection/inference_onnx.py
  services/realguard-detection/image_preprocessing.py
  services/realguard-detection/model_decision_policy.py
  services/realguard-detection/remote_inference.py
  services/realguard-detection/runtime.lock
  services/watermark-precheck/service.py
  services/watermark-precheck/policy.py
  services/watermark-precheck/evidence_probability.py
  services/watermark-precheck/yolo_adapter.py
  services/watermark-precheck/realguard-watermark-precheck.service
  services/watermark-precheck/realguard-watermark-precheck-yolo.conf
  services/watermark-precheck/realguard-v2-precheck-tunnel.service
  services/watermark-precheck/requirements.txt
  services/watermark-precheck/runtime.lock
  services/yolo-watermark/service.py
  services/yolo-watermark/realguard-yolo-watermark.service
  services/yolo-watermark/requirements.txt
  services/yolo-watermark/runtime.lock
  services/realguard-detection/realguard-detection.service
  services/realguard-detection/realguard-detection-gpu.conf
  services/realguard-detection/realguard-detection-shared-precheck.conf
  services/realguard-detection/public-detector-remote.conf
  services/realguard-detection/realguard-web-tunnel.service
  scripts/remote/activate_detection_service.sh
  scripts/remote/activate_public_gpu_deploy.sh
  scripts/remote/rollback_detection_service.sh
  scripts/remote/rollback_public_gpu_deploy.sh
  scripts/deploy_detection_service.sh
)
if [[ -n "$(git -C "$ROOT_DIR" status --porcelain --untracked-files=all -- "${paths[@]}")" ]]; then
  echo "GPU deployment paths must be tracked and committed before deployment" >&2
  exit 1
fi
commit_sha="$(git -C "$ROOT_DIR" log -1 --format=%h -- "${paths[@]}")"
PUBLIC_BACKUP_ROOT="/opt/realguard-data/gpu-deploy-backups/${commit_sha}-$$"
PUBLIC_RECOVERY_UNIT="realguard-public-gpu-rollback-${commit_sha}-$$"
PUBLIC_CONFIG_TMP="/tmp/realguard-public-detector-remote-${commit_sha}-$$.conf"
PUBLIC_ACTIVATE_TMP="/tmp/realguard-public-gpu-activate-${commit_sha}-$$.sh"
PUBLIC_ROLLBACK_TMP="/tmp/realguard-public-gpu-rollback-${commit_sha}-$$.sh"
PUBLIC_ROLLBACK_SCRIPT="/opt/realguard-data/gpu-deploy-backups/${commit_sha}-$$-rollback.sh"
printf '%s\n' "$commit_sha" > "$MARKER"

for runtime_lock in \
  "$SOURCE_DIR/runtime.lock" \
  "$WATERMARK_DIR/runtime.lock" \
  "$YOLO_DIR/runtime.lock"; do
  test -s "$runtime_lock"
done

python3 -m py_compile \
  "$SOURCE_DIR/inference_onnx.py" \
  "$SOURCE_DIR/image_preprocessing.py" \
  "$SOURCE_DIR/model_decision_policy.py" \
  "$SOURCE_DIR/remote_inference.py" \
  "$WATERMARK_DIR/service.py" \
  "$WATERMARK_DIR/policy.py" \
  "$WATERMARK_DIR/evidence_probability.py" \
  "$WATERMARK_DIR/yolo_adapter.py" \
  "$YOLO_DIR/service.py"
bash -n "$ACTIVATE_SCRIPT"
bash -n "$GPU_ROLLBACK_SOURCE"
bash -n "$PUBLIC_ACTIVATE_SOURCE"
bash -n "$PUBLIC_ROLLBACK_SOURCE"
install -d "$STAGE_DIR/model" "$STAGE_DIR/watermark" "$STAGE_DIR/yolo" "$STAGE_DIR/systemd" "$STAGE_DIR/config" "$STAGE_DIR/scripts"
cp "$SOURCE_DIR/inference_onnx.py" "$STAGE_DIR/model/"
cp "$SOURCE_DIR/image_preprocessing.py" "$STAGE_DIR/model/"
cp "$SOURCE_DIR/model_decision_policy.py" "$STAGE_DIR/model/"
cp "$SOURCE_DIR/remote_inference.py" "$STAGE_DIR/model/"
cp "$SOURCE_DIR/runtime.lock" "$STAGE_DIR/model/"
cp "$WATERMARK_DIR/service.py" "$STAGE_DIR/watermark/"
cp "$WATERMARK_DIR/policy.py" "$STAGE_DIR/watermark/"
cp "$WATERMARK_DIR/evidence_probability.py" "$STAGE_DIR/watermark/"
cp "$WATERMARK_DIR/yolo_adapter.py" "$STAGE_DIR/watermark/"
cp "$WATERMARK_DIR/requirements.txt" "$STAGE_DIR/watermark/"
cp "$WATERMARK_DIR/runtime.lock" "$STAGE_DIR/watermark/"
cp "$YOLO_DIR/service.py" "$STAGE_DIR/yolo/"
cp "$YOLO_DIR/requirements.txt" "$STAGE_DIR/yolo/"
cp "$YOLO_DIR/runtime.lock" "$STAGE_DIR/yolo/"
cp "$SOURCE_DIR/realguard-detection.service" "$STAGE_DIR/systemd/"
cp "$SOURCE_DIR/realguard-detection-gpu.conf" "$STAGE_DIR/systemd/"
cp "$SOURCE_DIR/realguard-detection-shared-precheck.conf" "$STAGE_DIR/systemd/"
cp "$SOURCE_DIR/public-detector-remote.conf" "$STAGE_DIR/config/"
cp "$SOURCE_DIR/realguard-web-tunnel.service" "$STAGE_DIR/systemd/"
cp "$WATERMARK_DIR/realguard-watermark-precheck.service" "$STAGE_DIR/systemd/"
cp "$WATERMARK_DIR/realguard-watermark-precheck-yolo.conf" "$STAGE_DIR/systemd/"
cp "$WATERMARK_DIR/realguard-v2-precheck-tunnel.service" "$STAGE_DIR/systemd/"
cp "$YOLO_DIR/realguard-yolo-watermark.service" "$STAGE_DIR/systemd/"
cp "$GPU_ROLLBACK_SOURCE" "$STAGE_DIR/scripts/"
tar -C "$STAGE_DIR" -czf "$ARCHIVE" model watermark yolo systemd config scripts

ssh_options=(-p "$GPU_PORT" -o StrictHostKeyChecking=accept-new)
scp_options=(-P "$GPU_PORT" -o StrictHostKeyChecking=accept-new)
if [[ -n "$GPU_KEY" ]]; then
  ssh_options+=(-i "$GPU_KEY" -o IdentitiesOnly=yes)
  scp_options+=(-i "$GPU_KEY" -o IdentitiesOnly=yes)
fi

if [[ "$DRY_RUN" == "1" ]]; then
  printf 'Would deploy GPU release %s to %s@%s\n' "$commit_sha" "$GPU_USER" "$GPU_HOST"
  exit 0
fi

scp "${scp_options[@]}" "$ARCHIVE" "$GPU_USER@$GPU_HOST:/tmp/realguard-detection-release.tgz"
scp "${scp_options[@]}" "$MARKER" "$GPU_USER@$GPU_HOST:/tmp/realguard-detection.DEPLOYED_COMMIT"
scp "${scp_options[@]}" "$ACTIVATE_SCRIPT" "$GPU_USER@$GPU_HOST:/tmp/realguard-activate-detection.sh"
if [[ -z "$WEB_DRAIN_KEY" ]]; then
  echo "GPU_WEB_DRAIN_SSH_KEY or DEPLOY_SSH_KEY is required to install the public detector configuration" >&2
  exit 1
fi
public_response_key_hash="$(public_ssh \
  "sudo awk -F= '/^REALGUARD_MODEL_RESPONSE_HMAC_KEY=/{print substr(\$0, index(\$0, \"=\") + 1); exit}' \
     /etc/realguard/model-inference.env | python3 -c \
     'import hashlib,sys; value=sys.stdin.read().strip(); assert len(value)==64 and all(c in \"0123456789abcdef\" for c in value); print(hashlib.sha256(value.encode()).hexdigest())'")"
gpu_response_key_hash="$(
  ssh -tt "${ssh_options[@]}" "$GPU_USER@$GPU_HOST" \
    "sudo awk -F= '/^REALGUARD_MODEL_RESPONSE_HMAC_KEY=/{print substr(\$0, index(\$0, \"=\") + 1); exit}' \
       /etc/realguard/model-inference.env | python3 -c \
       'import hashlib,sys; value=sys.stdin.read().strip(); assert len(value)==64 and all(c in \"0123456789abcdef\" for c in value); print(hashlib.sha256(value.encode()).hexdigest())'" \
    | tr -d '\r'
)"
[[ "$public_response_key_hash" =~ ^[0-9a-f]{64}$ ]]
test "$public_response_key_hash" = "$gpu_response_key_hash"
public_response_key_id="$(public_ssh \
  "value=\$(sudo awk -F= '/^REALGUARD_MODEL_RESPONSE_HMAC_KEY_ID=/{print substr(\$0, index(\$0, \"=\") + 1); exit}' \
     /etc/realguard/model-inference.env); printf '%s' \"\${value:-v1}\"")"
gpu_response_key_id="$(
  ssh -tt "${ssh_options[@]}" "$GPU_USER@$GPU_HOST" \
    "value=\$(sudo awk -F= '/^REALGUARD_MODEL_RESPONSE_HMAC_KEY_ID=/{print substr(\$0, index(\$0, \"=\") + 1); exit}' \
       /etc/realguard/model-inference.env); printf '%s' \"\${value:-v1}\"" \
    | tr -d '\r'
)"
[[ "$public_response_key_id" =~ ^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$ ]]
test "$public_response_key_id" = "$gpu_response_key_id"
scp -i "$WEB_DRAIN_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
  "$SOURCE_DIR/public-detector-remote.conf" \
  "$WEB_DRAIN_USER@$WEB_DRAIN_HOST:$PUBLIC_CONFIG_TMP"
scp -i "$WEB_DRAIN_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
  "$PUBLIC_ACTIVATE_SOURCE" \
  "$WEB_DRAIN_USER@$WEB_DRAIN_HOST:$PUBLIC_ACTIVATE_TMP"
scp -i "$WEB_DRAIN_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
  "$PUBLIC_ROLLBACK_SOURCE" \
  "$WEB_DRAIN_USER@$WEB_DRAIN_HOST:$PUBLIC_ROLLBACK_TMP"
if [[ "$WEB_DRAIN_ENABLED" == "1" ]]; then
  web_worker_drain_attempted=1
fi
public_ssh \
  "sudo bash '$PUBLIC_ACTIVATE_TMP' '$commit_sha' '$PUBLIC_CONFIG_TMP' \
     '$PUBLIC_BACKUP_ROOT' '$PUBLIC_CONFIG_TARGET' '$PUBLIC_CONFIG_MARKER' \
     '$PUBLIC_ROLLBACK_TMP' '$PUBLIC_ROLLBACK_SCRIPT' '$PUBLIC_RECOVERY_UNIT' \
     '$WEB_DRAIN_ENABLED' realguard-detector-backend.service \
     realguard-developer-worker.service; \
   sudo rm -f '$PUBLIC_ACTIVATE_TMP'"
public_config_switched=1
ssh -tt "${ssh_options[@]}" "$GPU_USER@$GPU_HOST" \
  "bash /tmp/realguard-activate-detection.sh"
gpu_activation_succeeded=1

if [[ "$web_worker_drain_attempted" == "1" ]]; then
  recover_public_worker
  web_worker_drain_attempted=0
fi

commit_visible=0
for _ in {1..60}; do
  if public_ssh \
      "model_token=\$(sudo awk -F= '/^REALGUARD_MODEL_INTERNAL_TOKEN=/{print substr(\$0, index(\$0, \"=\") + 1); exit}' /etc/realguard/model-inference.env); \
       response_key_id=\$(sudo awk -F= '/^REALGUARD_MODEL_RESPONSE_HMAC_KEY_ID=/{print substr(\$0, index(\$0, \"=\") + 1); exit}' /etc/realguard/model-inference.env); \
       response_key_id=\${response_key_id:-v1}; \
       test \"\${#model_token}\" -ge 32; \
       curl -fsS --max-time 10 -H \"X-RealGuard-Internal-Token: \$model_token\" \
         http://127.0.0.1:15000/internal/model/health | \
       python3 -c 'import json,sys; p=json.load(sys.stdin); d=p.get(\"data\") or {}; assert p.get(\"code\") == 200; assert d.get(\"activeProvider\") == \"CUDAExecutionProvider\"; assert d.get(\"deploymentCommit\") == sys.argv[1]; assert d.get(\"responseIntegrityReady\") is True; assert d.get(\"responseIntegrityKeyId\") == sys.argv[2]' '$commit_sha' \"\$response_key_id\"; \
       curl -fsS --max-time 10 http://127.0.0.1:15001/health | \
       python3 -c 'import json,sys; p=json.load(sys.stdin); assert p.get(\"capabilityReady\") is True'" \
    && curl -fsS --max-time 10 https://www.rrreal.cn/api/ready \
      | python3 -c 'import json,sys; p=json.load(sys.stdin); assert p.get("status") == "ready"' \
    && ssh "${ssh_options[@]}" "$GPU_USER@$GPU_HOST" \
      "test \"\$(tr -d '[:space:]' < /home/ymk/realguard-detection-releases/current/DEPLOYED_COMMIT)\" = '$commit_sha'"; then
    commit_visible=1
    break
  fi
  sleep 2
done
test "$commit_visible" = "1"
deployment_committed=1
gpu_activation_succeeded=0
public_config_switched=0

printf 'GPU detection service deployed from commit %s\n' "$commit_sha"
