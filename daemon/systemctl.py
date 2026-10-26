#!/usr/bin/env python3
"""apatch-chroot systemctl replacement — MIT licensed, clean-room.

A tiny init supervisor + systemctl command tool for Linux containers
(chroots) that have no real systemd. Written for apatch-chroot from
systemd's documented unit-file semantics and plain POSIX primitives;
not derived from any other implementation.

Modes
-----
    systemctl.py init CMD [ARGS...]   init supervisor: start enabled units
                                      (dependency order), run CMD as the
                                      session child, reap everything.
    systemctl.py VERB [UNIT...]       command mode: list-units, start, stop,
                                      restart, status, is-active, enable,
                                      disable.

Unit files live in /etc/systemd/system/*.service. `enable` creates a
multi-user.target.wants symlink, exactly like real systemd. Services log
to /var/log/apatch-chroot/<unit>.log and pidfile to
/run/apatch-chroot/<unit>.pid. Command mode talks to the running init
over the unix socket /run/apatch-chroot/control.sock; status-style verbs
degrade gracefully to pidfile//proc reads when the init is not running.
"""

import contextlib
import json
import os
import pwd
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time

UNIT_DIR = os.environ.get("APATCH_UNIT_DIR", "/etc/systemd/system")
WANT_SUBDIR = os.path.join("multi-user.target.wants")
RUNDIR = os.environ.get("APATCH_RUNDIR", "/run/apatch-chroot")
LOGDIR = os.environ.get("APATCH_LOGDIR", "/var/log/apatch-chroot")
CONTROL_SOCK = os.path.join(RUNDIR, "control.sock")
MAX_RESTARTS = 5
FORKING_PIDFILE_WAIT = 10.0
STOP_GRACE = 5.0

ACTIVE, INACTIVE, FAILED = "active", "inactive", "failed"


# ---------------------------------------------------------------- units

class Unit:
    __slots__ = ("name", "path", "description", "after", "wants", "requires",
                 "type", "exec_start", "cwd", "env", "env_file", "user",
                 "restart", "pidfile")
    name: str
    path: str
    description: str
    after: list
    wants: list
    requires: list
    type: str
    exec_start: list
    cwd: "str | None"
    env: dict
    env_file: "str | None"
    user: "str | None"
    restart: str
    pidfile: "str | None"

    def __init__(self, name, path):
        self.name = name
        self.path = path
        self.description = name
        self.after = []
        self.wants = []
        self.requires = []
        self.type = "simple"
        self.exec_start = []
        self.cwd = None
        self.env = {}
        self.env_file = None
        self.user = None
        self.restart = "no"
        self.pidfile = None


def parse_unit_file(path):
    """Parse a systemd-style .service file (subset). Unknown keys ignored."""
    name = os.path.basename(path)
    if not name.endswith(".service"):
        return None
    unit = Unit(name[:-len(".service")], path)
    section = None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith(("#", ";")):
                    continue
                if line.startswith("[") and line.endswith("]"):
                    section = line[1:-1].lower()
                    continue
                if "=" not in line or section not in ("unit", "service"):
                    continue
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip()
                if section == "unit":
                    if key == "Description":
                        unit.description = val
                    elif key in ("After", "Wants", "Requires"):
                        attr = {"After": "after", "Wants": "wants",
                                "Requires": "requires"}[key]
                        getattr(unit, attr).extend(
                            _unit_names(val))
                elif section == "service":
                    if key == "Type":
                        unit.type = val.lower()
                    elif key == "ExecStart":
                        unit.exec_start = shlex.split(_expand(val, unit.name))
                    elif key == "WorkingDirectory":
                        unit.cwd = val or None
                    elif key == "Environment":
                        for tok in shlex.split(val):
                            if "=" in tok:
                                k, _, v = tok.partition("=")
                                unit.env[k] = v
                    elif key == "EnvironmentFile" and val:
                        unit.env_file = val
                    elif key == "User":
                        unit.user = val or None
                    elif key == "Restart":
                        unit.restart = val.lower()
                    elif key == "PIDFile":
                        unit.pidfile = val or None
    except OSError:
        return None
    if not unit.exec_start and unit.type != "oneshot":
        return None
    return unit


