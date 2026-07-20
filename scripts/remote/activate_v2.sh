#!/usr/bin/env bash
set -euo pipefail

sudo install -m 600 -o ubuntu -g ubuntu /dev/null /var/lock/huijian-v2-deploy.lock
exec 9>/var/lock/huijian-v2-deploy.lock
flock -n 9 || { printf 'Another V2 activation is already running.\n' >&2; exit 75; }

commit_sha="$(tr -d '[:space:]' </tmp/jianzhen-v2.DEPLOYED_COMMIT)"
[[ "$commit_sha" =~ ^[0-9a-f]{7,40}$ ]]
release_id="${commit_sha}-$(date -u +%Y%m%dT%H%M%SZ)-$$"
release_root="/opt/jianzhen-v2/releases/$release_id"
current_app=""
current_runtime=""
frontend_switched=0
app_switched=0
runtime_switched=0
unit_switched=0

rollback() {
  status=$?
  trap - ERR
  printf 'V2 activation failed; restoring the previous application.\n' >&2
  if [[ "$frontend_switched" == "1" && -d /var/www/v2.previous ]]; then
    sudo rm -rf /var/www/v2
    sudo mv /var/www/v2.previous /var/www/v2
  fi
  if [[ "$app_switched" == "1" && -n "$current_app" && -e "$current_app" ]]; then
    sudo ln -sfn "$current_app" /opt/jianzhen-v2/app.next
    sudo mv -Tf /opt/jianzhen-v2/app.next /opt/jianzhen-v2/app
  fi
  if [[ "$runtime_switched" == "1" && -n "$current_runtime" && -e "$current_runtime" ]]; then
    sudo ln -sfn "$current_runtime" /opt/jianzhen-v2/.venv.next
    sudo mv -Tf /opt/jianzhen-v2/.venv.next /opt/jianzhen-v2/.venv
  fi
  if [[ "$unit_switched" == "1" ]]; then
    if [[ -f /tmp/jianzhen-v2-backend.service.previous ]]; then
      sudo cp -a /tmp/jianzhen-v2-backend.service.previous \
        /etc/systemd/system/jianzhen-v2-backend.service
    else
      sudo rm -f /etc/systemd/system/jianzhen-v2-backend.service
    fi
    sudo systemctl daemon-reload
  fi
  sudo systemctl restart jianzhen-v2-backend.service || true
  exit "$status"
}
trap rollback ERR

sudo install -d -m 700 /etc/realguard
sudo touch /etc/realguard/jianzhen-v2.env
if ! sudo grep -q '^JIANZHEN_REPORT_SHARE_SECRET=' /etc/realguard/jianzhen-v2.env; then
  report_share_secret="$(openssl rand -hex 32)"
  printf 'JIANZHEN_REPORT_SHARE_SECRET=%s\n' "$report_share_secret" \
    | sudo tee -a /etc/realguard/jianzhen-v2.env >/dev/null
fi
if ! sudo grep -q '^JIANZHEN_CONSENT_AUDIT_SALT=' /etc/realguard/jianzhen-v2.env; then
  consent_audit_salt="$(openssl rand -hex 32)"
  printf 'JIANZHEN_CONSENT_AUDIT_SALT=%s\n' "$consent_audit_salt" \
    | sudo tee -a /etc/realguard/jianzhen-v2.env >/dev/null
fi
if ! sudo grep -q '^JIANZHEN_PUBLIC_BASE_URL=' /etc/realguard/jianzhen-v2.env; then
  printf 'JIANZHEN_PUBLIC_BASE_URL=https://www.rrreal.cn\n' \
    | sudo tee -a /etc/realguard/jianzhen-v2.env >/dev/null
fi
if ! sudo grep -q '^JIANZHEN_DATA_DIR=' /etc/realguard/jianzhen-v2.env; then
  printf 'JIANZHEN_DATA_DIR=/opt/jianzhen-v2/data\n' \
    | sudo tee -a /etc/realguard/jianzhen-v2.env >/dev/null
fi
sudo chmod 600 /etc/realguard/jianzhen-v2.env
sudo chown root:root /etc/realguard/jianzhen-v2.env

sudo install -d -m 755 -o ubuntu -g ubuntu /opt/jianzhen-v2/releases
sudo install -d -m 700 -o ubuntu -g ubuntu /opt/jianzhen-v2/data
sudo rm -rf "$release_root"
sudo install -d -m 755 -o ubuntu -g ubuntu "$release_root"
sudo tar -xzf /tmp/jianzhen-v2-backend.tgz -C "$release_root"
sudo install -m 644 /tmp/jianzhen-v2.DEPLOYED_COMMIT "$release_root/DEPLOYED_COMMIT"

