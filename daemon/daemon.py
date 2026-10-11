#!/data/data/com.termux/files/usr/bin/python3
"""apatch-chroot daemon — the privileged core.

Runs as root, started by the APatch module's service.sh at boot. It owns the
mount namespace and performs every privileged syscall (mount, umount, chroot,
openpty) here, in a context outside Android's app seccomp filter. The Termux
client is a thin CLI that talks to this daemon over a unix socket.

Protocol: newline-delimited JSON over a unix socket.
  request  -> {"cmd": "...", "args": {...}}
  response <- {"ok": true, "data": {...}} | {"ok": false, "error": "..."}

This is intentionally minimal for the first milestone: prove the privileged
chroot + PTY flow works end to end. OCI/Docker Hub pull comes next.
"""

import contextlib
import json
import os
import socket
import sys
import threading
import time

SOCKET_PATH = "/data/local/tmp/apatch-chroot.sock"
CONTAINERS_DIR = "/data/apatch-chroot/containers"
# Keep in sync with client/apatch-chroot.py VERSION and module.prop.
VERSION = "v0.4.0"
# Where the daemon records the version it last ran, to detect an update.
STATE_DIR = "/data/apatch-chroot"
# The broker (Termux-uid relay) exposes this socket for the core to dial.
# app_data_file context: root CAN connect here, the CLI cannot reach a root
# socket, so this is the bridge. See broker.py for the full SELinux rationale.
BROKER_CORE_SOCKET = "/data/data/com.termux/files/usr/tmp/apatch-chroot-core.sock"


def _recv_line(conn):
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = conn.recv(4096)
        if not chunk:
            return None
        buf += chunk
    return buf[:-1]


def _send(conn, obj):
    payload = json.dumps(obj).encode()
    conn.sendall(b"\x02" + payload + b"\n")


def _detect_update():
    """Return the previous version string if the daemon just updated, else None.

    Compares the running VERSION against the last-run version stored on disk.
    On a change, records the new version and returns the old one so the client
    can show a one-time "updated from vX" banner. Never raises.
    """
    marker = os.path.join(STATE_DIR, "last_version")
    try:
        prev = None
        with contextlib.suppress(OSError):
            with open(marker) as f:
                prev = f.read().strip()
        if prev != VERSION:
            os.makedirs(STATE_DIR, exist_ok=True)
            with contextlib.suppress(OSError):
                with open(marker, "w") as f:
                    f.write(VERSION)
            return prev or None  # None on very first run (no prior version)
    except Exception:
        pass
    return None


def handle_request(req, conn):
    """Dispatch one request. Returns a response dict, or None if the command
    already wrote its own responses to *conn* (streaming commands)."""
    cmd = req.get("cmd")
    if cmd == "ping":
        return {"ok": True, "data": {"uid": os.getuid(), "pid": os.getpid(), "version": VERSION, "just_updated": _detect_update()}}
    if cmd == "run":
        from daemon_cmds import run_container
        req["args"]["_conn"] = conn
        return run_container(req.get("args", {}), conn)
    if cmd == "install":
        from daemon_cmds import install_container
        return install_container(req.get("args", {}), conn)
    if cmd == "list":
        from daemon_cmds import list_containers
        return list_containers(req.get("args", {}))
    if cmd == "remove":
        from daemon_cmds import remove_container
        return remove_container(req.get("args", {}))
    if cmd == "convert":
        from daemon_cmds import convert_from_chroot_distro
        return convert_from_chroot_distro(req.get("args", {}), conn)
    if cmd == "kill":
        from daemon_cmds import kill_container
        return kill_container(req.get("args", {}))
    if cmd == "exec":
        from daemon_cmds import exec_container
        return exec_container(req.get("args", {}), conn)
    if cmd == "clone":
        from daemon_cmds import clone_container
        return clone_container(req.get("args", {}))
    if cmd == "push":
        from daemon_cmds import copy_into_container
        return copy_into_container(req.get("args", {}))
    if cmd == "pull":
        from daemon_cmds import copy_from_container
        return copy_from_container(req.get("args", {}))
    if cmd == "service":
        # Host-side service management: run the container's systemctl shim via
        # the exec path. Reuses all the proven exec plumbing — no new chroot
        # logic. The shim lives at /usr/bin/systemctl inside the container
        # (installed by stage_init when the container was run with --init).
        from daemon_cmds import service_container
        return service_container(req.get("args", {}), conn)
    if cmd == "setuser":
        # Set (or clear with user='' or 'root') a container's default login user.
        from daemon_cmds import write_default_user
        a = req.get("args", {})
        ok = write_default_user(a.get("name"), a.get("user"))
        if not ok:
            return {"ok": False, "error": f"could not set user for '{a.get('name')}'"}
        return {"ok": True, "data": {"name": a.get("name"), "user": a.get("user")}}
    if cmd == "info":
        from daemon_cmds import host_info
        return {"ok": True, "data": host_info()}
    if cmd == "stats":
        from daemon_cmds import container_stats
        return {"ok": True, "data": container_stats(req.get("args", {}))}
    if cmd == "shutdown":
        return {"ok": True, "data": {"bye": True}, "_shutdown": True}
    return {"ok": False, "error": f"unknown cmd: {cmd}"}


