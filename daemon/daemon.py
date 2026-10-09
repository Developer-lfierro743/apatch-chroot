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

import json
import os
import socket
import sys
import threading

SOCKET_PATH = "/dev/apatch-chroot.sock"
CONTAINERS_DIR = "/data/apatch-chroot/containers"


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


def handle_request(req, conn):
    """Dispatch one request. Returns a response dict, or None if the command
    already wrote its own responses to *conn* (streaming commands)."""
    cmd = req.get("cmd")
    if cmd == "ping":
        return {"ok": True, "data": {"uid": os.getuid(), "pid": os.getpid()}}
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
            resp = handle_request(req, conn)
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
    try:
        os.unlink(SOCKET_PATH)
    except OSError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCKET_PATH)
    os.chmod(SOCKET_PATH, 0o600)
    srv.listen(8)
    print(f"apatch-chroot daemon listening on {SOCKET_PATH} (uid={os.getuid()})", flush=True)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=serve, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
