#!/usr/bin/env bash
set -euo pipefail

archive="${GPU_RELEASE_ARCHIVE:-/tmp/realguard-detection-release.tgz}"
marker="${GPU_RELEASE_MARKER:-/tmp/realguard-detection.DEPLOYED_COMMIT}"
service_name="${GPU_SERVICE_NAME:-realguard-detection.service}"
watermark_service_name="${GPU_WATERMARK_SERVICE_NAME:-realguard-watermark-precheck.service}"
yolo_service_name="${GPU_YOLO_SERVICE_NAME:-realguard-yolo-watermark.service}"
model_tunnel_service_name="${GPU_MODEL_TUNNEL_SERVICE_NAME:-realguard-web-tunnel.service}"
precheck_tunnel_service_name="${GPU_PRECHECK_TUNNEL_SERVICE_NAME:-realguard-v2-precheck-tunnel.service}"
application_root="${GPU_APPLICATION_ROOT:-/home/ymk/RealGuard/AIGC_image_detection_system}"
watermark_root="${GPU_WATERMARK_ROOT:-/home/ymk/services/watermark-precheck}"
yolo_root="${GPU_YOLO_ROOT:-/home/ymk/services/yolo-watermark}"
release_base="${GPU_RELEASE_BASE:-/home/ymk/realguard-detection-releases}"

commit_sha="$(tr -d '[:space:]' <"$marker")"
[[ "$commit_sha" =~ ^[0-9a-f]{7,40}$ ]]
release_id="${commit_sha}-$(date -u +%Y%m%dT%H%M%SZ)-$$"
release_root="$release_base/$release_id"
backup_root="$release_root/previous"
model_target="$application_root/imagedetection/Agent/tools/AIGC_Detection/inference_onnx.py"
preprocess_target="$application_root/imagedetection/Agent/tools/AIGC_Detection/image_preprocessing.py"
decision_policy_target="$application_root/imagedetection/Agent/tools/AIGC_Detection/model_decision_policy.py"
view_target="$application_root/imagedetection/views/remote_inference.py"
watermark_service_target="$watermark_root/service.py"
watermark_policy_target="$watermark_root/policy.py"
watermark_probability_target="$watermark_root/evidence_probability.py"
watermark_adapter_target="$watermark_root/yolo_adapter.py"
yolo_service_target="$yolo_root/service.py"
previous_current="$(readlink -f "$release_base/current" 2>/dev/null || true)"
switched=0
units_switched=0
managed_service_names=(
  "$service_name"
  "$watermark_service_name"
  "$yolo_service_name"
  "$model_tunnel_service_name"
  "$precheck_tunnel_service_name"
)
rollback_unit="realguard-gpu-deploy-rollback-${commit_sha}"
rollback_script_target="/var/lib/realguard-deploy/${release_id}-rollback.sh"

rollback() {
  status=$?
  trap - ERR
  printf 'GPU activation failed; restoring previous detector files.\n' >&2
  if [[ "$switched" == "1" || "$units_switched" == "1" ]]; then
    flock -u 9 || true
    if ! sudo "$rollback_script_target" "$release_root" force; then
      printf 'Immediate GPU rollback failed; the TTL watchdog remains armed.\n' >&2
    fi
  fi
  exit "$status"
}
trap rollback ERR

install -d -m 755 "$release_root" "$backup_root"
exec 9>"$release_base/.deploy.lock"
flock -w 60 9
tar -xzf "$archive" -C "$release_root"
cp -a "$model_target" "$backup_root/inference_onnx.py"
cp -a "$preprocess_target" "$backup_root/image_preprocessing.py"
if [[ -e "$decision_policy_target" ]]; then
  cp -a "$decision_policy_target" "$backup_root/model_decision_policy.py"
else
  touch "$backup_root/model_decision_policy.py.missing"
