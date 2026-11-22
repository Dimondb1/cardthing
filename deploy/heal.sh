#!/usr/bin/env bash
# Starts the background reader when it is found not running. install.sh copies this to /usr/local/sbin/ripraptor-heal
# and root's cron (/etc/cron.d/ripraptor-heal) runs it every five minutes.
#
# systemd already restarts the reader when it exits or its heartbeat stops (ripraptor-worker.service). What systemd
# never does is start a unit that was stopped on purpose, and an update stops the reader and starts it again at its
# end: an update that hung or was killed in between left it stopped until someone noticed (18 Nov 2026 overnight, and
# 21 Nov 2026 from 18:15 until the next morning; the owner: "This needs to self heal or recover or not break as much").
# This check does not depend on the update, the site or the reader's own code, so it holds when they fail.
#
# It leaves the reader alone when:
# - it is running, or systemd is already starting or stopping it;
# - an update is in progress: install.sh touches $MARK when it stops the reader and removes it on the way out. A mark
#   older than HOLD_MINUTES is an update that died, and no longer counts;
# - it was switched off for good (systemctl disable --now ripraptor-worker), the way to keep it stopped.
# Pause all on the Crawl health page stops its reads while it keeps running, and is not this check's business.
set -u
UNIT="${RIPRAPTOR_HEAL_UNIT:-ripraptor-worker}"
MARK="${RIPRAPTOR_HEAL_MARK:-/run/ripraptor-updating}"
LOG="${RIPRAPTOR_HEAL_LOG:-/var/log/ripraptor-import.log}"
HOLD_MINUTES="${RIPRAPTOR_HEAL_HOLD_MINUTES:-20}"

state="$(systemctl show "$UNIT" -p ActiveState --value 2>/dev/null || true)"
case "$state" in
  active|activating|deactivating|reloading) exit 0 ;;
esac
if [ -e "$MARK" ] && [ -n "$(find "$MARK" -mmin "-$HOLD_MINUTES" 2>/dev/null)" ]; then
  exit 0
fi
systemctl is-enabled -q "$UNIT" 2>/dev/null || exit 0
systemctl reset-failed "$UNIT" 2>/dev/null || true
if systemctl start "$UNIT" 2>/dev/null; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') heal: the background reader was not running (${state:-unknown}); started it" >> "$LOG"
else
  echo "$(date '+%Y-%m-%d %H:%M:%S') heal: the background reader was not running (${state:-unknown}) and could not be started" >> "$LOG"
fi
