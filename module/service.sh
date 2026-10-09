#!/system/bin/sh
# APatch late_start service: hand off to the daemon watchdog.
#
# Runs as root, outside the app seccomp filter — that is the whole point of
# the module; all mount/chroot work happens here.
#
# We wait until BOTH Android has booted AND the Termux prefix is unlocked
# (Termux's home is credential-encrypted, so python3 is not executable until
# FBE unlocks even after sys.boot_completed=1), then launch watchdog.sh which
# owns the daemon's lifecycle — starting it and restarting it if it ever dies.
# Bounded wait so a stuck boot cannot hang the service forever.

MODDIR=/data/adb/modules/apatch_chroot
DAEMON_DIR=/data/apatch-chroot/daemon
PYTHON=/data/data/com.termux/files/usr/bin/python3
WATCHDOG=$MODDIR/watchdog.sh

# Install all daemon files (first boot / update). These live under /data (not
# the encrypted Termux home), so this works as soon as /data is mounted.
mkdir -p "$DAEMON_DIR"
for f in daemon.py daemon_cmds.py registry.py layer_extract.py arch.py mounts.py syscalls.py; do
    cp -f "$MODDIR/daemon/$f" "$DAEMON_DIR/" 2>/dev/null
done
chmod 755 "$DAEMON_DIR"

# Wait until Android has booted AND the Termux interpreter is runnable
# (max ~120s). Both gates, because either alone is not enough.
i=0
while [ "$i" -lt 120 ]; do
    bc=$(getprop sys.boot_completed 2>/dev/null)
    if [ "$bc" = "1" ] && [ -x "$PYTHON" ]; then
        break
    fi
    sleep 1
    i=$((i + 1))
done

if [ ! -x "$PYTHON" ]; then
    echo "Termux python not available after wait; watchdog not started" > "$MODDIR/daemon.log"
    exit 0
fi

# Don't start a second watchdog if one is already running (e.g. after a
# module re-enable without a reboot).
if pgrep -f "$WATCHDOG" >/dev/null 2>&1; then
    exit 0
fi

# Launch the watchdog detached; it survives after this script returns and
# keeps the daemon alive.
setsid sh "$WATCHDOG" >/dev/null 2>&1 &

exit 0
