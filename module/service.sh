#!/system/bin/sh
# APatch late_start service: start the privileged chroot daemon.
#
# Runs as root, outside the app seccomp filter — that is the whole point of
# the module; all mount/chroot work happens here.
#
# We cannot start until BOTH Android has booted AND the Termux prefix is
# unlocked. Termux's home is credential-encrypted (FBE), so at early boot
# /data/data/com.termux/files/usr/bin/python3 does not exist yet even after
# sys.boot_completed=1 — the daemon would fail with "can't execute python3".
# So we poll for the interpreter itself, not just the boot flag, with a
# bounded wait so a stuck boot cannot hang the service forever.

MODDIR=/data/adb/modules/apatch_chroot
DAEMON_DIR=/data/apatch-chroot/daemon
DAEMON=$DAEMON_DIR/daemon.py
PYTHON=/data/data/com.termux/files/usr/bin/python3

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
    echo "Termux python not available after wait; daemon not started" > "$MODDIR/daemon.log"
    exit 0
fi

# Kill any previous instance.
pkill -f "$DAEMON" 2>/dev/null

# Start the daemon, detached, logging to the module dir.
nohup "$PYTHON" "$DAEMON" > "$MODDIR/daemon.log" 2>&1 &

exit 0
