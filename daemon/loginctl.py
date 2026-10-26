#!/usr/bin/env python3
"""apatch-chroot loginctl replacement — MIT licensed, clean-room.

A loginctl for containers without systemd, following the same pattern as
daemon/systemctl.py: staged into the container by stage_init, MIT, and
written from systemd's documented behavior + POSIX primitives.

Where systemd tracks sessions in systemd-logind's state, this keeps a
**filesystem session registry**: one JSON record per login session in

    /run/apatch-chroot/sessions/<leader-pid>.json

Writers:
  - the init supervisor (systemctl.py init) registers the login it spawns;
  - plain logins register themselves right after chroot (the daemon child
    writes the same record inline, no import needed).

Records whose leader pid is no longer alive are pruned automatically on
the next listing, so logout needs no cleanup handshake.

Linger (enabled via `loginctl enable-linger USER`, marker files under
/var/lib/apatch-chroot/linger/) gives services a real behavior here:
with linger on, the init supervisor leaves background services RUNNING
when the login shell exits (they keep serving; the next `--init` login
adopts them via their pidfiles; `apatch-chroot kill` still stops the
container). Without linger, exit stops services like before.

Verbs mirror systemd's loginctl:
    list-sessions | list-users | list-seats
    show-session ID | session-status ID | user-status USER | show-user USER
    terminate-session ID | terminate-user USER
    kill-session ID | kill-user USER
    enable-linger USER | disable-linger USER
"""

import json
import os
import pwd
import signal
import sys
import time

SESSIONS_DIR = os.environ.get("APATCH_SESSIONS_DIR",
                              "/run/apatch-chroot/sessions")
LINGER_DIR = os.environ.get("APATCH_LINGER_DIR",
                            "/var/lib/apatch-chroot/linger")

ACTIVE, CLOSING, DEAD = "active", "closing", "dead"


# ------------------------------------------------------------ registry

def _sessions_dir():
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    return SESSIONS_DIR


def record_session(leader_pid, user, command, tty=None):
    """Register a login session. Id = leader pid (unique per login)."""
    rec = {
        "id": str(leader_pid),
        "user": user or "root",
        "leader": int(leader_pid),
        "command": list(command or []),
        "tty": tty,
        "created": time.time(),
    }
    with open(os.path.join(_sessions_dir(), rec["id"] + ".json"), "w") as fh:
        json.dump(rec, fh)
    return rec


def remove_session(session_id):
    try:
        os.unlink(os.path.join(SESSIONS_DIR, str(session_id) + ".json"))
    except OSError:
        pass


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def load_sessions(prune=True):
    """Live sessions, newest first. Dead-leader records are pruned."""
    out = []
    try:
        names = sorted(os.listdir(SESSIONS_DIR))
    except OSError:
        return out
    for name in names:
        if not name.endswith(".json"):
            continue
        path = os.path.join(SESSIONS_DIR, name)
        try:
            with open(path) as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            os.unlink(path) if prune else None
            continue
        rec.setdefault("id", name[:-len(".json")])
        rec.setdefault("user", "root")
        rec.setdefault("leader", int(rec["id"]) if str(rec["id"]).isdigit() else 0)
        rec.setdefault("command", [])
        rec.setdefault("tty")
        rec.setdefault("created", 0.0)
        if not _pid_alive(rec["leader"]):
            if prune:
                try:
                    os.unlink(path)
                except OSError:
                    pass
            continue
        out.append(rec)
    out.sort(key=lambda r: r["created"], reverse=True)
    return out


def find_session(session_id):
    for rec in load_sessions(prune=False):
        if rec["id"] == str(session_id):
            return rec
    return None


def sessions_of(user):
    return [r for r in load_sessions() if r["user"] == user]


def session_state(rec):
    return ACTIVE if _pid_alive(rec["leader"]) else DEAD


# --------------------------------------------------------------- linger

def linger_path(user):
    return os.path.join(LINGER_DIR, user)


def set_linger(user, enabled):
    os.makedirs(LINGER_DIR, exist_ok=True)
    if enabled:
        with open(linger_path(user), "w") as fh:
            fh.write("")
    else:
        try:
            os.unlink(linger_path(user))
        except OSError:
            pass


def user_linger(user):
    return os.path.isfile(linger_path(user))


def any_linger():
    try:
        return bool(os.listdir(LINGER_DIR))
    except OSError:
        return False


def linger_users():
    try:
        return sorted(os.listdir(LINGER_DIR))
    except OSError:
        return []


# --------------------------------------------------------------- output