if [[ -L /opt/jianzhen-v2/app ]]; then
  current_app="$(readlink -f /opt/jianzhen-v2/app)"
elif [[ -d /opt/jianzhen-v2/app ]]; then
  legacy_root="/opt/jianzhen-v2/releases/legacy-$(date -u +%Y%m%dT%H%M%SZ)"
  sudo install -d -m 755 -o ubuntu -g ubuntu "$legacy_root"
  sudo mv /opt/jianzhen-v2/app "$legacy_root/app"
  current_app="$legacy_root/app"
  sudo ln -s "$current_app" /opt/jianzhen-v2/app
fi

if [[ -L /opt/jianzhen-v2/.venv ]]; then
  current_runtime="$(readlink -f /opt/jianzhen-v2/.venv)"
elif [[ -d /opt/jianzhen-v2/.venv ]]; then
  legacy_runtime_root="/opt/jianzhen-v2/releases/legacy-runtime-$(date -u +%Y%m%dT%H%M%SZ)"
  sudo install -d -m 755 -o ubuntu -g ubuntu "$legacy_runtime_root"
  sudo mv /opt/jianzhen-v2/.venv "$legacy_runtime_root/.venv"
  current_runtime="$legacy_runtime_root/.venv"
  sudo ln -s "$current_runtime" /opt/jianzhen-v2/.venv
fi

sudo -u ubuntu /usr/bin/python3 -m venv "$release_root/.venv"
sudo -u ubuntu "$release_root/.venv/bin/python" -m pip install \
  --disable-pip-version-check --no-cache-dir --quiet --require-hashes \
  -r "$release_root/requirements.lock"

sudo rm -f /tmp/jianzhen-v2-backend.service.previous
if [[ -f /etc/systemd/system/jianzhen-v2-backend.service ]]; then
  sudo cp -a /etc/systemd/system/jianzhen-v2-backend.service \
    /tmp/jianzhen-v2-backend.service.previous
fi
sudo install -m 644 /tmp/jianzhen-v2-backend.service \
  /etc/systemd/system/jianzhen-v2-backend.service
unit_switched=1
sudo systemctl daemon-reload
sudo systemctl enable jianzhen-v2-backend.service >/dev/null
sudo ln -sfn "$release_root/app" /opt/jianzhen-v2/app.next
sudo mv -Tf /opt/jianzhen-v2/app.next /opt/jianzhen-v2/app
app_switched=1
sudo ln -sfn "$release_root/.venv" /opt/jianzhen-v2/.venv.next
sudo mv -Tf /opt/jianzhen-v2/.venv.next /opt/jianzhen-v2/.venv
runtime_switched=1
sudo systemctl restart jianzhen-v2-backend.service

health_ready=0
for _ in {1..30}; do
  if curl -fsS --connect-timeout 2 --max-time 12 http://127.0.0.1:8848/api/ready >/dev/null; then
    health_ready=1
    break
  fi
  sleep 1
done
test "$health_ready" = "1"
systemctl is-active --quiet jianzhen-v2-backend.service

sudo rm -rf /var/www/v2.next
sudo install -d -m 755 /var/www/v2.next
sudo tar -xzf /tmp/jianzhen-v2-frontend.tgz -C /var/www/v2.next
sudo chown -R root:root /var/www/v2.next
sudo rm -rf /var/www/v2.previous
if [[ -d /var/www/v2 ]]; then
  sudo mv /var/www/v2 /var/www/v2.previous
fi
sudo mv /var/www/v2.next /var/www/v2
frontend_switched=1

curl -fsS -o /dev/null http://127.0.0.1/
curl -fsS http://127.0.0.1/v2-api/ready >/dev/null
sudo install -m 644 /tmp/jianzhen-v2.DEPLOYED_COMMIT /opt/jianzhen-v2/DEPLOYED_COMMIT
sudo rm -rf /var/www/v2.previous
frontend_switched=0
app_switched=0
runtime_switched=0
unit_switched=0
trap - ERR

sudo find /opt/jianzhen-v2/releases -mindepth 1 -maxdepth 1 -type d \
  -name '[0-9a-f]*' -printf '%T@ %p\n' \
  | sort -nr \
  | tail -n +4 \
  | cut -d' ' -f2- \
  | xargs -r sudo rm -rf

sudo rm -f /tmp/jianzhen-v2-backend.service.previous

rm -f \
  /tmp/jianzhen-v2-backend.tgz \
  /tmp/jianzhen-v2-frontend.tgz \
  /tmp/jianzhen-v2.DEPLOYED_COMMIT \
  /tmp/jianzhen-v2-backend.service \
  /tmp/jianzhen-activate-v2.sh

cat /opt/jianzhen-v2/DEPLOYED_COMMIT
