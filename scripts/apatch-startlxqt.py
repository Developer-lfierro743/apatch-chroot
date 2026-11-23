#!/usr/bin/python3
"""Start one Ubuntu LXQt desktop; never start or use a host service manager."""

import argparse
from contextlib import contextmanager, suppress
import os
from pathlib import Path
import re
import select
import signal
import subprocess
import sys
import tempfile
from xml.sax.saxutils import escape


PULSE_SOCKET = "unix:/tmp/.apatch-pulse/native"
AUTOSTART_MASKS = (
    "lxqt-policykit-agent.desktop",
    "lxqt-powermanagement.desktop",
    "lxqt-qlipper-autostart.desktop",
    "lxqt-xscreensaver-autostart.desktop",
    "print-applet.desktop",
    "pulseaudio.desktop",
    "xiccd.desktop",
)


def run_probe(argv, env):
    return subprocess.run(argv, env=env, capture_output=True, text=True, timeout=4)


def require_display(env):
    display = env.get("DISPLAY", "")
    if not re.fullmatch(r":[0-9]+(?:\.[0-9]+)?", display):
        raise RuntimeError("no local X11 display; launch from Termux with apatch-chroot desktop NAME")
    probe = run_probe(["xdpyinfo"], env)
    if probe.returncode:
        raise RuntimeError(f"cannot open X11 display {display}; check Termux:X11 and Xauthority")
    probe = run_probe(["xprop", "-root", "_NET_SUPPORTING_WM_CHECK"], env)
    if probe.returncode:
        raise RuntimeError(f"could not inspect X11 display {display}")
    match = re.search(r"window id #\s*(0x[0-9a-f]+)", probe.stdout, re.IGNORECASE)
    # A WM that already exited can leave a stale root property. Confirm the
    # supporting window still exists before blocking the new desktop.
    if match and int(match[1], 16):
        wm = run_probe(["xprop", "-id", match[1], "_NET_SUPPORTING_WM_CHECK"], env)
        if not wm.returncode and match[1].lower() in wm.stdout.lower():
            raise RuntimeError(f"X11 display {display} already has a window manager; log out of it first")
    return display


def write_once(path, text):
    try:
        with path.open("x", encoding="utf-8") as f:
            f.write(text)
    except FileExistsError:
        pass  # Leave a user's existing profile settings alone.


def prepare_profile(home):
    profile = home / ".config/apatch-chroot/lxqt"
    autostart = profile / "autostart"
    autostart.mkdir(mode=0o700, parents=True, exist_ok=True)
    for filename in AUTOSTART_MASKS:
        write_once(autostart / filename, "[Desktop Entry]\nType=Application\nHidden=true\n")
    config = profile / "lxqt"
    config.mkdir(mode=0o700, exist_ok=True)
    # Do not let absence of a conventional desktop login open the WM chooser.
    write_once(config / "session.conf", "[General]\nwindow_manager=openbox\n")
    # Remove hardware mount/volume plugins from the default panel only. Apps
    # still play audio when a shared PulseAudio server is available.
    panel = Path("/usr/share/lxqt/panel.conf")
    if panel.is_file():
        lines = panel.read_text(encoding="utf-8").splitlines()
        lines = ["plugins=" + ",".join(p for p in line[8:].split(",") if p not in ("mount", "volume"))
                 if line.startswith("plugins=") else line for line in lines]
        write_once(config / "panel.conf", "\n".join(lines) + "\n")
    return profile


def audio_environment(env, runtime):
    config = runtime / "pulse-client.conf"
    config.write_text("autospawn = no\nenable-shm = no\n", encoding="utf-8")
    env["PULSE_CLIENTCONFIG"] = str(config)
    if env.get("PULSE_SERVER") == PULSE_SOCKET:
        # Keep the guest's usual cookie when the LXQt profile changes
        # XDG_CONFIG_HOME. Do not read, copy or print authentication data.
        config_home = Path(env.get("XDG_CONFIG_HOME") or Path(env["HOME"]) / ".config")
        cookie = config_home / "pulse/cookie"
        if "PULSE_COOKIE" not in env and cookie.is_file():
            env["PULSE_COOKIE"] = str(cookie)
        try:
            if run_probe(["pactl", "info"], env).returncode == 0:
                return "connected to shared PulseAudio"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        print("apatch-startlxqt: shared PulseAudio unreachable or not authorized; continuing without audio", file=sys.stderr)
    # Do not fall back to a host loopback service, raw devices or a second
    # audio server. An explicit private, absent address disables autodiscovery.
    env["PULSE_SERVER"] = f"unix:{runtime}/no-pulse"
    return "audio disabled"