def _unit_names(val):
    return [v for v in (t.strip() for t in val.split()) if v]


def _expand(val, unit_name):
    """Handle the few %-specifiers that matter, without full systemd spec."""
    return val.replace("%n", unit_name + ".service").replace("%i", "")


def load_units():
    """All parseable units in UNIT_DIR, keyed by unit name."""
    units = {}
    try:
        entries = sorted(os.listdir(UNIT_DIR))
    except OSError:
        return units
    for entry in entries:
        if not entry.endswith(".service"):
            continue
        unit = parse_unit_file(os.path.join(UNIT_DIR, entry))
        if unit:
            units[unit.name] = unit
    return units


def want_dir():
    return os.path.join(UNIT_DIR, WANT_SUBDIR)


def is_enabled(name):
    return os.path.islink(os.path.join(want_dir(), name + ".service"))


def set_enabled(name, enabled):
    """enable/disable = create/remove the multi-user.target.wants symlink."""
    link = os.path.join(want_dir(), name + ".service")
    os.makedirs(want_dir(), exist_ok=True)
    if enabled:
        target = os.path.join(UNIT_DIR, name + ".service")
        if not os.path.isfile(target):
            raise FileNotFoundError("unit not found: " + name)
        if not os.path.islink(link):
            if os.path.exists(link):
                os.unlink(link)
            os.symlink(target, link)
    elif os.path.islink(link):
        os.unlink(link)


# ------------------------------------------------------- dependencies

def start_order(units):
    """Kahn topological sort over After/Wants/Requires among `units`."""
    names = set(units)
    deps = {n: {d for d in (u.after + u.wants + u.requires) if d in names}
            for n, u in units.items()}
    order, ready = [], sorted(n for n, d in deps.items() if not d)
    remaining = names - set(ready)
    while ready:
        n = ready.pop(0)
        order.append(n)
        for m, d in deps.items():
            if n in d and m in remaining:
                d.discard(n)
                if not d:
                    ready.append(m)
                    remaining.discard(m)
        ready.sort()
    if remaining:  # cycle: start them after the clean ones, order unknown
        order.extend(sorted(remaining))
    return order


# -------------------------------------------------------------- state