fi
cp -a "$view_target" "$backup_root/remote_inference.py"
cp -a "$watermark_service_target" "$backup_root/watermark-service.py"
cp -a "$watermark_policy_target" "$backup_root/watermark-policy.py"
cp -a "$watermark_probability_target" "$backup_root/watermark-evidence_probability.py"
cp -a "$watermark_adapter_target" "$backup_root/watermark-yolo_adapter.py"
cp -a "$yolo_service_target" "$backup_root/yolo-service.py"
install -m 644 "$marker" "$release_root/DEPLOYED_COMMIT"

/home/ymk/miniconda3/envs/realguard/bin/python -m py_compile \
  "$release_root/model/inference_onnx.py" \
  "$release_root/model/image_preprocessing.py" \
  "$release_root/model/model_decision_policy.py" \
  "$release_root/model/remote_inference.py" \
  "$release_root/watermark/service.py" \
  "$release_root/watermark/policy.py" \
  "$release_root/watermark/evidence_probability.py" \
  "$release_root/watermark/yolo_adapter.py" \
  "$release_root/yolo/service.py"

verify_pinned_requirements() {
  local python_path="$1"
  local requirements_path="$2"
  "$python_path" - "$requirements_path" <<'PY'
from importlib import metadata
from pathlib import Path
import re
import sys

seen = set()
for raw in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    requirement = raw.strip()
    if not requirement or requirement.startswith("#"):
        continue
    if "==" not in requirement:
        raise SystemExit(f"unpinned runtime requirement: {requirement}")
    package, expected = requirement.split("==", 1)
    package = package.strip()
    expected = expected.strip()
    if not package or not expected:
        raise SystemExit(f"invalid runtime requirement: {requirement}")
    canonical = re.sub(r"[-_.]+", "-", package).lower()
    if canonical in seen:
        raise SystemExit(f"duplicate runtime requirement: {package}")
    seen.add(canonical)
    actual = metadata.version(package)
    if actual != expected:
        raise SystemExit(f"runtime requirement mismatch: {package} {actual} != {expected}")
PY
  "$python_path" -m pip check
}

verify_pinned_requirements \
  /home/ymk/miniconda3/envs/realguard/bin/python \
  "$release_root/model/runtime.lock"
verify_pinned_requirements \
  /home/ymk/services/watermark-precheck/.venv/bin/python \
  "$release_root/watermark/runtime.lock"
verify_pinned_requirements \
  /home/ymk/services/yolo-watermark/.venv/bin/python \
  "$release_root/yolo/runtime.lock"

install -d "$backup_root/systemd"
systemd_sources=(
  realguard-detection.service
  realguard-detection-gpu.conf
  realguard-detection-shared-precheck.conf
  realguard-web-tunnel.service
  realguard-watermark-precheck.service
  realguard-watermark-precheck-yolo.conf
  realguard-v2-precheck-tunnel.service
  realguard-yolo-watermark.service
)
systemd_targets=(
  /etc/systemd/system/realguard-detection.service
  /etc/systemd/system/realguard-detection.service.d/gpu.conf
  /etc/systemd/system/realguard-detection.service.d/shared-precheck.conf
  /etc/systemd/system/realguard-web-tunnel.service
  /etc/systemd/system/realguard-watermark-precheck.service
  /etc/systemd/system/realguard-watermark-precheck.service.d/yolo.conf
  /etc/systemd/system/realguard-v2-precheck-tunnel.service
  /etc/systemd/system/realguard-yolo-watermark.service
)
for index in "${!systemd_sources[@]}"; do
  name="${systemd_sources[$index]}"
  target_file="${systemd_targets[$index]}"
  printf '%s\n' "$target_file" > "$backup_root/systemd/$name.target"
  if sudo test -f "$target_file"; then
    sudo install -o root -g root -m 0600 "$target_file" "$backup_root/systemd/$name.previous"
  else
    touch "$backup_root/systemd/$name.missing"
  fi
