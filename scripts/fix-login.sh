#!/bin/sh
# apatch-chroot login fixer — silences the noisy profile errors on login.
#
# Ubuntu/Debian default /etc/profile + /root/.bashrc reference tools that a slim
# container image doesn't ship, so every `bash -l` login prints:
#   bash: groups/mountpoint/lesspipe/dircolors/mount: command not found
#   bash: /.cargo/env: No such file or directory
# These are harmless (the shell works), just ugly. This fixes the root causes:
#   1. install the missing packages (coreutils, util-linux, lesspipe)
#   2. guard the rust '.cargo/env' line in .bashrc so it only sources if present
# Run ONCE inside the container (as root), then log out and back in.
#
#   apatch-chroot login ubuntu
#   bash fix-login.sh
#   exit
#
# Idempotent — safe to re-run. Never touches the host.

set -e

if [ "$(id -u)" != "0" ]; then
    echo "ERROR: run as root inside the container (apatch-chroot login <name>)." >&2
    exit 1
fi

echo "==> installing missing tools referenced by the default profile"
export DEBIAN_FRONTEND=noninteractive
# coreutils: groups, dircolors. util-linux: mount, mountpoint. lesspipe: lesspipe.
apt-get update -y >/dev/null 2>&1 || true
apt-get install -y --no-install-recommends coreutils util-linux lesspipe 2>/dev/null \
    || echo "  (some packages unavailable — continuing)"

echo "==> guarding the rust .cargo/env line in shell rc files"
# The stock rust install appends:  . "$HOME/.cargo/env"
# When HOME isn't set during early profile sourcing it becomes /.cargo/env and
# errors. Wrap it in an existence test so it's silent when rust isn't installed.
for rc in /root/.bashrc /root/.profile /etc/skel/.bashrc /etc/bash.bashrc; do
    [ -f "$rc" ] || continue
    if grep -q '\.cargo/env' "$rc" 2>/dev/null; then
        # Comment out the bare source line and add a guarded version after it.
        sed -i 's#^\([[:space:]]*\)\.\([[:space:]]*\)"\?\$HOME\?/\?\.cargo/env"\?#\1[ -f "$HOME/.cargo/env" ] \&\& . "$HOME/.cargo/env"#' "$rc" 2>/dev/null || true
        # Simpler robust fallback: if a bare '. /.cargo/env' or similar remains, guard it.
        sed -i 's#^\([[:space:]]*\)\.\([[:space:]]*\)/\.cargo/env#\1[ -f /.cargo/env ] \&\& . /.cargo/env#' "$rc" 2>/dev/null || true
        echo "  guarded: $rc"
    fi
done

echo
echo "==> Done. Log out (exit) and log back in — the errors should be gone."
echo "    If any remain, they reference a tool you can install with apt."
