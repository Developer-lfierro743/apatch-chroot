#!/system/bin/sh
# APatch late_start service: start the privileged chroot daemon at boot.
# Runs as root, in a context outside the app seccomp filter — this is the
# whole point of the module. All mount/chroot work happens here.

MODDIR=${0%/*}
DAEMON_DIR=/data/apatch-chroot/daemon
DAEMON=$DAEMON_DIR/daemon.py
PYTHON=/data/data/com.termux/files/usr/bin/python3

# Install daemon files on first boot / update.
mkdir -p "$DAEMON_DIR"
cp -f "$MODDIR/daemon/daemon.py" "$DAEMON_DIR/" 2>/dev/null
cp -f "$MODDIR/daemon/daemon_cmds.py" "$DAEMON_DIR/" 2>/dev/null
chmod 755 "$DAEMON_DIR"

# Kill any previous instance.
pkill -f "$DAEMON" 2>/dev/null

# Start the daemon, detached, logging to the module dir.
nohup "$PYTHON" "$DAEMON" > "$MODDIR/daemon.log" 2>&1 &

# Signal APatch the service completed.
exit 0
