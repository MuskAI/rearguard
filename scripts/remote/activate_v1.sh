#!/usr/bin/env bash
set -euo pipefail

sudo install -m 600 -o ubuntu -g ubuntu /dev/null /var/lock/realguard-public-release.lock
sudo install -m 600 -o ubuntu -g ubuntu /dev/null /var/lock/realguard-v1-deploy.lock
exec 8>/var/lock/realguard-public-release.lock
flock -w 900 8 || { printf 'Another public release transaction is still running.\n' >&2; exit 75; }
exec 9>/var/lock/realguard-v1-deploy.lock
flock -n 9 || { printf 'Another V1 activation is already running.\n' >&2; exit 75; }

: "${IP2REGION_XDB_SHA256:?missing IP2REGION_XDB_SHA256}"
DETECTOR_PORT="${REALGUARD_DETECTOR_PORT:-15001}"
commit_sha="$(tr -d '[:space:]' </tmp/realguard-v1.DEPLOYED_COMMIT)"
[[ "$commit_sha" =~ ^[0-9a-f]{7,40}$ ]]
release_id="${commit_sha}-$(date -u +%Y%m%dT%H%M%SZ)-$$"
release_root="/opt/realguard-server/releases/$release_id"
current_backend=""
current_runtime=""
backend_switched=0
runtime_switched=0
frontend_switched=0
nginx_switched=0
units_switched=0

rollback() {
  status=$?
  trap - ERR
  set +e
  printf 'V1 activation failed; restoring the previous application.\n' >&2
  if [[ "$frontend_switched" == "1" && -d /var/www/realguard-frontend.previous ]]; then
    sudo rm -rf /var/www/realguard-frontend
    sudo mv /var/www/realguard-frontend.previous /var/www/realguard-frontend
  fi
  if [[ "$backend_switched" == "1" && -n "$current_backend" && -e "$current_backend" ]]; then
    sudo ln -sfn "$current_backend" /opt/realguard-server/RealGuard.next
    sudo mv -Tf /opt/realguard-server/RealGuard.next /opt/realguard-server/RealGuard
  fi
  if [[ "$runtime_switched" == "1" && -n "$current_runtime" && -e "$current_runtime" ]]; then
    sudo ln -sfn "$current_runtime" /opt/realguard-server/.venv.next
    sudo mv -Tf /opt/realguard-server/.venv.next /opt/realguard-server/.venv
  fi
  if [[ "$units_switched" == "1" ]]; then
    for unit in \
      realguard-backend.service \
      realguard-detector-backend.service \
      realguard-developer-worker.service \
      realguard-alert-worker.service \
      realguard-alert-watchdog.service \
      realguard-alert-watchdog.timer \
      realguard-backup.service \
      realguard-backup.timer \
      realguard-restore-drill.service \
      realguard-restore-drill.timer \
      realguard-security-audit-verify.service \
      realguard-security-audit-verify.timer; do
      if [[ -f "/tmp/$unit.previous" ]]; then
        sudo cp -a "/tmp/$unit.previous" "/etc/systemd/system/$unit"
      else
        sudo rm -f "/etc/systemd/system/$unit"
      fi
    done
    if [[ -f /tmp/realguard-detector-url.conf.previous ]]; then
      sudo install -d -m 755 /etc/systemd/system/realguard-backend.service.d
      sudo cp -a /tmp/realguard-detector-url.conf.previous \
        /etc/systemd/system/realguard-backend.service.d/40-detector-backend-url.conf
    else
      sudo rm -f /etc/systemd/system/realguard-backend.service.d/40-detector-backend-url.conf
    fi
    sudo systemctl daemon-reload || true
  fi
  if [[ "$nginx_switched" == "1" ]]; then
    if [[ -f /tmp/realguard-frontend.nginx.previous ]]; then
      sudo cp -a /tmp/realguard-frontend.nginx.previous /etc/nginx/sites-enabled/realguard-frontend
    else
      sudo rm -f /etc/nginx/sites-enabled/realguard-frontend
    fi
    if [[ -f /tmp/realguard-https.nginx.previous ]]; then
      sudo cp -a /tmp/realguard-https.nginx.previous /etc/nginx/conf.d/myapp.conf
    else
      sudo rm -f /etc/nginx/conf.d/myapp.conf
    fi
    if [[ -f /tmp/realguard-zones.nginx.previous ]]; then
      sudo cp -a /tmp/realguard-zones.nginx.previous /etc/nginx/conf.d/00-realguard-zones.conf
    else
      sudo rm -f /etc/nginx/conf.d/00-realguard-zones.conf
    fi
    if [[ -d /tmp/realguard-snippets.previous ]]; then
      sudo rm -rf /etc/nginx/snippets
      sudo cp -a /tmp/realguard-snippets.previous /etc/nginx/snippets
    fi
    sudo nginx -t && sudo systemctl reload nginx || true
  fi
  sudo systemctl restart realguard-detector-backend.service || true
  sudo systemctl restart realguard-developer-worker.service || true
  sudo systemctl restart realguard-alert-worker.service || true
  sudo systemctl restart realguard-backend.service || true
  exit "$status"
}

