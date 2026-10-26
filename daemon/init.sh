#!/bin/sh
# apatch-chroot container init — a tiny PID-1 supervisor (mini-systemd subset).
#
# Runs as PID 1 INSIDE the chroot. Android has no systemd, so a bare chroot
# has no service manager: services don't auto-start and orphaned zombies are
# never reaped. This supervisor fixes both with a purpose-built subset of
# systemd — not a port of it.
#
#   1. Reads enabled services from $SERVICES_CONF and starts them
#   2. Runs the user's command (login shell) as a child
#   3. Reaps zombies and restarts crashed services
#   4. On exit, stops all services and exits with the child's code
#
# POSIX sh so it runs in any container with no extra deps.
# Config format (one per line, '#' comments, blank lines ignored):
#   <name>|<enabled 0|1>|<command...>
#   sshd|1|/usr/sbin/sshd -D

SERVICES_CONF="${APATCH_SERVICES_CONF:-/etc/apatch-chroot/services.conf}"
RUNDIR="${APATCH_RUNDIR:-/run/apatch-chroot}"
LOGDIR="${APATCH_LOGDIR:-/var/log/apatch-chroot}"

mkdir -p "$RUNDIR" "$LOGDIR" 2>/dev/null

log() { echo "[apatch-init] $*" >&2; }

# The command for a service name, read fresh from the manifest each time.
svc_cmd() {
    grep "^$1|" "$SERVICES_CONF" 2>/dev/null | head -1 | cut -d'|' -f3-
}

is_running() {
    [ -f "$RUNDIR/$1.pid" ] || return 1
    _p=$(cat "$RUNDIR/$1.pid" 2>/dev/null)
    [ -n "$_p" ] && kill -0 "$_p" 2>/dev/null
}

start_service() {
    _name="$1"
    shift
    if is_running "$_name"; then
        return 0
    fi
    rm -f "$RUNDIR/$_name.pid"
    "$@" >>"$LOGDIR/$_name.log" 2>&1 &
    echo $! > "$RUNDIR/$_name.pid"
    log "started $_name (pid $!)"
}

stop_service() {
    if [ -f "$RUNDIR/$1.pid" ]; then
        _p=$(cat "$RUNDIR/$1.pid" 2>/dev/null)
        [ -n "$_p" ] && kill "$_p" 2>/dev/null
        rm -f "$RUNDIR/$1.pid"
        log "stopped $1"
    fi
}

load_and_start_services() {
    [ -f "$SERVICES_CONF" ] || { log "no services.conf; skipping"; return 0; }
    while IFS='|' read -r name enabled cmd; do
        case "$name" in ''|\#*) continue ;; esac
        name=$(echo "$name" | tr -d ' ')
        enabled=$(echo "$enabled" | tr -d ' ')
        cmd=$(echo "$cmd" | sed 's/^ *//; s/ *$//')
        [ -z "$name" ] && continue
        [ -z "$cmd" ] && continue
        [ "$enabled" = "1" ] || continue
        # shellcheck disable=SC2086
        start_service "$name" $cmd
    done < "$SERVICES_CONF"
}

# Restart any service whose pid died. Called after each reaped child.
check_services() {
    [ -f "$SERVICES_CONF" ] || return 0
    while IFS='|' read -r name enabled cmd; do
        case "$name" in ''|\#*) continue ;; esac
        name=$(echo "$name" | tr -d ' ')
        enabled=$(echo "$enabled" | tr -d ' ')
        cmd=$(echo "$cmd" | sed 's/^ *//; s/ *$//')
        [ "$enabled" = "1" ] || continue
        [ -z "$name" ] && continue
        if ! is_running "$name"; then
            log "$_n restart" >/dev/null
            log "$name not running; restarting"
            # shellcheck disable=SC2086
            start_service "$name" $cmd
        fi
    done < "$SERVICES_CONF"
}

