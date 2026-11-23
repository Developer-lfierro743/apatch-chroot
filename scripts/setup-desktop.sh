#!/bin/sh
# Install the APatch LXQt launcher inside an Ubuntu or Debian guest.
set -eu

launcher_only=false
if [ "${1:-}" = --launcher-only ]; then
    launcher_only=true
    shift
fi
name=${1:-ubuntu}
if [ "$#" -gt 1 ] || [ "${name#-}" != "$name" ]; then
    printf 'Usage: %s [--launcher-only] [CONTAINER_NAME]\n' "$0" >&2
    exit 2
fi

if [ "$(id -u)" != 0 ] || [ ! -x /usr/bin/dpkg ] || \
   ! grep -Eq '^ID=(ubuntu|debian)$' /etc/os-release; then
    printf '%s\n' 'Run this as root inside the Ubuntu or Debian guest, not in Termux.' >&2
    exit 1
fi

here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
if [ ! -f "$here/apatch-startlxqt.py" ]; then
    printf '%s\n' 'Copy apatch-startlxqt.py beside setup-desktop.sh inside the guest.' >&2
    exit 1
fi

if [ "$launcher_only" = false ]; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y --no-install-recommends \
        lxqt-core openbox qterminal pcmanfm-qt featherpad fonts-dejavu \
        python3 dbus dbus-x11 x11-utils pulseaudio-utils
fi

for tool in python3 startlxqt lxqt-session openbox dbus-daemon dbus-send xdpyinfo xprop; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        printf 'Guest is missing %s. Run setup-desktop.sh without --launcher-only to install dependencies.\n' "$tool" >&2
        exit 1
    fi
done

install -D -m 0755 "$here/apatch-startlxqt.py" /usr/local/bin/apatch-startlxqt
printf 'LXQt launcher installed. From Termux with Termux:X11 :0 running:\n'
printf '  apatch-chroot desktop %s --check\n  apatch-chroot desktop %s\n' "$name" "$name"
printf 'Set a guest user with setuser, or pass --root explicitly for a root session.\n'