printf '%s  %s\n' "$IP2REGION_XDB_SHA256" /tmp/realguard-ip2region-v4.xdb | sha256sum -c -
if sudo nginx -T 2>/dev/null \
  | grep -Eq '^[[:space:]]*listen[[:space:]]+([^;[:space:]]*:)?5000([[:space:]]|;)'; then
  echo "Nginx must not listen on the internal V1 application port 5000" >&2
  exit 1
fi
sudo install -d -m 700 -o ubuntu -g ubuntu /opt/realguard-data/privacy-erasure
sudo -u ubuntu python3 - /opt/realguard-data/privacy-erasure/privacy-erasure-tombstones.sqlite3 <<'PY'
import os
from pathlib import Path
import sqlite3
import sys

path = Path(sys.argv[1])
with sqlite3.connect(path) as connection:
    connection.executescript("""
    CREATE TABLE IF NOT EXISTS privacy_erasure_tombstones (
        tombstone_id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        source_system TEXT NOT NULL,
        resource_kind TEXT NOT NULL,
        primary_id TEXT NOT NULL,
        secondary_id TEXT,
        payload_json TEXT NOT NULL,
        payload_sha256 TEXT NOT NULL UNIQUE
    );
    CREATE INDEX IF NOT EXISTS idx_privacy_tombstones_created
        ON privacy_erasure_tombstones(created_at, tombstone_id);
    CREATE TRIGGER IF NOT EXISTS privacy_tombstones_immutable_update
    BEFORE UPDATE ON privacy_erasure_tombstones
    BEGIN
        SELECT RAISE(ABORT, 'privacy erasure tombstones are immutable');
    END;
    CREATE TRIGGER IF NOT EXISTS privacy_tombstones_immutable_delete
    BEFORE DELETE ON privacy_erasure_tombstones
    BEGIN
        SELECT RAISE(ABORT, 'privacy erasure tombstones are immutable');
    END;
    """)
os.chmod(path, 0o600)
PY
sudo install -m 700 /tmp/realguard-backup /usr/local/sbin/realguard-backup
sudo install -m 700 /tmp/realguard-restore-verify /usr/local/sbin/realguard-restore-verify
sudo install -m 700 /tmp/realguard-replay-privacy-erasures /usr/local/sbin/realguard-replay-privacy-erasures
sudo bash -lc '
  set -euo pipefail
  set -a
  for env_file in \
    /etc/realguard/session.env \
    /etc/realguard/realguard-backend.env \
    /etc/realguard/detector-db.env \
    /etc/realguard/backup.env; do
    [ ! -f "$env_file" ] || . "$env_file"
  done
  set +a
  backup_output="$(/usr/local/sbin/realguard-backup 2>&1)"
  printf "%s\n" "$backup_output"
  backup_dir="$(printf "%s\n" "$backup_output" \
    | sed -n "s/^RealGuard backup completed: //p" \
    | tail -n 1)"
  test -n "$backup_dir"
  test -d "$backup_dir"
  (cd "$backup_dir" && sha256sum -c SHA256SUMS)
  echo "Pre-migration backup verified: $backup_dir"
'

# Build and validate the immutable release while the current services are still
# serving traffic. The maintenance window below is then limited to migration,
# pointer switches, and process restart.
sudo install -d -m 755 -o ubuntu -g ubuntu /opt/realguard-server/releases
sudo rm -rf "$release_root"
sudo install -d -m 755 -o ubuntu -g ubuntu "$release_root" "$release_root/RealGuard"
sudo tar -xzf /tmp/realguard-v1-backend.tgz -C "$release_root/RealGuard"
sudo install -m 644 /tmp/realguard-v1.DEPLOYED_COMMIT "$release_root/DEPLOYED_COMMIT"
sudo -u ubuntu python3 -m venv "$release_root/.venv"
sudo -u ubuntu "$release_root/.venv/bin/python" -m pip install \
  --no-cache-dir --quiet --upgrade -r "$release_root/RealGuard/requirements.lock"
