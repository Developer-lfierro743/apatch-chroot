#!/system/bin/sh
# APatch install-time script: install the client into Termux's PATH.

MODDIR=${0%/*}
BINDIR=/data/data/com.termux/files/usr/bin
CLIENT_SRC="$MODDIR/client/apatch-chroot.py"

if [ -d "$BINDIR" ]; then
    cp -f "$CLIENT_SRC" "$BINDIR/apatch-chroot"
    chmod 755 "$BINDIR/apatch-chroot"
    # Point the client's shebang at Termux python if not already.
    echo "apatch-chroot client installed to $BINDIR/apatch-chroot"
else
    echo "Termux bin dir not found; client not installed."
fi

# Containers live outside Termux so the daemon (root) can own them.
mkdir -p /data/apatch-chroot/containers
chmod 755 /data/apatch-chroot

exit 0
