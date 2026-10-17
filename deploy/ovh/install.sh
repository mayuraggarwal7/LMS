#!/usr/bin/env bash
# CampusFlow LMS - one-command install on an OVHcloud VPS (Ubuntu 22.04 / 24.04 or Debian 12).
#
#   ssh ubuntu@<vps-ip>
#   curl -fsSL https://raw.githubusercontent.com/mayuraggarwal7/LMS/main/deploy/ovh/install.sh -o install.sh
#   sudo bash install.sh                       # HTTPS on <ip>.sslip.io, no domain needed
#   sudo DOMAIN=lms.example.edu bash install.sh  # your own domain (point its A record at the VPS first)
#
# Options (environment variables):
#   DOMAIN     public hostname. Default: <vps-ip-with-dashes>.sslip.io (free wildcard DNS, real HTTPS)
#   REPO_URL   git URL to clone. Default: https://github.com/mayuraggarwal7/LMS.git
#              (private repo: https://<github-token>@github.com/mayuraggarwal7/LMS.git)
#   BRANCH     branch to deploy. Default: main
#   DEMO       1 (default) loads demo + FRCRCE data on first install; 0 loads FRCRCE data only
#
# Re-running the script updates the code, migrates and restarts; the database is kept.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "Run with sudo."; exit 1; }

REPO_URL="${REPO_URL:-https://github.com/mayuraggarwal7/LMS.git}"
BRANCH="${BRANCH:-main}"
DEMO="${DEMO:-1}"
APP_DIR=/opt/campusflow
DATA_DIR=/var/lib/campusflow
ENV_FILE=/etc/campusflow.env
APP_USER=campusflow

IP="$(curl -4 -fsS https://api.ipify.org || hostname -I | awk '{print $1}')"
DOMAIN="${DOMAIN:-${IP//./-}.sslip.io}"
echo "==> Deploying $REPO_URL ($BRANCH) as https://$DOMAIN"

echo "==> Installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q python3 python3-venv python3-pip git curl debian-keyring debian-archive-keyring apt-transport-https gnupg ufw sqlite3
if ! command -v caddy >/dev/null; then
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -q && apt-get install -y -q caddy
fi

id "$APP_USER" >/dev/null 2>&1 || useradd --system --home "$DATA_DIR" --shell /usr/sbin/nologin "$APP_USER"
mkdir -p "$DATA_DIR/media"

echo "==> Fetching code"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch -q origin "$BRANCH" && git -C "$APP_DIR" checkout -q -B "$BRANCH" "origin/$BRANCH"
else
  git clone -q --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

if [ ! -f "$ENV_FILE" ]; then
  echo "==> Writing $ENV_FILE"
  cat > "$ENV_FILE" <<EOF
DJANGO_DEBUG=0
DJANGO_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')
DJANGO_ALLOWED_HOSTS=$DOMAIN,$IP,localhost,127.0.0.1
DJANGO_HTTPS=1
LMS_SQLITE_PATH=$DATA_DIR/db.sqlite3
LMS_MEDIA_ROOT=$DATA_DIR/media
LMS_SERVE_MEDIA=1
LMS_TIME_ZONE=Asia/Kolkata
# ANTHROPIC_API_KEY=   (optional: Claude-assisted syllabus parsing and question generation)
EOF
  chmod 640 "$ENV_FILE"
  chgrp "$APP_USER" "$ENV_FILE"
fi
chown -R "$APP_USER:$APP_USER" "$DATA_DIR" "$APP_DIR"

manage() { sudo -u "$APP_USER" bash -c "set -a; . $ENV_FILE; set +a; cd $APP_DIR && .venv/bin/python manage.py $*"; }

echo "==> Migrating database"
FIRST_RUN=0; [ -f "$DATA_DIR/db.sqlite3" ] || FIRST_RUN=1
manage migrate --noinput
manage collectstatic --noinput -v0
if [ "$FIRST_RUN" = 1 ]; then
  echo "==> Loading data"
  if [ "$DEMO" = 1 ]; then manage seed_demo; manage seed_institution --demo; else manage seed_institution; fi
fi

echo "==> Configuring services"
cat > /etc/systemd/system/campusflow.service <<EOF
[Unit]
Description=CampusFlow LMS (gunicorn)
After=network.target

[Service]
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$APP_DIR/.venv/bin/gunicorn campusflow.wsgi --bind 127.0.0.1:8000 --workers 3 --timeout 120
Restart=always

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/caddy/Caddyfile <<EOF
$DOMAIN {
    encode gzip
    request_body {
        max_size 50MB
    }
    reverse_proxy 127.0.0.1:8000
}
EOF

ufw allow OpenSSH >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; ufw --force enable >/dev/null

systemctl daemon-reload
systemctl enable -q --now campusflow
systemctl restart campusflow
systemctl enable -q caddy
systemctl restart caddy

# nightly database backup, 14 days kept
cat > /etc/cron.daily/campusflow-backup <<EOF
#!/bin/sh
mkdir -p $DATA_DIR/backups
sqlite3 $DATA_DIR/db.sqlite3 ".backup '$DATA_DIR/backups/db-\$(date +%F).sqlite3'" 2>/dev/null || cp $DATA_DIR/db.sqlite3 $DATA_DIR/backups/db-\$(date +%F).sqlite3
find $DATA_DIR/backups -name 'db-*.sqlite3' -mtime +14 -delete
EOF
chmod +x /etc/cron.daily/campusflow-backup

sleep 3
if curl -fsS -o /dev/null http://127.0.0.1:8000/login/; then
  echo
  echo "CampusFlow is running:  https://$DOMAIN"
  echo "(the HTTPS certificate is issued on the first visit; allow up to a minute)"
  [ "$DEMO" = 1 ] && echo "Logins: admin/admin123 (super admin), teacher/teacher123, s01..s12/student123 - change these before real use."
else
  echo "The app did not respond; check: journalctl -u campusflow -n 50"; exit 1
fi