sudo -u ubuntu "$release_root/.venv/bin/python" -m pip install \
  --no-cache-dir --quiet --no-deps 'invisible-watermark==0.2.0'
sudo bash -lc '
  set -euo pipefail
  set -a
  for env_file in \
    /etc/realguard/session.env \
    /etc/realguard/realguard-backend.env \
    /etc/realguard/detector-db.env \
    /etc/realguard/agent.env \
    /etc/realguard/model-inference.env; do
    [ ! -f "$env_file" ] || . "$env_file"
  done
  set +a
  cd '"$release_root"'/RealGuard
  '"$release_root"'/.venv/bin/python -c "import run, detector_backend"
'

trap rollback ERR
sudo systemctl stop realguard-developer-worker.service 2>/dev/null || true
sudo systemctl stop realguard-backend.service 2>/dev/null || true
sudo install -d -m 755 -o ubuntu -g ubuntu /opt/realguard-data
sudo install -d -m 700 -o ubuntu -g ubuntu /opt/realguard-data/developer-spool
sudo install -d -m 700 -o ubuntu -g ubuntu /opt/realguard-data/web-spool
sudo install -d -m 700 -o ubuntu -g ubuntu /opt/realguard-data/privacy-erasure
sudo install -d -m 700 -o ubuntu -g ubuntu /opt/realguard-data/evidence-manifests
sudo install -m 644 /tmp/realguard-ip2region-v4.xdb /opt/realguard-data/ip2region_v4.xdb
if [[ ! -f /opt/realguard-data/admin_state.json ]]; then
  if [[ -f /home/ubuntu/.local/state/realguard/admin_state.json ]]; then
    sudo install -m 600 -o ubuntu -g ubuntu \
      /home/ubuntu/.local/state/realguard/admin_state.json \
      /opt/realguard-data/admin_state.json
  else
    sudo -u ubuntu touch /opt/realguard-data/admin_state.json
    sudo chmod 600 /opt/realguard-data/admin_state.json
  fi
fi
sudo chown ubuntu:ubuntu /opt/realguard-data/admin_state.json
sudo chmod 600 /opt/realguard-data/admin_state.json

sudo install -d -m 755 -o ubuntu -g ubuntu /opt/realguard-server/releases
if [[ -L /opt/realguard-server/RealGuard ]]; then
  current_backend="$(readlink -f /opt/realguard-server/RealGuard)"
elif [[ -d /opt/realguard-server/RealGuard ]]; then
  legacy_root="/opt/realguard-server/releases/legacy-$(date -u +%Y%m%dT%H%M%SZ)"
  sudo install -d -m 755 -o ubuntu -g ubuntu "$legacy_root"
  sudo mv /opt/realguard-server/RealGuard "$legacy_root/RealGuard"
  current_backend="$legacy_root/RealGuard"
  sudo ln -s "$current_backend" /opt/realguard-server/RealGuard
fi
if [[ -L /opt/realguard-server/.venv ]]; then
  current_runtime="$(readlink -f /opt/realguard-server/.venv)"
elif [[ -d /opt/realguard-server/.venv ]]; then
  legacy_runtime="/opt/realguard-server/releases/legacy-runtime-$(date -u +%Y%m%dT%H%M%SZ)"
  sudo mv /opt/realguard-server/.venv "$legacy_runtime"
  current_runtime="$legacy_runtime"
  sudo ln -s "$current_runtime" /opt/realguard-server/.venv
fi

if [[ ! -d /opt/realguard-data/uploads ]]; then
  if [[ -n "$current_backend" && -d "$current_backend/imagedetection/static/uploads" \
    && ! -L "$current_backend/imagedetection/static/uploads" ]]; then
    sudo mv "$current_backend/imagedetection/static/uploads" /opt/realguard-data/uploads
  else
    sudo install -d -m 755 -o ubuntu -g ubuntu /opt/realguard-data/uploads
  fi