class Supervisor:
    def __init__(self):
        self.lock = threading.RLock()
        self.units = load_units()
        self.pids = {}       # name -> tracked pid (Type=simple/forking)
        self.restarts = {}   # name -> restart attempts
        self.stop_all = False

    # -- process helpers
    @staticmethod
    def _pid_alive(pid):
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    @staticmethod
    def _drop_user(user):
        if not user:
            return
        try:
            ent = pwd.getpwnam(user)
        except KeyError:
            return
        try:
            os.initgroups(user, ent.pw_gid)
        except (AttributeError, OSError):
            pass
        os.setgid(ent.pw_gid)
        os.setuid(ent.pw_uid)

    def _service_env(self, unit):
        env = dict(os.environ)
        if unit.env_file and os.path.isfile(unit.env_file):
            with open(unit.env_file, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        env[k.strip()] = v.strip()
        env.update(unit.env)
        return env

    def _open_log(self, name):
        os.makedirs(LOGDIR, exist_ok=True)
        return open(os.path.join(LOGDIR, name + ".log"), "ab",
                    buffering=0)

    def _write_pidfile(self, name, pid):
        os.makedirs(RUNDIR, exist_ok=True)
        with open(os.path.join(RUNDIR, name + ".pid"), "w") as fh:
            fh.write(str(pid))

    def _read_pidfile(self, name):
        try:
            with open(os.path.join(RUNDIR, name + ".pid")) as fh:
                return int(fh.read().strip())
        except (OSError, ValueError):
            return None

    def _clear_pidfile(self, name):
        try:
            os.unlink(os.path.join(RUNDIR, name + ".pid"))
        except OSError:
            pass

    # -- service lifecycle
    def start_unit(self, name):
        with self.lock:
            unit = self.units.get(name)
            if unit is None:
                return {"ok": False, "error": "unit not found: " + name}
            if name in self.pids and self._pid_alive(self.pids[name]):
                return {"ok": True, "state": ACTIVE, "pid": self.pids[name]}
            if not unit.exec_start:
                return {"ok": False, "error": "no ExecStart: " + name}
            log = self._open_log(name)
            try:
                proc = subprocess.Popen(
                    unit.exec_start,
                    cwd=unit.cwd or None,
                    env=self._service_env(unit),
                    stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                    preexec_fn=lambda: self._drop_user(unit.user),
                    start_new_session=True,
                )
            except OSError as exc:
                return {"ok": False, "error": f"{name}: {exc}"}
            finally:
                log.close()
            pid = proc.pid
            if unit.type == "forking":
                deadline = time.monotonic() + FORKING_PIDFILE_WAIT
                while time.monotonic() < deadline:
                    if not self._pid_alive(pid):
                        break  # parent exited; daemon child owns the work
                    main = unit.pidfile and self._read_main_pid(unit.pidfile)
                    if main:
                        pid = main
                        break
                    if not self._pid_alive(pid):
                        break
                    time.sleep(0.1)
            self.pids[name] = pid
            self._write_pidfile(name, pid)
            return {"ok": True, "state": ACTIVE, "pid": pid}

    def _read_main_pid(self, pidfile):
        try:
            with open(pidfile) as fh:
                pid = int(fh.read().strip())
            return pid if pid > 0 else None
        except (OSError, ValueError):
            return None

    def stop_unit(self, name, remove_pidfile=True):
        with self.lock:
            pid = self.pids.pop(name, None) or self._read_pidfile(name)
            if pid and self._pid_alive(pid):
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
                deadline = time.monotonic() + STOP_GRACE
                while time.monotonic() < deadline and self._pid_alive(pid):
                    time.sleep(0.1)
                if self._pid_alive(pid):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except OSError:
                        pass
            if remove_pidfile:
                self._clear_pidfile(name)
            return {"ok": True, "state": INACTIVE}

    def restart_unit(self, name):
        self.stop_unit(name)
        return self.start_unit(name)

    # -- queries
    def unit_state(self, name):
        with self.lock:
            pid = self.pids.get(name) or self._read_pidfile(name)
            if pid and self._pid_alive(pid):
                return ACTIVE, pid
            if self.restarts.get(name, 0) >= MAX_RESTARTS:
                return FAILED, None
            return INACTIVE, None

    def snapshot(self):
        with self.lock:
            rows = []
            for name, unit in sorted(self.units.items()):
                state, pid = self.unit_state(name)
                rows.append({"unit": name + ".service",
                             "load": "loaded" if unit.exec_start else "error",
                             "active": state,
                             "sub": "running" if state == ACTIVE else
                                    ("failed" if state == FAILED else "dead"),
                             "description": unit.description})
            return rows

    def reap(self):
        """Collect dead children; apply Restart= policy. Returns exit codes
        of tracked units that finished."""
        finished = {}
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                break
            except OSError:
                break
            if pid == 0:
                break
            name = next((n for n, p in self.pids.items() if p == pid), None)
            code = os.waitstatus_to_exitcode(status) if status else 0
            if name:
                del self.pids[name]
                finished[name] = code
                unit = self.units.get(name)
                if unit and unit.type == "oneshot":
                    self._clear_pidfile(name)
                    continue
                want = (unit.restart if unit else "no")
                if want in ("on-failure", "always") and not self.stop_all:
                    tries = self.restarts.get(name, 0)
                    if code != 0 or want == "always":
                        if tries < MAX_RESTARTS:
                            self.restarts[name] = tries + 1
                            self.start_unit(name)
                        else:
                            self._clear_pidfile(name)
        return finished

    # -- control socket
    def serve_control(self, stop_event):
        os.makedirs(RUNDIR, exist_ok=True)
        try:
            os.unlink(CONTROL_SOCK)
        except OSError:
            pass
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(CONTROL_SOCK)
        os.chmod(CONTROL_SOCK, 0o600)
        srv.listen(8)
        srv.settimeout(0.2)
        while not stop_event.is_set():
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                try:
                    req = json.loads(conn.recv(65536).decode())
                    resp = self.dispatch(req.get("verb"), req.get("units") or [])
                except (ValueError, OSError) as exc:
                    resp = {"ok": False, "error": str(exc)}
                try:
                    conn.sendall(json.dumps(resp).encode() + b"\n")
                except OSError:
                    pass
        try:
            srv.close()
            os.unlink(CONTROL_SOCK)
        except OSError:
            pass

    def dispatch(self, verb, units):
        verb = verb or ""
        if verb in ("start", "stop", "restart"):
            fn = {"start": self.start_unit, "stop": self.stop_unit,
                  "restart": self.restart_unit}[verb]
            return {"ok": True,
                    "results": {u: fn(u) for u in units}}
        if verb == "list-units":
            return {"ok": True, "units": self.snapshot()}
        if verb == "status":
            out = []
            for u in (units or sorted(self.units)):
                unit = self.units.get(u)
                if not unit:
                    out.append({"unit": u, "error": "unit not found"})
                    continue
                state, pid = self.unit_state(u)
                out.append({"unit": u, "description": unit.description,
                            "loaded": unit.path, "active": state,
                            "main_pid": pid,
                            "enabled": is_enabled(u)})
            return {"ok": True, "status": out}
        if verb == "is-active":
            states = {u: self.unit_state(u)[0] for u in units}
            return {"ok": True, "states": states}
        return {"ok": False, "error": "unknown verb: " + verb}


# ------------------------------------------------------------ init mode

def run_init(command):
    """Init supervisor: start enabled units, run `command`, reap, exit."""
    sup = Supervisor()
    os.makedirs(RUNDIR, exist_ok=True)
    os.makedirs(LOGDIR, exist_ok=True)

    enabled = [n for n in sup.units if is_enabled(n)]
    for name in start_order({n: sup.units[n] for n in enabled}):
        res = sup.start_unit(name)
        if not res.get("ok"):
            sys.stderr.write(f"apatch-chroot init: {name}: {res.get('error')}\n")

    stop_event = threading.Event()
    threading.Thread(target=sup.serve_control, args=(stop_event,),
                     daemon=True).start()

    def on_term(_sig, _frm):
        sup.stop_all = True
        stop_event.set()

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)

    # loginctl integration (same directory, staged alongside): register the
    # login session + honor linger. Best-effort; absent module = old behavior.
    loginctl = None
    try:
        import loginctl as loginctl_mod
        loginctl = loginctl_mod
    except ImportError:
        pass
    cmd_pid = None
    if command:
        cmd_pid = os.fork()
        if cmd_pid == 0:
            try:
                os.execvp(command[0], command)
            except OSError as exc:
                sys.stderr.write(f"apatch-chroot init: exec {command[0]}: {exc}\n")
                os._exit(127)
        # session child lives its own life; init supervises everything
        if loginctl:
            try:
                tty = os.ttyname(0)
            except OSError:
                tty = None
            try:
                loginctl.record_session(cmd_pid, "root", command, tty)
            except OSError:
                pass

    code = 0
    try:
        while True:
            sup.reap()
            if cmd_pid is not None:
                try:
                    waited, status = os.waitpid(cmd_pid, os.WNOHANG)
                    if waited == cmd_pid:
                        code = os.waitstatus_to_exitcode(status)
                        break
                except ChildProcessError:
                    break
            if sup.stop_all:
                break
            time.sleep(0.2)
    finally:
        stop_event.set()
        if cmd_pid is not None and loginctl:
            with contextlib.suppress(Exception):
                loginctl.remove_session(cmd_pid)
        # stop services in reverse start order — UNLESS linger is enabled:
        # then background services survive logout (the next --init login
        # adopts them via their pidfiles; apatch-chroot kill still stops all).
        keep_services = bool(loginctl and loginctl.any_linger())
        if not keep_services:
            enabled_set = set(enabled)
            for name in reversed([n for n in sup.pids if n in enabled_set]):
                sup.stop_unit(name)
        if cmd_pid is not None and sup.stop_all and code == 0:
            # shutting down: take the session child down with us
            try:
                os.kill(cmd_pid, signal.SIGTERM)
            except OSError:
                pass
    return code


