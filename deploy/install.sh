#!/usr/bin/env bash
# One-shot install on a fresh Ubuntu 22.04 or 24.04 server.
#
#   curl -fsSL https://raw.githubusercontent.com/Dimondb1/cardthing/claude/compassionate-edison-aot3li/deploy/install.sh | sudo bash -s ripraptor.com
#
# Installs Python, Caddy (automatic HTTPS), clones the site to /srv/ripraptor,
# sets it up, starts it and the background reader, and schedules the hourly
# fallback. Run it again to update to the latest code.
set -euo pipefail
# The address as typed, checked before anything is changed: a web address is letters, numbers, dots and hyphens. On
# 26 Nov 2026 an update typed with a stray "~" after it wrote the web server's settings for "ripraptor.com~", and every
# visitor was served a blank page until the file was mended by hand.
DOMAIN="$(printf '%s' "${1:?Usage: install.sh yourdomain.com}" | tr 'A-Z' 'a-z')"
WEB_ADDRESS='^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$'
if [[ ! "$DOMAIN" =~ $WEB_ADDRESS ]]; then
  echo "Stopped before changing anything: \"$1\" is not a web address. Type it as ripraptor.com, with nothing after it." >&2
  exit 1
fi
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

# Ubuntu's own updates install themselves each night (unattended-upgrades): its security fixes and its
# ordinary fixes (-updates), so a hole found in the system is closed without anyone logging in and the
# ordinary ones do not pile up. They install at 02:10 and, only when one needs it (a kernel fix), the
# server restarts at 02:40, about a minute offline, the site and the reader starting again by themselves.
# The owner, 23 Nov 2026, with 23 updates waiting and a restart pending: "All updates + night restart".
# 02:40 is the quietest minute of the crontab: after the backup (00:40), before the delivery checks
# (03:30 on Sundays, 04:30 daily, up to 50 minutes each). The server keeps UTC.
apt-get install -y -qq unattended-upgrades >/dev/null
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'APT'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT
cat > /etc/apt/apt.conf.d/52ripraptor-upgrades <<'APT'
Unattended-Upgrade::Allowed-Origins:: "${distro_id}:${distro_codename}-updates";
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "02:40";
APT
mkdir -p /etc/systemd/system/apt-daily-upgrade.timer.d
cat > /etc/systemd/system/apt-daily-upgrade.timer.d/ripraptor.conf <<'TIMER'
[Timer]
OnCalendar=
OnCalendar=*-*-* 02:10
RandomizedDelaySec=0
TIMER
systemctl daemon-reload 2>/dev/null || true
systemctl restart apt-daily-upgrade.timer 2>/dev/null || true
systemctl enable -q --now unattended-upgrades 2>/dev/null || true

# Firewall: from outside, only the web (80, and 443 over TCP and UDP for HTTP/3) and SSH answer. The site
# listens on 127.0.0.1 alone already; this keeps it so for anything installed later. SSH is allowed before
# the firewall goes on, on 22 and on any port its settings name, so the way in is never shut. A server
# that cannot run one (some containers) is told so, and the update carries on.
{
  apt-get install -y -qq ufw >/dev/null &&
  for port in 22 $(sshd -T 2>/dev/null | awk '$1 == "port" {print $2}'); do ufw allow "$port/tcp" >/dev/null; done &&
  ufw allow 80/tcp >/dev/null && ufw allow 443/tcp >/dev/null && ufw allow 443/udp >/dev/null &&
  ufw --force enable >/dev/null
} || echo "Could not turn on the firewall; carrying on without it."

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

