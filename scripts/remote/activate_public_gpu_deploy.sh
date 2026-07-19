#!/usr/bin/env bash
set -euo pipefail

commit_sha="${1:?commit SHA is required}"
config_tmp="${2:?temporary config is required}"
backup_root="${3:?backup root is required}"
config_target="${4:?config target is required}"
marker_target="${5:?marker target is required}"
rollback_tmp="${6:?temporary rollback script is required}"
rollback_target="${7:?rollback target is required}"
rollback_unit="${8:?rollback unit is required}"
drain_worker="${9:-1}"
detector_service="${10:-realguard-detector-backend.service}"
worker_service="${11:-realguard-developer-worker.service}"

[[ "$commit_sha" =~ ^[0-9a-f]{7,40}$ ]]
[[ "$backup_root" == /opt/realguard-data/gpu-deploy-backups/* ]]
[[ "$config_target" == "/etc/systemd/system/realguard-detector-backend.service.d/remote.conf" ]]
[[ "$marker_target" == "/opt/realguard-data/public-detector-remote.DEPLOYED_COMMIT" ]]
[[ "$rollback_target" == /opt/realguard-data/gpu-deploy-backups/*-rollback.sh ]]
[[ "$rollback_unit" =~ ^realguard-public-gpu-rollback-[0-9a-f]{7,40}-[0-9]+$ ]]
[[ "$drain_worker" == "0" || "$drain_worker" == "1" ]]
[[ "$detector_service" == "realguard-detector-backend.service" ]]
[[ "$worker_service" == "realguard-developer-worker.service" ]]
test -s "$config_tmp"
test -s "$rollback_tmp"

exec 9>/var/lock/realguard-public-gpu-deploy.lock
flock -w 60 9

response_hmac_key="$(awk -F= '/^REALGUARD_MODEL_RESPONSE_HMAC_KEY=/{print substr($0, index($0, "=") + 1); exit}' /etc/realguard/model-inference.env)"
[[ "$response_hmac_key" =~ ^[0-9a-f]{64}$ ]]
response_hmac_key_id="$(awk -F= '/^REALGUARD_MODEL_RESPONSE_HMAC_KEY_ID=/{print substr($0, index($0, "=") + 1); exit}' /etc/realguard/model-inference.env)"
response_hmac_key_id="${response_hmac_key_id:-v1}"
[[ "$response_hmac_key_id" =~ ^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$ ]]

rollback_on_error() {
  status=$?
  trap - ERR
  flock -u 9 || true
  if [[ -x "$rollback_target" ]]; then
    "$rollback_target" "$backup_root" "$config_target" "$marker_target" \
      "$detector_service" "$worker_service" "$commit_sha" force || true
  fi
  exit "$status"
}
trap rollback_on_error ERR

install -d -o root -g root -m 0700 "$backup_root"
install -o root -g root -m 0700 "$rollback_tmp" "$rollback_target"
rm -f "$rollback_tmp"
if [[ -f "$config_target" ]]; then
  install -o root -g root -m 0600 "$config_target" "$backup_root/remote.conf.previous"
else
  touch "$backup_root/remote.conf.missing"
fi
if [[ -f "$marker_target" ]]; then
  install -o root -g root -m 0600 "$marker_target" "$backup_root/marker.previous"
else
  touch "$backup_root/marker.missing"
fi

for service in "$detector_service" "$worker_service"; do
  systemctl is-enabled "$service" > "$backup_root/$service.enabled" 2>/dev/null || true
  systemctl is-active "$service" > "$backup_root/$service.active" 2>/dev/null || true
  case "$(cat "$backup_root/$service.enabled")" in
    enabled|enabled-runtime|disabled|static|indirect|generated|masked|masked-runtime) ;;
    *) echo "unsupported UnitFileState for $service" >&2; exit 1 ;;
  esac
  case "$(cat "$backup_root/$service.active")" in
    active|inactive) ;;
    *) echo "unsupported ActiveState for $service" >&2; exit 1 ;;
  esac
done

systemd-run --quiet --unit="$rollback_unit" --on-active=15m \
  "$rollback_target" "$backup_root" "$config_target" "$marker_target" \
  "$detector_service" "$worker_service" "$commit_sha" watchdog

printf '%s\n' "$commit_sha" > "$backup_root/marker.next"
install -D -o root -g root -m 0644 "$backup_root/marker.next" "$marker_target"
install -D -o root -g root -m 0644 "$config_tmp" "$config_target"
rm -f "$config_tmp" "$backup_root/marker.next"
systemctl daemon-reload
if [[ "$drain_worker" == "1" ]]; then
  systemctl stop "$worker_service"
fi
systemctl restart "$detector_service"
systemctl is-active --quiet "$detector_service"
flock -u 9
