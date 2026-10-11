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
import shutil
import socket
import sys
import time

from pages import HELP_PAGES, TOP_COMMANDS, FRONT_NOTES
from render import render_front, render_page, term_width

SOCKET_PATH = "/data/data/com.termux/files/usr/tmp/apatch-chroot-broker.sock"
# The broker runs as the Termux uid and owns this app_data_file socket, which
# the untrusted_app (this CLI) CAN reach. The root core dials the broker; we
# never touch the core's root socket directly (SELinux denies that). See
# broker.py. Older installs used these; kept as fallbacks.
SOCKET_FALLBACKS = (
    "/data/local/tmp/apatch-chroot.sock",
    "/dev/apatch-chroot.sock",
)
VERSION = "v0.4.0.2"
PROGRAM = "apatch-chroot"
# Where check-update looks for the latest release.
GITHUB_REPO = "Developer-lfierro743/apatch-chroot"
RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases/latest"


def connect(wait: bool = True, timeout: float = 20.0):
    """Connect to the daemon socket, trying the primary path then fallbacks.

    After a reboot the daemon needs time to start (Android finishes booting,
    then FBE unlocks Termux's home, then the watchdog launches python). During
    that window the socket does not exist yet. With wait=True we retry for up
    to `timeout` seconds instead of crashing with a raw traceback — the common
    "I just rebooted and it's not up yet" case.
    """
    deadline = time.monotonic() + (timeout if wait else 0)
    last = None
    _spawned_broker = False
    while True:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        for path in (SOCKET_PATH, *SOCKET_FALLBACKS):
            try:
                s.connect(path)
                return s
            except OSError as e:
                last = e
        s.close()
        # The broker runs as the Termux uid (that's the whole point — it owns an
        # app_data_file socket we can reach). If it isn't up, start it once,
        # then keep waiting for it to come ready.
        if not _spawned_broker:
            _spawned_broker = True
            _start_broker()
        if time.monotonic() >= deadline:
            break
        time.sleep(1.0)
    raise DaemonNotReady(last)


