#!/system/bin/sh
# APatch watchdog: keep the privileged chroot daemon running across boots.
#
# service.sh launches this detached and exits; this loop then owns the daemon's
# lifecycle. It handles the two failure modes we hit on device:
#   1. boot timing — Termux's python is not runnable until FBE is unlocked, so
#      we wait for the interpreter itself before starting (not just boot flag);
#   2. daemon death — if the daemon ever exits, we restart it.
# Checking every 15s is cheap and catches a crash within one interval.

MODDIR=/data/adb/modules/apatch_chroot
DAEMON_DIR=/data/apatch-chroot/daemon
DAEMON=$DAEMON_DIR/daemon.py
PYTHON=/data/data/com.termux/files/usr/bin/python3
SOCK=/data/data/com.termux/files/usr/tmp/apatch-chroot.sock
LOG=$MODDIR/daemon.log

while true; do
    if [ -x "$PYTHON" ]; then
        # pgrep -f matches the daemon's own cmdline; the watchdog's cmdline is
        # this script's path and does not contain daemon.py, so no self-match.
        if ! pgrep -f "$DAEMON" >/dev/null 2>&1; then
            rm -f "$SOCK" 2>/dev/null
            echo "[watchdog] starting daemon $(date)" >> "$LOG"
            setsid "$PYTHON" "$DAEMON" >> "$LOG" 2>&1 &
        fi
    fi
    sleep 15
done
