#!/usr/bin/env bash
# One-time setup on a fresh Ubuntu server. Run as root from the repo checkout at /srv/ripraptor.
# deploy/install.sh does all of this and more, and is the way to update; this is the short manual path.
# As in install.sh, the code, .venv and .env belong to root (ripraptor reads .env through its group), ripraptor
# writes only /var/lib/ripraptor, media and staticfiles, and every manage.py step runs as ripraptor.
set -euo pipefail
cd /srv/ripraptor
id -u ripraptor >/dev/null 2>&1 || useradd --system --home /srv/ripraptor --shell /usr/sbin/nologin ripraptor
python3 -m venv .venv
.venv/bin/python -I -m pip install -r requirements.txt
if [ ! -f .env ]; then
  # .env.example with a long random key in place of its stand-in, which the site refuses (ripraptor/settings.py).
  SECRET=$(python3 -I -c "import secrets; print(secrets.token_urlsafe(50))")
  (umask 077 && sed "s/^DJANGO_SECRET_KEY=.*/DJANGO_SECRET_KEY=$SECRET/" .env.example > .env)
  echo "Check /srv/ripraptor/.env (the domain and contact address) before going live."
fi
chown -R root:root /srv/ripraptor
chown root:ripraptor .env && chmod 640 .env
mkdir -p /var/lib/ripraptor/cache media staticfiles
chown -R ripraptor:ripraptor /var/lib/ripraptor media staticfiles
site() { sudo -u ripraptor bash -c 'cd /srv/ripraptor && set -a && . ./.env && set +a && exec "$@"' site "$@"; }
site .venv/bin/python manage.py migrate
site .venv/bin/python manage.py collectstatic --noinput
site .venv/bin/python manage.py seed_catalogue
python3 -I -m compileall -q manage.py ripraptor catalogue content web inbox guides twostep >/dev/null
cp deploy/ripraptor.service /etc/systemd/system/ripraptor.service
cp deploy/ripraptor-worker.service /etc/systemd/system/ripraptor-worker.service
systemctl daemon-reload && systemctl enable --now ripraptor ripraptor-worker
crontab -u ripraptor deploy/crontab
echo "Done. Create an admin user with: sudo -u ripraptor bash -c 'set -a; . ./.env; set +a; .venv/bin/python manage.py createsuperuser'"
