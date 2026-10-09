#!/system/bin/sh
# APatch install-time script: install the client into Termux's PATH.
#
# The client is three modules that import each other (apatch-chroot.py imports
# pages and render), so they must live together. We install a small launcher
# into Termux's bin that runs the real client from the module dir, keeping the
# three files in one place instead of scattering them across PATH.

MODDIR=${0%/*}
BINDIR=/data/data/com.termux/files/usr/bin
PYTHON=/data/data/com.termux/files/usr/bin/python3
CLIENT_DIR=$MODDIR/client

if [ -d "$BINDIR" ]; then
    cat > "$BINDIR/apatch-chroot" <<EOF
#!$PYTHON
import sys
sys.path.insert(0, "$CLIENT_DIR")
from apatch_chroot_launcher import main
sys.exit(main(sys.argv))
EOF
    chmod 755 "$BINDIR/apatch-chroot"
    echo "apatch-chroot client installed to $BINDIR/apatch-chroot"
else
    echo "Termux bin dir not found; client not installed."
fi

# Containers live outside Termux so the daemon (root) can own them.
mkdir -p /data/apatch-chroot/containers
chmod 755 /data/apatch-chroot

exit 0
