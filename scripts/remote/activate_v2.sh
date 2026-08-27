#!/usr/bin/env bash
set -euo pipefail

sudo install -m 600 -o ubuntu -g ubuntu /dev/null /var/lock/realguard-public-release.lock
sudo install -m 600 -o ubuntu -g ubuntu /dev/null /var/lock/huijian-v2-deploy.lock
exec 8>/var/lock/realguard-public-release.lock
flock -w 900 8 || { printf 'Another public release transaction is still running.\n' >&2; exit 75; }
exec 9>/var/lock/huijian-v2-deploy.lock
flock -n 9 || { printf 'Another V2 activation is already running.\n' >&2; exit 75; }

commit_sha="$(tr -d '[:space:]' </tmp/jianzhen-v2.DEPLOYED_COMMIT)"
[[ "$commit_sha" =~ ^[0-9a-f]{7,40}$ ]]
frontend_version="v2-${commit_sha}"
frontend_version_root="/var/www/huijian-frontends"
frontend_release_root="${frontend_version_root}/${frontend_version}"
release_id="${commit_sha}-$(date -u +%Y%m%dT%H%M%SZ)-$$"
release_root="/opt/jianzhen-v2/releases/$release_id"
current_app=""
current_runtime=""
frontend_previous_moved=0
frontend_installed=0
app_switched=0
runtime_switched=0
unit_switched=0

