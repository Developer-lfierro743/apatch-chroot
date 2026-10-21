#!/bin/sh
# apatch-chroot desktop setup — installs a lightweight LXQt desktop.
#
# Run this INSIDE a container (via `apatch-chroot login <name>`), as root. It
# installs LXQt + a terminal + core utilities, then prints how to launch.
#
# LXQt is chosen over XFCE because it's lighter (better on a phone) and Qt-based
# (snappier). It needs: a window manager (openbox ships with LXQt), a terminal
# (qterminal), and DBus (session/notification backbone).
#
# After running this, launch the desktop from the host with:
#   termux-x11 :0 &
#   pulseaudio --start          # optional, for sound
#   apatch-chroot login <name> --shared-display --shared-audio
#   startlxqt
#
# This script is idempotent — safe to re-run. It only installs packages; it
# never touches the host.

set -e

NAME="${1:-}"   # optional: container name, only used for the final hint

echo "==> apatch-chroot desktop setup (LXQt)"

if [ "$(id -u)" != "0" ]; then
    echo "ERROR: run this as root inside the container (apatch-chroot login <name>)." >&2
    exit 1
fi

# Make sure we can resolve packages (the daemon drops in a resolv.conf, but be
# safe) and refresh the index.
echo "==> updating package index"
apt-get update -y || { echo "apt-get update failed — check DNS/network." >&2; exit 1; }

# LXQt core + openbox (its WM) + a terminal + dbus + a few essentials.
# tigervnc tools not needed (we use termux-x11), but x11 utils help debugging.
echo "==> installing LXQt desktop + terminal + essentials"
export DEBIAN_FRONTEND=noninteractive
apt-get install -y --no-install-recommends \
    lxqt \
    openbox \
    qterminal \
    dbus-x11 \
    lxqt-sudo \
    pcmanfm-qt \
    featherpad \
    x11-utils \
    xdg-utils \
    fonts-dejavu \
    || { echo "package install failed." >&2; exit 1; }

# A tiny launcher so `startlxqt` works even if the package didn't drop one.
if ! command -v startlxqt >/dev/null 2>&1; then
    echo "==> creating startlxqt launcher"
    cat > /usr/local/bin/startlxqt <<'LAUNCH'
#!/bin/sh
# Launch the LXQt session inside the container (display comes from termux-x11).
export DISPLAY="${DISPLAY:-:0}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp/runtime-$(id -u)}"
mkdir -p "$XDG_RUNTIME_DIR" && chmod 700 "$XDG_RUNTIME_DIR"
# Start a session dbus if none is running (LXQt needs it).
if [ -z "$DBUS_SESSION_BUS_ADDRESS" ]; then
    eval "$(dbus-launch --sh-syntax 2>/dev/null)" 2>/dev/null || true
    export DBUS_SESSION_BUS_ADDRESS DBUS_SESSION_BUS_PID
fi
exec startlxqt-core 2>/dev/null || exec lxqt-session
LAUNCH
    chmod +x /usr/local/bin/startlxqt
fi

echo
echo "==> Done. LXQt is installed."
echo
echo "To launch the desktop, from the HOST (Termux):"
echo "  termux-x11 :0 &"
echo "  pulseaudio --start            # optional, for sound"
echo "  apatch-chroot login ${NAME:-<container>} --shared-display --shared-audio"
echo "  startlxqt"
echo
echo "First launch can be slow (LXQt builds its config). If the screen is blank,"
echo "check DISPLAY is :0 and termux-x11 is running."