fi
if [[ -n "$current_backend" ]]; then
  sudo rm -rf "$current_backend/imagedetection/static/uploads"
  sudo ln -s /opt/realguard-data/uploads "$current_backend/imagedetection/static/uploads"
fi
sudo rm -rf "$release_root/RealGuard/imagedetection/static/uploads"
sudo ln -s /opt/realguard-data/uploads "$release_root/RealGuard/imagedetection/static/uploads"
sudo install -d -m 755 -o ubuntu -g ubuntu /opt/realguard-data/uploads/aliyun-probes
sudo install -d -m 750 -o root -g ubuntu /opt/realguard-data/legacy-governance-evidence
sudo chown -R ubuntu:ubuntu /opt/realguard-data/uploads

sudo install -d -m 700 /etc/realguard
sudo install -d -m 700 -o root -g root /opt/realguard-audit-checkpoint
sudo touch /etc/realguard/backup.env
sudo chmod 600 /etc/realguard/backup.env
sudo chown root:root /etc/realguard/backup.env
session_secret="$(sudo awk -F= '/^SECRET_KEY=/{print substr($0, index($0, "=") + 1); exit}' /etc/realguard/session.env 2>/dev/null || true)"
session_secret_lower="$(printf '%s' "$session_secret" | tr '[:upper:]' '[:lower:]')"
if [[ ${#session_secret} -lt 32 \
    || "$session_secret_lower" == "change-me" \
    || "$session_secret_lower" == "changeme" \
    || "$session_secret_lower" == "secret" \
    || "$session_secret_lower" == "test" \
    || "$session_secret_lower" == change-* \
    || "$session_secret_lower" == replace-* \
    || "$session_secret_lower" == example-* \
    || "$session_secret_lower" == your-* ]]; then
  secret_key="$(openssl rand -hex 48)"
  if sudo grep -q '^SECRET_KEY=' /etc/realguard/session.env 2>/dev/null; then
    sudo sed -i "s/^SECRET_KEY=.*/SECRET_KEY=$secret_key/" /etc/realguard/session.env
  else
    printf 'SECRET_KEY=%s\n' "$secret_key" | sudo tee -a /etc/realguard/session.env >/dev/null
  fi
fi
sudo chmod 600 /etc/realguard/session.env
sudo chown root:root /etc/realguard/session.env
sudo touch /etc/realguard/realguard-backend.env
if ! sudo grep -q '^REALGUARD_CONSENT_AUDIT_SALT=' /etc/realguard/realguard-backend.env; then
  consent_salt="$(openssl rand -hex 32)"
  printf 'REALGUARD_CONSENT_AUDIT_SALT=%s\n' "$consent_salt" \
    | sudo tee -a /etc/realguard/realguard-backend.env >/dev/null
fi
if ! sudo grep -q '^REALGUARD_DEVELOPER_IDEMPOTENCY_SECRET=' /etc/realguard/realguard-backend.env; then
  idempotency_secret="$(openssl rand -hex 32)"
  printf 'REALGUARD_DEVELOPER_IDEMPOTENCY_SECRET=%s\n' "$idempotency_secret" \
    | sudo tee -a /etc/realguard/realguard-backend.env >/dev/null
fi
security_audit_key="$(sudo awk -F= '/^REALGUARD_SECURITY_AUDIT_HMAC_KEY=/{print substr($0, index($0, "=") + 1); exit}' /etc/realguard/realguard-backend.env 2>/dev/null || true)"
if [[ ! "$security_audit_key" =~ ^[0-9a-fA-F]{64}$ ]]; then
  security_audit_key="$(openssl rand -hex 32)"
  if sudo grep -q '^REALGUARD_SECURITY_AUDIT_HMAC_KEY=' /etc/realguard/realguard-backend.env; then
    sudo sed -i "s/^REALGUARD_SECURITY_AUDIT_HMAC_KEY=.*/REALGUARD_SECURITY_AUDIT_HMAC_KEY=$security_audit_key/" \
      /etc/realguard/realguard-backend.env
  else
    printf 'REALGUARD_SECURITY_AUDIT_HMAC_KEY=%s\n' "$security_audit_key" \
      | sudo tee -a /etc/realguard/realguard-backend.env >/dev/null
  fi
fi
if ! sudo grep -q '^REALGUARD_SECURITY_AUDIT_HMAC_KEY_ID=' /etc/realguard/realguard-backend.env; then
  printf 'REALGUARD_SECURITY_AUDIT_HMAC_KEY_ID=v1\n' \
    | sudo tee -a /etc/realguard/realguard-backend.env >/dev/null
fi
if ! sudo grep -q '^REALGUARD_SECURITY_AUDIT_HMAC_KEYS_JSON=' /etc/realguard/realguard-backend.env; then
  printf "REALGUARD_SECURITY_AUDIT_HMAC_KEYS_JSON='{}'\n" \
    | sudo tee -a /etc/realguard/realguard-backend.env >/dev/null
fi
if ! sudo grep -q '^REALGUARD_PRIVACY_ERASURE_LEDGER_PATH=' /etc/realguard/realguard-backend.env; then
  printf 'REALGUARD_PRIVACY_ERASURE_LEDGER_PATH=/opt/realguard-data/privacy-erasure/privacy-erasure-tombstones.sqlite3\n' \
    | sudo tee -a /etc/realguard/realguard-backend.env >/dev/null
fi
if ! sudo grep -q '^REALGUARD_EVIDENCE_HMAC_KEY=' /etc/realguard/realguard-backend.env; then
  evidence_key="$(openssl rand -hex 32)"
  printf 'REALGUARD_EVIDENCE_HMAC_KEY=%s\n' "$evidence_key" \
    | sudo tee -a /etc/realguard/realguard-backend.env >/dev/null
fi
detector_token="$(sudo awk -F= '/^REALGUARD_DETECTOR_INTERNAL_TOKEN=/{print substr($0, index($0, "=") + 1); exit}' /etc/realguard/realguard-backend.env)"
if [[ ${#detector_token} -lt 32 || "$detector_token" == "change-me" || "$detector_token" == "replace-me" ]]; then
  detector_token="$(openssl rand -hex 32)"
  if sudo grep -q '^REALGUARD_DETECTOR_INTERNAL_TOKEN=' /etc/realguard/realguard-backend.env; then
    sudo sed -i "s/^REALGUARD_DETECTOR_INTERNAL_TOKEN=.*/REALGUARD_DETECTOR_INTERNAL_TOKEN=$detector_token/" \
      /etc/realguard/realguard-backend.env
  else
    printf 'REALGUARD_DETECTOR_INTERNAL_TOKEN=%s\n' "$detector_token" \
      | sudo tee -a /etc/realguard/realguard-backend.env >/dev/null
  fi
fi
if ! sudo grep -q '^REALGUARD_EVIDENCE_HMAC_KEY_ID=' /etc/realguard/realguard-backend.env; then
  printf 'REALGUARD_EVIDENCE_HMAC_KEY_ID=v1\n' \
    | sudo tee -a /etc/realguard/realguard-backend.env >/dev/null
fi
if ! sudo grep -q '^REALGUARD_EVIDENCE_HMAC_KEYS_JSON=' /etc/realguard/realguard-backend.env; then
  # Verification-only history. Existing operator-managed keyrings are never replaced.
  printf "REALGUARD_EVIDENCE_HMAC_KEYS_JSON='{}'\n" \
    | sudo tee -a /etc/realguard/realguard-backend.env >/dev/null
fi
if ! sudo grep -q '^REALGUARD_ADMIN_STATE_PATH=' /etc/realguard/realguard-backend.env; then
  printf 'REALGUARD_ADMIN_STATE_PATH=/opt/realguard-data/admin_state.json\n' \
    | sudo tee -a /etc/realguard/realguard-backend.env >/dev/null
fi
sudo chmod 600 /etc/realguard/realguard-backend.env
sudo chown root:root /etc/realguard/realguard-backend.env

for unit in \
  realguard-backend.service \
  realguard-detector-backend.service \
  realguard-developer-worker.service \
  realguard-alert-worker.service \
  realguard-alert-watchdog.service \
  realguard-alert-watchdog.timer \
  realguard-backup.service \
  realguard-backup.timer \
  realguard-restore-drill.service \
  realguard-restore-drill.timer \
  realguard-security-audit-verify.service \
  realguard-security-audit-verify.timer; do
  if [[ -f "/etc/systemd/system/$unit" ]]; then
    sudo cp -a "/etc/systemd/system/$unit" "/tmp/$unit.previous"
  else
    sudo rm -f "/tmp/$unit.previous"
  fi
done
if [[ -f /etc/systemd/system/realguard-backend.service.d/40-detector-backend-url.conf ]]; then
  sudo cp -a /etc/systemd/system/realguard-backend.service.d/40-detector-backend-url.conf \
    /tmp/realguard-detector-url.conf.previous
else
  sudo rm -f /tmp/realguard-detector-url.conf.previous
fi
units_switched=1
sudo install -m 644 /tmp/realguard-backend.service /etc/systemd/system/realguard-backend.service
sudo sed "s/15001/$DETECTOR_PORT/g" /tmp/realguard-detector-backend.service \
  | sudo tee /etc/systemd/system/realguard-detector-backend.service >/dev/null
sudo sed "s/15001/$DETECTOR_PORT/g" /tmp/realguard-developer-worker.service \
  | sudo tee /etc/systemd/system/realguard-developer-worker.service >/dev/null
sudo install -m 644 /tmp/realguard-alert-worker.service /etc/systemd/system/realguard-alert-worker.service
sudo install -m 644 /tmp/realguard-alert-watchdog.service /etc/systemd/system/realguard-alert-watchdog.service
sudo install -m 644 /tmp/realguard-alert-watchdog.timer /etc/systemd/system/realguard-alert-watchdog.timer
sudo install -m 644 /tmp/realguard-backup.service /etc/systemd/system/realguard-backup.service
sudo install -m 644 /tmp/realguard-backup.timer /etc/systemd/system/realguard-backup.timer
sudo install -m 644 /tmp/realguard-restore-drill.service /etc/systemd/system/realguard-restore-drill.service
sudo install -m 644 /tmp/realguard-restore-drill.timer /etc/systemd/system/realguard-restore-drill.timer
sudo install -m 644 /tmp/realguard-security-audit-verify.service /etc/systemd/system/realguard-security-audit-verify.service
sudo install -m 644 /tmp/realguard-security-audit-verify.timer /etc/systemd/system/realguard-security-audit-verify.timer
sudo install -d -m 755 /etc/systemd/system/realguard-backend.service.d
sudo tee /etc/systemd/system/realguard-backend.service.d/40-detector-backend-url.conf >/dev/null <<UNIT
[Service]
Environment=REALGUARD_DETECTION_BACKEND_URL=http://127.0.0.1:$DETECTOR_PORT
UNIT

sudo bash -lc '
  set -a
  [ ! -f /etc/realguard/session.env ] || . /etc/realguard/session.env
  [ ! -f /etc/realguard/realguard-backend.env ] || . /etc/realguard/realguard-backend.env
  [ ! -f /etc/realguard/detector-db.env ] || . /etc/realguard/detector-db.env
  set +a
  cd '"$release_root"'/RealGuard
  '"$release_root"'/.venv/bin/python -c "from imagedetection.views import privacy_erasure_ledger as ledger; assert True in ledger.healthcheck().values()"
  '"$release_root"'/.venv/bin/python -m flask --app run:app identity-db-upgrade
  '"$release_root"'/.venv/bin/python -m flask --app run:app migrate-password-hashes
  '"$release_root"'/.venv/bin/python -m flask --app run:app admin-db-upgrade
  '"$release_root"'/.venv/bin/python -m flask --app run:app developer-db-upgrade
  '"$release_root"'/.venv/bin/python -m flask --app run:app reconcile-detection-jobs
  audit_checkpoint=/opt/realguard-audit-checkpoint/checkpoint.json
  audit_bootstrap_marker=/etc/realguard/security-audit-bootstrap.once
  if [[ -f "$audit_checkpoint" ]]; then
    '"$release_root"'/.venv/bin/python -m flask --app run:app security-audit-verify
  elif [[ -f "$audit_bootstrap_marker" ]]; then
    '"$release_root"'/.venv/bin/python -m flask --app run:app security-audit-verify --bootstrap
    rm -f -- "$audit_bootstrap_marker"
  else
    echo "Security audit checkpoint is missing. Create the one-time root marker $audit_bootstrap_marker only after verifying this is a new trusted installation." >&2
    exit 1
  fi
'
sudo chown ubuntu:ubuntu /opt/realguard-data/admin_state.json
sudo chmod 600 /opt/realguard-data/admin_state.json
if [[ -f /opt/realguard-data/.admin_state.json.lock ]]; then
  sudo chown ubuntu:ubuntu /opt/realguard-data/.admin_state.json.lock
  sudo chmod 600 /opt/realguard-data/.admin_state.json.lock
fi

sudo systemctl daemon-reload
sudo systemctl enable \
  realguard-backend.service \
  realguard-detector-backend.service \
  realguard-developer-worker.service \
  realguard-alert-worker.service >/dev/null
sudo systemctl enable --now \
  realguard-backup.timer \
  realguard-restore-drill.timer \
  realguard-alert-watchdog.timer \
  realguard-security-audit-verify.timer >/dev/null
sudo ln -sfn "$release_root/RealGuard" /opt/realguard-server/RealGuard.next
sudo mv -Tf /opt/realguard-server/RealGuard.next /opt/realguard-server/RealGuard
backend_switched=1
sudo ln -sfn "$release_root/.venv" /opt/realguard-server/.venv.next
sudo mv -Tf /opt/realguard-server/.venv.next /opt/realguard-server/.venv
runtime_switched=1
sudo systemctl restart realguard-detector-backend.service
sudo systemctl restart realguard-developer-worker.service
sudo systemctl restart realguard-alert-worker.service
sudo systemctl restart realguard-backend.service

sudo rm -rf /var/www/realguard-frontend.next
sudo install -d -m 755 /var/www/realguard-frontend.next
sudo tar -xzf /tmp/realguard-v1-frontend.tgz -C /var/www/realguard-frontend.next
sudo chown -R root:root /var/www/realguard-frontend.next
sudo rm -rf /var/www/realguard-frontend.previous
if [[ -d /var/www/realguard-frontend ]]; then
  sudo mv /var/www/realguard-frontend /var/www/realguard-frontend.previous
fi
sudo mv /var/www/realguard-frontend.next /var/www/realguard-frontend
frontend_switched=1

sudo rm -rf /tmp/realguard-snippets.previous
if [[ -d /etc/nginx/snippets ]]; then
  sudo cp -a /etc/nginx/snippets /tmp/realguard-snippets.previous
fi
sudo rm -f \
  /tmp/realguard-frontend.nginx.previous \
  /tmp/realguard-https.nginx.previous \
  /tmp/realguard-zones.nginx.previous
if [[ -f /etc/nginx/sites-enabled/realguard-frontend ]]; then
  sudo cp -a /etc/nginx/sites-enabled/realguard-frontend /tmp/realguard-frontend.nginx.previous
fi
if [[ -f /etc/nginx/conf.d/myapp.conf ]]; then
  sudo cp -a /etc/nginx/conf.d/myapp.conf /tmp/realguard-https.nginx.previous
fi
if [[ -f /etc/nginx/conf.d/00-realguard-zones.conf ]]; then
  sudo cp -a /etc/nginx/conf.d/00-realguard-zones.conf /tmp/realguard-zones.nginx.previous
fi
nginx_switched=1
sudo install -d -m 755 /etc/nginx/snippets
sudo tar -xzf /tmp/realguard-nginx-snippets.tgz -C /etc/nginx/snippets --no-same-owner
sudo install -m 644 /etc/nginx/snippets/realguard-zones.conf /etc/nginx/conf.d/00-realguard-zones.conf
sudo rm -f /etc/nginx/conf.d/00-realguard-security-zones.conf
sudo install -m 644 /tmp/realguard-frontend.nginx.conf /etc/nginx/sites-enabled/realguard-frontend
sudo install -m 644 /tmp/realguard-https.nginx.conf /etc/nginx/conf.d/myapp.conf
sudo nginx -t
sudo systemctl reload nginx

health_ready=0
for _ in {1..30}; do
  if curl -fsS --connect-timeout 2 --max-time 12 "http://127.0.0.1:$DETECTOR_PORT/ready" >/dev/null \
    && curl -fsS --connect-timeout 2 --max-time 12 http://127.0.0.1:5000/api/ready >/dev/null; then
    health_ready=1
    break
  fi
  sleep 1
done
test "$health_ready" = "1"
detector_token="$(sudo awk -F= '/^REALGUARD_DETECTOR_INTERNAL_TOKEN=/{print substr($0, index($0, "=") + 1); exit}' /etc/realguard/realguard-backend.env)"
test "${#detector_token}" -ge 32
sudo -u ubuntu "$release_root/.venv/bin/python" -c '
from PIL import Image
Image.new("RGB", (64, 64), (240, 244, 245)).save("/tmp/realguard-deployment-probe.png")
'
curl -fsS --max-time 180 \
  -H "X-RealGuard-Detector-Token: $detector_token" \
  -F internal_probe=1 \
  -F openid=deployment-probe \
  -F image_file=@/tmp/realguard-deployment-probe.png \
  "http://127.0.0.1:$DETECTOR_PORT/image" \
  | grep -q '"probe":true'
systemctl is-active --quiet realguard-detector-backend.service
systemctl is-active --quiet realguard-developer-worker.service
systemctl is-active --quiet realguard-alert-worker.service
systemctl is-active --quiet realguard-backend.service
systemctl is-enabled --quiet realguard-developer-worker.service
systemctl is-enabled --quiet realguard-alert-worker.service
systemctl is-enabled --quiet realguard-alert-watchdog.timer
systemctl is-active --quiet realguard-alert-watchdog.timer
systemctl is-enabled --quiet realguard-backup.timer
systemctl is-active --quiet realguard-backup.timer
systemctl is-enabled --quiet realguard-restore-drill.timer
systemctl is-active --quiet realguard-restore-drill.timer
systemctl is-enabled --quiet realguard-security-audit-verify.timer
systemctl is-active --quiet realguard-security-audit-verify.timer
sudo systemctl start realguard-backup.service
sudo test -L /var/backups/realguard/latest
test -r /opt/realguard-data/ip2region_v4.xdb
test "$(stat -c '%a' /opt/realguard-data/developer-spool)" = "700"
test "$(stat -c '%a' /opt/realguard-data/web-spool)" = "700"
test "$(stat -c '%a' /opt/realguard-data/evidence-manifests)" = "700"
test "$(stat -c '%a' /opt/realguard-data/admin_state.json)" = "600"
curl -fsS http://127.0.0.1:5000/admin/login | grep -q '慧鉴 AI 管理员认证'
admin_register_code="$(curl -sS -o /tmp/realguard-admin-register.html -w '%{http_code}' http://127.0.0.1:5000/admin/register)"
test "$admin_register_code" = "403"
! grep -q '注册管理员' /tmp/realguard-admin-register.html
big_screen_code="$(curl -sS -o /tmp/realguard-big-screen.json -w '%{http_code}' http://127.0.0.1:5000/api/admin/big-screen)"
test "$big_screen_code" = "401"

sudo install -m 644 /tmp/realguard-v1.DEPLOYED_COMMIT /opt/realguard-server/DEPLOYED_COMMIT
sudo rm -rf /var/www/realguard-frontend.previous
sudo rm -rf /tmp/realguard-snippets.previous
sudo rm -f \
  /tmp/realguard-frontend.nginx.previous \
  /tmp/realguard-https.nginx.previous \
  /tmp/realguard-zones.nginx.previous
backend_switched=0
frontend_switched=0
nginx_switched=0
trap - ERR

sudo find /opt/realguard-server/releases -mindepth 1 -maxdepth 1 -type d \
  -name '[0-9a-f]*' -printf '%T@ %p\n' \
  | sort -nr \
  | tail -n +4 \
  | cut -d' ' -f2- \
  | xargs -r sudo rm -rf

rm -f \
  /tmp/realguard-v1-backend.tgz \
  /tmp/realguard-v1-frontend.tgz \
  /tmp/realguard-nginx-snippets.tgz \
  /tmp/realguard-backend.service \
  /tmp/realguard-detector-backend.service \
  /tmp/realguard-developer-worker.service \
  /tmp/realguard-alert-worker.service \
  /tmp/realguard-alert-watchdog.service \
  /tmp/realguard-alert-watchdog.timer \
  /tmp/realguard-backup \
  /tmp/realguard-restore-verify \
  /tmp/realguard-backup.service \
  /tmp/realguard-backup.timer \
  /tmp/realguard-restore-drill.service \
  /tmp/realguard-restore-drill.timer \
  /tmp/realguard-security-audit-verify.service \
  /tmp/realguard-security-audit-verify.timer \
  /tmp/realguard-v1.DEPLOYED_COMMIT \
  /tmp/realguard-ip2region-v4.xdb \
  /tmp/realguard-frontend.nginx.conf \
  /tmp/realguard-https.nginx.conf \
  /tmp/realguard-activate-v1.sh \
  /tmp/realguard-admin-register.html \
  /tmp/realguard-big-screen.json

cat /opt/realguard-server/DEPLOYED_COMMIT