# Root and ripraptor (the account the site, the reader and the cron jobs run as) are kept apart by who owns what.
# Everything root runs or reads here belongs to root, and ripraptor can only read it: the code, its history (.git),
# the Python packages (.venv) and the settings (.env, which ripraptor reads through its group). ripraptor writes only
# its data: /var/lib/ripraptor (the database, the cache, saved keys, backups), media (uploaded pictures) and
# staticfiles (collectstatic's copy). Every manage.py step below runs as ripraptor (site), never as root. Until
# 24 Nov 2026 the whole folder belonged to ripraptor and root ran its .venv and shell-read its .env on every update,
# so a break-in to the site could have become root at the next update (the security audit).
# A checkout whose history or packages belonged to ripraptor has both made again, once, by root: nothing that
# account could have changed is ever run as root. Root's old exception for it (safe.directory) goes too.
git config --global --fixed-value --unset-all safe.directory "$DIR" >/dev/null 2>&1 || true
FRESH_VENV=0
if [ -d "$DIR" ]; then
  if [ -e "$DIR/.git" ] && [ "$(stat -c %u "$DIR/.git")" != 0 ]; then rm -rf "${DIR:?}/.git"; fi
  if [ ! -d "$DIR/.venv" ] || [ "$(stat -c %u "$DIR/.venv")" != 0 ]; then FRESH_VENV=1; fi
  # A link ripraptor may have left where a file root writes goes, or where its own folders go, is never followed.
  if [ -L "$DIR/.env" ]; then echo "$DIR/.env is a link, not a file. Stopping: look at it before updating."; exit 1; fi
  for kept in media staticfiles; do if [ -L "$DIR/$kept" ]; then rm -f "${DIR:?}/$kept"; fi; done
  find "$DIR" -xdev \( -path "$DIR/media" -o -path "$DIR/staticfiles" \) -prune -o -exec chown -h root:root {} +
  find "$DIR" -xdev \( -path "$DIR/media" -o -path "$DIR/staticfiles" \) -prune -o ! -type l -exec chmod go-w {} +
  if [ -f "$DIR/.env" ]; then chown root:ripraptor "$DIR/.env" && chmod 640 "$DIR/.env"; fi
fi
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" fetch -q origin "$BRANCH" && git -C "$DIR" checkout -q "$BRANCH" && git -C "$DIR" reset -q --hard "origin/$BRANCH"
elif [ -d "$DIR" ]; then
  git -C "$DIR" init -q
  git -C "$DIR" remote add origin "$REPO"
  git -C "$DIR" fetch -q origin "$BRANCH"
  git -C "$DIR" checkout -q -f -B "$BRANCH" "origin/$BRANCH"
else
  git clone -q --branch "$BRANCH" "$REPO" "$DIR"
  FRESH_VENV=1
fi
cd "$DIR"
# The packages are run as python -m (pip, gunicorn), never by a script's own first line, so a folder made beside the
# old one and then moved into its place works: the running site keeps its packages until it is restarted below.
if [ "$FRESH_VENV" = 1 ]; then
  rm -rf .venv-new .venv-old
  python3 -m venv .venv-new
  .venv-new/bin/python -I -m pip install -q -r requirements.txt
  if [ -e .venv ]; then mv .venv .venv-old; fi
  mv .venv-new .venv
  rm -rf .venv-old
else
  .venv/bin/python -I -m pip install -q -r requirements.txt
fi
# Python cannot save its compiled copies in a folder ripraptor cannot write, so root makes them once per update and
# every process starts as quickly as before.
python3 -I -m compileall -q -j 0 manage.py ripraptor catalogue content web inbox guides twostep >/dev/null

if [ ! -f .env ]; then
  SECRET=$(python3 -I -c "import secrets; print(secrets.token_urlsafe(50))")
  (umask 077 && cat > .env <<ENV
DJANGO_DEBUG=0
DJANGO_SECRET_KEY=$SECRET
DJANGO_ALLOWED_HOSTS=$DOMAIN,www.$DOMAIN
DJANGO_CSRF_TRUSTED_ORIGINS=https://$DOMAIN,https://www.$DOMAIN
DJANGO_SQLITE_PATH=/var/lib/ripraptor/db.sqlite3
RIPRAPTOR_USE_FEED_IMAGES=1
RIPRAPTOR_GEOIP_DB=/var/lib/ripraptor/dbip-country.mmdb
ENV
  )