done
for managed_service in "${managed_service_names[@]}"; do
  systemctl is-enabled "$managed_service" > "$backup_root/systemd/$managed_service.enabled" 2>/dev/null || true
  systemctl is-active "$managed_service" > "$backup_root/systemd/$managed_service.active" 2>/dev/null || true
  case "$(cat "$backup_root/systemd/$managed_service.enabled")" in
    enabled|enabled-runtime|disabled|static|indirect|generated|masked|masked-runtime) ;;
    *) echo "unsupported UnitFileState for $managed_service" >&2; exit 1 ;;
  esac
  case "$(cat "$backup_root/systemd/$managed_service.active")" in
    active|inactive) ;;
    *) echo "unsupported ActiveState for $managed_service" >&2; exit 1 ;;
  esac
done
printf '%s\n' "$previous_current" > "$backup_root/previous_current"
sudo install -d -o root -g root -m 0700 /var/lib/realguard-deploy
sudo install -o root -g root -m 0700 \
  "$release_root/scripts/rollback_detection_service.sh" "$rollback_script_target"
printf '%s\n' "$rollback_unit" > "$release_root/rollback_unit"
printf '%s\n' "$rollback_script_target" > "$release_root/rollback_script_path"
sudo systemd-run --quiet --unit="$rollback_unit" --on-active=15m \
  "$rollback_script_target" "$release_root" watchdog

switched=1
ln -sfn "$release_root" "$release_base/current.next"
mv -Tf "$release_base/current.next" "$release_base/current"
sudo systemctl stop "$service_name"
sudo systemctl stop "$watermark_service_name"
sudo systemctl stop "$yolo_service_name"
port_released=0
for _ in {1..10}; do
  if ! sudo ss -ltnp "sport = :5000" | grep -q LISTEN; then
    port_released=1
    break
  fi
  sleep 1
done
if [[ "$port_released" != "1" ]]; then
  echo "Port 5000 is still owned by a process outside the managed detector service" >&2
  sudo ss -ltnp "sport = :5000" >&2 || true
  exit 1
fi
ln -sfn "$release_root/model/inference_onnx.py" "$model_target.next"
mv -Tf "$model_target.next" "$model_target"
ln -sfn "$release_root/model/image_preprocessing.py" "$preprocess_target.next"
mv -Tf "$preprocess_target.next" "$preprocess_target"
ln -sfn "$release_root/model/model_decision_policy.py" "$decision_policy_target.next"
mv -Tf "$decision_policy_target.next" "$decision_policy_target"
ln -sfn "$release_root/model/remote_inference.py" "$view_target.next"
mv -Tf "$view_target.next" "$view_target"
ln -sfn "$release_root/watermark/service.py" "$watermark_service_target.next"
mv -Tf "$watermark_service_target.next" "$watermark_service_target"
ln -sfn "$release_root/watermark/policy.py" "$watermark_policy_target.next"
mv -Tf "$watermark_policy_target.next" "$watermark_policy_target"
ln -sfn "$release_root/watermark/evidence_probability.py" "$watermark_probability_target.next"
mv -Tf "$watermark_probability_target.next" "$watermark_probability_target"
ln -sfn "$release_root/watermark/yolo_adapter.py" "$watermark_adapter_target.next"
mv -Tf "$watermark_adapter_target.next" "$watermark_adapter_target"
ln -sfn "$release_root/yolo/service.py" "$yolo_service_target.next"
mv -Tf "$yolo_service_target.next" "$yolo_service_target"
sudo install -d -m 755 \
  /etc/systemd/system/realguard-detection.service.d \
  /etc/systemd/system/realguard-watermark-precheck.service.d
units_switched=1
sudo install -m 644 "$release_root/systemd/realguard-detection.service" \
  /etc/systemd/system/realguard-detection.service
sudo install -m 644 "$release_root/systemd/realguard-detection-gpu.conf" \
  /etc/systemd/system/realguard-detection.service.d/gpu.conf
sudo install -m 644 "$release_root/systemd/realguard-detection-shared-precheck.conf" \
  /etc/systemd/system/realguard-detection.service.d/shared-precheck.conf
sudo install -m 644 "$release_root/systemd/realguard-web-tunnel.service" \
  /etc/systemd/system/realguard-web-tunnel.service
sudo install -m 644 "$release_root/systemd/realguard-watermark-precheck.service" \
  /etc/systemd/system/realguard-watermark-precheck.service
