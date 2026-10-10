#!/system/bin/sh
# APatch watchdog: keep the privileged chroot daemon running across boots.
#
# service.sh launches this detached and exits; this loop then owns the daemon's
# lifecycle. A PID-file lock guarantees exactly one watchdog (pgrep -f is
# unreliable here: the loop's own sleep/pgrep children match the pattern, and
# duplicate watchdogs race to start the daemon).
#
# Handles two failure modes seen on device:
#   1. boot timing — Termux python is not runnable until FBE unlocks, so we
#      wait for the interpreter before starting (not just the boot flag);
#   2. daemon death — if the daemon exits, restart it within one interval.

MODDIR=/data/adb/modules/apatch_chroot
DAEMON_DIR=/data/apatch-chroot/daemon
DAEMON=$DAEMON_DIR/daemon.py
PYTHON=/data/data/com.termux/files/usr/bin/python3
SOCK=/data/data/com.termux/files/usr/tmp/apatch-chroot.sock
LOG=$MODDIR/daemon.log
LOCKDIR=$MODDIR/watchdog.lock

# Atomic single-instance lock via mkdir. A plain pidfile check is racy: APatch
# can trigger service.sh several times before the first watchdog writes its
# pidfile, so N watchdogs all pass "not locked" and spawn N daemons fighting
# over one socket. mkdir is atomic on every filesystem: exactly one wins.
if ! mkdir "$LOCKDIR" 2>/dev/null; then
    old=$(cat "$LOCKDIR/pid" 2>/dev/null)
    if [ -n "$old" ] && kill -0 "$old" 2>/dev/null; then
        exit 0
    fi
    rm -rf "$LOCKDIR"
    mkdir "$LOCKDIR" 2>/dev/null || exit 0
fi
echo $$ > "$LOCKDIR/pid"

while true; do
    if [ -x "$PYTHON" ]; then
        # pgrep -f matches the daemon cmdline; this watchdog's cmdline is
        # "sh .../watchdog.sh" and does not contain daemon.py, so no self-match.
        if ! pgrep -f "$DAEMON" >/dev/null 2>&1; then
            rm -f "$SOCK" 2>/dev/null
            echo "[watchdog] starting daemon $(date)" >> "$LOG"
            setsid "$PYTHON" "$DAEMON" >> "$LOG" 2>&1 &
        fi
    fi
    sleep 15
done
