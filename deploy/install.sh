#!/usr/bin/env bash
# One-shot install on a fresh Ubuntu 22.04 or 24.04 server.
#
#   curl -fsSL https://raw.githubusercontent.com/Dimondb1/cardthing/claude/compassionate-edison-aot3li/deploy/install.sh | sudo bash -s ripraptor.com
#
# Installs Python, Caddy (automatic HTTPS), clones the site to /srv/ripraptor,
# sets it up, starts it, and schedules hourly price checks. Run it again to
# update to the latest code.
set -euo pipefail
DOMAIN="${1:?Usage: install.sh yourdomain.com}"
REPO="https://github.com/Dimondb1/cardthing.git"
BRANCH="${BRANCH:-claude/compassionate-edison-aot3li}"
DIR=/srv/ripraptor

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# Caddy comes from Ubuntu's own repository: the signing key on Caddy's
# third-party repository expired and apt then refuses it.
rm -f /etc/apt/sources.list.d/caddy-stable.list
apt-get update -qq
apt-get install -y -qq git python3 python3-venv python3-pip curl caddy >/dev/null

id -u ripraptor >/dev/null 2>&1 || useradd --system --home "$DIR" --shell /usr/sbin/nologin ripraptor
git config --global --add safe.directory "$DIR" >/dev/null 2>&1 || true   # the checkout is owned by ripraptor, git runs as root
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" fetch -q origin "$BRANCH" && git -C "$DIR" checkout -q "$BRANCH" && git -C "$DIR" reset -q --hard "origin/$BRANCH"
else
  git clone -q --branch "$BRANCH" "$REPO" "$DIR"
fi
cd "$DIR"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt

if [ ! -f .env ]; then
  SECRET=$(.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(50))")
  cat > .env <<ENV
DJANGO_DEBUG=0
DJANGO_SECRET_KEY=$SECRET
DJANGO_ALLOWED_HOSTS=$DOMAIN,www.$DOMAIN
DJANGO_CSRF_TRUSTED_ORIGINS=https://$DOMAIN,https://www.$DOMAIN
DJANGO_SQLITE_PATH=/var/lib/ripraptor/db.sqlite3
RIPRAPTOR_USE_FEED_IMAGES=1
RIPRAPTOR_GEOIP_DB=/var/lib/ripraptor/dbip-country.mmdb
ENV
fi
mkdir -p /var/lib/ripraptor media
set -a; . ./.env; set +a
# A copy of the database before any change, so an update can be rolled back. backup_db uses
# SQLite's own backup, which is safe while the site is running; cp is not (it misses the -wal
# file). The newest five copies are kept in /var/lib/ripraptor/backups/.
if [ -f /var/lib/ripraptor/db.sqlite3 ]; then
  .venv/bin/python manage.py backup_db --keep 5
fi
.venv/bin/python manage.py migrate -v0
.venv/bin/python manage.py backfill_restocks >/dev/null
.venv/bin/python manage.py collectstatic --noinput -v0
.venv/bin/python manage.py setup_shops
grep -q RIPRAPTOR_GEOIP_DB .env || echo "RIPRAPTOR_GEOIP_DB=/var/lib/ripraptor/dbip-country.mmdb" >> .env
set -a; . ./.env; set +a
[ -f /var/lib/ripraptor/dbip-country.mmdb ] || .venv/bin/python manage.py fetch_geoip || true
chown -R ripraptor:ripraptor "$DIR" /var/lib/ripraptor

cp deploy/ripraptor.service /etc/systemd/system/ripraptor.service
systemctl daemon-reload
systemctl enable -q --now ripraptor
systemctl restart ripraptor

cat > /etc/caddy/Caddyfile <<CADDY
$DOMAIN, www.$DOMAIN {
    reverse_proxy 127.0.0.1:8000
    encode gzip
}
CADDY
systemctl enable -q --now caddy
systemctl reload caddy

# The hourly import waits up to 30 minutes for the lock rather than skipping, so the
# 10-minute stock watcher (which fires at the same minute and skips while the lock is
# held) can never crowd it out. Output is unbuffered so the log shows progress live.
# Every command runs under timeout, set to its budget plus five minutes: a command that hangs
# while holding a lock would otherwise make every later run behind it give up silently.
# The snapshot and the delivery check put timeout inside flock, so time spent waiting for a
# long import does not use up their limit.
# deploy/crontab carries the same lines; catalogue/tests_ops.py fails when they differ.
echo "PYTHONUNBUFFERED=1
0 * * * *  cd $DIR && set -a && . ./.env && set +a && timeout -k 60 3300 flock -w 1800 /tmp/ripraptor-import.lock .venv/bin/python manage.py import_prices >> /var/log/ripraptor-import.log 2>&1 && timeout -k 60 1500 .venv/bin/python manage.py tidy_all >> /var/log/ripraptor-import.log 2>&1
15 0 * * * cd $DIR && set -a && . ./.env && set +a && flock /tmp/ripraptor-import.lock timeout -k 60 1800 .venv/bin/python manage.py snapshot_daily_prices >> /var/log/ripraptor-import.log 2>&1
30 3 * * 0 cd $DIR && set -a && . ./.env && set +a && flock /tmp/ripraptor-import.lock timeout -k 60 3000 .venv/bin/python manage.py check_delivery --apply >> /var/log/ripraptor-import.log 2>&1
*/10 * * * * cd $DIR && set -a && . ./.env && set +a && timeout -k 30 540 flock -n /tmp/ripraptor-import.lock .venv/bin/python manage.py watch_stock >> /var/log/ripraptor-import.log 2>&1
*/10 * * * * cd $DIR && set -a && . ./.env && set +a && timeout -k 30 540 flock -n /tmp/ripraptor-alerts.lock .venv/bin/python manage.py send_stock_alerts >> /var/log/ripraptor-import.log 2>&1
45 4 5 * * cd $DIR && set -a && . ./.env && set +a && timeout -k 60 1800 .venv/bin/python manage.py fetch_geoip >> /var/log/ripraptor-import.log 2>&1" | crontab -u ripraptor -
touch /var/log/ripraptor-import.log && chown ripraptor /var/log/ripraptor-import.log

# First price import in the background so the site is usable straight away.
sudo -u ripraptor bash -c "cd $DIR && set -a && . ./.env && set +a && PYTHONUNBUFFERED=1 nohup flock -w 1800 /tmp/ripraptor-import.lock .venv/bin/python manage.py import_prices >> /var/log/ripraptor-import.log 2>&1 &"

echo
echo "Done. https://$DOMAIN should answer within a minute (Caddy fetches the certificate)."
echo "Create your admin login with:"
echo "  cd $DIR && sudo -u ripraptor bash -c 'set -a; . ./.env; set +a; .venv/bin/python manage.py createsuperuser'"
echo "Prices are importing now; watch with: tail -f /var/log/ripraptor-import.log"
