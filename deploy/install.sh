#!/usr/bin/env bash
# One-shot install on a fresh Ubuntu 22.04 or 24.04 server.
#
#   curl -fsSL https://raw.githubusercontent.com/Dimondb1/cardthing/claude/compassionate-edison-aot3li/deploy/install.sh | sudo bash -s ripraptor.com
#
# Installs Python, Caddy (automatic HTTPS), clones the site to /srv/ripraptor,
# sets it up, starts it and the background reader, and schedules the hourly
# fallback. Run it again to update to the latest code.
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

# Swap: a 1 GB server with none has nowhere to go when memory runs out, so the kernel kills a
# process outright. On 14 Oct 2026 it killed the site's gunicorn (the reader, both web workers and
# cron jobs at once). With a 1 GB swap file it moves idle memory to disk instead. swappiness 10
# keeps it to that: the site stays in memory while there is room. Made once; existing swap is kept.
# A server that cannot have swap (some containers) is told so, and the update carries on.
if [ -z "$(swapon --show --noheadings 2>/dev/null)" ]; then
  {
    if [ ! -f /swapfile ]; then
      fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null
    fi && swapon /swapfile && { grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab; }
  } || echo "Could not turn on a swap file; carrying on without one."
fi
echo 'vm.swappiness=10' > /etc/sysctl.d/90-ripraptor.conf
sysctl -q -p /etc/sysctl.d/90-ripraptor.conf || true

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
# The cache the web app and every cron command share (RIPRAPTOR_CACHE_DIR's default). It must
# belong to ripraptor: a folder the site cannot write falls back to a cache per process.
install -d -o ripraptor -g ripraptor /var/lib/ripraptor/cache
set -a; . ./.env; set +a
# A spreadsheet being made (manage.py data_files, which the site starts) holds this lock until its run
# ends, at most 25 minutes. Wait for it, so the restart below never stops a file part way and the
# database is not shrunk or migrated under it. As ripraptor, so the lock file stays the runner's.
DATA_LOCK="${RIPRAPTOR_CACHE_DIR:-/var/lib/ripraptor/cache}/data-files.lock"
if [ -f "$DATA_LOCK" ]; then
  sudo -u ripraptor timeout 1800 flock "$DATA_LOCK" true || echo "A spreadsheet is still being made; carrying on."
fi
# First shrink the database (old import records once filled the disk), so the copy below copies only
# what is in use. Then a copy of the database before any change, so an update can be rolled back.
# backup_db uses SQLite's own backup, which is safe while the site is running; cp is not (it misses
# the -wal file). It makes room first and stops, changing nothing, if there is still not enough.
# The newest five copies are kept in /var/lib/ripraptor/backups/.
if [ -f /var/lib/ripraptor/db.sqlite3 ]; then
  # As the site's own user, so the -wal and -shm files it may create stay writable by the running site.
  sudo -E -u ripraptor .venv/bin/python manage.py compact_db
  chown ripraptor:ripraptor /var/lib/ripraptor/db.sqlite3*
  .venv/bin/python manage.py backup_db --keep 5
fi
# Keep the import log to a fixed size: four weekly files, each cut at 50 MB.
cat > /etc/logrotate.d/ripraptor <<'ROTATE'
/var/log/ripraptor-import.log {
    weekly
    maxsize 50M
    rotate 4
    compress
    missingok
    notifempty
    copytruncate
}
ROTATE
.venv/bin/python manage.py migrate -v0
.venv/bin/python manage.py backfill_restocks >/dev/null
.venv/bin/python manage.py set_languages >/dev/null
.venv/bin/python manage.py set_types >/dev/null
.venv/bin/python manage.py collectstatic --noinput -v0
.venv/bin/python manage.py setup_shops
# Products added by hand rather than by a shop read, so they are on the site before any shop lists them.
.venv/bin/python manage.py import_products catalogue/data/union_arena.csv >/dev/null
grep -q RIPRAPTOR_GEOIP_DB .env || echo "RIPRAPTOR_GEOIP_DB=/var/lib/ripraptor/dbip-country.mmdb" >> .env
# Pre-order emails go only to alerts asked for from this install on, when the form first promised them.
# Written once, so a later update never moves the cut-off and an earlier subscriber never hears about pre-orders.
grep -q RIPRAPTOR_PREORDER_ALERTS_FROM .env || echo "RIPRAPTOR_PREORDER_ALERTS_FROM=$(date -u +%Y-%m-%dT%H:%M:%S+00:00)" >> .env
# A server with under 1.5 GB of memory runs two background reader jobs at once instead of three, so
# the reader stays under its 300 MB limit beside the site. A "1 GB" server reports about 960 MB, which
# the old 900 MB line missed. Written once; a value set by hand stays.
grep -q RIPRAPTOR_WORKER_THREADS .env || { [ "$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)" -lt 1500000 ] && echo "RIPRAPTOR_WORKER_THREADS=2" >> .env || true; }
set -a; . ./.env; set +a
[ -f /var/lib/ripraptor/dbip-country.mmdb ] || .venv/bin/python manage.py fetch_geoip || true
chown -R ripraptor:ripraptor "$DIR" /var/lib/ripraptor

