#!/usr/bin/env bash
set -euo pipefail

bundle="$(readlink -f "${1:?runtime bundle is required}")"
checkpoint="$(readlink -f "${2:?checkpoint is required}")"
expected_sha256="f527d8a7542061eb58b0a2953ea86b66b0ecf0b16f3c84d64886e8104c341081"
revision="2026-08-31-f527d8a75420"
model_name="huijian/yolo11x_explicit_watermark_binary"
yolo_root="/home/ymk/services/yolo-watermark"
precheck_root="/home/ymk/services/watermark-precheck"
env_file="$yolo_root/.env"
release_root="$yolo_root/releases/$revision-$(date -u +%Y%m%dT%H%M%SZ)-$$"
backup_root="$release_root/previous"
yolo_unit="/etc/systemd/system/realguard-yolo-watermark.service"
precheck_dropin="/etc/systemd/system/realguard-watermark-precheck.service.d/yolo.conf"
switched=0

case "$bundle" in /tmp/*) ;; *) echo "unsafe runtime bundle path" >&2; exit 2 ;; esac
case "$checkpoint" in /tmp/*) ;; *) echo "unsafe checkpoint path" >&2; exit 2 ;; esac
test -s "$bundle"
test -s "$checkpoint"
test "$(sha256sum "$checkpoint" | awk '{print $1}')" = "$expected_sha256"

restore_path() {
  local label="$1" target="$2"
  rm -f "$target"
  if [[ -f "$backup_root/$label.symlink" ]]; then
    ln -s "$(cat "$backup_root/$label.symlink")" "$target"
  elif [[ -f "$backup_root/$label.missing" ]]; then
    return 0
  else
    install -D -o ymk -g ymk -m 0644 "$backup_root/$label.file" "$target"
  fi
}

backup_path() {
  local source="$1" label="$2"
  if [[ -L "$source" ]]; then
    readlink "$source" > "$backup_root/$label.symlink"
  elif [[ -e "$source" ]]; then
    cp -aL "$source" "$backup_root/$label.file"
  else
    touch "$backup_root/$label.missing"
  fi
}

rollback() {
  local status=$?
  trap - ERR
  if [[ "$switched" == "1" ]]; then
    printf 'Watermark model activation failed; restoring previous runtime.\n' >&2
    restore_path yolo-service "$yolo_root/service.py"
    restore_path yolo-model "$yolo_root/models/best.pt"
    restore_path yolo-manifest "$yolo_root/model-manifest.json"
    restore_path precheck-adapter "$precheck_root/yolo_adapter.py"
    install -o ymk -g ymk -m 0600 "$backup_root/yolo.env" "$env_file"
    sudo install -m 0644 "$backup_root/yolo-unit" "$yolo_unit"
    sudo install -m 0644 "$backup_root/precheck-dropin" "$precheck_dropin"
    sudo systemctl daemon-reload
    sudo systemctl restart realguard-yolo-watermark.service
    sudo systemctl restart realguard-watermark-precheck.service
  fi
  exit "$status"
}
trap rollback ERR

install -d -m 0755 "$release_root" "$backup_root"
tar -xzf "$bundle" -C "$release_root"
runtime_root="$release_root/services"
test -s "$runtime_root/yolo-watermark/service.py"
test -s "$runtime_root/yolo-watermark/model-manifest.json"
test -s "$runtime_root/yolo-watermark/realguard-yolo-watermark.service"
test -s "$runtime_root/watermark-precheck/yolo_adapter.py"
test -s "$runtime_root/watermark-precheck/realguard-watermark-precheck-yolo.conf"

python3 -m py_compile \
  "$runtime_root/yolo-watermark/service.py" \
  "$runtime_root/watermark-precheck/yolo_adapter.py"
python3 - "$runtime_root/yolo-watermark/model-manifest.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as manifest_file:
    manifest = json.load(manifest_file)
assert manifest["model"] == "huijian/yolo11x_explicit_watermark_binary"
assert manifest["revision"] == "2026-08-31-f527d8a75420"
assert manifest["sha256"] == "f527d8a7542061eb58b0a2953ea86b66b0ecf0b16f3c84d64886e8104c341081"
assert manifest["classes"] == {"0": "watermark"}
PY

backup_path "$yolo_root/service.py" yolo-service
backup_path "$yolo_root/models/best.pt" yolo-model
backup_path "$yolo_root/model-manifest.json" yolo-manifest
backup_path "$precheck_root/yolo_adapter.py" precheck-adapter
cp -aL "$env_file" "$backup_root/yolo.env"
sudo cp -aL "$yolo_unit" "$backup_root/yolo-unit"
sudo cp -aL "$precheck_dropin" "$backup_root/precheck-dropin"

install -o ymk -g ymk -m 0644 "$checkpoint" "$release_root/best.pt"
test "$(sha256sum "$release_root/best.pt" | awk '{print $1}')" = "$expected_sha256"
ln -sfn "$release_root/best.pt" "$yolo_root/models/best.pt.next"
mv -Tf "$yolo_root/models/best.pt.next" "$yolo_root/models/best.pt"
ln -sfn "$runtime_root/yolo-watermark/service.py" "$yolo_root/service.py.next"
mv -Tf "$yolo_root/service.py.next" "$yolo_root/service.py"
ln -sfn "$runtime_root/yolo-watermark/model-manifest.json" "$yolo_root/model-manifest.json.next"
mv -Tf "$yolo_root/model-manifest.json.next" "$yolo_root/model-manifest.json"
ln -sfn "$runtime_root/watermark-precheck/yolo_adapter.py" "$precheck_root/yolo_adapter.py.next"
mv -Tf "$precheck_root/yolo_adapter.py.next" "$precheck_root/yolo_adapter.py"
python3 - "$env_file" <<'PY'
import os
import stat
import sys
from pathlib import Path

path = Path(sys.argv[1])
updates = {
    "YOLO_WATERMARK_MODEL_NAME": "huijian/yolo11x_explicit_watermark_binary",
    "YOLO_WATERMARK_REVISION": "2026-08-31-f527d8a75420",
    "YOLO_WATERMARK_MODEL_SHA256": "f527d8a7542061eb58b0a2953ea86b66b0ecf0b16f3c84d64886e8104c341081",
    "YOLO_WATERMARK_IMAGE_SIZE": "512",
    "YOLO_WATERMARK_CONFIDENCE": "0.25",
    "YOLO_WATERMARK_IOU": "0.45",
    "YOLO_WATERMARK_WARMUP": "true",
}
seen = set()
output = []
for line in path.read_text(encoding="utf-8").splitlines():
    key = line.split("=", 1)[0] if "=" in line and not line.lstrip().startswith("#") else ""
    if key in updates:
        output.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        output.append(line)
for key, value in updates.items():
    if key not in seen:
        output.append(f"{key}={value}")
temporary = path.with_name(f".{path.name}.next")
temporary.write_text("\n".join(output) + "\n", encoding="utf-8")
os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
os.replace(temporary, path)
PY
sudo install -m 0644 \
  "$runtime_root/yolo-watermark/realguard-yolo-watermark.service" "$yolo_unit"
sudo install -m 0644 \
  "$runtime_root/watermark-precheck/realguard-watermark-precheck-yolo.conf" "$precheck_dropin"
switched=1

sudo systemctl daemon-reload
sudo systemctl restart realguard-yolo-watermark.service
yolo_ready=0
for _ in {1..60}; do
  if payload="$(curl -fsS --max-time 5 http://127.0.0.1:5067/health 2>/dev/null)" \
    && printf '%s' "$payload" | python3 -c '
import json, sys
p = json.load(sys.stdin)
assert p.get("status") == "ok"
assert p.get("model") == sys.argv[1]
assert p.get("modelRevision") == sys.argv[2]
assert p.get("modelSha256") == sys.argv[3]
assert p.get("modelResident") is True
assert p.get("modelLoadCount") == 1
assert p.get("warmupCompleted") is True
assert p.get("cudaReady") is True and p.get("device") != "cpu" and p.get("gpu")
assert p.get("inputSize") == 512
assert float(p.get("confidenceThreshold")) == 0.25
' "$model_name" "$revision" "$expected_sha256" 2>/dev/null; then
    yolo_ready=1
    break
  fi
  sleep 1
done
test "$yolo_ready" = "1"

sudo systemctl restart realguard-watermark-precheck.service
precheck_ready=0
for _ in {1..30}; do
  if payload="$(curl -fsS --max-time 5 http://127.0.0.1:5066/health 2>/dev/null)" \
    && printf '%s' "$payload" | python3 -c '
import json, sys
p = json.load(sys.stdin)
g = p.get("genericVisibleWatermark") or {}
assert p.get("status") == "ok"
assert g.get("available") is True
assert g.get("model") == sys.argv[1]
assert g.get("modelRevision") == sys.argv[2]
assert g.get("modelSha256") == sys.argv[3]
assert g.get("modelResident") is True
assert g.get("modelLoadCount") == 1
assert g.get("warmupCompleted") is True
assert g.get("cudaReady") is True
' "$model_name" "$revision" "$expected_sha256" 2>/dev/null; then
    precheck_ready=1
    break
  fi
  sleep 1
done
test "$precheck_ready" = "1"

install -o ymk -g ymk -m 0644 \
  "$runtime_root/yolo-watermark/model-manifest.json" "$yolo_root/DEPLOYED_MODEL.json"
printf '%s\n' "$release_root" > "$yolo_root/CURRENT_MODEL_RELEASE"
trap - ERR
printf 'Activated %s (%s) from %s\n' "$model_name" "$revision" "$release_root"