# ------------------------------------------------------- command mode

def _talk(req):
    """Send one request to the running init; None when it is not running."""
    try:
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.settimeout(5.0)
        conn.connect(CONTROL_SOCK)
        conn.sendall(json.dumps(req).encode() + b"\n")
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = conn.recv(65536)
            if not chunk:
                break
            buf += chunk
        conn.close()
        return json.loads(buf.decode())
    except (OSError, ValueError):
        return None


def _print_rows(rows):
    width = max([len(r["unit"]) for r in rows] or [4])
    print(f"{'UNIT'.ljust(width)} LOAD    ACTIVE   SUB      DESCRIPTION")
    for r in rows:
        print(f"{r['unit'].ljust(width)} {r['load']:<7} {r['active']:<8} "
              f"{r['sub']:<8} {r['description']}")


def _status_from_files(units):
    """Degrade gracefully when no init is running: pidfile + /proc reads."""
    rows = []
    all_units = load_units()
    for name in (units or sorted(all_units)):
        unit = all_units.get(name)
        if not unit:
            rows.append({"unit": name, "error": "unit not found"})
            continue
        try:
            with open(os.path.join(RUNDIR, name + ".pid")) as fh:
                pid = int(fh.read().strip())
        except (OSError, ValueError):
            pid = None
        active = bool(pid and (os.path.isdir(f"/proc/{pid}")
                               or os.path.exists(f"/proc/{pid}")))
        rows.append({"unit": name, "description": unit.description,
                     "loaded": unit.path,
                     "active": ACTIVE if active else INACTIVE,
                     "main_pid": pid if active else None,
                     "enabled": is_enabled(name)})
    return rows


