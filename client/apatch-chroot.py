#!/data/data/com.termux/files/usr/bin/python3
"""apatch-chroot client — thin Termux CLI.

Talks to the privileged daemon over a unix socket. Does NO privileged syscalls
itself (those all happen in the daemon, outside Android's app seccomp filter),
so it runs fine as the Termux app with no root.

Usage:
  apatch-chroot ping
  apatch-chroot run <name> [-- CMD...]
"""

import json
import os
import socket
import sys

from pages import HELP_PAGES, TOP_COMMANDS
from render import render_front, render_page, term_width

SOCKET_PATH = "/dev/apatch-chroot.sock"
VERSION = "v0.1.0"
PROGRAM = "apatch-chroot"


def connect():
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(SOCKET_PATH)
    return s


def request(s, obj):
    s.sendall(json.dumps(obj).encode() + b"\n")


def _read_exact(s, n):
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def read_control_line(s):
    """Read one control frame (0x02 + json + newline). Skips data frames."""
    while True:
        hdr = _read_exact(s, 1)
        if hdr is None:
            return None
        if hdr == b"\x01":
            lenb = _read_exact(s, 4)
            if lenb is None:
                return None
            n = int.from_bytes(lenb, "big")
            _read_exact(s, n)  # discard unexpected data in control context
            continue
        # 0x02 control
        line = b""
        while not line.endswith(b"\n"):
            c = s.recv(1)
            if not c:
                return None
            line += c
        return json.loads(line[:-1])


def cmd_ping():
    s = connect()
    request(s, {"cmd": "ping"})
    resp = read_control_line(s)
    s.close()
    if resp and resp.get("ok"):
        d = resp["data"]
        print(f"daemon alive: uid={d['uid']} pid={d['pid']}")
        return 0
    print(f"error: {resp}", file=sys.stderr)
    return 1


def cmd_run(name, command):
    s = connect()
    request(s, {"cmd": "run", "args": {"name": name, "command": command}})
    import termios
    import tty

    # Put our terminal in raw mode and shuttle bytes to/from the daemon's PTY.
    old = None
    try:
        old = termios.tcgetattr(0)
        tty.setraw(0)
    except (termios.error, OSError):
        old = None

    import select

    exit_code = 0
    got_pid = False
    try:
        while True:
            readable, _, _ = select.select([s, 0], [], [], 1.0)
            if s in readable:
                hdr = _read_exact(s, 1)
                if hdr is None:
                    break
                if hdr == b"\x01":
                    lenb = _read_exact(s, 4)
                    n = int.from_bytes(lenb, "big")
                    data = _read_exact(s, n)
                    os.write(1, data)
                elif hdr == b"\x02":
                    line = b""
                    while not line.endswith(b"\n"):
                        c = s.recv(1)
                        if not c:
                            break
                        line += c
                    msg = json.loads(line[:-1])
                    if not got_pid:
                        got_pid = True
                        if not msg.get("ok"):
                            print(f"\r\nerror: {msg.get('error')}", file=sys.stderr)
                            return 1
                    else:
                        exit_code = msg.get("data", {}).get("exit", 0)
                        break
            if 0 in readable:
                try:
                    kb = os.read(0, 4096)
                except OSError:
                    kb = b""
                if not kb:
                    break
                s.sendall(kb)
    finally:
        if old is not None:
            with _suppress():
                termios.tcsetattr(0, termios.TCSAFLUSH, old)
    return exit_code


class _suppress:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return True


def main(argv):
    # Help interception: --help / -h / no command, before any dispatch.
    args = argv[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        if len(args) >= 2 and args[1] in HELP_PAGES:
            render_page(args[1], HELP_PAGES[args[1]], term_width())
        else:
            render_front(PROGRAM, VERSION, TOP_COMMANDS, term_width())
        return 0
    if args[0] in HELP_PAGES and ("-h" in args[1:] or "--help" in args[1:]):
        render_page(args[0], HELP_PAGES[args[0]], term_width())
        return 0

    cmd = args[0]
    if cmd == "ping":
        return cmd_ping()
    if cmd == "run":
        if len(args) < 2:
            print("usage: apatch-chroot run <name> [-- CMD...]", file=sys.stderr)
            return 2
        name = args[1]
        rest = args[2:]
        if rest and rest[0] == "--":
            rest = rest[1:]
        command = rest or ["/bin/bash"]
        return cmd_run(name, command)
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