fi
grep -q RIPRAPTOR_GEOIP_DB .env || echo "RIPRAPTOR_GEOIP_DB=/var/lib/ripraptor/dbip-country.mmdb" >> .env
# Pre-order emails go only to alerts asked for from this install on, when the form first promised them.
# Written once, so a later update never moves the cut-off and an earlier subscriber never hears about pre-orders.
grep -q RIPRAPTOR_PREORDER_ALERTS_FROM .env || echo "RIPRAPTOR_PREORDER_ALERTS_FROM=$(date -u +%Y-%m-%dT%H:%M:%S+00:00)" >> .env
# A server with under 1.5 GB of memory runs two background reader jobs at once instead of three, so
# the reader stays under its memory limit beside the site. A "1 GB" server reports about 960 MB, which
# the old 900 MB line missed. Written once; a value set by hand stays. A server resized to 1.5 GB or more
# since loses the 2 written for it, so its reader runs three jobs at the next update.
[ "$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)" -lt 1500000 ] || sed -i '/^RIPRAPTOR_WORKER_THREADS=2$/d' .env
grep -q RIPRAPTOR_WORKER_THREADS .env || { [ "$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)" -lt 1500000 ] && echo "RIPRAPTOR_WORKER_THREADS=2" >> .env || true; }
# The Google Analytics id the site used until 30 Oct 2026 reached no stream of the owner's: a copy of it in .env
# would keep sending there, so it goes and the site's default (settings.py) applies.
sed -i '/^RIPRAPTOR_GA_ID=G-LGFBHYP6JC$/d' .env
# Root reads the few settings it needs from .env as text, and never runs it as a script.
env_value() { sed -n "s/^$1=//p" .env | tail -n 1 | sed -e 's/^["'"'"']//' -e 's/["'"'"']$//'; }
# The site refuses to start with .env.example's stand-in key, or one under 50 characters (ripraptor/settings.py), so
# such a key is replaced here with a long random one. Signed links already sent stop working and staff sign in again.
KEY="$(env_value DJANGO_SECRET_KEY)"
if [ "$KEY" = replace-with-a-long-random-string ] || [ "${#KEY}" -lt 50 ] || [ "$(printf '%s' "$KEY" | fold -w1 | sort -u | wc -l)" -lt 5 ]; then
  sed -i '/^DJANGO_SECRET_KEY=/d' .env
  echo "DJANGO_SECRET_KEY=$(python3 -I -c "import secrets; print(secrets.token_urlsafe(50))")" >> .env
  echo "The secret key in .env was the example one or too short, so a new one was written. Staff sign in again."
fi
unset KEY
chown root:ripraptor .env && chmod 640 .env
# Every manage.py step runs as ripraptor, with .env loaded by ripraptor's own shell.
site() { sudo -u ripraptor bash -c 'cd "$0" && set -a && . ./.env && set +a && exec "$@"' "$DIR" "$@"; }

# ripraptor's own folders. The cache the web app and every cron command share (RIPRAPTOR_CACHE_DIR's default) must
# be ripraptor's: a folder the site cannot write falls back to a cache per process. chown -R changes a link itself,
# never what it points to.
mkdir -p /var/lib/ripraptor media staticfiles
chown -R ripraptor:ripraptor /var/lib/ripraptor media staticfiles
sudo -u ripraptor mkdir -p /var/lib/ripraptor/cache /var/lib/ripraptor/backups
RIPRAPTOR_CACHE_DIR="$(env_value RIPRAPTOR_CACHE_DIR)"
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
  site .venv/bin/python manage.py compact_db
  site .venv/bin/python manage.py backup_db --keep 5
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
site .venv/bin/python manage.py migrate -v0
# Football cards became Sports cards at /games/sports-cards/, filed by product line (catalogue/sports.py). The
# address moves before the language and type steps below read it, with the background reader stopped first so
# its old code never meets the new address (it is started again at the end). The old pages send visitors on.
# The reader's safety net (deploy/heal.sh), in place before the reader is stopped: root's cron starts the reader within
# five minutes whenever it is found not running, whatever became of this script. It holds off only while the mark
# below is fresh (20 minutes), so an update that hangs or is killed outright (which no trap survives) costs 25 minutes
# of reading at most, not a night. On 21 Nov 2026 the reader stayed stopped from 18:15 until the owner looked the
# next morning: "This needs to self heal or recover or not break as much".
install -m 755 deploy/heal.sh /usr/local/sbin/ripraptor-heal
printf '%s\n' 'PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin' '*/5 * * * * root /usr/local/sbin/ripraptor-heal' > /etc/cron.d/ripraptor-heal
chmod 644 /etc/cron.d/ripraptor-heal
touch /run/ripraptor-updating
systemctl stop ripraptor-worker 2>/dev/null || true
# Whatever ends this script from here (a step that fails, the console closed or reconnected, Ctrl+C), the reader
# starts again on the way out: it is only started at the very end otherwise. On 18 Nov 2026 an update cut short
# after this point left it stopped overnight, with only the hourly fallback reading the shops. On a normal finish
# it is already running by then and this only takes the mark away.
trap 'rm -f /run/ripraptor-updating; systemctl start ripraptor-worker 2>/dev/null || true' EXIT
# Every step while the reader is stopped has a time limit: one that hangs is ended, which ends the script and starts
# the reader on the way out, where before it would have waited for ever with the reader stopped.
STEP="timeout -k 30 900"
site $STEP .venv/bin/python manage.py file_sports_cards
site $STEP .venv/bin/python manage.py backfill_restocks >/dev/null
site $STEP .venv/bin/python manage.py set_languages >/dev/null
site $STEP .venv/bin/python manage.py set_types >/dev/null
site $STEP .venv/bin/python manage.py collectstatic --noinput -v0
site $STEP .venv/bin/python manage.py setup_shops
# Products added by hand rather than by a shop read, so they are on the site before any shop lists them.
site $STEP .venv/bin/python manage.py import_products catalogue/data/union_arena.csv >/dev/null
[ -f /var/lib/ripraptor/dbip-country.mmdb ] || site $STEP .venv/bin/python manage.py fetch_geoip || true

