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
apt-get install -y -qq git python3 python3-venv python3-pip debian-keyring debian-archive-keyring apt-transport-https curl >/dev/null
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq && apt-get install -y -qq caddy >/dev/null
fi

id -u ripraptor >/dev/null 2>&1 || useradd --system --home "$DIR" --shell /usr/sbin/nologin ripraptor
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
ENV
fi
mkdir -p /var/lib/ripraptor media
set -a; . ./.env; set +a
.venv/bin/python manage.py migrate -v0
.venv/bin/python manage.py collectstatic --noinput -v0
.venv/bin/python manage.py setup_shops
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

echo "0 * * * *  cd $DIR && set -a && . ./.env && set +a && flock -n /tmp/ripraptor-import.lock .venv/bin/python manage.py import_prices >> /var/log/ripraptor-import.log 2>&1
15 0 * * * cd $DIR && set -a && . ./.env && set +a && flock /tmp/ripraptor-import.lock .venv/bin/python manage.py snapshot_daily_prices >> /var/log/ripraptor-import.log 2>&1
30 3 * * 0 cd $DIR && set -a && . ./.env && set +a && flock /tmp/ripraptor-import.lock .venv/bin/python manage.py check_delivery --apply >> /var/log/ripraptor-import.log 2>&1
*/10 * * * * cd $DIR && set -a && . ./.env && set +a && flock -n /tmp/ripraptor-import.lock .venv/bin/python manage.py watch_stock >> /var/log/ripraptor-import.log 2>&1" | crontab -u ripraptor -
touch /var/log/ripraptor-import.log && chown ripraptor /var/log/ripraptor-import.log

# First price import in the background so the site is usable straight away.
sudo -u ripraptor bash -c "cd $DIR && set -a && . ./.env && set +a && nohup .venv/bin/python manage.py import_prices >> /var/log/ripraptor-import.log 2>&1 &"

echo
echo "Done. https://$DOMAIN should answer within a minute (Caddy fetches the certificate)."
echo "Create your admin login with:"
echo "  cd $DIR && sudo -u ripraptor bash -c 'set -a; . ./.env; set +a; .venv/bin/python manage.py createsuperuser'"
echo "Prices are importing now; watch with: tail -f /var/log/ripraptor-import.log"