def run_command(argv):
    verb = argv[0] if argv else "list-units"
    units = [u[:-len(".service")] if u.endswith(".service") else u
             for u in argv[1:]]

    if verb == "list-units":
        resp = _talk({"verb": "list-units"})
        if resp and resp.get("ok"):
            _print_rows(resp["units"])
        else:
            rows = []
            for n in sorted(load_units()):
                st = _status_from_files([n])[0]
                rows.append({"unit": n + ".service",
                             "load": "loaded",
                             "active": st["active"],
                             "sub": "running" if st["active"] == ACTIVE
                             else "dead",
                             "description": st["description"]})
            _print_rows(rows)
        return 0

    if verb in ("start", "stop", "restart"):
        resp = _talk({"verb": verb, "units": units})
        if resp is None:
            print(f"apatch-chroot systemctl: init supervisor not running "
                  f"(start the container with --init)", file=sys.stderr)
            return 1
        if not resp.get("ok"):
            print("apatch-chroot systemctl: " + str(resp.get("error")),
                  file=sys.stderr)
            return 1
        for name, res in (resp.get("results") or {}).items():
            if not res.get("ok"):
                print(f"{name}: {res.get('error')}", file=sys.stderr)
                return 1
        return 0

    if verb == "status":
        resp = _talk({"verb": "status", "units": units})
        entries = (resp or {}).get("status") if resp else None
        if entries is None:
            entries = _status_from_files(units)
        rc = 0
        for e in entries:
            if "error" in e:
                print(f"Unit {e['unit']}.service could not be found.")
                rc = 4
                continue
            print(f"  {e.get('description', '')}")
            print(f"    Loaded: loaded ({e.get('loaded', '')}"
                  f"{'; enabled' if e.get('enabled') else '; disabled'})")
            active = e.get("active", INACTIVE)
            print(f"    Active: {active}"
                  + (f" (main pid {e.get('main_pid')})"
                     if e.get("main_pid") else ""))
        return rc

    if verb == "is-active":
        if units:
            resp = _talk({"verb": "is-active", "units": units})
            if resp and resp.get("ok"):
                states = resp["states"]
            else:
                states = {e["unit"]: e["active"]
                          for e in _status_from_files(units)}
            return 0 if all(v == ACTIVE for v in states.values()) else 3
        return 3

    if verb in ("enable", "disable"):
        try:
            for name in units:
                set_enabled(name, verb == "enable")
        except (OSError, FileNotFoundError) as exc:
            print(f"apatch-chroot systemctl: {exc}", file=sys.stderr)
            return 1
        return 0

    print(f"apatch-chroot systemctl: unknown verb '{verb}'", file=sys.stderr)
    return 2


def main():
    argv = sys.argv[1:]
    if argv and argv[0] == "init":
        return run_init(argv[1:])
    return run_command(argv)


if __name__ == "__main__":
    sys.exit(main())
