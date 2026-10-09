#!/usr/bin/env bash
# One-time setup on a fresh Ubuntu server. Run as root from the repo checkout at /srv/ripraptor.
set -euo pipefail
cd /srv/ripraptor
id -u ripraptor >/dev/null 2>&1 || useradd --system --home /srv/ripraptor ripraptor
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
test -f .env || { cp .env.example .env; echo "Edit /srv/ripraptor/.env before starting."; }
mkdir -p /var/lib/ripraptor/cache && chown -R ripraptor /var/lib/ripraptor
set -a; . ./.env; set +a
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py seed_catalogue
chown -R ripraptor /srv/ripraptor
cp deploy/ripraptor.service /etc/systemd/system/ripraptor.service
cp deploy/ripraptor-worker.service /etc/systemd/system/ripraptor-worker.service
systemctl daemon-reload && systemctl enable --now ripraptor ripraptor-worker
crontab -u ripraptor deploy/crontab
echo "Done. Create an admin user with: sudo -u ripraptor .venv/bin/python manage.py createsuperuser"