def stop_child(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


@contextmanager
def private_bus(runtime, kind, env):
    if kind not in ("session", "system"):
        raise ValueError("unknown bus type")
    address = f"unix:path={runtime}/{kind}-bus"
    config = runtime / f"{kind}.conf"
    # The socket lives under XDG_RUNTIME_DIR (0700); use filesystem sockets,
    # not Linux abstract sockets in Android's shared network namespace.
    # Only the session bus activates user services (e.g. accessibility).
    activation = "<standard_session_servicedirs/>" if kind == "session" else ""
    ownership = "allow" if kind == "session" else "deny"
    config.write_text(f'''<busconfig>
  <type>{kind}</type>
  <listen>{escape(address)}</listen>
  <auth>EXTERNAL</auth>
  {activation}
  <policy context="default">
    <allow user="*"/>
    <allow send_destination="*"/>
    <allow receive_sender="*"/>
    <{ownership} own="*"/>
  </policy>
</busconfig>
''', encoding="utf-8")
    proc = subprocess.Popen(
        ["dbus-daemon", f"--config-file={config}", "--nofork", "--nopidfile", "--print-address=1"],
        env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
    )
    try:
        pipe = proc.stdout
        assert pipe is not None  # Popen above creates this pipe.
        readable, _, _ = select.select([pipe], [], [], 4)
        ready = os.read(pipe.fileno(), 4096) if readable else b""
        if not ready.startswith(address.encode() + b","):
            raise RuntimeError(f"private {kind} D-Bus did not start; check dbus and the guest machine ID")
        yield address
    finally:
        stop_child(proc)
        if proc.stdout is not None:
            proc.stdout.close()
        (runtime / f"{kind}-bus").unlink(missing_ok=True)


def check_bus(address, env):
    result = run_probe(["dbus-send", f"--bus={address}", "--print-reply",
                        "--dest=org.freedesktop.DBus", "/org/freedesktop/DBus",
                        "org.freedesktop.DBus.ListNames"], env)
    if result.returncode:
        raise RuntimeError(f"private D-Bus health check failed: {result.stderr.strip()}")


def run_lxqt(env):
    child = subprocess.Popen(["/usr/bin/startlxqt"], env=env,
                             stdin=subprocess.DEVNULL, start_new_session=True)
    try:
        # Keep the process-group leader unreaped until we have signalled its
        # own group. Its PID cannot be reused by an unrelated Android process.
        os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT)
    finally:
        with suppress(ProcessLookupError):
            os.killpg(child.pid, signal.SIGTERM)
        try:
            child.wait(timeout=4)
        except subprocess.TimeoutExpired:
            with suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGKILL)
            child.wait()
    return child.returncode if child.returncode >= 0 else 128 - child.returncode


class SessionInterrupted(Exception):
    pass


def interrupt(signum, frame):
    # Only the first signal unwinds the stack. Let cleanup finish even if the
    # user presses Ctrl+C again as the session is leaving.
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, signal.SIG_IGN)
    raise SessionInterrupted(signum)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="test prerequisites without launching LXQt")
    args = parser.parse_args(argv)
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, interrupt)
    env = os.environ.copy()
    for var in ("DBUS_SESSION_BUS_ADDRESS", "DBUS_SYSTEM_BUS_ADDRESS", "DBUS_SESSION_BUS_PID",
                "DBUS_STARTER_BUS_TYPE", "DBUS_STARTER_ADDRESS", "SESSION_MANAGER", "WAYLAND_DISPLAY",
                "XDG_SESSION_ID", "XDG_SEAT", "XDG_VTNR"):
        env.pop(var, None)
    try:
        if not os.access("/usr/bin/startlxqt", os.X_OK) or not os.access("/usr/bin/openbox", os.X_OK):
            raise RuntimeError("LXQt and Openbox must be installed inside the guest")
        display = require_display(env)
        if not os.path.isdir("/dev/shm") or not os.access("/dev/shm", os.W_OK | os.X_OK):
            raise RuntimeError("guest /dev/shm is unavailable or not writable")
        with tempfile.TemporaryDirectory(prefix="aplx-") as directory:
            runtime = Path(directory)
            env["XDG_RUNTIME_DIR"] = directory
            env["XDG_SESSION_TYPE"] = "x11"
            audio = audio_environment(env, runtime)
            if not args.check:
                env["XDG_CONFIG_HOME"] = str(prepare_profile(Path(env["HOME"])))
            with private_bus(runtime, "system", env) as system:
                env["DBUS_SYSTEM_BUS_ADDRESS"] = system
                with private_bus(runtime, "session", env) as session:
                    env["DBUS_SESSION_BUS_ADDRESS"] = session
                    if args.check:
                        check_bus(system, env)
                        check_bus(session, env)
                        print(f"LXQt ready: X11 {display}, writable /dev/shm, both guest D-Bus instances; {audio}")
                        return 0
                    return run_lxqt(env)
    except SessionInterrupted as e:
        return 128 + e.args[0]
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as e:
        print(f"apatch-startlxqt: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