# Scripts that ran our pages left out of people and clicks (catalogue/automated.py), now rather than at the hour, so
# the deals rails and the stock checks stop following them at once.
site timeout -k 30 600 .venv/bin/python manage.py classify_visits || true
# Each product's Price check for lists (the Fairest price sort, the gauge on cards), now rather than at the hour.
site timeout -k 30 600 .venv/bin/python manage.py refresh_price_checks || true
# How many packs, and cards a pack, each product holds, from the shops' descriptions: now rather than at the hour.
# Both before the cached lists are emptied and built again below, so the home rows and Cheapest per card read them.
site timeout -k 30 600 .venv/bin/python manage.py refresh_contents || true

cp deploy/ripraptor.service /etc/systemd/system/ripraptor.service
systemctl daemon-reload
systemctl enable -q --now ripraptor
systemctl restart ripraptor
# The cache folder outlives a restart, so the new code would read lists the old code pickled for up
# to five minutes: a query per row for a field a migration added, or an error for one it now sets.
# Empty it once the old workers have stopped; clearing before the restart would let them refill it.
site $STEP .venv/bin/python manage.py shell -c 'from django.core.cache import cache; cache.clear()'
# Then build the lists the home, Deals and drops pages read, so the first visitors after a deploy do not wait
# for them. A failure leaves them to be built by the first visitor, as before.
site $STEP .venv/bin/python manage.py warm_pages || true
# Cardmarket's catalogue and prices, once, when none are held yet, so matching can start without waiting for the
# daily read at 6:44. A failure leaves it to that read.
site .venv/bin/python manage.py shell -c 'from catalogue.models import CardmarketProduct; raise SystemExit(0 if CardmarketProduct.objects.exists() else 1)' || site timeout -k 30 900 .venv/bin/python manage.py import_cardmarket || true

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
# indexnow runs hourly at 25 past on its own lock: it sends the product pages that changed to Bing and the other
# IndexNow search engines (catalogue/indexnow.py).
# tidy_referrals runs hourly at 55 past on its own lock: it retries refer a friend address emails and deletes
# details the offer no longer needs (see the Terms page).
# warm_pages --due runs at 1 past the fives on its own lock: it builds the cached page lists again when an
# import or an edit cleared them or they are nearly expired, so the floor holds when the reader is not running.
# The alerts start at 3 past the tens and the judge at 2 past the fives, so no more than two of these
# Django processes start in the same minute: on 14 Oct 2026 three at once beside the reader and the
# site ran a 1 GB server out of memory.
# Every command runs at a lower priority than the website (RIPRAPTOR_NICE, read as each command starts), so a visitor's
# page is served first when a scheduled job and the website both want a processor.
# Every command runs under timeout, set to its budget plus five minutes: a command that hangs
# while holding a lock would otherwise make every later run behind it give up silently.
# The snapshot, the delivery check and tidy_all put timeout inside flock, so time spent waiting
# for a long import does not use up their limit.
# The delivery check reads every shop's charges on Sundays at 3:30, and at 4:30 every day the shops whose
# standard charge is still unknown, waiting up to 30 minutes for the import lock and otherwise trying the next day.
# Cardmarket's sealed catalogue and price guide are read daily at 6:44 (after Cardmarket's own early update), and Claude
# matches them to our products every 15 minutes from 7 past, on its own lock so it never waits behind the judge.
# Claude looks for the RRPs of products without one daily at 7:15, on its own lock (find_rrps).
# deploy/crontab carries the same lines; catalogue/tests_ops.py fails when they differ.
echo "PYTHONUNBUFFERED=1
RIPRAPTOR_NICE=10
0 * * * *  cd $DIR && set -a && . ./.env && set +a && timeout -k 60 3300 flock -w 1800 /tmp/ripraptor-import.lock .venv/bin/python manage.py import_prices --due --if-worker-dead 30 >> /var/log/ripraptor-import.log 2>&1
5 * * * *  cd $DIR && set -a && . ./.env && set +a && flock -n /tmp/ripraptor-tidy.lock flock -w 1800 /tmp/ripraptor-import.lock timeout -k 60 1500 .venv/bin/python manage.py tidy_all >> /var/log/ripraptor-import.log 2>&1
15 0 * * * cd $DIR && set -a && . ./.env && set +a && flock /tmp/ripraptor-import.lock timeout -k 60 1800 .venv/bin/python manage.py snapshot_daily_prices >> /var/log/ripraptor-import.log 2>&1
30 3 * * 0 cd $DIR && set -a && . ./.env && set +a && flock /tmp/ripraptor-import.lock timeout -k 60 3000 .venv/bin/python manage.py check_delivery --apply >> /var/log/ripraptor-import.log 2>&1
30 4 * * * cd $DIR && set -a && . ./.env && set +a && flock -w 1800 /tmp/ripraptor-import.lock timeout -k 60 3000 .venv/bin/python manage.py check_delivery --apply --unknown >> /var/log/ripraptor-import.log 2>&1
*/10 * * * * cd $DIR && set -a && . ./.env && set +a && timeout -k 30 540 flock -n /tmp/ripraptor-import.lock .venv/bin/python manage.py watch_stock --if-worker-dead 30 >> /var/log/ripraptor-import.log 2>&1
3-59/10 * * * * cd $DIR && set -a && . ./.env && set +a && timeout -k 30 540 flock -n /tmp/ripraptor-alerts.lock .venv/bin/python manage.py send_stock_alerts >> /var/log/ripraptor-import.log 2>&1
9-59/15 * * * * cd $DIR && set -a && . ./.env && set +a && timeout -k 30 540 flock -n /tmp/ripraptor-drops.lock .venv/bin/python manage.py send_drop_alerts >> /var/log/ripraptor-import.log 2>&1
4-59/15 * * * * cd $DIR && set -a && . ./.env && set +a && timeout -k 30 540 flock -n /tmp/ripraptor-search-alerts.lock .venv/bin/python manage.py send_search_alerts >> /var/log/ripraptor-import.log 2>&1
8-58/10 * * * * cd $DIR && set -a && . ./.env && set +a && timeout -k 30 300 flock -n /tmp/ripraptor-discord.lock .venv/bin/python manage.py post_discord >> /var/log/ripraptor-import.log 2>&1
2-59/5 * * * * cd $DIR && set -a && . ./.env && set +a && timeout -k 30 900 flock -n /tmp/ripraptor-judge.lock .venv/bin/python manage.py judge_checks >> /var/log/ripraptor-import.log 2>&1
51 6 * * * cd $DIR && set -a && . ./.env && set +a && flock -w 1200 /tmp/ripraptor-judge.lock timeout -k 30 1200 .venv/bin/python manage.py check_set_retail >> /var/log/ripraptor-import.log 2>&1
44 6 * * * cd $DIR && set -a && . ./.env && set +a && timeout -k 60 900 .venv/bin/python manage.py import_cardmarket >> /var/log/ripraptor-import.log 2>&1
7-59/15 * * * * cd $DIR && set -a && . ./.env && set +a && flock -n /tmp/ripraptor-cardmarket.lock timeout -k 30 840 .venv/bin/python manage.py match_cardmarket >> /var/log/ripraptor-import.log 2>&1
6-59/10 * * * * cd $DIR && set -a && . ./.env && set +a && timeout -k 30 540 flock -n /tmp/ripraptor-pages.lock .venv/bin/python manage.py check_new_pages >> /var/log/ripraptor-import.log 2>&1
15 7 * * * cd $DIR && set -a && . ./.env && set +a && timeout -k 30 1800 flock -n /tmp/ripraptor-rrp.lock .venv/bin/python manage.py find_rrps >> /var/log/ripraptor-import.log 2>&1
20 * * * * cd $DIR && set -a && . ./.env && set +a && timeout -k 30 600 .venv/bin/python manage.py check_worker >> /var/log/ripraptor-import.log 2>&1
40 0 * * * cd $DIR && set -a && . ./.env && set +a && timeout -k 60 1800 .venv/bin/python manage.py backup_db --keep 5 >> /var/log/ripraptor-import.log 2>&1
45 4 5 * * cd $DIR && set -a && . ./.env && set +a && timeout -k 60 1800 .venv/bin/python manage.py fetch_geoip >> /var/log/ripraptor-import.log 2>&1
55 * * * * cd $DIR && set -a && . ./.env && set +a && timeout -k 30 600 flock -n /tmp/ripraptor-refer.lock .venv/bin/python manage.py tidy_referrals >> /var/log/ripraptor-import.log 2>&1
25 * * * * cd $DIR && set -a && . ./.env && set +a && timeout -k 30 300 flock -n /tmp/ripraptor-indexnow.lock .venv/bin/python manage.py indexnow >> /var/log/ripraptor-import.log 2>&1
1-59/5 * * * * cd $DIR && set -a && . ./.env && set +a && timeout -k 30 240 flock -n /tmp/ripraptor-warm.lock .venv/bin/python manage.py warm_pages --due >> /var/log/ripraptor-import.log 2>&1" | crontab -u ripraptor -
touch /var/log/ripraptor-import.log && chown ripraptor /var/log/ripraptor-import.log

