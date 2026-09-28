#!/usr/bin/env bash
# One-time setup on a fresh Ubuntu server. Run as root from the repo checkout at /srv/cardscout.
set -euo pipefail
cd /srv/cardscout
id -u cardscout >/dev/null 2>&1 || useradd --system --home /srv/cardscout cardscout
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
test -f .env || { cp .env.example .env; echo "Edit /srv/cardscout/.env before starting."; }
mkdir -p /var/lib/cardscout && chown cardscout /var/lib/cardscout
set -a; . ./.env; set +a
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py seed_catalogue
chown -R cardscout /srv/cardscout
cp deploy/cardscout.service /etc/systemd/system/cardscout.service
systemctl daemon-reload && systemctl enable --now cardscout
crontab -u cardscout deploy/crontab
echo "Done. Create an admin user with: sudo -u cardscout .venv/bin/python manage.py createsuperuser"
