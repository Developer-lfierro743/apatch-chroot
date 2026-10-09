#!/system/bin/sh
# APatch install-time script: install the client into Termux's own tree.
#
# The client must be readable by the Termux app. The module dir lives under
# /data/adb with an SELinux system_file context that the untrusted_app domain
# cannot read, so a launcher pointing there fails with ModuleNotFoundError.
# Instead we copy the client modules into Termux's usr/lib (app-readable) and
# drop a launcher in bin that adds that dir to sys.path.
#
# MODDIR is hardcoded, not derived from $0: APatch resolves ${0%/*} to a bare
# "sh", which produced a launcher pointing at "sh/client".

MODDIR=/data/adb/modules/apatch_chroot
TERMUX_USR=/data/data/com.termux/files/usr
BINDIR=$TERMUX_USR/bin
CLIENT_LIB=$TERMUX_USR/lib/apatch-chroot
PYTHON=$TERMUX_USR/bin/python3

# Copy the client into Termux's lib (readable by the app).
mkdir -p "$CLIENT_LIB"
cp -f "$MODDIR"/client/*.py "$CLIENT_LIB/" 2>/dev/null
chmod 644 "$CLIENT_LIB/"*.py 2>/dev/null

if [ -d "$BINDIR" ]; then
    cat > "$BINDIR/apatch-chroot" <<EOF
#!$PYTHON
import sys
sys.path.insert(0, "$CLIENT_LIB")
from apatch_chroot_launcher import main
sys.exit(main(sys.argv))
EOF
    chmod 755 "$BINDIR/apatch-chroot"
    echo "apatch-chroot client installed to $BINDIR/apatch-chroot (client lib: $CLIENT_LIB)"
else
    echo "Termux bin dir not found; client not installed."
fi

# Containers live outside Termux so the daemon (root) can own them.
mkdir -p /data/apatch-chroot/containers
chmod 755 /data/apatch-chroot

exit 0
