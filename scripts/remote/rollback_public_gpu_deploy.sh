#!/usr/bin/env bash
set -euo pipefail

backup_root="$(readlink -f "${1:?backup root is required}")"
config_target="${2:?config target is required}"
marker_target="${3:?marker target is required}"
detector_service="${4:-realguard-detector-backend.service}"
worker_service="${5:-realguard-developer-worker.service}"
expected_commit="${6:?expected commit is required}"
mode="${7:-watchdog}"

case "$backup_root" in
  /opt/realguard-data/gpu-deploy-backups/*) ;;
  *) echo "unsafe public rollback path" >&2; exit 2 ;;
esac
[[ "$config_target" == "/etc/systemd/system/realguard-detector-backend.service.d/remote.conf" ]]
[[ "$marker_target" == "/opt/realguard-data/public-detector-remote.DEPLOYED_COMMIT" ]]
[[ "$detector_service" == "realguard-detector-backend.service" ]]
[[ "$worker_service" == "realguard-developer-worker.service" ]]
[[ "$expected_commit" =~ ^[0-9a-f]{7,40}$ ]]
[[ "$mode" == "watchdog" || "$mode" == "force" ]]

exec 9>/var/lock/realguard-public-gpu-deploy.lock
flock -w 60 9
current_commit=""
if [[ -f "$marker_target" ]]; then
  current_commit="$(tr -d '[:space:]' < "$marker_target")"
fi
if [[ "$current_commit" != "$expected_commit" ]]; then
  exit 0
fi
watchdog_probe() {
  [[ "$(systemctl is-active "$detector_service" 2>/dev/null || true)" == "active" ]] \
    && [[ "$(systemctl is-active "$worker_service" 2>/dev/null || true)" == "$(cat "$backup_root/$worker_service.active")" ]] \
    && curl -fsS --connect-timeout 3 --max-time 10 http://127.0.0.1:15001/health | python3 -c '
import json, sys
payload = json.load(sys.stdin)
remote = payload.get("remoteInference") or {}
assert payload.get("capabilityReady") is True
assert remote.get("deploymentCommit") == sys.argv[1]
' "$expected_commit"
}
if [[ "$mode" == "watchdog" ]]; then
  for attempt in 1 2 3 4 5 6; do
    if watchdog_probe; then
      exit 0
    fi
    [[ "$attempt" == "6" ]] || sleep 10
  done
fi

if [[ -f "$backup_root/remote.conf.missing" ]]; then
  rm -f "$config_target"
else
  install -D -o root -g root -m 0644 "$backup_root/remote.conf.previous" "$config_target"
fi
if [[ -f "$backup_root/marker.missing" ]]; then
  rm -f "$marker_target"
else
  install -D -o root -g root -m 0644 "$backup_root/marker.previous" "$marker_target"
fi
systemctl daemon-reload
restore_unit_state() {
  local service="$1"
  local enabled active actual_enabled actual_active
  enabled="$(cat "$backup_root/$service.enabled")"
  active="$(cat "$backup_root/$service.active")"
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
restore_unit_state "$detector_service"
restore_unit_state "$worker_service"
flock -u 9