cp deploy/ripraptor.service /etc/systemd/system/ripraptor.service
systemctl daemon-reload
systemctl enable -q --now ripraptor
systemctl restart ripraptor
# The cache folder outlives a restart, so the new code would read lists the old code pickled for up
# to five minutes: a query per row for a field a migration added, or an error for one it now sets.
# Empty it once the old workers have stopped; clearing before the restart would let them refill it.
sudo -u ripraptor bash -c "cd $DIR && set -a && . ./.env && set +a && .venv/bin/python manage.py shell -c 'from django.core.cache import cache; cache.clear()'"
# Then build the lists the home, Deals and drops pages read, so the first visitors after a deploy do not wait
# for them. A failure leaves them to be built by the first visitor, as before.
sudo -u ripraptor bash -c "cd $DIR && set -a && . ./.env && set +a && .venv/bin/python manage.py warm_pages" || true
# Cardmarket's catalogue and prices, once, when none are held yet, so matching can start without waiting for the
# daily read at 6:44. A failure leaves it to that read.
sudo -u ripraptor bash -c "cd $DIR && set -a && . ./.env && set +a && .venv/bin/python manage.py shell -c 'from catalogue.models import CardmarketProduct; raise SystemExit(0 if CardmarketProduct.objects.exists() else 1)' || timeout -k 30 900 .venv/bin/python manage.py import_cardmarket" || true

# One address for the site: www. sends visitors and Google to the same page without it, permanently.
# Serving both made Google index the whole site twice and split each page's standing between them.
cat > /etc/caddy/Caddyfile <<CADDY
www.$DOMAIN, http://www.$DOMAIN {
    redir https://$DOMAIN{uri} permanent
}
$DOMAIN {
    # The largest upload is a 10 MB spreadsheet; a bigger body is refused here, before a web worker reads it.
    request_body {
        max_size 25MB
    }
    reverse_proxy 127.0.0.1:8000
    encode gzip
}
CADDY
systemctl enable -q --now caddy
systemctl reload caddy

