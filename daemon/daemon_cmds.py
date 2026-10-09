"""Privileged container commands for the apatch-chroot daemon.

Runs in the daemon's root context, outside Android's app seccomp filter, so
mount(2)/umount2(2)/chroot(2)/openpty succeed here where they SIGSYS in the
Termux app process.

Milestone 1: bare chroot + PTY, I/O proxied over the socket. Proves the
privileged context works. Mount orchestration and OCI pull come next.
"""

import contextlib
import fcntl
import os
import pty
import select
import struct
import termios

MAX = 65536


def _set_winsize(fd, rows=24, cols=80):
    with contextlib.suppress(OSError):
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def run_container(args, conn):
    """Fork a chrooted child on a PTY; proxy its I/O over *conn*.

    args: {"name", "command", "cwd"}. Blocks until the child exits, streaming
    bytes both ways. The first response line carries the child pid; the final
    line carries the exit code. Raw PTY bytes in between are length-prefixed
    frames so they can't be confused with control JSON.

    Frame format: 1 byte type — 0x01 = pty data (4-byte big-endian len + bytes),
    0x02 = control JSON line.
    """
    import json

    name = args.get("name")
    command = args.get("command") or ["/bin/bash"]
    cwd = args.get("cwd") or "/"

    rootfs = f"/data/apatch-chroot/containers/{name}/rootfs"
    if not os.path.isdir(rootfs):
        return {"ok": False, "error": f"container '{name}' not installed at {rootfs}"}

    def send_control(obj):
        payload = json.dumps(obj).encode()
        conn.sendall(b"\x02" + payload + b"\n")

    def send_data(b):
        conn.sendall(b"\x01" + len(b).to_bytes(4, "big") + b)

    master_fd, slave_fd = pty.openpty()
    _set_winsize(master_fd)

    pid = os.fork()
    if pid == 0:
        try:
            os.close(master_fd)
            os.setsid()
            with contextlib.suppress(OSError):
                fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            if slave_fd > 2:
                os.close(slave_fd)
            os.chroot(rootfs)
            os.chdir(cwd)
            os.execvp(command[0], command)
        except Exception as e:
            with contextlib.suppress(OSError):
                os.write(2, f"exec failed: {e}\n".encode())
            os._exit(127)

    os.close(slave_fd)
    send_control({"ok": True, "data": {"pid": pid}})

    conn.setblocking(False)
    try:
        while True:
            try:
                readable, _, _ = select.select([master_fd, conn], [], [], 1.0)
            except (OSError, ValueError):
                break
            if master_fd in readable:
                try:
                    data = os.read(master_fd, MAX)
                except OSError:
                    data = b""
                if not data:
                    break
                send_data(data)
            if conn in readable:
                try:
                    incoming = conn.recv(MAX)
                except OSError:
                    incoming = b""
                if not incoming:
                    break
                with contextlib.suppress(OSError):
                    os.write(master_fd, incoming)
            # Reap if the child already exited and the pty drained.
            wpid, status = os.waitpid(pid, os.WNOHANG)
            if wpid == pid:
                # Drain remaining output.
                with contextlib.suppress(OSError):
                    while True:
                        data = os.read(master_fd, MAX)
                        if not data:
                            break
                        send_data(data)
                send_control({"ok": True, "data": {"exit": os.waitstatus_to_exitcode(status)}})
                return None
    finally:
        with contextlib.suppress(OSError):
            os.close(master_fd)
        with contextlib.suppress(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)
    return None