# The background reader. A shop never read before has no next read, so on its first start it reads
# every shop; nothing else needs starting. It is restarted after the code and the cache are updated.
cp deploy/ripraptor-worker.service /etc/systemd/system/ripraptor-worker.service
# The reader's processor ceiling (CPUQuota in the unit: 50, half of one processor, unless .env says otherwise).
# RIPRAPTOR_WORKER_CPU_PERCENT is a whole number of percent of one processor, 10 to 400; anything else is left out.
WORKER_CPU="$(env_value RIPRAPTOR_WORKER_CPU_PERCENT)"
case "$WORKER_CPU" in
  ''|*[!0-9]*) ;;
  *) if [ "$WORKER_CPU" -ge 10 ] && [ "$WORKER_CPU" -le 400 ]; then
       sed -i "s/^CPUQuota=.*/CPUQuota=${WORKER_CPU}%/" /etc/systemd/system/ripraptor-worker.service
     fi ;;
esac
systemctl daemon-reload
systemctl enable -q ripraptor-worker
systemctl restart ripraptor-worker

echo
echo "Done. https://$DOMAIN should answer within a minute (Caddy fetches the certificate)."
echo "Create your admin login with:"
echo "  cd $DIR && sudo -u ripraptor bash -c 'set -a; . ./.env; set +a; .venv/bin/python manage.py createsuperuser'"
echo "Prices are being read now; watch with: journalctl -fu ripraptor-worker"
RIPRAPTOR_WORKER_THREADS="$(env_value RIPRAPTOR_WORKER_THREADS)"
echo "The background reader runs ${RIPRAPTOR_WORKER_THREADS:-4} jobs at once on this server's $(( $(awk '/^MemTotal:/ {print $2}' /proc/meminfo) / 1024 )) MB of memory."
echo "It may use up to $(sed -n 's/^CPUQuota=//p' /etc/systemd/system/ripraptor-worker.service) of one of this server's $(nproc) processors."
