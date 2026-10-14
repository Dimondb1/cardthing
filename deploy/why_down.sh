#!/usr/bin/env bash
# What happened to the site: a short, read-only report for when it was down. It changes nothing.
# Run it as root on the server (the same console as the update command):
#
#   curl -fsSL https://raw.githubusercontent.com/Dimondb1/cardthing/claude/compassionate-edison-aot3li/deploy/why_down.sh | bash
#
# Every time it prints is UK time. It reads the last two days of the system journal, the code updates,
# the import log, memory, disk and the running processes. Sections that found nothing say (nothing).
# Pages that showed a server error leave no trace here: Django logs only the catalogue logger, and
# neither gunicorn nor Caddy keeps an access log.
( export TZ=Europe/London PAGER=cat GIT_PAGER=cat SYSTEMD_PAGER=cat
S='2 days ago'; D=/srv/ripraptor; L=/var/log/ripraptor-import.log
h() { printf '== %s ==\n' "$*"; }
show() { o=$(cat); if [ -n "$o" ]; then printf '%s\n' "$o"; else echo '(nothing)'; fi; }
j() { journalctl --no-pager -q -o short --since "$S" "$@" 2>/dev/null | sed -E 's/^(\S+ +\S+ \S+) \S+ /\1 /; s/\[[0-9-]+ [0-9:]+ [+-][0-9]{4}\] //'; }
g() { git -c safe.directory="$D" -C "$D" "$@" 2>/dev/null || runuser -u ripraptor -- git -C "$D" "$@"; }
h "1 Now (UK time) and last boots"
echo "now $(date '+%a %d %b %H:%M %Z'), booted $(uptime -s)"; uptime
{ last -x reboot shutdown 2>/dev/null | grep -E '^(reboot|shutdown)' || journalctl --list-boots --no-pager | tail -n 3; } | head -n 3 | show
h "2 Code updates, newest first (each update = 2 lines: before, after)"
g reflog -n 5 --date=format-local:'%a %d %b %H:%M' --format='%gd %h %<(36,trunc)%s' 2>&1 | show
for f in ripraptor ripraptor-worker; do echo "$(date -r /etc/systemd/system/$f.service '+%a %d %b %H:%M' 2>/dev/null || echo missing) update copied $f.service"; done
echo "backups: $(printf '%s\n' /var/lib/ripraptor/backups/* | grep -o '20[0-9]\{6\}-[0-9]\{4\}' | sort | tail -n 4 | tr '\n' ' ')"
grep -h -E '^(Start-Date|Commandline)' /var/log/apt/history.log 2>/dev/null | tail -n 2 | cut -c1-100 | show
h "3 Services now"
for u in ripraptor ripraptor-worker caddy; do echo "$u: $(systemctl show "$u" -p ActiveState -p ActiveEnterTimestamp -p NRestarts 2>/dev/null | tr '\n' ' ' | sed 's/ActiveState=//; s/ActiveEnterTimestamp=/since /; s/NRestarts=/auto-restarts=/')"; done
h "4 Web app (gunicorn): stops, starts, crashes, timeouts"
j -u ripraptor | grep -E 'systemd\[1\]|WORKER TIMEOUT|was sent|exited with code|Booting worker|Worker failed|Exception in worker|\[ERROR\]|\[CRITICAL\]' | grep -v -E 'Consumed|Deactivated' | tail -n 10 | cut -c1-100 | show
echo "page views that met a locked database: $(j -u ripraptor | grep -c 'database is locked')"
h "5 Background reader and Caddy: stops, starts, kills"
j -u ripraptor-worker | grep -E 'systemd\[1\]|Background reader started' | grep -v -E 'Consumed|Deactivated' | tail -n 6 | cut -c1-100 | show
j -u caddy | grep 'systemd\[1\]' | grep -v -E 'Consumed|Deactivated' | tail -n 4 | cut -c1-100 | show
h "6 Caddy could not reach the site (visitors got 502): busiest minutes, first and last"
c=$(j -u caddy | grep -E 'dial tcp|"status":50[0-9]|upstream|reset by peer')
if [ -z "$c" ]; then echo '(nothing)'; else
  echo "total $(printf '%s\n' "$c" | grep -c .)"
  printf '%s\n' "$c" | awk 'NF {print $1, $2, substr($3, 1, 5)}' | uniq -c | sort -rn | head -n 6 | sort -k2,2M -k3,3n -k4,4
  printf '%s\n' "$c" | sed -n '1p;$p' | sed -E 's/^(\S+ +\S+ \S+) .*"msg":"([^"]*)".*/\1 \2/' | cut -c1-100
fi
h "7 Out-of-memory kills (all boots)"
j _TRANSPORT=kernel | grep -i -E 'out of memory|killed process' | tail -n 4 | cut -c1-100 | show
h "8 Import log: locked database and errors"
if [ -f "$L" ]; then echo "lines $(wc -l < "$L"), locked $(grep -c 'database is locked' "$L"), tracebacks $(grep -c Traceback "$L")"
  grep -n -E 'database is locked|^[A-Za-z_.]*(Error|Exception)\b' "$L" | tail -n 4 | cut -c1-100 | show; else echo '(no import log)'; fi
h "9 Memory (MB), disk, database files, load"
free -m; df -h / | tail -n 1
echo "db: $(du -h /var/lib/ripraptor/db.sqlite3* 2>/dev/null | tr '\n' ' ')"
echo "load $(cut -d' ' -f1-3 /proc/loadavg) on $(nproc) CPUs"
h "10 Site processes now, oldest first (age, memory)"
ps -C gunicorn,python,python3 --sort=start_time -o etime=,rss=,args= 2>/dev/null | awk '{printf "%11s %4dMB", $1, $2 / 1024; $1 = ""; $2 = ""; print substr($0, 2, 70)}' | head -n 6 | show
)
