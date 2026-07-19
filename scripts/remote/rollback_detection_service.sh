#!/usr/bin/env bash
set -euo pipefail

release_root="$(readlink -f "${1:?release root is required}")"
mode="${2:-watchdog}"
case "$release_root" in
  /home/ymk/realguard-detection-releases/*) ;;
  *) echo "unsafe GPU rollback path" >&2; exit 2 ;;
esac
backup_root="$release_root/previous"
test -d "$backup_root/systemd"
release_base="/home/ymk/realguard-detection-releases"
application_root="/home/ymk/RealGuard/AIGC_image_detection_system"
watermark_root="/home/ymk/services/watermark-precheck"
yolo_root="/home/ymk/services/yolo-watermark"
expected_commit="$(tr -d '[:space:]' < "$release_root/DEPLOYED_COMMIT")"
[[ "$expected_commit" =~ ^[0-9a-f]{7,40}$ ]]
[[ "$mode" == "watchdog" || "$mode" == "force" ]]

exec 9>"$release_base/.deploy.lock"
flock -w 60 9
current_release="$(readlink -f "$release_base/current" 2>/dev/null || true)"
if [[ "$current_release" != "$release_root" ]]; then
  exit 0
fi

watchdog_probe() {
  services_healthy=1
  for service in \
    realguard-detection.service \
    realguard-watermark-precheck.service \
    realguard-yolo-watermark.service \
    realguard-web-tunnel.service \
    realguard-v2-precheck-tunnel.service; do
    if ! systemctl is-active --quiet "$service" || \
       [[ "$(systemctl is-enabled "$service" 2>/dev/null || true)" != "enabled" ]]; then
      services_healthy=0
      break
    fi
  done
  model_token="$(awk -F= '/^REALGUARD_MODEL_INTERNAL_TOKEN=/{print substr($0, index($0, "=") + 1); exit}' /etc/realguard/model-inference.env)"
  response_key_id="$(awk -F= '/^REALGUARD_MODEL_RESPONSE_HMAC_KEY_ID=/{print substr($0, index($0, "=") + 1); exit}' /etc/realguard/model-inference.env)"
  response_key_id="${response_key_id:-v1}"
  [[ "$services_healthy" == "1" && "${#model_token}" -ge 32 ]] \
    && curl -fsS --connect-timeout 3 --max-time 10 \
      -H "X-RealGuard-Internal-Token: $model_token" \
      http://127.0.0.1:5000/internal/model/health \
    | /home/ymk/miniconda3/envs/realguard/bin/python -c '
import json, sys
payload = json.load(sys.stdin)
data = payload.get("data") or {}
assert payload.get("code") == 200
assert data.get("activeProvider") == "CUDAExecutionProvider"
assert data.get("deploymentCommit") == sys.argv[1]
assert data.get("responseIntegrityReady") is True
assert data.get("responseIntegrityKeyId") == sys.argv[2]
' "$expected_commit" "$response_key_id"
}
if [[ "$mode" == "watchdog" ]]; then
  for attempt in 1 2 3 4 5 6; do
    if watchdog_probe; then
      exit 0
    fi
    [[ "$attempt" == "6" ]] || sleep 10
  done
fi

restore_file() {
  local backup="$1"
  local target="$2"
  local missing_marker="${3:-}"
  rm -f "$target"
  if [[ -z "$missing_marker" || ! -f "$missing_marker" ]]; then
    install -D -o ymk -g ymk -m 0644 "$backup" "$target"
  fi
}

restore_file "$backup_root/inference_onnx.py" \
  "$application_root/imagedetection/Agent/tools/AIGC_Detection/inference_onnx.py"
restore_file "$backup_root/image_preprocessing.py" \
  "$application_root/imagedetection/Agent/tools/AIGC_Detection/image_preprocessing.py"
restore_file "$backup_root/model_decision_policy.py" \
  "$application_root/imagedetection/Agent/tools/AIGC_Detection/model_decision_policy.py" \
  "$backup_root/model_decision_policy.py.missing"
restore_file "$backup_root/remote_inference.py" \
  "$application_root/imagedetection/views/remote_inference.py"
restore_file "$backup_root/watermark-service.py" "$watermark_root/service.py"
restore_file "$backup_root/watermark-policy.py" "$watermark_root/policy.py"
restore_file "$backup_root/watermark-evidence_probability.py" "$watermark_root/evidence_probability.py"
restore_file "$backup_root/watermark-yolo_adapter.py" "$watermark_root/yolo_adapter.py"
restore_file "$backup_root/yolo-service.py" "$yolo_root/service.py"

for name in \
  realguard-detection.service \
  realguard-detection-gpu.conf \
  realguard-detection-shared-precheck.conf \
  realguard-web-tunnel.service \
  realguard-watermark-precheck.service \
  realguard-watermark-precheck-yolo.conf \
  realguard-v2-precheck-tunnel.service \
  realguard-yolo-watermark.service; do
  target_file="$(cat "$backup_root/systemd/$name.target")"
  if [[ -f "$backup_root/systemd/$name.missing" ]]; then
    rm -f "$target_file"
  else
    install -D -o root -g root -m 0644 "$backup_root/systemd/$name.previous" "$target_file"
  fi
done

previous_current="$(cat "$backup_root/previous_current" 2>/dev/null || true)"
if [[ -n "$previous_current" && -e "$previous_current" ]]; then
  ln -sfn "$previous_current" "$release_base/current.next"
  mv -Tf "$release_base/current.next" "$release_base/current"
else
  rm -f "$release_base/current"
fi

systemctl daemon-reload
managed_services=(
  realguard-detection.service
  realguard-watermark-precheck.service
  realguard-yolo-watermark.service
  realguard-web-tunnel.service
  realguard-v2-precheck-tunnel.service
)
restore_unit_state() {
  local service="$1"
  local enabled active actual_enabled actual_active
  enabled="$(cat "$backup_root/systemd/$service.enabled")"
  active="$(cat "$backup_root/systemd/$service.active")"
  systemctl unmask "$service" >/dev/null 2>&1 || true
  case "$enabled" in
    enabled) systemctl enable "$service" >/dev/null ;;
    enabled-runtime) systemctl enable --runtime "$service" >/dev/null ;;
    masked|masked-runtime) ;;
    *) systemctl disable "$service" >/dev/null 2>&1 || true ;;
  esac
  case "$active" in
    active) systemctl restart "$service" ;;
    inactive) systemctl stop "$service" ;;
    *) echo "unsupported saved ActiveState for $service: $active" >&2; return 1 ;;
  esac
  case "$enabled" in
    masked) systemctl mask "$service" >/dev/null ;;
    masked-runtime) systemctl mask --runtime "$service" >/dev/null ;;
  esac
  actual_enabled="$(systemctl is-enabled "$service" 2>/dev/null || true)"
  actual_active="$(systemctl is-active "$service" 2>/dev/null || true)"
  [[ "$actual_enabled" == "$enabled" ]]
  [[ "$actual_active" == "$active" ]]
}
for service in "${managed_services[@]}"; do
  restore_unit_state "$service"
done