def _uid(user):
    try:
        return pwd.getpwnam(user).pw_uid
    except KeyError:
        return 0


def _tty(rec):
    return rec.get("tty") or "-"


def print_sessions(sessions):
    width = max([len(r["id"]) for r in sessions] or [7])
    print(f"{'SESSION'.ljust(width)}  {'USER':<10} {'STATE':<8} "
          f"{'SEAT':<6} TTY         COMMAND")
    for r in sessions:
        cmd = " ".join(r["command"]) if r["command"] else "-"
        print(f"{r['id'].ljust(width)}  {r['user']:<10} "
              f"{session_state(r):<8} {'seat0':<6} "
              f"{_tty(r):<11} {cmd}")


def print_users():
    by_user = {}
    for r in load_sessions():
        by_user.setdefault(r["user"], []).append(r)
    print(f"{'USER':<16} {'UID':<6} {'STATE':<8} SESSIONS  LINGER")
    names = sorted(set(by_user) | set(linger_users()))
    for user in names:
        recs = by_user.get(user, [])
        state = ACTIVE if recs else "lingering"
        print(f"{user:<16} {_uid(user):<6} {state:<8} "
              f"{len(recs):<9} {'yes' if user_linger(user) else 'no'}")


def _iso(ts):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def show_session(rec):
    lines = [
        f"Id={rec['id']}",
        f"User={rec['user']}",
        f"Leader={rec['leader']}",
        f"State={session_state(rec)}",
        f"TTY={rec.get('tty') or ''}",
        f"Seat=seat0",
        f"Remote=no",
        f"Created={_iso(rec['created'])}",
        f"Command={' '.join(rec['command']) if rec['command'] else '-'}",
        f"Linger={'yes' if user_linger(rec['user']) else 'no'}",
    ]
    print("\n".join(lines))


def show_user(user):
    recs = sessions_of(user)
    lines = [f"User={user}", f"Uid={_uid(user)}",
             f"Linger={'yes' if user_linger(user) else 'no'}",
             f"State={ACTIVE if recs else ('lingering' if user_linger(user) else 'inactive')}",
             f"Sessions={len(recs)}"]
    print("\n".join(lines))


def session_status(rec):
    print(f"{rec['id']} - {rec['user']} ({_uid(rec['user'])})")
    show_session(rec)


def user_status(user):
    show_user(user)
    recs = sessions_of(user)
    if recs:
        print()
        print_sessions(recs)


# ---------------------------------------------------------------- CLI

def _terminate(rec, sig=signal.SIGTERM):
    try:
        os.kill(rec["leader"], sig)
        return True
    except OSError as exc:
        print(f"loginctl: cannot signal session {rec['id']}: {exc}",
              file=sys.stderr)
        return False


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    verb = argv[0] if argv else "list-sessions"
    args = argv[1:]

    if verb == "list-sessions":
        print_sessions(load_sessions())
        return 0

    if verb == "list-users":
        print_users()
        return 0

    if verb == "list-seats":
        print("SEAT   DESCRIPTION")
        print("seat0  local container logins")
        return 0

    if verb in ("show-session", "session-status"):
        if not args:
            print("loginctl: missing session id", file=sys.stderr)
            return 1
        rec = find_session(args[0])
        if rec is None:
            print(f"loginctl: session {args[0]} not found", file=sys.stderr)
            return 1
        (show_session if verb == "show-session" else session_status)(rec)
        return 0

    if verb in ("show-user", "user-status"):
        if not args:
            print("loginctl: missing user", file=sys.stderr)
            return 1
        (show_user if verb == "show-user" else user_status)(args[0])
        return 0

    if verb in ("terminate-session", "kill-session"):
        if not args:
            print("loginctl: missing session id", file=sys.stderr)
            return 1
        rec = find_session(args[0])
        if rec is None:
            print(f"loginctl: session {args[0]} not found", file=sys.stderr)
            return 1
        return 0 if _terminate(rec) else 1

    if verb in ("terminate-user", "kill-user"):
        if not args:
            print("loginctl: missing user", file=sys.stderr)
            return 1
        ok = all(_terminate(r) for r in sessions_of(args[0]))
        return 0 if ok else 1

    if verb in ("enable-linger", "disable-linger"):
        if not args:
            print("loginctl: missing user", file=sys.stderr)
            return 1
        for user in args:
            set_linger(user, verb == "enable-linger")
            print(f"{user}: linger {'enabled' if verb == 'enable-linger' else 'disabled'}")
        return 0

    print(f"loginctl: unknown verb '{verb}'", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