# The background reader reads the shops all day (see below). The cron lines are the floor under it:
# the hourly import and the ten-minute stock watch do nothing while its heartbeat is younger than 30
# minutes (--if-worker-dead 30), and read the shops whose turn has come when it is not running. The
# hourly import waits up to 30 minutes for the import lock rather than skipping, so the 10-minute
# stock watcher (which fires at the same minute and skips while the lock is held) can never crowd it
# out. tidy_all runs at five past on its own lock and then waits up to 30 minutes for the import
# lock, so it never merges or moves products while a shop read is saving offers. The stock alerts
# (every 10 minutes), the drop reminders (every 15, at 9 past, on their own lock) and the search alerts (every 15,
# at 4 past, on their own lock) never depend on the reader, the Claude judge looks every 5 minutes on its own lock and does nothing
# until the owner saves a key and switches it on in Things to check, check_worker logs whether the reader is alive (and tells the owner,
# at most once a day, when it has stopped or never starts), and backup_db keeps five nightly copies.
# tidy_referrals runs hourly at 55 past on its own lock: it retries refer a friend address emails and deletes
# details the offer no longer needs (see the Terms page).
# warm_pages --due runs at 1 past the fives on its own lock: it builds the cached page lists again when an
# import or an edit cleared them or they are nearly expired, so the floor holds when the reader is not running.
# The alerts start at 3 past the tens and the judge at 2 past the fives, so no more than two of these
# Django processes start in the same minute: on 14 Oct 2026 three at once beside the reader and the
# site ran a 1 GB server out of memory.
# Every command runs under timeout, set to its budget plus five minutes: a command that hangs
# while holding a lock would otherwise make every later run behind it give up silently.
# The snapshot, the delivery check and tidy_all put timeout inside flock, so time spent waiting
# for a long import does not use up their limit.
# Cardmarket's sealed catalogue and price guide are read daily at 6:44 (after Cardmarket's own early update), and Claude
# matches them to our products hourly at 29 past, waiting for the judge's lock so only one Claude job runs at once.
# deploy/crontab carries the same lines; catalogue/tests_ops.py fails when they differ.
echo "PYTHONUNBUFFERED=1
0 * * * *  cd $DIR && set -a && . ./.env && set +a && timeout -k 60 3300 flock -w 1800 /tmp/ripraptor-import.lock .venv/bin/python manage.py import_prices --due --if-worker-dead 30 >> /var/log/ripraptor-import.log 2>&1
5 * * * *  cd $DIR && set -a && . ./.env && set +a && flock -n /tmp/ripraptor-tidy.lock flock -w 1800 /tmp/ripraptor-import.lock timeout -k 60 1500 .venv/bin/python manage.py tidy_all >> /var/log/ripraptor-import.log 2>&1
15 0 * * * cd $DIR && set -a && . ./.env && set +a && flock /tmp/ripraptor-import.lock timeout -k 60 1800 .venv/bin/python manage.py snapshot_daily_prices >> /var/log/ripraptor-import.log 2>&1
30 3 * * 0 cd $DIR && set -a && . ./.env && set +a && flock /tmp/ripraptor-import.lock timeout -k 60 3000 .venv/bin/python manage.py check_delivery --apply >> /var/log/ripraptor-import.log 2>&1
*/10 * * * * cd $DIR && set -a && . ./.env && set +a && timeout -k 30 540 flock -n /tmp/ripraptor-import.lock .venv/bin/python manage.py watch_stock --if-worker-dead 30 >> /var/log/ripraptor-import.log 2>&1
3-59/10 * * * * cd $DIR && set -a && . ./.env && set +a && timeout -k 30 540 flock -n /tmp/ripraptor-alerts.lock .venv/bin/python manage.py send_stock_alerts >> /var/log/ripraptor-import.log 2>&1
9-59/15 * * * * cd $DIR && set -a && . ./.env && set +a && timeout -k 30 540 flock -n /tmp/ripraptor-drops.lock .venv/bin/python manage.py send_drop_alerts >> /var/log/ripraptor-import.log 2>&1
4-59/15 * * * * cd $DIR && set -a && . ./.env && set +a && timeout -k 30 540 flock -n /tmp/ripraptor-search-alerts.lock .venv/bin/python manage.py send_search_alerts >> /var/log/ripraptor-import.log 2>&1
8-58/10 * * * * cd $DIR && set -a && . ./.env && set +a && timeout -k 30 300 flock -n /tmp/ripraptor-discord.lock .venv/bin/python manage.py post_discord >> /var/log/ripraptor-import.log 2>&1
2-59/5 * * * * cd $DIR && set -a && . ./.env && set +a && timeout -k 30 900 flock -n /tmp/ripraptor-judge.lock .venv/bin/python manage.py judge_checks >> /var/log/ripraptor-import.log 2>&1
51 6 * * * cd $DIR && set -a && . ./.env && set +a && flock -w 1200 /tmp/ripraptor-judge.lock timeout -k 30 1200 .venv/bin/python manage.py check_set_retail >> /var/log/ripraptor-import.log 2>&1
44 6 * * * cd $DIR && set -a && . ./.env && set +a && timeout -k 60 900 .venv/bin/python manage.py import_cardmarket >> /var/log/ripraptor-import.log 2>&1
29 * * * * cd $DIR && set -a && . ./.env && set +a && flock -w 600 /tmp/ripraptor-judge.lock timeout -k 30 1200 .venv/bin/python manage.py match_cardmarket >> /var/log/ripraptor-import.log 2>&1
20 * * * * cd $DIR && set -a && . ./.env && set +a && timeout -k 30 600 .venv/bin/python manage.py check_worker >> /var/log/ripraptor-import.log 2>&1
40 0 * * * cd $DIR && set -a && . ./.env && set +a && timeout -k 60 1800 .venv/bin/python manage.py backup_db --keep 5 >> /var/log/ripraptor-import.log 2>&1
45 4 5 * * cd $DIR && set -a && . ./.env && set +a && timeout -k 60 1800 .venv/bin/python manage.py fetch_geoip >> /var/log/ripraptor-import.log 2>&1
55 * * * * cd $DIR && set -a && . ./.env && set +a && timeout -k 30 600 flock -n /tmp/ripraptor-refer.lock .venv/bin/python manage.py tidy_referrals >> /var/log/ripraptor-import.log 2>&1
1-59/5 * * * * cd $DIR && set -a && . ./.env && set +a && timeout -k 30 240 flock -n /tmp/ripraptor-warm.lock .venv/bin/python manage.py warm_pages --due >> /var/log/ripraptor-import.log 2>&1" | crontab -u ripraptor -
touch /var/log/ripraptor-import.log && chown ripraptor /var/log/ripraptor-import.log

# The background reader. A shop never read before has no next read, so on its first start it reads
# every shop; nothing else needs starting. It is restarted after the code and the cache are updated.
cp deploy/ripraptor-worker.service /etc/systemd/system/ripraptor-worker.service
systemctl daemon-reload
systemctl enable -q ripraptor-worker
systemctl restart ripraptor-worker

echo
echo "Done. https://$DOMAIN should answer within a minute (Caddy fetches the certificate)."
echo "Create your admin login with:"
echo "  cd $DIR && sudo -u ripraptor bash -c 'set -a; . ./.env; set +a; .venv/bin/python manage.py createsuperuser'"
echo "Prices are being read now; watch with: journalctl -fu ripraptor-worker"
