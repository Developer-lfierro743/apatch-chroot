#!/bin/sh
# apatch-chroot systemctl shim — a drop-in replacement for systemctl(1) that
# talks to the apatch-chroot mini-init instead of systemd.
#
# WHY: Ubuntu/Debian containers ship systemd and countless unit files, scripts,
# and tutorials call `systemctl start sshd`. But systemd cannot be PID 1 in our
# chroot (no cgroups, no dbus, no /run/systemd), so the real binary just fails.
# This shim intercepts the common systemctl verbs and translates them into
# operations on the mini-init's manifest (/etc/apatch-chroot/services.conf) and
# pidfiles (/run/apatch-chroot/<name>.pid).
#
# Supported verbs: start stop restart reload try-restart status is-active
#                  is-enabled enable disable list-units daemon-reload
# Unknown verbs/flags are accepted and reported as no-ops so scripts don't die.
#
# How a service name maps to a command:
#   1. If it is already in the manifest, use that command.
#   2. Else parse the unit file (sshd, sshd.service) for ExecStart=.
#   3. Else fall back to a small hardcoded map of common services.
# `enable`/`disable` only set the manifest's enabled flag (auto-start on next
# container boot); they do not need a command.

SERVICES_CONF="${APATCH_SERVICES_CONF:-/etc/apatch-chroot/services.conf}"
RUNDIR="${APATCH_RUNDIR:-/run/apatch-chroot}"
LOGDIR="${APATCH_LOGDIR:-/var/log/apatch-chroot}"

mkdir -p "$RUNDIR" "$LOGDIR" 2>/dev/null

# strip a trailing .service / .socket suffix systemd tolerates.
unit_name() {
    _n="$1"
    case "$_n" in
        *.service) _n="${_n%.service}" ;;
        *.socket)  _n="${_n%.socket}" ;;
    esac
    echo "$_n"
}

is_running() {
    [ -f "$RUNDIR/$1.pid" ] || return 1
    _p=$(cat "$RUNDIR/$1.pid" 2>/dev/null)
    [ -n "$_p" ] && kill -0 "$_p" 2>/dev/null
}

manifest_line() {
    grep "^$1|" "$SERVICES_CONF" 2>/dev/null | head -1
}

manifest_cmd() {
    manifest_line "$1" | cut -d'|' -f3-
}

manifest_enabled() {
    manifest_line "$1" | cut -d'|' -f2
}

# Find ExecStart= from a unit file. Searches the usual systemd dirs.
unit_execstart() {
    _u="$1"
    for d in /etc/systemd/system /run/systemd/system /lib/systemd/system \
             /usr/lib/systemd/system /etc/init.d; do
        for f in "$d/$_u.service" "$d/$_u"; do
            if [ -f "$f" ]; then
                # Grab the first ExecStart= (ignore ExecStartPre/Post), strip
                # the key and surrounding quotes, drop a leading - (ignore-fail).
                _es=$(grep -m1 '^ExecStart=' "$f" 2>/dev/null | cut -d= -f2-)
                _es=$(echo "$_es" | sed 's/^-//; s/^"//; s/"$//')
                [ -n "$_es" ] && { echo "$_es"; return 0; }
            fi
        done
    done
    return 1
}

# Hardcoded fallback for common services with non-standard units.
fallback_cmd() {
    case "$1" in
        sshd|ssh)        echo "/usr/sbin/sshd -D" ;;
        dbus)            echo "/usr/bin/dbus-daemon --system --nofork" ;;
        cron|crond)      echo "/usr/sbin/cron -f" ;;
        rsyslog|syslog)  echo "/usr/sbin/rsyslogd -n" ;;
        nginx)           echo "/usr/sbin/nginx -g 'daemon off;'" ;;
        *)               return 1 ;;
    esac
}

# Resolve the command for a service, trying manifest -> unit file -> fallback.
resolve_cmd() {
    _c=$(manifest_cmd "$1")
    [ -n "$_c" ] && { echo "$_c"; return 0; }
    _c=$(unit_execstart "$1") && { echo "$_c"; return 0; }
    _c=$(fallback_cmd "$1") && { echo "$_c"; return 0; }
    return 1
}

# Add or update a service in the manifest.
set_manifest() {
    _name="$1"
    _enabled="$2"
    _cmd="$3"
    touch "$SERVICES_CONF" 2>/dev/null
    if manifest_line "$_name" >/dev/null 2>&1; then
        # Replace the existing line.
        _tmp="$SERVICES_CONF.tmp.$$"
        grep -v "^$_name|" "$SERVICES_CONF" > "$_tmp" 2>/dev/null
        mv "$_tmp" "$SERVICES_CONF" 2>/dev/null
    fi
    echo "$_name|$_enabled|$_cmd" >> "$SERVICES_CONF"
}

