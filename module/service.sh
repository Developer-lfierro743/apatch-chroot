#!/system/bin/sh
# APatch late_start service: start the privileged chroot daemon, but only
# after Android has fully booted.
#
# Runs as root, outside the app seccomp filter — that is the whole point of
# the module; all mount/chroot work happens here.
#
# We deliberately wait for sys.boot_completed=1 before starting: the audio
# HAL, servicemanager and zygote must be up, and /data must be decrypted, or
# the daemon starts against a half-booted system. We poll rather than sleep a
# fixed time, and give up after a bounded wait so a stuck boot does not hang
# the service forever.

MODDIR=${0%/*}
DAEMON_DIR=/data/apatch-chroot/daemon
DAEMON=$DAEMON_DIR/daemon.py
PYTHON=/data/data/com.termux/files/usr/bin/python3

# Wait for boot to complete (max ~60s).
i=0
while [ "$i" -lt 60 ]; do
    bc=$(getprop sys.boot_completed 2>/dev/null)
    if [ "$bc" = "1" ]; then
        break
    fi
    sleep 1
    i=$((i + 1))
done

# Install all daemon files (first boot / update).
mkdir -p "$DAEMON_DIR"
for f in daemon.py daemon_cmds.py registry.py layer_extract.py arch.py mounts.py syscalls.py; do
    cp -f "$MODDIR/daemon/$f" "$DAEMON_DIR/" 2>/dev/null
done
chmod 755 "$DAEMON_DIR"

# Kill any previous instance.
pkill -f "$DAEMON" 2>/dev/null

# Start the daemon, detached, logging to the module dir.
nohup "$PYTHON" "$DAEMON" > "$MODDIR/daemon.log" 2>&1 &

exit 0
