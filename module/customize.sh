#!/system/bin/sh
# APatch install-time script: install the client launcher into Termux's PATH.
#
# The client is modules that import each other (apatch-chroot.py imports pages
# and render, and the launcher imports apatch_chroot_launcher), so they must
# live together. We install a small launcher into Termux's bin that adds the
# module's client dir to sys.path and runs it — keeping the files in one place
# instead of scattering them across PATH.
#
# MODDIR is hardcoded, not derived from $0: APatch invokes this script in a way
# that makes ${0%/*} unreliable (it resolved to a bare "sh"), which produced a
# launcher pointing at "sh/client". The install location is fixed, so pin it.

MODDIR=/data/adb/modules/apatch_chroot
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
    echo "apatch-chroot client installed to $BINDIR/apatch-chroot (client dir: $CLIENT_DIR)"
else
    echo "Termux bin dir not found; client not installed."
fi

# Containers live outside Termux so the daemon (root) can own them.
mkdir -p /data/apatch-chroot/containers
chmod 755 /data/apatch-chroot

exit 0