do_start() {
    _name=$(unit_name "$1")
    if is_running "$_name"; then
        echo "● $_name — already active"; return 0
    fi
    _cmd=$(resolve_cmd "$_name") || {
        echo "Failed to start $_name: no command known (no unit file, not in manifest)" >&2
        return 5
    }
    # Persist to the manifest so the init supervisor can also see/manage it.
    _en=$(manifest_enabled "$_name"); [ -z "$_en" ] && _en=1
    set_manifest "$_name" "$_en" "$_cmd"
    # shellcheck disable=SC2086
    $_cmd >>"$LOGDIR/$_name.log" 2>&1 &
    echo $! > "$RUNDIR/$_name.pid"
    echo "● $_name — started"
    return 0
}

do_stop() {
    _name=$(unit_name "$1")
    if [ -f "$RUNDIR/$_name.pid" ]; then
        _p=$(cat "$RUNDIR/$_name.pid" 2>/dev/null)
        [ -n "$_p" ] && kill "$_p" 2>/dev/null
        rm -f "$RUNDIR/$_name.pid"
        echo "○ $_name — stopped"
    else
        echo "○ $_name — not active"
    fi
    return 0
}

do_restart() {
    do_stop "$1"
    sleep 1
    do_start "$1"
}

do_status() {
    _name=$(unit_name "$1")
    if is_running "$_name"; then
        _p=$(cat "$RUNDIR/$_name.pid" 2>/dev/null)
        _en=$(manifest_enabled "$_name")
        echo "● $_name - active (running)"
        echo "     Main PID: $_p"
        [ "$_en" = "1" ] && echo "     enabled: yes" || echo "     enabled: no"
        return 0
    fi
    echo "○ $_name - inactive"
    return 3
}

do_is_active() {
    unit_name "$1" >/dev/null
    _n=$(unit_name "$1")
    is_running "$_n" && { echo "active"; return 0; }
    echo "inactive"; return 3
}

do_is_enabled() {
    _n=$(unit_name "$1")
    [ "$(manifest_enabled "$_n")" = "1" ] && { echo "enabled"; return 0; }
    echo "disabled"; return 1
}

do_enable() {
    _n=$(unit_name "$1")
    _cmd=$(resolve_cmd "$_n") || _cmd=""
    _en=1
    [ -z "$_cmd" ] && _cmd=$(manifest_cmd "$_n")
    set_manifest "$_n" "$_en" "$_cmd"
    echo "Enabled $_n (auto-starts on next container start)."
    # --now: also start immediately.
    for a in "$@"; do [ "$a" = "--now" ] && do_start "$_n"; done
    return 0
}

do_disable() {
    _n=$(unit_name "$1")
    _cmd=$(manifest_cmd "$_n")
    set_manifest "$_n" "0" "${_cmd:-true}"
    echo "Disabled $_n."
    return 0
}

do_list_units() {
    echo "UNIT                LOAD   ACTIVE   SUB"
    while IFS='|' read -r name enabled cmd; do
        case "$name" in ''|\#*) continue ;; esac
        if is_running "$name"; then a="active"; s="running"; else a="inactive"; s="dead"; fi
        printf "%-19s %-6s %-8s %s\n" "$name.service" "loaded" "$a" "$s"
    done < "$SERVICES_CONF" 2>/dev/null
    return 0
}

# --- main ---
[ $# -eq 0 ] && { echo "Usage: systemctl [start|stop|restart|status|enable|disable|is-active|is-enabled|list-units] UNIT..."; exit 1; }

VERB="$1"
shift

# Flags we accept and ignore (systemd-isms that don't apply here).
ARGS=""
for a in "$@"; do
    case "$a" in
        --now|--no-block|--no-pager|--quiet|-q|--system|--user) : ;;
        *) ARGS="$ARGS $a" ;;
    esac
done
# shellcheck disable=SC2086
set -- $ARGS

rc=0
case "$VERB" in
    start)       for u in "$@"; do do_start "$u" || rc=$?; done ;;
    stop)        for u in "$@"; do do_stop "$u" || rc=$?; done ;;
    restart)     for u in "$@"; do do_restart "$u" || rc=$?; done ;;
    reload|try-restart) for u in "$@"; do do_restart "$u" || rc=$?; done ;;
    status)      for u in "$@"; do do_status "$u" || rc=$?; done ;;
    is-active)   for u in "$@"; do do_is_active "$u" || rc=$?; done ;;
    is-enabled)  for u in "$@"; do do_is_enabled "$u" || rc=$?; done ;;
    is-failed)   echo "inactive"; rc=1 ;;
    enable)      for u in "$@"; do do_enable "$u" || rc=$?; done ;;
    disable)     for u in "$@"; do do_disable "$u" || rc=$?; done ;;
    list-units|list-unit-files) do_list_units ;;
    daemon-reload|daemon-reexec) : ;;  # no-op: nothing to reload
    *) echo "systemctl (apatch shim): '$VERB' not supported (no-op)" >&2; rc=0 ;;
esac

exit "$rc"