sudo install -m 644 "$release_root/systemd/realguard-watermark-precheck-yolo.conf" \
  /etc/systemd/system/realguard-watermark-precheck.service.d/yolo.conf
sudo install -m 644 "$release_root/systemd/realguard-v2-precheck-tunnel.service" \
  /etc/systemd/system/realguard-v2-precheck-tunnel.service
sudo install -m 644 "$release_root/systemd/realguard-yolo-watermark.service" \
  /etc/systemd/system/realguard-yolo-watermark.service
sudo systemctl daemon-reload
for managed_service in "${managed_service_names[@]}"; do
  sudo systemctl unmask "$managed_service" >/dev/null 2>&1 || true
  sudo systemctl enable "$managed_service" >/dev/null
done

sudo systemctl restart "$yolo_service_name"
sudo systemctl restart "$watermark_service_name"
sudo systemctl restart "$service_name"
watermark_ready=0
for _ in {1..30}; do
  if curl -fsS --max-time 5 http://127.0.0.1:5066/health \
    | /home/ymk/miniconda3/envs/realguard/bin/python -c '
import json, sys
payload = json.load(sys.stdin)
generic = payload.get("genericVisibleWatermark") or {}
assert payload.get("status") == "ok"
assert payload.get("registryReady") is True
assert payload.get("tokenReady") is True
assert payload.get("coordinateSpace") == "display_normalized_v1"
assert generic.get("available") is True
assert generic.get("cudaReady") is True
assert generic.get("device") != "cpu"
assert generic.get("modelRevision") == "796a3b58a1121f20c5976d59314baea3db659a66"
assert generic.get("modelSha256") == "6ac71b6ab8db27ec7928b5176e60a359c65e1579a5c1d58cf2f98df30cf3085e"
'; then
    watermark_ready=1
    break
  fi
  sleep 1
done
test "$watermark_ready" = "1"
sudo systemctl is-active --quiet "$yolo_service_name"
model_token="$(sudo awk -F= '/^REALGUARD_MODEL_INTERNAL_TOKEN=/{print substr($0, index($0, "=") + 1); exit}' /etc/realguard/model-inference.env)"
test "${#model_token}" -ge 32
response_hmac_key="$(sudo awk -F= '/^REALGUARD_MODEL_RESPONSE_HMAC_KEY=/{print substr($0, index($0, "=") + 1); exit}' /etc/realguard/model-inference.env)"
[[ "$response_hmac_key" =~ ^[0-9a-f]{64}$ ]]
response_hmac_key_id="$(sudo awk -F= '/^REALGUARD_MODEL_RESPONSE_HMAC_KEY_ID=/{print substr($0, index($0, "=") + 1); exit}' /etc/realguard/model-inference.env)"
response_hmac_key_id="${response_hmac_key_id:-v1}"
[[ "$response_hmac_key_id" =~ ^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$ ]]
expected_model_revision="$(sed -n 's/^Environment=REALGUARD_V2_MODEL_REVISION=//p' "$release_root/systemd/realguard-detection-gpu.conf" | tail -1)"
expected_model_sha256="$(sed -n 's/^Environment=REALGUARD_V2_MODEL_SHA256=//p' "$release_root/systemd/realguard-detection-gpu.conf" | tail -1)"
test -n "$expected_model_revision"
[[ "$expected_model_sha256" =~ ^[0-9a-f]{64}$ ]]
health_ready=0
for _ in {1..60}; do
  if curl -fsS --max-time 5 \
    -H "X-RealGuard-Internal-Token: $model_token" \
    http://127.0.0.1:5000/internal/model/health \
    | /home/ymk/miniconda3/envs/realguard/bin/python -c '