rollback() {
  status=$?
  trap - ERR
  set +e
  printf 'V2 activation failed; restoring the previous application.\n' >&2
  if [[ "$frontend_installed" == "1" ]]; then
    sudo rm -rf /var/www/v2
  fi
  if [[ "$frontend_previous_moved" == "1" && -d /var/www/v2.previous ]]; then
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

validate_frontend_assets() {
  local release_dir="$1"
  local asset_ref asset_count=0
  test -s "$release_dir/index.html"
  test -d "$release_dir/assets"
  while IFS= read -r asset_ref; do
    asset_count=$((asset_count + 1))
    test -f "$release_dir$asset_ref"
  done < <(grep -oE '/assets/[A-Za-z0-9._/-]+' "$release_dir/index.html" | sort -u)
  test "$asset_count" -gt 0
}

sudo install -d -m 700 /etc/realguard
# The service user owns the signing key but must be able to traverse the
# secrets directory. Keep directory listing disabled; individual secret files
# retain their own restrictive modes.
sudo chmod 711 /etc/realguard
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
sudo sed -i \
  -e '/^JIANZHEN_REPORT_QA_RECALL_MAX_CANDIDATES=/d' \
  -e '/^JIANZHEN_REPORT_QA_WEB_EXTRACT_MAX_URLS=/d' \
  /etc/realguard/jianzhen-v2.env
printf 'JIANZHEN_REPORT_QA_RECALL_MAX_CANDIDATES=20\n' \
  | sudo tee -a /etc/realguard/jianzhen-v2.env >/dev/null
printf 'JIANZHEN_REPORT_QA_WEB_EXTRACT_MAX_URLS=5\n' \
  | sudo tee -a /etc/realguard/jianzhen-v2.env >/dev/null
detector_token="$(sudo awk -F= '/^REALGUARD_DETECTOR_INTERNAL_TOKEN=/{print substr($0, index($0, "=") + 1); exit}' /etc/realguard/realguard-backend.env)"
if [[ ! "$detector_token" =~ ^[A-Za-z0-9_-]{32,256}$ ]]; then
  printf 'The primary detector token is missing or invalid. Deploy V1 first.\n' >&2
  exit 1
fi
detector_port="$(systemctl show realguard-detector-backend.service -p Environment --value \
  | tr ' ' '\n' \
  | sed -n 's/^REALGUARD_DETECTOR_PORT=//p' \
  | tail -n 1)"
if [[ ! "$detector_port" =~ ^[0-9]{2,5}$ ]]; then
  detector_port=15001
fi
sudo sed -i \
  -e '/^JIANZHEN_PRIMARY_IMAGE_DETECT_URL=/d' \
  -e '/^JIANZHEN_PRIMARY_IMAGE_DETECT_TOKEN=/d' \
  /etc/realguard/jianzhen-v2.env
printf 'JIANZHEN_PRIMARY_IMAGE_DETECT_URL=http://127.0.0.1:%s/image\n' "$detector_port" \
  | sudo tee -a /etc/realguard/jianzhen-v2.env >/dev/null
printf 'JIANZHEN_PRIMARY_IMAGE_DETECT_TOKEN=%s\n' "$detector_token" \
  | sudo tee -a /etc/realguard/jianzhen-v2.env >/dev/null
if ! sudo grep -q '^REALGUARD_PRIVACY_ERASURE_LEDGER_PATH=' /etc/realguard/jianzhen-v2.env; then
  printf 'REALGUARD_PRIVACY_ERASURE_LEDGER_PATH=/opt/realguard-data/privacy-erasure/privacy-erasure-tombstones.sqlite3\n' \
    | sudo tee -a /etc/realguard/jianzhen-v2.env >/dev/null
fi
sudo install -d -m 700 -o ubuntu -g ubuntu /opt/realguard-data/privacy-erasure
evidence_key_file=/etc/realguard/jianzhen-v2-evidence-signing-ed25519.pem
evidence_inline="$(sudo awk -F= '/^JIANZHEN_EVIDENCE_SIGNING_PRIVATE_KEY=/{print substr($0, index($0, "=") + 1); exit}' /etc/realguard/jianzhen-v2.env)"
evidence_configured_file="$(sudo awk -F= '/^JIANZHEN_EVIDENCE_SIGNING_PRIVATE_KEY_FILE=/{print substr($0, index($0, "=") + 1); exit}' /etc/realguard/jianzhen-v2.env)"
if [[ -n "$evidence_inline" && -n "$evidence_configured_file" ]]; then
  echo "V2 evidence signing has both inline and file private keys configured." >&2
  exit 1
fi
if [[ -z "$evidence_inline" && -z "$evidence_configured_file" ]]; then
  if [[ ! -f "$evidence_key_file" ]]; then
    sudo openssl genpkey -algorithm ED25519 -out "$evidence_key_file"
  fi
  sudo chown ubuntu:ubuntu "$evidence_key_file"
  sudo chmod 600 "$evidence_key_file"
  sudo openssl pkey -in "$evidence_key_file" -pubout -noout >/dev/null
  sudo sed -i \
    -e '/^JIANZHEN_EVIDENCE_SIGNING_PRIVATE_KEY=/d' \
    -e '/^JIANZHEN_EVIDENCE_SIGNING_PRIVATE_KEY_FILE=/d' \
    /etc/realguard/jianzhen-v2.env
  printf 'JIANZHEN_EVIDENCE_SIGNING_PRIVATE_KEY_FILE=%s\n' "$evidence_key_file" \
    | sudo tee -a /etc/realguard/jianzhen-v2.env >/dev/null
  evidence_configured_file="$evidence_key_file"
fi
if ! sudo grep -Eq '^JIANZHEN_EVIDENCE_SIGNING_KEY_ID=[A-Za-z0-9][A-Za-z0-9._:-]{2,63}$' /etc/realguard/jianzhen-v2.env; then
  sudo sed -i '/^JIANZHEN_EVIDENCE_SIGNING_KEY_ID=/d' /etc/realguard/jianzhen-v2.env
  printf 'JIANZHEN_EVIDENCE_SIGNING_KEY_ID=huijian-evidence-v1\n' \
    | sudo tee -a /etc/realguard/jianzhen-v2.env >/dev/null
fi
sudo chmod 600 /etc/realguard/jianzhen-v2.env
sudo chown root:root /etc/realguard/jianzhen-v2.env

sudo install -d -m 755 -o ubuntu -g ubuntu /opt/jianzhen-v2/releases
sudo install -d -m 755 -o root -g root "$frontend_version_root"
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
curl -fsS --connect-timeout 2 --max-time 12 "http://127.0.0.1:$detector_port/ready" >/dev/null

sudo rm -rf /var/www/v2.next "${frontend_release_root}.next"
sudo install -d -m 755 "${frontend_release_root}.next"
sudo tar -xzf /tmp/jianzhen-v2-frontend.tgz -C "${frontend_release_root}.next"
sudo install -m 644 /tmp/jianzhen-v2.DEPLOYED_COMMIT "${frontend_release_root}.next/DEPLOYED_COMMIT"
printf '%s\n' "$frontend_version" | sudo tee "${frontend_release_root}.next/.HUIJIAN_FRONTEND_VERSION" >/dev/null
validate_frontend_assets "${frontend_release_root}.next"
sudo chown -R root:root "${frontend_release_root}.next"
sudo rm -rf "$frontend_release_root"
sudo mv "${frontend_release_root}.next" "$frontend_release_root"
sudo cp -a "$frontend_release_root" /var/www/v2.next
sudo rm -rf /var/www/v2.previous
if [[ -d /var/www/v2 ]]; then
  sudo mv /var/www/v2 /var/www/v2.previous
  frontend_previous_moved=1
fi
sudo mv /var/www/v2.next /var/www/v2
frontend_installed=1
validate_frontend_assets /var/www/v2

sudo install -m 755 -o root -g root /tmp/huijian-frontend-version /usr/local/sbin/huijian-frontend-version

# The direct backend readiness check above is the atomic release gate. Public
# Nginx checks happen after this pointer switch; a transient proxy connection
# during file promotion must not roll back a healthy backend release.
sudo install -m 644 /tmp/jianzhen-v2.DEPLOYED_COMMIT /opt/jianzhen-v2/DEPLOYED_COMMIT
sudo rm -rf /var/www/v2.previous
frontend_previous_moved=0
frontend_installed=0
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
  /tmp/jianzhen-activate-v2.sh \
  /tmp/huijian-frontend-version

cat /opt/jianzhen-v2/DEPLOYED_COMMIT