def serve(conn):
    try:
        while True:
            line = _recv_line(conn)
            if line is None:
                break
            try:
                req = json.loads(line.decode())
            except ValueError as e:
                _send(conn, {"ok": False, "error": f"bad json: {e}"})
                continue
            # A bug in a command handler must not kill the connection and hang
            # the client — catch it, report it, keep serving on this relay.
            try:
                resp = handle_request(req, conn)
            except Exception as e:
                _send(conn, {"ok": False, "error": f"handler error: {e!r}"})
                continue
            if resp is not None:
                shutdown = resp.pop("_shutdown", False)
                _send(conn, resp)
                if shutdown:
                    os._exit(0)
    except OSError:
        pass
    finally:
        conn.close()


def main():
    os.makedirs(CONTAINERS_DIR, exist_ok=True)

    # SELinux reality: the CLI (untrusted_app) cannot connect to a root-owned
    # socket. So we do NOT listen on our own socket. Instead we DIAL the broker
    # (which runs as the Termux uid, app_data_file — root CAN connect there) and
    # register ready relay connections. The broker pairs each CLI request with
    # one of our connections and pipes bytes; our serve() logic is unchanged.
    print(
        f"apatch-chroot core starting; dialing broker at {BROKER_CORE_SOCKET} (uid={os.getuid()})",
        flush=True,
    )

    def register_relay():
        """Keep a pool of connections to the broker ready to serve requests.

        Robust against any failure: a bad request handler or a dropped broker
        connection must not kill the thread (that would permanently shrink the
        pool until the daemon restarts). Every path loops back to a fresh dial.
        """
        while True:
            try:
                conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                conn.connect(BROKER_CORE_SOCKET)
            except OSError as e:
                # Broker not up yet (Termux not started, or booting). Retry.
                print(f"broker not reachable ({e}); retrying in 3s", flush=True)
                time.sleep(3)
                continue
            # Hand this connection to serve() — it blocks until the broker
            # pipes a CLI request through it, then loops for the next.
            try:
                serve(conn)
            except Exception as e:
                # Catch EVERYTHING, not just OSError: a bug in a command
                # handler would otherwise kill this relay thread for good.
                print(f"relay error ({e!r}); reopening", flush=True)
            finally:
                try:
                    conn.close()
                except OSError:
                    pass
            # serve() returned (broker closed the relay); open a fresh one.
            time.sleep(0.2)

    # A small pool so a couple of concurrent CLI calls don't serialize.
    for _ in range(4):
        threading.Thread(target=register_relay, daemon=True).start()

    # Block forever; the relay threads do the work.
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