def _start_broker():
    """Spawn the broker (Termux-uid relay) detached, if not already running."""
    import subprocess

    broker = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "broker.py"
    )
    broker = os.path.normpath(broker)
    if not os.path.exists(broker):
        # Installed layout: broker sits beside the client in the module/lib dir.
        for cand in (
            "/data/adb/modules/apatch_chroot/daemon/broker.py",
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "broker.py"),
        ):
            if os.path.exists(cand):
                broker = cand
                break
    if not os.path.exists(broker):
        return
    # Don't stack brokers: skip if one already holds the socket path's process.
    try:
        subprocess.Popen(
            [sys.executable, broker],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        pass


class DaemonNotReady(Exception):
    """The daemon socket never appeared within the wait window."""

    def __init__(self, cause):
        self.cause = cause
        super().__init__(str(cause))

    def hint(self) -> str:
        return (
            "Could not reach the apatch-chroot daemon.\n"
            "  - Just rebooted? The daemon starts ~30-60s after boot (it waits\n"
            "    for Android to finish booting and Termux storage to unlock).\n"
            "    Wait a moment and retry.\n"
            "  - Still failing? Check the module is enabled in APatch and the\n"
            "    watchdog is running:\n"
            "      su -c 'pgrep -f apatch-chroot/daemon/daemon.py'\n"
            "      su -c 'tail /data/adb/modules/apatch_chroot/daemon.log'"
        )


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
        print(f"daemon alive: uid={d['uid']} pid={d['pid']} ({d.get('version', '?')})")
        # One-time banner when the daemon just updated to a new version.
        prev = d.get("just_updated")
        if prev:
            print(f"  ↑ updated from {prev} — welcome to {d.get('version', 'the latest')}!")
        return 0
    print(f"error: {resp}", file=sys.stderr)
    return 1


def _human(nbytes):
    """Format bytes as a human-readable size (GiB/MiB)."""
    n = float(nbytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TiB"


def cmd_info():
    s = connect()
    request(s, {"cmd": "info"})
    resp = read_control_line(s)
    s.close()
    if not (resp and resp.get("ok")):
        print(f"error: {(resp or {}).get('error', 'could not reach daemon')}", file=sys.stderr)
        return 1
    d = resp["data"]
    caps = d.get("capabilities", {})
    seccomp_mode = caps.get("seccomp_mode")
    seccomp_txt = {None: "unknown", 0: "none (unfiltered — good)", 1: "strict", 2: "filtered"}.get(
        seccomp_mode, f"mode {seccomp_mode}"
    )
    print("apatch-chroot info")
    print(f"  daemon:      pid {d.get('daemon_pid')} (uid {d.get('daemon_uid')})")
    print(f"  python:      {d.get('python')}")
    print(f"  kernel:      {d.get('kernel')}")
    print(f"  arch:        {d.get('arch')}")
    print(f"  containers:  {d.get('containers')} ({_human(d.get('containers_bytes', 0))})")
    print(f"  store:       {d.get('containers_dir')}")
    print("  capabilities:")
    print(f"    mount namespace:   {'yes' if caps.get('mount_namespace') else 'NO'}")
    print(f"    binfmt_misc:       {'yes' if caps.get('binfmt_misc') else 'no'} (for --box64)")
    print(f"    seccomp:           {seccomp_txt}")
    return 0


def cmd_stats():
    """Show per-container disk usage, running sessions, and default user."""
    s = connect()
    request(s, {"cmd": "stats"})
    resp = read_control_line(s)
    s.close()
    if not (resp and resp.get("ok")):
        print(f"error: {(resp or {}).get('error', 'could not reach daemon')}", file=sys.stderr)
        return 1
    containers = resp["data"].get("containers", [])
    if not containers:
        print("no containers installed.")
        return 0
    # Column header + rows. Truncate image to keep it phone-width friendly.
    print(f"{'NAME':<16}{'SIZE':>10}  {'SESSIONS':>8}  {'USER':<10}IMAGE")
    total = 0
    for c in containers:
        total += c.get("size_bytes", 0)
        img = c.get("image") or "-"
        if len(img) > 28:
            img = img[:27] + "…"
        print(
            f"{c['name']:<16}{_human(c.get('size_bytes', 0)):>10}  "
            f"{c.get('running_sessions', 0):>8}  {c.get('default_user', 'root'):<10}{img}"
        )
    print(f"{'TOTAL':<16}{_human(total):>10}")
    return 0


def _version_tuple(v):
    """Turn 'v0.4.0.1' into (0, 4, 0, 1) for comparison, always length 4.

    Supports a 4th 'subpatch' component for hotfixes (v0.4.0.1, v0.4.0.2, …).
    Pads with zeros so indexing is always safe. Non-numeric parts -> 0.
    """
    import re
    nums = [int(n) for n in re.findall(r"\d+", v or "")]
    while len(nums) < 4:
        nums.append(0)
    return tuple(nums[:4])


def cmd_check_update():
    """Check GitHub for a newer release than the installed VERSION."""
    import urllib.request
    print(f"installed: {VERSION}")
    try:
        req = urllib.request.Request(
            RELEASES_API, headers={"User-Agent": f"apatch-chroot/{VERSION}"}
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        # No network / GitHub unreachable — not fatal, just can't check.
        print(f"could not check for updates: {e}")
        print(f"check manually: {RELEASES_URL}")
        return 0
    latest = data.get("tag_name", "")
    if not latest:
        print("no release found upstream.")
        return 0
    cur = _version_tuple(VERSION)
    lat = _version_tuple(latest)
    if lat > cur:
        # Classify the bump across 4 slots: major, minor, patch, subpatch.
        if lat[0] > cur[0]:
            kind = "MAJOR"
        elif lat[1] > cur[1]:
            kind = "minor"
        elif lat[2] > cur[2]:
            kind = "patch"
        else:
            kind = "subpatch"
        print(f"latest:    {latest}  ← {kind} UPDATE AVAILABLE")
        # Point at the zip asset if the release has one.
        for asset in data.get("assets", []):
            if asset.get("name", "").endswith(".zip"):
                print(f"download:  {asset.get('browser_download_url')}")
                break
        print(f"or visit:  {RELEASES_URL}")
        print("then flash the zip in APatch and reboot.")
        return 1
    print(f"latest:    {latest}  — you're up to date.")
    return 0


def _fetch_latest_release():
    """Fetch the latest release metadata from GitHub. Returns (tag, zip_url) or
    (None, None) on failure. Shared by check-update and self-update."""
    import urllib.request
    try:
        req = urllib.request.Request(
            RELEASES_API, headers={"User-Agent": f"apatch-chroot/{VERSION}"}
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        return None, None
    tag = data.get("tag_name", "")
    zip_url = None
    for asset in data.get("assets", []):
        if asset.get("name", "").endswith(".zip"):
            zip_url = asset.get("browser_download_url")
            break
    return tag or None, zip_url


def cmd_self_update():
    """Download the latest module zip so you can flash it in APatch.

    This does NOT flash or reboot (only you should do that) — it fetches the
    newest release zip to a known path and tells you exactly what to do. Works
    regardless of whether APatch's updateJson polling noticed the release.
    """
    import urllib.request
    tag, zip_url = _fetch_latest_release()
    if not tag:
        print("could not reach GitHub — check your network and try again.")
        return 1
    if _version_tuple(tag) <= _version_tuple(VERSION):
        print(f"you're already on the latest ({VERSION}).")
        return 0
    if not zip_url:
        print(f"release {tag} has no zip asset — get it manually: {RELEASES_URL}")
        return 1
    # Save to shared storage so you can find it in a file manager / APatch.
    dest_dir = "/sdcard/Download"
    dest = os.path.join(dest_dir, "apatch-chroot.zip")
    try:
        os.makedirs(dest_dir, exist_ok=True)
        print(f"downloading {tag} → {dest}")
        req = urllib.request.Request(
            zip_url, headers={"User-Agent": f"apatch-chroot/{VERSION}"}
        )
        with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as f:
            shutil.copyfileobj(resp, f)
    except Exception as e:
        print(f"download failed: {e}")
        print(f"get it manually: {zip_url}")
        return 1
    size = os.path.getsize(dest)
    print(f"downloaded {size} bytes to {dest}")
    print()
    print("To install:")
    print("  1. APatch → Modules → Install from storage")
    print("  2. Pick /sdcard/Download/apatch-chroot.zip")
    print("  3. Reboot")
    return 0


def _stream_controls(s, on_progress):
    """Read framed control messages until a final ok/error one. Returns (ok, data)."""
    while True:
        hdr = _read_exact(s, 1)
        if hdr is None:
            return False, {"error": "connection closed"}
        if hdr == b"\x01":  # stray data frame in a control context; discard
            lenb = _read_exact(s, 4)
            if lenb is None:
                return False, {"error": "connection closed"}
            _read_exact(s, int.from_bytes(lenb, "big"))
            continue
        line = b""
        while not line.endswith(b"\n"):
            c = s.recv(1)
            if not c:
                return False, {"error": "connection closed"}
            line += c
        msg = json.loads(line[:-1])
        if "phase" in msg:
            on_progress(msg["phase"], msg.get("detail", ""))
            continue
        if msg.get("ok"):
            return True, msg.get("data", {})
        return False, {"error": msg.get("error", "unknown error")}


def cmd_install(image, name):
    s = connect()
    args = {"image": image}
    if name:
        args["name"] = name
    request(s, {"cmd": "install", "args": args})

    def on_progress(phase, detail):
        print(f"  [{phase}] {detail}", file=sys.stderr)

    ok, data = _stream_controls(s, on_progress)
    s.close()
    if ok:
        print(f"installed '{data['installed']}' -> {data['rootfs']}")
        return 0
    print(f"error: {data['error']}", file=sys.stderr)
    return 1


def cmd_list():
    s = connect()
    request(s, {"cmd": "list"})
    resp = read_control_line(s)
    s.close()
    if not resp or not resp.get("ok"):
        print(f"error: {resp}", file=sys.stderr)
        return 1
    containers = resp["data"]["containers"]
    if not containers:
        print("no containers installed")
        return 0
    name_w = max(len(c["name"]) for c in containers)
    for c in containers:
        mb = c["size_bytes"] / (1024 * 1024)
        print(f"  {c['name'].ljust(name_w)}  {c['image'] or '(unknown)':30s}  {mb:8.1f} MB")
    return 0


def cmd_remove(name):
    s = connect()
    request(s, {"cmd": "remove", "args": {"name": name}})
    resp = read_control_line(s)
    s.close()
    if resp and resp.get("ok"):
        print(f"removed '{name}'")
        return 0
    print(f"error: {(resp or {}).get('error', 'unknown')}", file=sys.stderr)
    return 1


def cmd_convert(name, as_name, move=False):
    s = connect()
    args = {"name": name}
    if as_name:
        args["as_name"] = as_name
    if move:
        args["move"] = True
    request(s, {"cmd": "convert", "args": args})

    def on_progress(phase, detail):
        print(f"  [{phase}] {detail}", file=sys.stderr)

    ok, data = _stream_controls(s, on_progress)
    s.close()
    if ok:
        src = f" (from {data['image']})" if data.get("image") else ""
        if data.get("moved"):
            print(f"moved '{data['converted']}'{src} — renamed, no copy")
        else:
            print(f"converted '{data['converted']}'{src} — {data['entries']} entries")
        return 0
    print(f"error: {data['error']}", file=sys.stderr)
    return 1


def cmd_kill(name):
    s = connect()
    request(s, {"cmd": "kill", "args": {"name": name}})
    resp = read_control_line(s)
    s.close()
    if resp and resp.get("ok"):
        killed = resp["data"].get("killed", [])
        note = resp["data"].get("note")
        if killed:
            print(f"stopped sessions of '{name}': pids {killed}")
        elif note:
            print(note)
        else:
            print(f"no running sessions of '{name}'")
        return 0
    print(f"error: {(resp or {}).get('error', 'unknown')}", file=sys.stderr)
    return 1


def cmd_exec(name, command, use_init=False, use_box64=False, user=None, as_root=False, shared_display=False, shared_audio=False):
    """Run a command non-interactively; print its output and exit code."""
    s = connect()
    request(s, {"cmd": "exec", "args": {"name": name, "command": command, "use_init": use_init, "use_box64": use_box64, "user": user, "as_root": as_root, "shared_display": shared_display, "shared_audio": shared_audio}})
    resp = read_control_line(s)
    s.close()
    if not resp or not resp.get("ok"):
        print(f"error: {(resp or {}).get('error', 'unknown')}", file=sys.stderr)
        return 1
    data = resp["data"]
    if data.get("stdout"):
        sys.stdout.write(data["stdout"])
    if data.get("stderr"):
        sys.stderr.write(data["stderr"])
    return data.get("exit", 0)


def cmd_clone(src, dst):
    s = connect()
    request(s, {"cmd": "clone", "args": {"src": src, "dst": dst}})
    resp = read_control_line(s)
    s.close()
    if resp and resp.get("ok"):
        print(f"cloned '{src}' -> '{resp['data']['cloned']}' ({resp['data']['entries']} entries)")
        return 0
    print(f"error: {(resp or {}).get('error', 'unknown')}", file=sys.stderr)
    return 1


def cmd_push(name, src, dest):
    s = connect()
    request(s, {"cmd": "push", "args": {"name": name, "src": src, "dest": dest}})
    resp = read_control_line(s)
    s.close()
    if resp and resp.get("ok"):
        print(f"pushed {resp['data']['copied']} entrie(s) into '{name}:{resp['data']['to']}'")
        return 0
    print(f"error: {(resp or {}).get('error', 'unknown')}", file=sys.stderr)
    return 1


def cmd_pull(name, src, dest):
    s = connect()
    request(s, {"cmd": "pull", "args": {"name": name, "src": src, "dest": dest}})
    resp = read_control_line(s)
    s.close()
    if resp and resp.get("ok"):
        print(f"pulled {resp['data']['copied']} entrie(s) to {resp['data']['to']}")
        return 0
    print(f"error: {(resp or {}).get('error', 'unknown')}", file=sys.stderr)
    return 1


# Verbs we accept for `service`. Anything else is passed through to the shim.
SERVICE_VERBS = {
    "start", "stop", "restart", "reload", "status", "enable", "disable",
    "is-active", "is-enabled", "list-units",
}


def cmd_service(name, verb, unit=None):
    """Manage a container's services from the host via the systemctl shim."""
    if verb not in SERVICE_VERBS:
        print(
            f"error: unknown service verb '{verb}'. Use one of: "
            + " ".join(sorted(SERVICE_VERBS)),
            file=sys.stderr,
        )
        return 2
    s = connect()
    request(s, {"cmd": "service", "args": {"name": name, "verb": verb, "unit": unit}})
    resp = read_control_line(s)
    s.close()
    if not resp or not resp.get("ok"):
        print(f"error: {(resp or {}).get('error', 'unknown')}", file=sys.stderr)
        return 1
    data = resp["data"]
    if data.get("stdout"):
        sys.stdout.write(data["stdout"])
    if data.get("stderr"):
        sys.stderr.write(data["stderr"])
    return data.get("exit", 0)


def cmd_setuser(name, user):
    """Set a container's default login user ('' or 'root' restores root)."""
    s = connect()
    request(s, {"cmd": "setuser", "args": {"name": name, "user": user}})
    resp = read_control_line(s)
    s.close()
    if resp and resp.get("ok"):
        d = resp["data"].get("user") or "root"
        print(f"container '{name}' will now log in as '{d}' by default")
        return 0
    print(f"error: {(resp or {}).get('error', 'unknown')}", file=sys.stderr)
    return 1


def cmd_run(name, command, use_init=False, use_box64=False, user=None, as_root=False, shared_display=False, shared_audio=False):
    s = connect()
    request(s, {"cmd": "run", "args": {"name": name, "command": command, "use_init": use_init, "use_box64": use_box64, "user": user, "as_root": as_root, "shared_display": shared_display, "shared_audio": shared_audio}})
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


# Flags shared by run/login/exec. Boolean flags have no value; --user takes one.
_BOOL_FLAGS = ("--init", "--box64", "--root", "--shared-display", "--shared-x11")
_VALUE_FLAGS = ("--user",)


def parse_session_flags(rest):
    """Split CLI flags from the command for run/login/exec.

    Returns (opts, command) where opts is a dict of the recognized flags and
    command is the remaining positional args (the command to run). --shared-x11
    is an alias for --shared-display.
    """
    opts = {
        "use_init": False,
        "use_box64": False,
        "as_root": False,
        "shared_display": False,
        "shared_audio": False,
        "user": None,
    }
    cmd = []
    i = 0
    while i < len(rest):
        a = rest[i]
        if a in ("--shared-display", "--shared-x11"):
            opts["shared_display"] = True
        elif a in ("--shared-audio", "--shared-sound"):
            opts["shared_audio"] = True
        elif a == "--init":
            opts["use_init"] = True
        elif a == "--box64":
            opts["use_box64"] = True
        elif a == "--root":
            opts["as_root"] = True
        elif a == "--user":
            if i + 1 < len(rest):
                opts["user"] = rest[i + 1]
                i += 1
        else:
            cmd.append(a)
        i += 1
    # Drop a leading '--' separator.
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    return opts, cmd


def main(argv):
    # Help interception: --help / -h / no command, before any dispatch.
    args = argv[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        if len(args) >= 2 and args[1] in HELP_PAGES:
            render_page(args[1], HELP_PAGES[args[1]], term_width())
        else:
            render_front(PROGRAM, VERSION, TOP_COMMANDS, term_width(), notes=FRONT_NOTES)
        return 0
    if args[0] in HELP_PAGES and ("-h" in args[1:] or "--help" in args[1:]):
        render_page(args[0], HELP_PAGES[args[0]], term_width())
        return 0

    cmd = args[0]
    if cmd == "ping":
        return cmd_ping()
    if cmd == "info":
        return cmd_info()
    if cmd == "stats":
        return cmd_stats()
    if cmd in ("check-update", "update"):
        return cmd_check_update()
    if cmd == "self-update":
        return cmd_self_update()
    if cmd == "install":
        if len(args) < 2:
            print("usage: apatch-chroot install IMAGE [AS_NAME]", file=sys.stderr)
            return 2
        return cmd_install(args[1], args[2] if len(args) > 2 else None)
    if cmd == "list":
        return cmd_list()
    if cmd == "remove":
        if len(args) < 2:
            print("usage: apatch-chroot remove NAME", file=sys.stderr)
            return 2
        return cmd_remove(args[1])
    if cmd == "convert":
        # Strip flags from the positional args (NAME, AS_NAME).
        positional = [a for a in args[1:] if not a.startswith("--")]
        move = "--move" in args
        if not positional:
            print("usage: apatch-chroot convert NAME [AS_NAME] [--move]", file=sys.stderr)
            print("  --move  rename instead of copy (zero extra space; consumes the source)", file=sys.stderr)
            return 2
        return cmd_convert(positional[0], positional[1] if len(positional) > 1 else None, move=move)
    if cmd == "kill":
        if len(args) < 2:
            print("usage: apatch-chroot kill NAME", file=sys.stderr)
            return 2
        return cmd_kill(args[1])
    if cmd == "exec":
        if len(args) < 3:
            print("usage: apatch-chroot exec NAME [flags] -- CMD [ARG...]", file=sys.stderr)
            return 2
        name = args[1]
        opts, command = parse_session_flags(args[2:])
        if not command:
            print("usage: apatch-chroot exec NAME [flags] -- CMD [ARG...]", file=sys.stderr)
            return 2
        return cmd_exec(name, command, **opts)
    if cmd == "clone":
        if len(args) < 3:
            print("usage: apatch-chroot clone SRC DST", file=sys.stderr)
            return 2
        return cmd_clone(args[1], args[2])
    if cmd == "push":
        if len(args) < 4:
            print("usage: apatch-chroot push NAME HOST_SRC CONTAINER_DEST", file=sys.stderr)
            return 2
        return cmd_push(args[1], args[2], args[3])
    if cmd == "pull":
        if len(args) < 4:
            print("usage: apatch-chroot pull NAME CONTAINER_SRC HOST_DEST", file=sys.stderr)
            return 2
        return cmd_pull(args[1], args[2], args[3])
    if cmd == "service":
        if len(args) < 3:
            print("usage: apatch-chroot service NAME VERB [UNIT]", file=sys.stderr)
            print("  verbs: " + " ".join(sorted(SERVICE_VERBS)), file=sys.stderr)
            return 2
        unit = args[3] if len(args) > 3 else None
        return cmd_service(args[1], args[2], unit)
    if cmd == "setuser":
        if len(args) < 2:
            print("usage: apatch-chroot setuser NAME [USER]", file=sys.stderr)
            print("  apatch-chroot setuser ubuntu luis     # log in as 'luis' by default")
            print("  apatch-chroot setuser ubuntu root     # back to root")
            return 2
        user = args[2] if len(args) > 2 else "root"
        return cmd_setuser(args[1], user)
    if cmd == "run":
        if len(args) < 2:
            print("usage: apatch-chroot run <name> [flags] [-- CMD...]", file=sys.stderr)
            print("  flags: --init --box64 --shared-display(--shared-x11) --shared-audio --user U --root", file=sys.stderr)
            return 2
        name = args[1]
        opts, command = parse_session_flags(args[2:])
        # Default to a login shell so a bare 'run ubuntu' feels like a real
        # login (loads /etc/profile, proper prompt). An explicit command
        # (after --) runs as-is, no -l.
        command = command or ["/bin/bash", "-l"]
        return cmd_run(name, command, **opts)
    if cmd == "login":
        if len(args) < 2:
            print("usage: apatch-chroot login <name> [flags]", file=sys.stderr)
            print("  flags: --init --box64 --shared-display(--shared-x11) --shared-audio --user U --root", file=sys.stderr)
            return 2
        name = args[1]
        opts, _command = parse_session_flags(args[2:])
        # A real login shell: bash -l sources /etc/profile + ~/.bash_profile,
        # sets a proper prompt (not the bare 'root@localhost:/#'), PATH, etc.
        # This is what makes 'login' feel like logging into a real machine.
        return cmd_run(name, ["/bin/bash", "-l"], **opts)
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except DaemonNotReady as e:
        # Friendly message instead of a raw Python traceback — the daemon not
        # being up (usually right after a reboot) is expected, not a crash.
        print(f"apatch-chroot: {e.hint()}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
    except BrokenPipeError:
        sys.exit(0)