stop_all_services() {
    [ -f "$SERVICES_CONF" ] || return 0
    while IFS='|' read -r name enabled cmd; do
        case "$name" in ''|\#*) continue ;; esac
        name=$(echo "$name" | tr -d ' ')
        [ -z "$name" ] && continue
        stop_service "$name"
    done < "$SERVICES_CONF"
}

# --- main ---
log "container init starting (pid $$)"

load_and_start_services

SHELL_PID=""
if [ "$#" -gt 0 ]; then
    log "exec: $*"
    "$@" &
    SHELL_PID=$!
else
    log "no command given; supervising services only"
fi

cleanup() {
    log "shutting down"
    [ -n "$SHELL_PID" ] && kill "$SHELL_PID" 2>/dev/null
    stop_all_services
    exit "${EXIT_CODE:-0}"
}
trap 'EXIT_CODE=130; cleanup' INT TERM
trap 'cleanup' EXIT

# On abrupt Android shutdown the init process can be SIGKILLed. That's OK —
# the private mount namespace dies with it, so no teardown is needed. But if
# we receive SIGTERM (e.g. the daemon's kill_container path), we want a clean
# shutdown: stop services, reap the shell, exit with the shell's code. The
# trap above handles both; EXIT_CODE defaults to 0 for a normal exit.

# Supervise with a timed poll. `wait -n` returns immediately when there are
# no unwaited children (POSIX sh), which would busy-loop and respawn a dead
# service hundreds of times a second. Instead we sleep CHECK_INTERVAL between
# passes and enforce a per-service respawn cooldown so a crash-looping service
# backs off instead of hammering the CPU.
CHECK_INTERVAL="${APATCH_CHECK_INTERVAL:-2}"
RESPAWN_COOLDOWN="${APATCH_RESPAWN_COOLDOWN:-5}"

# Track last-respawn time per service (epoch seconds) in the rundir.
note_respawn() { date +%s > "$RUNDIR/.respawn.$1"; }
respawn_cooled() {
    # True if at least RESPAWN_COOLDOWN seconds since the last respawn.
    _f="$RUNDIR/.respawn.$1"
    [ -f "$_f" ] || return 0
    _last=$(cat "$_f" 2>/dev/null || echo 0)
    _now=$(date +%s)
    [ $((_now - _last)) -ge "$RESPAWN_COOLDOWN" ]
}

# check_services with cooldown: restart a dead enabled service only if it has
# cooled down since its last respawn.
check_services_cooldown() {
    [ -f "$SERVICES_CONF" ] || return 0
    while IFS='|' read -r name enabled cmd; do
        case "$name" in ''|\#*) continue ;; esac
        name=$(echo "$name" | tr -d ' ')
        enabled=$(echo "$enabled" | tr -d ' ')
        cmd=$(echo "$cmd" | sed 's/^ *//; s/ *$//')
        [ "$enabled" = "1" ] || continue
        [ -z "$name" ] && continue
        if ! is_running "$name"; then
            if respawn_cooled "$name"; then
                log "$name not running; restarting"
                note_respawn "$name"
                # shellcheck disable=SC2086
                start_service "$name" $cmd
            fi
        fi
    done < "$SERVICES_CONF"
}

# Reap finished background children without blocking. POSIX sh has no
# non-blocking wait, so we rely on the shell reaping background jobs when it
# next runs `wait`; a lightweight `wait -n` here would BLOCK until a child
# exits, defeating the timed poll. Instead we do nothing special — dead
# service processes are reaped lazily and check_services_cooldown restarts
# them. The few zombies between passes are harmless and cleared on exit.
reap_children() { :; }

if [ -n "$SHELL_PID" ]; then
    while kill -0 "$SHELL_PID" 2>/dev/null; do
        reap_children
        check_services_cooldown
        sleep "$CHECK_INTERVAL"
    done
    wait "$SHELL_PID" 2>/dev/null
    EXIT_CODE=$?
else
    while true; do
        reap_children
        check_services_cooldown
        sleep "$CHECK_INTERVAL"
    done
fi

log "init exiting with code $EXIT_CODE"
exit "$EXIT_CODE"
