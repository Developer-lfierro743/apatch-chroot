#!/bin/sh
# apatch-chroot login wrapper — a clean login shell.
#
# Solves TWO problems seen on converted Ubuntu containers:
#
# 1. PATH LEAK + LOSS: the container's profile (often baked in during a
#    chroot-distro conversion) exports a PATH full of ANDROID HOST paths
#    (/system/bin, /apex/..., /data/data/com.termux/...) and MISSING the
#    container's own /usr/bin and /bin. Result: `ls`, `apt`, even `bash`
#    report "command not found" though the binaries exist at /usr/bin.
#
# 2. NOISE: /etc/profile + /root/.bashrc call tools a slim image lacks
#    (groups, dircolors, mountpoint, mount, lesspipe) and source
#    "$HOME/.cargo/env", spamming "command not found" on every login.
#
# The fix: define no-op stubs for the commonly-missing tools and set HOME
# BEFORE sourcing (so .cargo/env resolves correctly), source the profile with
# errors suppressed, then set a CLEAN container PATH that REPLACES (not appends)
# the polluted one — so host paths never leak in and /usr/bin is always present.

# HOME first — an unset HOME makes .bashrc's `. "$HOME/.cargo/env"` resolve to
# /.cargo/env and error.
export HOME="${HOME:-/root}"

# No-op stubs for tools a slim container image lacks, so the profile's calls to
# them succeed silently instead of erroring. Only define if not a real command.
for _c in groups dircolors mountpoint mount lesspipe; do
    command -v "$_c" >/dev/null 2>&1 || eval "$_c() { :; }" 2>/dev/null
done
unset _c

# Source the standard login files, errors suppressed (a missing tool or stray
# source line can't spam the login or abort the profile).
[ -f /etc/profile ] && . /etc/profile 2>/dev/null
[ -f "$HOME/.bash_profile" ] && . "$HOME/.bash_profile" 2>/dev/null
[ -f "$HOME/.profile" ] && . "$HOME/.profile" 2>/dev/null
[ -f "$HOME/.bashrc" ] && . "$HOME/.bashrc" 2>/dev/null

# Set a CLEAN container PATH that REPLACES the polluted one. We deliberately do
# NOT append the incoming $PATH — that's exactly the host-path leak we're
# fixing. /usr/bin and /bin are the container's real tool dirs.
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export TERM="${TERM:-xterm-256color}"
export HOME="${HOME:-/root}"

# Hand off to an interactive shell. NOT --login: the wrapper already sourced the
# profile files above, and bash --login would source them AGAIN, re-polluting
# PATH with the host-path leak we just cleaned. Plain -i keeps aliases/prompt.
exec /bin/bash -i
