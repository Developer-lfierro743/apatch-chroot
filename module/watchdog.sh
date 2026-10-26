#!/system/bin/sh
# APatch watchdog: keep the privileged chroot daemon running across boots.
#
# service.sh launches this detached and exits; this loop then owns the daemon's
# lifecycle. A PID-file lock guarantees exactly one watchdog (pgrep -f is
# unreliable here: the loop's own sleep/pgrep children match the pattern, and
# duplicate watchdogs race to start the daemon).
#
# Handles three failure modes seen on device:
#   1. boot timing — Termux python is not runnable until FBE unlocks, so we
#      wait for the interpreter before starting (not just the boot flag);
#   2. daemon death — if the daemon exits, restart it within one interval;
#   3. abrupt shutdown — Android can SIGKILL processes at any moment. This
#      loop is the only thing that survives to relaunch the daemon after a
#      reboot, so it must be resilient to its own death: the lock dir is
#      cleaned on start so a stale lock never blocks a fresh boot, and the
#      daemon is started with setsid so it outlives this shell if this shell
#      is killed before the loop notices.

MODDIR=/data/adb/modules/apatch_chroot
DAEMON_DIR=/data/apatch-chroot/daemon
DAEMON=$DAEMON_DIR/daemon.py
PYTHON=/data/data/com.termux/files/usr/bin/python3
SOCK=/data/local/tmp/apatch-chroot.sock
LOG=$MODDIR/daemon.log
LOCKDIR=$MODDIR/watchdog.lock

# Atomic single-instance lock via mkdir. A plain pidfile check is racy: APatch
# can trigger service.sh several times before the first watchdog writes its
# pidfile, so N watchdogs all pass "not locked" and spawn N daemons fighting
# over one socket. mkdir is atomic on every filesystem: exactly one wins.
# On a fresh boot after an abrupt shutdown, the lock dir may be stale (the
# old watchdog was SIGKILLed mid-write). We detect that: if the pid inside is
# dead, remove the stale lock and re-acquire.
if ! mkdir "$LOCKDIR" 2>/dev/null; then
    old=$(cat "$LOCKDIR/pid" 2>/dev/null)
    if [ -n "$old" ] && kill -0 "$old" 2>/dev/null; then
        exit 0
    fi
    rm -rf "$LOCKDIR"
    mkdir "$LOCKDIR" 2>/dev/null || exit 0
fi
echo $$ > "$LOCKDIR/pid"

# Trap EXIT so the lock dir is always cleaned when this watchdog exits
# (normal exit, or if the shell receives a signal it can handle). This
# prevents a stale lock from blocking the next watchdog on a clean restart.
# SIGKILL cannot be trapped — that's fine, the stale-lock detection above
# handles the abrupt-shutdown case on the next boot.
cleanup_lock() {
    rm -rf "$LOCKDIR"
}
trap cleanup_lock EXIT

# Track the daemon pid so we can distinguish "daemon died, restart it" from
# "watchdog itself died, nothing to do". The loop's own children (pgrep,
# sleep) do not match the daemon cmdline, so pgrep -f is safe here.
DAEMON_PID=""

while true; do
    if [ -x "$PYTHON" ]; then
        # Keep the client + broker in sync with the flashed module on every
        # boot. The module dir is system_file context (app can't read it), so
        # the daemon (root) copies them into Termux's app-readable lib. The
        # broker MUST be app-readable because the CLI (Termux uid) spawns it.
        # This also fixes client drift across reflashes.
        CLIENT_SRC=$MODDIR/client
        CLIENT_DST=/data/data/com.termux/files/usr/lib/apatch-chroot
        if [ -d "$CLIENT_SRC" ]; then
            mkdir -p "$CLIENT_DST" 2>/dev/null
            for cf in "$CLIENT_SRC"/*.py; do
                [ -f "$cf" ] && cp -f "$cf" "$CLIENT_DST/" 2>/dev/null
            done
            chmod 644 "$CLIENT_DST/"*.py 2>/dev/null
        fi
        # The broker lives in the daemon dir; copy it beside the client so the
        # CLI's _start_broker() finds it in app-readable space.
        if [ -f "$DAEMON_DIR/broker.py" ]; then
            cp -f "$DAEMON_DIR/broker.py" "$CLIENT_DST/broker.py" 2>/dev/null
            chmod 644 "$CLIENT_DST/broker.py" 2>/dev/null
        fi
        # Helper scripts (fix-login.sh, setup-desktop.sh) — copy into Termux's
        # bin so users can run them after an update. The module dir is
        # system_file context (app can't read it), so they must be copied out.
        if [ -d "$MODDIR/scripts" ]; then
            SCRIPTS_DST=/data/data/com.termux/files/usr/bin
            mkdir -p "$SCRIPTS_DST" 2>/dev/null
            for sf in "$MODDIR"/scripts/*; do
                [ -f "$sf" ] || continue
                cp -f "$sf" "$SCRIPTS_DST/$(basename "$sf")" 2>/dev/null
                chmod 755 "$SCRIPTS_DST/$(basename "$sf")" 2>/dev/null
            done
        fi

        # Remove a stale socket from a previous (abruptly-killed) daemon so
        # the new one can bind cleanly. Without this, an abrupt shutdown can
        # leave /data/local/tmp/apatch-chroot.sock in a half-bound state and
        # the fresh daemon's bind() fails with EADDRINUSE.
        if [ -S "$SOCK" ]; then
            rm -f "$SOCK" 2>/dev/null
        fi

        # pgrep -f matches the daemon cmdline; this watchdog's cmdline is
        # "sh .../watchdog.sh" and does not contain daemon.py, so no self-match.
        if ! pgrep -f "$DAEMON" >/dev/null 2>&1; then
            echo "[watchdog] starting daemon $(date)" >> "$LOG"
            # setsid detaches the daemon from this shell's process group so
            # it survives even if this watchdog shell is killed before the
            # loop notices. The daemon has its own signal handling now.
            setsid "$PYTHON" "$DAEMON" >> "$LOG" 2>&1 &
            DAEMON_PID=$!
        fi
    fi
    sleep 15
done