import json, sys
payload = json.load(sys.stdin)
data = payload.get("data") or {}
policy = data.get("modelDecisionPolicy") or {}
assert payload.get("code") == 200
assert data.get("activeProvider") == "CUDAExecutionProvider"
assert data.get("modelRevision") == sys.argv[1]
assert data.get("modelSha256") == sys.argv[2]
assert data.get("deploymentCommit") == sys.argv[3]
assert data.get("visiblePrecheckReady") is True
assert data.get("responseIntegrityReady") is True
assert data.get("responseIntegrityKeyId") == sys.argv[4]
assert policy.get("mode") in {"review_only", "calibrated_verdict"}
assert isinstance(policy.get("ready"), bool)
' "$expected_model_revision" "$expected_model_sha256" "$commit_sha" "$response_hmac_key_id"; then
    health_ready=1
    break
  fi
  sleep 2
done
test "$health_ready" = "1"
sudo systemctl is-active --quiet "$service_name"

printf '%s' 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC' \
  | base64 -d > /tmp/realguard-gpu-deployment-probe.png
set -a
. /home/ymk/services/watermark-precheck/.env
set +a
curl -fsS --max-time 60 \
  -H "Authorization: Bearer $WATERMARK_PRECHECK_TOKEN" \
  -F file=@/tmp/realguard-gpu-deployment-probe.png \
  http://127.0.0.1:5066/v1/precheck \
  | /home/ymk/miniconda3/envs/realguard/bin/python -c '
import json, sys
payload = json.load(sys.stdin)
generic = payload.get("genericVisibleWatermark") or {}
size = payload.get("displaySize") or {}
assert payload.get("status") == "ok"
assert payload.get("coordinateSpace") == "display_normalized_v1"
assert int(size.get("width") or 0) > 0 and int(size.get("height") or 0) > 0
assert generic.get("available") is True
'
curl -fsS --max-time 180 \
  -H "X-RealGuard-Internal-Token: $model_token" \
  -H 'X-RealGuard-Request-Nonce: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' \
  -F image_file=@/tmp/realguard-gpu-deployment-probe.png \
  http://127.0.0.1:5000/internal/model/predict \
  | /home/ymk/miniconda3/envs/realguard/bin/python -c '
import json, sys
payload = json.load(sys.stdin)
data = payload.get("data") or {}
runtime = data.get("runtime") or {}
precheck = data.get("visibleWatermarkPrecheck") or {}
generic = precheck.get("genericVisibleWatermark") or {}
decision = data.get("modelDecision") or {}
integrity = payload.get("integrity") or {}
assert payload.get("code") == 200
assert runtime.get("activeProvider") == "CUDAExecutionProvider"
assert runtime.get("modelRevision") == sys.argv[1]
assert runtime.get("modelSha256") == sys.argv[2]
assert runtime.get("deploymentCommit") == sys.argv[3]
assert runtime.get("visiblePrecheckStatus") == "success"
assert precheck.get("coordinateSpace") == "display_normalized_v1"
assert generic.get("available") is True
assert generic.get("cudaReady") is True
assert generic.get("device") != "cpu"
assert generic.get("modelRevision") == "796a3b58a1121f20c5976d59314baea3db659a66"
assert generic.get("modelSha256") == "6ac71b6ab8db27ec7928b5176e60a359c65e1579a5c1d58cf2f98df30cf3085e"
assert decision.get("mode") in {"review_only", "calibrated_verdict"}
assert integrity.get("schema") == "cn.huijian.remote-inference-response-v1"
assert integrity.get("requestNonce") == "a" * 32
assert len(str(integrity.get("imageSha256") or "")) == 64
assert len(str(integrity.get("bodySha256") or "")) == 64
assert len(str(integrity.get("hmacSha256") or "")) == 64
if decision.get("ready") is not True:
    assert data.get("finalLabel") == "需人工复核"
    assert float(data.get("fakeProbability")) == 0.5
' "$expected_model_revision" "$expected_model_sha256" "$commit_sha"

sudo systemctl restart "$model_tunnel_service_name"
sudo systemctl restart "$precheck_tunnel_service_name"
for managed_service in "${managed_service_names[@]}"; do
  sudo systemctl is-enabled --quiet "$managed_service"
  sudo systemctl is-active --quiet "$managed_service"
done

switched=0
units_switched=0
flock -u 9
printf 'GPU detection release activated: %s\n' "$release_root"
