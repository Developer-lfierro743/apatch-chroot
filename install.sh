#!/data/data/com.termux/files/usr/bin/bash
# Install apatch-chroot as an APatch module by hand (no Manager, no zip).
#
# Copies the module into /data/adb/modules/apatch_chroot/ (the layout APatch
# and KernelSU both read), then runs customize.sh so the `apatch-chroot`
# launcher lands in Termux's bin. Reboot afterwards and service.sh starts the
# daemon after boot completes.
#
# Must run as root. Usage: sudo ./install.sh   (or: su -c ./install.sh)

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
MODDIR=/data/adb/modules/apatch_chroot

if [ "$(id -u)" -ne 0 ]; then
    echo "error: run as root (su -c ./install.sh)" >&2
    exit 1
fi

echo "installing to $MODDIR"
mkdir -p "$MODDIR/daemon" "$MODDIR/client"

cp -f "$HERE/module/module.prop"  "$MODDIR/"
cp -f "$HERE/module/service.sh"   "$MODDIR/"
cp -f "$HERE/module/customize.sh" "$MODDIR/"
cp -f "$HERE"/daemon/*.py  "$MODDIR/daemon/"
cp -f "$HERE"/client/*.py  "$MODDIR/client/"

chmod 755 "$MODDIR" "$MODDIR/daemon" "$MODDIR/client"
chmod 644 "$MODDIR/daemon/"*.py "$MODDIR/client/"*.py
chmod 755 "$MODDIR/service.sh" "$MODDIR/customize.sh"

# Install the client launcher into Termux bin now (customize.sh normally does
# this at flash time; here we invoke it directly).
sh "$MODDIR/customize.sh"

echo
echo "installed. reboot, then:  apatch-chroot ping"
