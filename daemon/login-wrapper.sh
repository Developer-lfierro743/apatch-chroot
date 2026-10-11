#!/bin/sh
# apatch-chroot login wrapper — a login shell that can't lose PATH/TERM.
#
# WHY: `bash -l` sources /etc/profile and /root/.bashrc, which on Ubuntu can
# overwrite (or error out and clear) PATH and TERM — leaving a shell where
# `ls`, `apt`, even `bash` report "command not found" and TERM is unset. This
# wrapper sources the normal login files FIRST (so aliases/prompt still load),
# then RE-ASSERTS our environment LAST so it always wins.
#
# Used by the daemon for `login`/`run` instead of a bare `bash -l`. The daemon
# passes the real env in; we re-export the critical vars after profile sourcing.

# Source the standard login files (best-effort — never fatal).
[ -f /etc/profile ] && . /etc/profile 2>/dev/null
[ -f "$HOME/.bash_profile" ] && . "$HOME/.bash_profile" 2>/dev/null
[ -f "$HOME/.profile" ] && . "$HOME/.profile" 2>/dev/null

# Re-assert our environment AFTER profile sourcing, so it always wins.
# APATCH_ENV holds "KEY=VALUE" lines the daemon set (PATH, TERM, HOME, ...).
if [ -n "$APATCH_ENV" ]; then
    printf '%s\n' "$APATCH_ENV" | while IFS= read -r kv; do
        [ -n "$kv" ] && export "$kv"
    done
fi

# Fallbacks if APATCH_ENV wasn't provided.
[ -z "$PATH" ] && export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
[ -z "$TERM" ] && export TERM="xterm-256color"
[ -z "$HOME" ] && export HOME="/root"

# Hand off to the interactive login shell.
exec /bin/bash --login -i
