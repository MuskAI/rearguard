#!/usr/bin/env bash
set -euo pipefail

: "${IP2REGION_XDB_SHA256:?missing IP2REGION_XDB_SHA256}"
DETECTOR_PORT="${REALGUARD_DETECTOR_PORT:-15001}"

printf '%s  %s\n' "$IP2REGION_XDB_SHA256" /tmp/realguard-ip2region-v4.xdb | sha256sum -c -
sudo install -d -m 755 -o ubuntu -g ubuntu /opt/realguard-data
sudo install -m 644 /tmp/realguard-ip2region-v4.xdb /opt/realguard-data/ip2region_v4.xdb
sudo tar -xzf /tmp/realguard-v1-backend.tgz -C /opt/realguard-server/RealGuard
sudo install -m 644 /tmp/realguard-v1.DEPLOYED_COMMIT /opt/realguard-server/DEPLOYED_COMMIT
sudo install -d -m 755 -o ubuntu -g ubuntu /opt/realguard-server/RealGuard/imagedetection/static/uploads/aliyun-probes
sudo chown -R ubuntu:ubuntu /opt/realguard-server/RealGuard/imagedetection/static/uploads

sudo -u ubuntu /opt/realguard-server/.venv/bin/python -m pip install \
  --no-cache-dir --quiet --upgrade -r /opt/realguard-server/RealGuard/requirements.txt
sudo -u ubuntu /opt/realguard-server/.venv/bin/python -m pip install \
  --no-cache-dir --quiet --no-deps --upgrade 'invisible-watermark>=0.2.0'

sudo install -d -m 700 /etc/realguard
if ! sudo grep -q '^SECRET_KEY=' /etc/realguard/session.env 2>/dev/null; then
  secret_key="$(openssl rand -hex 48)"
  printf 'SECRET_KEY=%s\n' "$secret_key" | sudo tee /etc/realguard/session.env >/dev/null
fi
sudo chmod 600 /etc/realguard/session.env
sudo chown root:root /etc/realguard/session.env
sudo touch /etc/realguard/realguard-backend.env
if ! sudo grep -q '^REALGUARD_CONSENT_AUDIT_SALT=' /etc/realguard/realguard-backend.env; then
  consent_salt="$(openssl rand -hex 32)"
  printf 'REALGUARD_CONSENT_AUDIT_SALT=%s\n' "$consent_salt" \
    | sudo tee -a /etc/realguard/realguard-backend.env >/dev/null
fi
sudo chmod 600 /etc/realguard/realguard-backend.env
sudo chown root:root /etc/realguard/realguard-backend.env

sudo install -m 644 /tmp/realguard-backend.service /etc/systemd/system/realguard-backend.service
sudo sed "s/15001/$DETECTOR_PORT/g" /tmp/realguard-detector-backend.service \
  | sudo tee /etc/systemd/system/realguard-detector-backend.service >/dev/null
sudo install -m 700 /tmp/realguard-backup /usr/local/sbin/realguard-backup
sudo install -m 644 /tmp/realguard-backup.service /etc/systemd/system/realguard-backup.service
sudo install -m 644 /tmp/realguard-backup.timer /etc/systemd/system/realguard-backup.timer
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
  cd /opt/realguard-server/RealGuard
  /opt/realguard-server/.venv/bin/python -m flask --app run:app admin-db-upgrade
  /opt/realguard-server/.venv/bin/python -m flask --app run:app developer-db-upgrade
'

sudo systemctl daemon-reload
sudo systemctl enable realguard-backend.service realguard-detector-backend.service >/dev/null
sudo systemctl enable realguard-backup.timer >/dev/null
sudo systemctl restart realguard-detector-backend.service
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
sudo rm -rf /var/www/realguard-frontend.previous

sudo install -d -m 755 /etc/nginx/snippets
sudo tar -xzf /tmp/realguard-nginx-snippets.tgz -C /etc/nginx/snippets --no-same-owner
sudo install -m 644 /etc/nginx/snippets/realguard-zones.conf /etc/nginx/conf.d/00-realguard-zones.conf
sudo install -m 644 /tmp/realguard-frontend.nginx.conf /etc/nginx/sites-enabled/realguard-frontend
sudo install -m 644 /tmp/realguard-https.nginx.conf /etc/nginx/conf.d/myapp.conf
sudo nginx -t
sudo systemctl reload nginx

health_ready=0
for _ in {1..30}; do
  if curl -fsS "http://127.0.0.1:$DETECTOR_PORT/health" >/dev/null \
    && curl -fsS http://127.0.0.1:5000/api/me >/dev/null; then
    health_ready=1
    break
  fi
  sleep 1
done
test "$health_ready" = "1"
systemctl is-active --quiet realguard-detector-backend.service
systemctl is-active --quiet realguard-backend.service
systemctl is-enabled --quiet realguard-backup.timer
sudo systemctl start realguard-backup.service
test -L /var/backups/realguard/latest
test -r /opt/realguard-data/ip2region_v4.xdb
curl -fsS http://127.0.0.1/admin/login | grep -q '慧鉴 AI 管理员认证'
admin_register_code="$(curl -sS -o /tmp/realguard-admin-register.html -w '%{http_code}' http://127.0.0.1/admin/register)"
test "$admin_register_code" = "403"
! grep -q '注册管理员' /tmp/realguard-admin-register.html
big_screen_code="$(curl -sS -o /tmp/realguard-big-screen.json -w '%{http_code}' http://127.0.0.1/api/admin/big-screen)"
test "$big_screen_code" = "401"

rm -f \
  /tmp/realguard-v1-backend.tgz \
  /tmp/realguard-v1-frontend.tgz \
  /tmp/realguard-nginx-snippets.tgz \
  /tmp/realguard-backend.service \
  /tmp/realguard-detector-backend.service \
  /tmp/realguard-backup \
  /tmp/realguard-backup.service \
  /tmp/realguard-backup.timer \
  /tmp/realguard-v1.DEPLOYED_COMMIT \
  /tmp/realguard-ip2region-v4.xdb \
  /tmp/realguard-frontend.nginx.conf \
  /tmp/realguard-https.nginx.conf \
  /tmp/realguard-activate-v1.sh \
  /tmp/realguard-admin-register.html \
  /tmp/realguard-big-screen.json

cat /opt/realguard-server/DEPLOYED_COMMIT
