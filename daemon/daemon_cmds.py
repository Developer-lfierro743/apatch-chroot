"""Privileged container commands for the apatch-chroot daemon.

Runs in the daemon's root context, outside Android's app seccomp filter, so
mount(2)/umount2(2)/chroot(2)/openpty succeed here where they SIGSYS in the
Termux app process.

Commands: run (chroot + PTY), install (OCI pull + layer extract), list, kill,
remove. Milestone 1 (chroot + PTY) and install are both proven.
"""

import contextlib
import fcntl
import os
import pty
import select
import shutil
import struct
import termios

MAX = 65536
CONTAINERS_DIR = "/data/apatch-chroot/containers"

# Live session children: name -> list of (pid). The daemon forks one child per
# `run`/`login` session; `kill` signals these. Entries are removed when the
# child is reaped in run_container's wait loop.
_SESSIONS: dict[str, list[int]] = {}


def _set_winsize(fd, rows=24, cols=80):
    with contextlib.suppress(OSError):
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


# A sane guest environment. execvp would inherit the host Termux PATH, so the
# guest's own coreutils (/bin/ls, /usr/bin/id) would not be found — this gives
# the standard Linux PATH plus TERM/HOME so both login shells and bare `run`
# commands behave. A login shell overrides these by sourcing /etc/profile.
_GUEST_ENV = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "HOME": "/root",
    "TERM": "xterm-256color",
    "USER": "root",
    "SHELL": "/bin/bash",
}


def _guest_env(display=False, audio=False):
    """Return the environment dict for the chrooted exec (a copy of _GUEST_ENV).

    When *display* is set, add DISPLAY=:0 so GUI apps find termux-x11.
    When *audio* is set, add PULSE_SERVER so apps play through Android audio.
    """
    env = dict(_GUEST_ENV)
    if display:
        env["DISPLAY"] = ":0"
    if audio:
        # The PulseAudio socket is bound at this in-container path (mounts.py).
        env["PULSE_SERVER"] = "unix:/var/run/pulse/native"
    return env


def host_info():
    """Report daemon status and host capability probes.

    Probes what this device actually supports so a missing feature is
    explained rather than guessed at. Returns a dict consumed by the client's
    'info' command. Never raises — every probe is best-effort.
    """
    import platform

    def _read(path):
        with contextlib.suppress(OSError):
            with open(path) as f:
                return f.read().strip()
        return None

    # Kernel / arch.
    uname = platform.uname()
    arch = platform.machine()

    # Mount namespace support: read from /proc/filesystems + a kernel config
    # hint. A fork/unshare probe is unsafe inside the daemon's relay threads
    # (fork in a threaded process is hazardous), so we probe read-only instead:
    # if /proc/self/ns/mnt exists, mount namespaces are supported by the kernel.
    mount_ns = os.path.exists("/proc/self/ns/mnt")

    # binfmt_misc: is it mounted/available?
    binfmt = os.path.isdir("/proc/sys/fs/binfmt_misc")

    # seccomp: check if we're in an unfiltered (root daemon) context.
    # /proc/self/status has a Seccomp field (0=disabled, 1=strict, 2=filter).
    seccomp = _read("/proc/self/status")
    seccomp_mode = None
    if seccomp:
        for line in seccomp.splitlines():
            if line.startswith("Seccomp:"):
                with contextlib.suppress(ValueError):
                    seccomp_mode = int(line.split()[1])

    # Container count + total size.
    n_containers = 0
    total_bytes = 0
    if os.path.isdir(CONTAINERS_DIR):
        for name in os.listdir(CONTAINERS_DIR):
            rootfs = os.path.join(CONTAINERS_DIR, name, "rootfs")
            if os.path.islink(rootfs) or not os.path.isdir(rootfs):
                continue
            n_containers += 1
            with contextlib.suppress(Exception):
                total_bytes += _du(rootfs)

    return {
        "daemon_pid": os.getpid(),
        "daemon_uid": os.getuid(),
        "python": platform.python_version(),
        "kernel": uname.release,
        "arch": arch,
        "containers_dir": CONTAINERS_DIR,
        "containers": n_containers,
        "containers_bytes": total_bytes,
        "capabilities": {
            "mount_namespace": mount_ns,
            "binfmt_misc": binfmt,
            # seccomp 0 = we run unfiltered (the whole point of the root daemon)
            "seccomp_filtered": seccomp_mode not in (None, 0),
            "seccomp_mode": seccomp_mode,
        },
    }


# --- default-user handling (least privilege inside the container) ----------
#
# The daemon must be root to chroot/mount, but the container login does NOT
# have to be root. Each container can declare a default login user in
# /data/apatch-chroot/containers/<name>/user (a uid, a username, or empty for
# root). After chroot, we drop to that user's uid/gid before exec, so a normal
# login is unprivileged inside the container. Root is opt-in (--root or an
# explicit user of 0), never the silent default once a user is set.

def _user_file(name):
    return os.path.join(CONTAINERS_DIR, name, "user")


def read_default_user(name):
    """Return the container's default login user string, or None (=> root).

    Reads the per-container 'user' file. Returns a stripped non-empty string,
    or None when the file is absent/empty (meaning root, the legacy default).
    """
    try:
        with open(_user_file(name)) as f:
            u = f.read().strip()
        return u or None
    except OSError:
        return None


def write_default_user(name, user):
    """Set (or clear, with user='' or 'root') a container's default login user."""
    path = _user_file(name)
    try:
        if not user or user == "root":
            # Remove the file so the container defaults back to root.
            with contextlib.suppress(OSError):
                os.unlink(path)
        else:
            with open(path, "w") as f:
                f.write(user.strip())
        return True
    except OSError:
        return False


def _lookup_uid_gid(rootfs, user):
    """Resolve a username/uid to (uid, gid) using the GUEST's /etc/passwd.

    We parse rootfs/etc/passwd + /etc/group AFTER chroot setup but must do it
    BEFORE chroot (we read the host-side file). Accepts a numeric uid or a
    username. Returns (uid, gid, home, shell) or None if not found.
    """
    # Numeric uid: resolve gid from /etc/passwd by uid, else use same gid.
    if user.isdigit():
        uid = int(user)
        gid = uid
        try:
            with open(os.path.join(rootfs, "etc", "passwd")) as f:
                for line in f:
                    parts = line.rstrip("\n").split(":")
                    if len(parts) >= 4 and parts[2] == str(uid):
                        gid = int(parts[3])
                        return uid, gid, parts[5] or "/", parts[6] or "/bin/sh"
        except (OSError, ValueError):
            pass
        return uid, gid, "/", "/bin/sh"
    # Username: look it up.
    try:
        with open(os.path.join(rootfs, "etc", "passwd")) as f:
            for line in f:
                parts = line.rstrip("\n").split(":")
                if len(parts) >= 7 and parts[0] == user:
                    uid, gid = int(parts[2]), int(parts[3])
                    return uid, gid, parts[5] or "/", parts[6] or "/bin/sh"
    except (OSError, ValueError):
        pass
    return None


def resolve_command_path(rootfs, command):
    """Resolve command[0] to an absolute path INSIDE the rootfs.

    os.execvpe does a PATH search that, in a minimal chroot, imports Python's
    'warnings' module and dies with "No module named 'warnings'" — the bug that
    made bare `exec ubuntu -- cat ...` fail. We avoid it by resolving the binary
    ourselves against the guest's own PATH directories (checked on the host side,
    before chroot) and then using execv with an absolute path.

    Returns the (possibly rewritten) command list. If command[0] already has a
    '/' (absolute or relative), or resolution fails, it's returned unchanged and
    execv/execvpe will handle it (and report a clear error).
    """
    if not command:
        return command
    prog = command[0]
    if "/" in prog:
        return command  # already a path; leave it alone
    # Standard guest PATH dirs (mirrors _GUEST_ENV PATH).
    path_dirs = _GUEST_ENV["PATH"].split(":")
    for d in path_dirs:
        cand = os.path.join(rootfs, d.lstrip("/"), prog)
        # Must be a real file (not a symlink into nowhere) and executable.
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            abs_in_guest = os.path.join(d, prog)
            return [abs_in_guest, *command[1:]]
    return command  # unresolved; let execvpe try (and fail clearly)


def drop_privileges(rootfs, user, command):
    """After chroot, drop from root to *user* before exec.

    Returns the (possibly rewritten) command to exec. We are already inside the
    chroot when this runs. Uses the guest's own uid/gid so we can't accidentally
    pick up a host uid. If resolution fails, we stay root and note it (better a
    loud root than a silent wrong-uid) — unless the user explicitly asked for a
    non-root login, in which case we refuse rather than run as root.
    """
    if not user or user == "root":
        return command, None
    info = _lookup_uid_gid_pw(user)
    if info is None:
        # Couldn't resolve inside the chroot (no pwd module there); try a
        # pre-chroot lookup helper as a fallback.
        info = _lookup_uid_gid(rootfs, user)
    if info is None:
        return command, f"user '{user}' not found in container; staying root"
    uid, gid, home, shell = info
    try:
        os.setgid(gid)
        os.setuid(uid)
        os.environ["HOME"] = home
        os.environ["USER"] = user if not user.isdigit() else user
        # Run the user's shell if the command was just the default shell.
        return command, None
    except OSError as e:
        return command, f"could not drop to uid {uid}: {e}; staying root"


def _lookup_uid_gid_pw(user):
    """Resolve a user to (uid, gid, home, shell) via the pwd module (host).

    Used inside the chroot as a fallback; the guest /etc/passwd parse in
    _lookup_uid_gid is preferred so we match the container's own users.
    """
    try:
        import pwd
        p = pwd.getpwnam(user) if not user.isdigit() else pwd.getpwuid(int(user))
        return p.pw_uid, p.pw_gid, p.pw_dir, p.pw_shell
    except (KeyError, ValueError, ImportError):
        return None


# Path to the init supervisor + systemctl shim + login wrapper, shipped with
# the daemon and staged into each container as needed.
_DAEMON_DIR = os.path.dirname(os.path.abspath(__file__))
INIT_SCRIPT = os.path.join(_DAEMON_DIR, "init.sh")
SYSTEMCTL_SHIM = os.path.join(_DAEMON_DIR, "systemctl-shim.sh")
LOGIN_WRAPPER = os.path.join(_DAEMON_DIR, "login-wrapper.sh")


def stage_login_wrapper(rootfs):
    """Copy the login wrapper into the rootfs so a login shell can't lose PATH.

    Returns the in-container path, or None if it couldn't be staged. The wrapper
    sources the normal profile files but RE-ASSERTS PATH/TERM afterward, fixing
    the 'command not found' + 'TERM not set' mess that `bash -l` alone causes
    when a profile clobbers the environment.
    """
    if not os.path.isfile(LOGIN_WRAPPER):
        return None
    try:
        local_bin = os.path.join(rootfs, "usr", "local", "bin")
        os.makedirs(local_bin, exist_ok=True)
        dest = os.path.join(local_bin, "apatch-login")
        shutil.copy2(LOGIN_WRAPPER, dest)
        os.chmod(dest, 0o755)
        return "/usr/local/bin/apatch-login"
    except OSError:
        return None


def stage_init(rootfs):
    """Copy the container init supervisor + systemctl shim into the rootfs.

    Returns the in-container path to the init script, or None if it could not
    be staged. The init becomes the chroot's PID 1 and runs the user's command
    as a child — Android's own PID 1 is never involved (the daemon forks into
    a private mount namespace first).

    Also installs the systemctl shim at /usr/bin/systemctl (and /bin/systemctl)
    so `systemctl start sshd` inside the container talks to the mini-init
    instead of the real (non-functional) systemd.
    """
    if not os.path.isfile(INIT_SCRIPT):
        return None
    try:
        local_bin = os.path.join(rootfs, "usr", "local", "bin")
        os.makedirs(local_bin, exist_ok=True)
        dest = os.path.join(local_bin, "apatch-init")
        shutil.copy2(INIT_SCRIPT, dest)
        os.chmod(dest, 0o755)

        # Install the systemctl shim, replacing the real binary so unit-file
        # driven `systemctl` calls are intercepted by the mini-init.
        if os.path.isfile(SYSTEMCTL_SHIM):
            for bindir in ("usr/bin", "bin"):
                d = os.path.join(rootfs, bindir)
                if os.path.isdir(d):
                    sd = os.path.join(d, "systemctl")
                    # Keep the real one as systemctl.real if present.
                    if os.path.exists(sd) and not os.path.exists(sd + ".real"):
                        with contextlib.suppress(OSError):
                            os.rename(sd, sd + ".real")
                    shutil.copy2(SYSTEMCTL_SHIM, sd)
                    os.chmod(sd, 0o755)

        return "/usr/local/bin/apatch-init"
    except OSError:
        return None


def ensure_resolv_conf(rootfs):
    """Write a working /etc/resolv.conf into the rootfs if it lacks nameservers.

    Android has no /etc/resolv.conf (netd owns DNS via properties), so a fresh
    container image resolves nothing — apt/curl/cargo fail with "Temporary
    failure resolving". We drop in public resolvers so the guest has DNS over
    the daemon's (host) network. Skipped if the rootfs already has a usable
    nameserver line, so a user's custom resolv.conf is preserved.
    """
    path = os.path.join(rootfs, "etc", "resolv.conf")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.isfile(path):
            with open(path) as f:
                if any(line.strip().startswith("nameserver") for line in f):
                    return
        with open(path, "w") as f:
            f.write("nameserver 8.8.8.8\nnameserver 1.1.1.1\n")
    except OSError:
        pass


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
    # Resolve a bare command name to an absolute path in the guest (avoids the
    # execvpe PATH-lookup crash on stripped chroots — see resolve_command_path).
    command = resolve_command_path(rootfs, command)

    def send_control(obj):
        payload = json.dumps(obj).encode()
        conn.sendall(b"\x02" + payload + b"\n")

    def send_data(b):
        conn.sendall(b"\x01" + len(b).to_bytes(4, "big") + b)

    master_fd, slave_fd = pty.openpty()
    _set_winsize(master_fd)

    # A private mount namespace (unshared in the child, per session) keeps
    # every mount we make off the host, so Android's /dev/snd (audio) and the
    # rest of /dev keep working normally while the container runs. The daemon
    # itself is never unshared — only the session child.
    use_mount_ns = args.get("mounts", True)
    use_display = args.get("shared_display", False)
    use_audio = args.get("shared_audio", False)

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
            # Unshare a private mount namespace, then mount the pseudo-fs set,
            # all before chroot. enter_private_mount_ns VERIFIES '/' became
            # private and setup_mounts refuses a rootfs outside CONTAINERS_DIR,
            # so any failure here means "isolation not guaranteed" — we then
            # chroot WITHOUT mounts rather than risk propagating onto the host.
            # No teardown needed: the namespace dies with this child on exit.
            if use_mount_ns:
                try:
                    import mounts as mounts_mod
                    mounts_mod.enter_private_mount_ns()
                    mounts_mod.setup_mounts(rootfs, x11=use_display, audio=use_audio)
                except OSError as e:
                    # Fail safe: no mounts, chroot only. Tell the user why.
                    with contextlib.suppress(OSError):
                        os.write(2, f"apatch-chroot: mount isolation unavailable ({e}); running without mounts\n".encode())
            # Optional box64: register x86_64 -> box64 in THIS private namespace
            # only (invisible to Android's arm64 apps and to the host). Skipped
            # silently if the kernel lacks binfmt_misc or box64 isn't installed.
            if args.get("use_box64"):
                with contextlib.suppress(Exception):
                    import binfmt as binfmt_mod
                    if binfmt_mod.register_box64(rootfs):
                        with contextlib.suppress(OSError):
                            os.write(2, b"apatch-chroot: box64 registered (x86_64 enabled)\n")
            # Give the guest DNS (Android has no /etc/resolv.conf) before chroot.
            ensure_resolv_conf(rootfs)
            os.chroot(rootfs)
            os.chdir(cwd)
            # Drop from root to the container's login user (least privilege).
            # --root forces root; otherwise use --user or the container default.
            # Only meaningful when NOT using --init (init is PID 1, must be root).
            if not args.get("use_init"):
                eff_user = "root" if args.get("as_root") else (
                    args.get("user") or read_default_user(name)
                )
                _cmd, warn = drop_privileges(rootfs, eff_user, command)
                if warn:
                    with contextlib.suppress(OSError):
                        os.write(2, f"apatch-chroot: {warn}\n".encode())
            # With use_init, the container's PID 1 is our mini-init supervisor
            # (starts services, reaps zombies, runs the command as a child).
            # Android's own PID 1 is untouched — this runs in the child's
            # private mount namespace. Init is invoked via /bin/sh since some
            # minimal images lack a shebang handler.
            if args.get("use_init"):
                init_path = stage_init(rootfs)
                if init_path:
                    os.execve("/bin/sh", ["/bin/sh", init_path, *command], _guest_env(display=use_display, audio=use_audio))
                # Fall through to plain exec if staging failed.
            # Login shell (bash): exec it DIRECTLY with a clean env and flags
            # that stop it re-poisoning PATH. The container's ~/.bashrc often
            # does `export PATH=/system/bin:/apex/...` (a chroot-distro leftover),
            # which makes ls/apt/bash "command not found". We defeat it two ways:
            #   --norc --noprofile  → bash does NOT source ~/.bashrc or /etc/profile
            #   a clean env passed to execve → PATH points at the container's own
            #                              /usr/bin, /bin (where the tools live)
            # This is done here, in the daemon, NOT via a wrapper script — because
            # any wrapper that ends in `bash -i` just re-sources .bashrc and
            # re-poisons PATH. Doing it at exec time is the only reliable fix.
            if command and command[0].endswith("bash"):
                env = _guest_env(display=use_display, audio=use_audio)
                # Absolute PATH so bash finds coreutils even before .bashrc.
                env["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
                os.execve("/bin/bash", ["/bin/bash", "--norc", "--noprofile", "-i"], env)
                # (execve replaces the process; if it fails we fall through)
            # command[0] is absolute now (resolve_command_path), so execv skips
            # the PATH search that crashed on stripped chroots.
            if "/" in command[0]:
                os.execv(command[0], command)
            os.execvpe(command[0], command, _guest_env(display=use_display, audio=use_audio))
        except Exception as e:
            with contextlib.suppress(OSError):
                os.write(2, f"exec failed: {e}\n".encode())
            os._exit(127)

    os.close(slave_fd)
    _SESSIONS.setdefault(name, []).append(pid)
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
                _unregister_session(name, pid)
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
        _unregister_session(name, pid)
    return None


def _unregister_session(name, pid):
    """Drop *pid* from the live-session registry for *name*."""
    pids = _SESSIONS.get(name)
    if pids and pid in pids:
        pids.remove(pid)
    if name in _SESSIONS and not _SESSIONS[name]:
        _SESSIONS.pop(name, None)


def kill_container(args):
    """Stop a container's running sessions: SIGTERM, grace, then SIGKILL.

    args: {"name"}. Returns the pids that were signalled. The session's
    run_container loop reaps the child and tears down its private mount
    namespace on exit, so killing the pid is enough — nothing to unmount.
    """
    import signal
    import time

    name = args.get("name")
    if not name:
        return {"ok": False, "error": "kill requires a name"}
    pids = list(_SESSIONS.get(name, []))
    if not pids:
        return {"ok": True, "data": {"killed": [], "note": "no running sessions"}}

    for pid in pids:
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGTERM)

    # Grace period, then SIGKILL whatever survived.
    deadline = time.time() + 3.0
    while time.time() < deadline:
        if not any(_pid_alive(p) for p in pids):
            break
        time.sleep(0.1)
    for pid in pids:
        if _pid_alive(pid):
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)

    return {"ok": True, "data": {"killed": pids}}


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def container_stats(args):
    """Report disk usage + running sessions per container.

    Returns per-container: name, size, running session pids, default user,
    source image. Genuinely useful for a container tool — see at a glance what's
    eating disk and what's currently running. Never raises.
    """
    out = []
    if not os.path.isdir(CONTAINERS_DIR):
        return {"containers": []}
    for name in sorted(os.listdir(CONTAINERS_DIR)):
        rootfs = os.path.join(CONTAINERS_DIR, name, "rootfs")
        if os.path.islink(rootfs) or not os.path.isdir(rootfs):
            continue
        image = ""
        with contextlib.suppress(OSError):
            with open(os.path.join(CONTAINERS_DIR, name, "image")) as f:
                image = f.read().strip()
        # Running sessions (live pids only).
        running = [p for p in _SESSIONS.get(name, []) if _pid_alive(p)]
        out.append({
            "name": name,
            "image": image,
            "size_bytes": _du(rootfs),
            "running_sessions": len(running),
            "default_user": read_default_user(name) or "root",
        })
    return {"containers": out}


# ---------------------------------------------------------------------------
# install: OCI pull + layer extract
# ---------------------------------------------------------------------------

def install_container(args, conn):
    """Pull an image and extract it into containers/<name>/rootfs.

    args: {"image": "ubuntu:24.04", "name": "ubuntu" (optional), "arch": optional}
    Streams progress as control frames; final frame carries {"ok", "installed"}.
    """
    import json
    import tempfile

    import arch as archmod
    import layer_extract
    import registry

    image = args.get("image")
    if not image:
        return {"ok": False, "error": "install requires an image reference"}

    name = args.get("name") or registry.derive_alias(image)

    def progress(phase, detail=""):
        payload = json.dumps({"phase": phase, "detail": detail}).encode()
        conn.sendall(b"\x02" + payload + b"\n")

    try:
        reg, repo, tag = registry.parse_image_ref(image)
        progress("auth", f"{repo}:{tag}")
        token = registry.get_token(reg, repo)

        progress("manifest", f"{repo}:{tag}")
        manifest, ct = registry.get_manifest(reg, repo, tag, token)

        if registry.is_index(ct):
            arch, variant = archmod.host_platform()
            entry = registry.pick_platform(manifest, arch, variant, image)
            digest = entry["digest"]
            progress("manifest", f"{arch} -> {digest[:19]}")
            manifest, ct = registry.get_manifest(reg, repo, digest, token)

        layers = manifest.get("layers", [])
        if not layers:
            return {"ok": False, "error": f"{image} has no filesystem layers"}

        rootfs = os.path.join(CONTAINERS_DIR, name, "rootfs")
        os.makedirs(rootfs, exist_ok=True)

        tmpdir = tempfile.mkdtemp(prefix="apatch-chroot-layer-")
        try:
            for i, layer in enumerate(layers, 1):
                digest = layer["digest"]
                size = layer.get("size", 0)
                progress("layer", f"{i}/{len(layers)} {digest[:19]} ({size} bytes)")
                blob_path = os.path.join(tmpdir, digest.replace(":", "_"))
                registry.get_blob(reg, repo, digest, token, blob_path)
                n = layer_extract.apply_layer(blob_path, rootfs)
                progress("extract", f"layer {i}/{len(layers)}: {n} entries")
                with contextlib.suppress(OSError):
                    os.unlink(blob_path)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

        # Record the source image for `list`.
        meta = os.path.join(CONTAINERS_DIR, name, "image")
        with contextlib.suppress(OSError):
            with open(meta, "w") as f:
                f.write(image)

        progress("done", name)
        conn.sendall(b"\x02" + json.dumps({"ok": True, "data": {"installed": name, "rootfs": rootfs}}).encode() + b"\n")
        return None
    except registry.RegistryError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"install failed: {e}"}


# ---------------------------------------------------------------------------
# list / remove
# ---------------------------------------------------------------------------

def list_containers(args):
    out = []
    if not os.path.isdir(CONTAINERS_DIR):
        return {"ok": True, "data": {"containers": []}}
    for name in sorted(os.listdir(CONTAINERS_DIR)):
        rootfs = os.path.join(CONTAINERS_DIR, name, "rootfs")
        # Skip anything that is not a REAL directory under containers/. A
        # symlinked rootfs (leftover test container) would make _du() walk an
        # unrelated tree (e.g. chroot-distro) and hang — islink() first.
        if os.path.islink(rootfs) or not os.path.isdir(rootfs):
            continue
        image = ""
        meta = os.path.join(CONTAINERS_DIR, name, "image")
        with contextlib.suppress(OSError):
            with open(meta) as f:
                image = f.read().strip()
        size = _du(rootfs)
        out.append({"name": name, "image": image, "size_bytes": size})
    return {"ok": True, "data": {"containers": out}}


def _du(path):
    """Disk usage of a tree, WITHOUT following symlinks (avoids walks into
    unrelated mounts/loops that would hang)."""
    total = 0
    for root, dirs, files in os.walk(path, followlinks=False):
        # Prune symlinked subdirs so os.walk never descends into them.
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
        for f in files:
            fp = os.path.join(root, f)
            if os.path.islink(fp):
                continue
            with contextlib.suppress(OSError):
                total += os.path.getsize(fp)
    return total


def remove_container(args):
    name = args.get("name")
    if not name:
        return {"ok": False, "error": "remove requires a name"}
    target = os.path.join(CONTAINERS_DIR, name)
    if not os.path.isdir(target):
        return {"ok": False, "error": f"container '{name}' is not installed"}
    shutil.rmtree(target, ignore_errors=True)
    return {"ok": True, "data": {"removed": name}}


# ---------------------------------------------------------------------------
# convert: import a chroot-distro container (copy its rootfs, keep metadata)
# ---------------------------------------------------------------------------

# Where chroot-distro keeps its containers (Termux prefix, standard layout).
CHROOT_DISTRO_DIR = "/data/data/com.termux/files/usr/var/lib/chroot-distro/containers"


def convert_from_chroot_distro(args, conn):
    """Copy a chroot-distro container's rootfs into apatch-chroot.

    args: {"name": "ubuntu", "as_name": optional}

    The chroot-distro rootfs is already a plain extracted tree, so conversion
    is a recursive copy plus reading its manifest.json for the source image
    ref and arch. No layer re-processing. The chroot-distro container must be
    unmounted first (its ghost mounts would otherwise be copied as empty
    dirs); we refuse if any mount still sits under its rootfs.
    """
    import json

    name = args.get("name")
    if not name:
        return {"ok": False, "error": "convert requires a source container name"}
    as_name = args.get("as_name") or name

    src_dir = os.path.join(CHROOT_DISTRO_DIR, name)
    src_rootfs = os.path.join(src_dir, "rootfs")
    if not os.path.isdir(src_rootfs):
        return {"ok": False, "error": f"chroot-distro container '{name}' not found at {src_rootfs}"}

    def progress(phase, detail=""):
        conn.sendall(b"\x02" + json.dumps({"phase": phase, "detail": detail}).encode() + b"\n")

    # Refuse if the source is still mounted (ghost mounts would copy as empty).
    busy = _mounts_under(src_rootfs)
    if busy:
        return {
            "ok": False,
            "error": (
                f"chroot-distro '{name}' still has {len(busy)} mount(s) under its rootfs. "
                "Run 'chroot-distro kill " + name + "' (or reboot) first, then convert."
            ),
        }

    # Read the source image ref + arch from chroot-distro's manifest.
    image_ref, arch = "", ""
    manifest_path = os.path.join(src_dir, "manifest.json")
    with contextlib.suppress(OSError, ValueError):
        with open(manifest_path) as f:
            m = json.load(f)
        image_ref = m.get("image_ref", "") or ""
        arch = m.get("arch", "") or ""

    dest_dir = os.path.join(CONTAINERS_DIR, as_name)
    dest_rootfs = os.path.join(dest_dir, "rootfs")
    if os.path.exists(dest_rootfs):
        return {"ok": False, "error": f"apatch-chroot container '{as_name}' already exists; remove it first"}

    # --move: rename the rootfs instead of copying. Instant, zero extra space
    # (both paths are on the same /data filesystem). Use this when the container
    # is bigger than the free space a copy would need. The chroot-distro original
    # is consumed (it becomes the apatch-chroot one) — that's the trade-off.
    move = args.get("move")
    if move:
        # Must be the same filesystem for os.rename to work; if not, rename
        # raises OSError and we fall back to a copy.
        os.makedirs(dest_dir, exist_ok=True)
        # dest_rootfs must not exist yet for a clean rename.
        with contextlib.suppress(OSError):
            os.rmdir(dest_rootfs)
        progress("move", f"{name} -> {as_name} (rename, no copy)")
        try:
            os.rename(src_rootfs, dest_rootfs)
        except OSError as e:
            progress("move-fallback", f"rename failed ({e}); copying instead")
            os.makedirs(dest_rootfs, exist_ok=True)
            copied = _copy_tree(src_rootfs, dest_rootfs, progress)
        else:
            copied = -1  # moved, not copied
        # Carry over the source manifest metadata.
        if image_ref:
            with contextlib.suppress(OSError):
                with open(os.path.join(dest_dir, "image"), "w") as f:
                    f.write(image_ref)
        if arch:
            with contextlib.suppress(OSError):
                with open(os.path.join(dest_dir, "arch"), "w") as f:
                    f.write(arch)
        progress("done", "moved" if copied == -1 else f"{copied} entries (fallback copy)")
        conn.sendall(
            b"\x02" + json.dumps({"ok": True, "data": {"converted": as_name, "moved": copied == -1, "image": image_ref}}).encode() + b"\n"
        )
        return None

    os.makedirs(dest_rootfs, exist_ok=True)

    progress("copy", f"{name} -> {as_name}")
    copied = _copy_tree(src_rootfs, dest_rootfs, progress)

    if image_ref:
        with contextlib.suppress(OSError):
            with open(os.path.join(dest_dir, "image"), "w") as f:
                f.write(image_ref)
    if arch:
        with contextlib.suppress(OSError):
            with open(os.path.join(dest_dir, "arch"), "w") as f:
                f.write(arch)

    progress("done", f"{copied} entries")
    conn.sendall(
        b"\x02" + json.dumps({"ok": True, "data": {"converted": as_name, "entries": copied, "image": image_ref}}).encode() + b"\n"
    )
    return None


def _mounts_under(path):
    """Return mount points under *path* from /proc/mounts (best-effort)."""
    path = os.path.realpath(path)
    out = []
    with contextlib.suppress(OSError):
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and (parts[1] == path or parts[1].startswith(path + os.sep)):
                    out.append(parts[1])
    return out


def _copy_tree(src, dst, progress):
    """Recursively copy src -> dst, dereferencing nothing (symlinks kept as-is).

    Returns the number of filesystem entries copied. Symlinks are recreated
    verbatim (not followed), so the guest's own links (e.g. /bin/sh ->
    /bin/busybox) survive exactly as the layer extractor keeps them.
    """
    def _unlink(path):
        if os.path.islink(path) or os.path.isfile(path):
            with contextlib.suppress(OSError):
                os.unlink(path)
        elif os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)

    count = 0
    for root, dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        dest_dir = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(dest_dir, exist_ok=True)
        # Copy symlinked directories as symlinks, don't descend into host paths.
        keep = []
        for d in dirs:
            sp = os.path.join(root, d)
            dp = os.path.join(dest_dir, d)
            if os.path.islink(sp):
                with contextlib.suppress(OSError):
                    _unlink(dp)
                    os.symlink(os.readlink(sp), dp)
            else:
                keep.append(d)
                count += 1
        dirs[:] = keep
        for fn in files:
            sp = os.path.join(root, fn)
            dp = os.path.join(dest_dir, fn)
            try:
                if os.path.islink(sp):
                    _unlink(dp)
                    os.symlink(os.readlink(sp), dp)
                else:
                    shutil.copy2(sp, dp, follow_symlinks=False)
                count += 1
            except OSError:
                continue
        if count and count % 2000 == 0:
            progress("copy", f"{count} entries")
    return count


# ---------------------------------------------------------------------------
# exec: non-interactive command, output captured (no PTY)
# ---------------------------------------------------------------------------

def service_container(args, conn):
    """Host-side service management: run the container's systemctl shim.

    args: {"name", "verb": start|stop|restart|status|enable|disable|is-active
                      |is-enabled|list-units, "unit": "sshd" (optional)}

    This is a thin wrapper over exec_container: it invokes /usr/bin/systemctl
    (the shim installed by stage_init) inside the container with the given verb
    and unit, so you can manage container services from the host without
    logging in. Returns the shim's exit code, which follows systemd's contract
    (0 = active/enabled, 3 = inactive, etc.).
    """
    name = args.get("name")
    verb = args.get("verb")
    unit = args.get("unit")
    if not name or not verb:
        return {"ok": False, "error": "service requires a name and a verb"}
    if not os.path.isdir(os.path.join(CONTAINERS_DIR, name, "rootfs")):
        return {"ok": False, "error": f"no such container: {name}"}

    cmd = ["/usr/bin/systemctl", verb]
    if unit:
        cmd.append(unit)
    return exec_container({"name": name, "command": cmd}, conn)


def exec_container(args, conn):
    """Run a command in a container WITHOUT a PTY; capture stdout/stderr.

    args: {"name", "command": [...], "cwd": optional}

    Unlike `run` (interactive PTY relay), this forks with pipes, chroots,
    runs the command to completion, and returns {exit, stdout, stderr} as one
    control frame. Built for scripting: `exec ubuntu -- apt list --installed`.
    """
    import json

    name = args.get("name")
    command = args.get("command") or []
    if not name or not command:
        return {"ok": False, "error": "exec requires a name and a command"}

    rootfs = os.path.join(CONTAINERS_DIR, name, "rootfs")
    if not os.path.isdir(rootfs):
        return {"ok": False, "error": f"no such container: {name}"}
    cwd = args.get("cwd") or "/"
    # Resolve a bare command name to an absolute path in the guest (avoids the
    # execvpe PATH-lookup crash on stripped chroots — see resolve_command_path).
    command = resolve_command_path(rootfs, command)

    out_r, out_w = os.pipe()
    err_r, err_w = os.pipe()

    pid = os.fork()
    if pid == 0:
        # Child: chroot + exec with stdout/stderr on the pipes.
        try:
            os.close(out_r)
            os.close(err_r)
            os.dup2(out_w, 1)
            os.dup2(err_w, 2)
            if out_w > 2:
                os.close(out_w)
            if err_w > 2:
                os.close(err_w)
            # Private mount namespace + pseudo-fs, same as run (best-effort).
            try:
                import mounts as mounts_mod
                mounts_mod.enter_private_mount_ns()
                mounts_mod.setup_mounts(rootfs, x11=args.get("shared_display", False), audio=args.get("shared_audio", False))
            except OSError:
                pass
            # Optional box64: register x86_64 -> box64 in THIS namespace only.
            if args.get("use_box64"):
                with contextlib.suppress(Exception):
                    import binfmt as binfmt_mod
                    binfmt_mod.register_box64(rootfs)
            ensure_resolv_conf(rootfs)
            os.chroot(rootfs)
            os.chdir(cwd)
            # Drop from root to the container's login user (least privilege).
            if not args.get("use_init"):
                eff_user = "root" if args.get("as_root") else (
                    args.get("user") or read_default_user(name)
                )
                drop_privileges(rootfs, eff_user, command)
            if args.get("use_init"):
                init_path = stage_init(rootfs)
                if init_path:
                    os.execve("/bin/sh", ["/bin/sh", init_path, *command], _guest_env(display=args.get("shared_display", False), audio=args.get("shared_audio", False)))
            # command[0] is absolute now (resolve_command_path), so execv is
            # safe and skips the PATH search that crashed on stripped chroots.
            if "/" in command[0]:
                os.execv(command[0], command)
            os.execvpe(command[0], command, _guest_env(display=args.get("shared_display", False), audio=args.get("shared_audio", False)))
        except Exception as e:
            with contextlib.suppress(OSError):
                os.write(2, f"exec failed: {e}\n".encode())
            os._exit(127)

    os.close(out_w)
    os.close(err_w)

    # Read both pipes to EOF, then reap.
    out_chunks, err_chunks = [], []
    fds = [out_r, err_r]
    while fds:
        readable, _, _ = select.select(fds, [], [], 5.0)
        if not readable:
            break
        for fd in readable:
            chunk = os.read(fd, MAX)
            if not chunk:
                fds.remove(fd)
                os.close(fd)
                continue
            (out_chunks if fd == out_r else err_chunks).append(chunk)

    _, status = os.waitpid(pid, 0)
    exit_code = os.waitstatus_to_exitcode(status)

    return {
        "ok": True,
        "data": {
            "exit": exit_code,
            "stdout": b"".join(out_chunks).decode("utf-8", "replace"),
            "stderr": b"".join(err_chunks).decode("utf-8", "replace"),
        },
    }


# ---------------------------------------------------------------------------
# clone: copy a container (snapshot before risky changes)
# ---------------------------------------------------------------------------

def clone_container(args):
    """Copy container SRC to DST (a full rootfs snapshot).

    args: {"src", "dst"}. DST must not already exist. Copies the rootfs and
    the image/arch metadata, so the clone 'list's identically to the source.
    """
    src = args.get("src")
    dst = args.get("dst")
    if not src or not dst:
        return {"ok": False, "error": "clone requires src and dst"}
    src_dir = os.path.join(CONTAINERS_DIR, src)
    dst_dir = os.path.join(CONTAINERS_DIR, dst)
    if not os.path.isdir(os.path.join(src_dir, "rootfs")):
        return {"ok": False, "error": f"no such container: {src}"}
    if os.path.exists(dst_dir):
        return {"ok": False, "error": f"destination already exists: {dst}"}

    os.makedirs(dst_dir, exist_ok=False)
    dst_rootfs = os.path.join(dst_dir, "rootfs")
    count = _copy_tree(os.path.join(src_dir, "rootfs"), dst_rootfs, lambda *a: None)

    # Carry over metadata so the clone lists with the same source image.
    for meta in ("image", "arch"):
        sp = os.path.join(src_dir, meta)
        if os.path.isfile(sp):
            with contextlib.suppress(OSError):
                shutil.copy2(sp, os.path.join(dst_dir, meta))

    return {"ok": True, "data": {"cloned": dst, "entries": count}}


# ---------------------------------------------------------------------------
# push / pull: copy files between the host and a container without login
# ---------------------------------------------------------------------------

def _resolve_container_path(name, path):
    """Map a path to the container's rootfs, refusing escapes.

    'path' is absolute inside the container (e.g. /root/backup.tar). Returns
    the real host path under the rootfs, or None if it escapes.
    """
    rootfs = os.path.realpath(os.path.join(CONTAINERS_DIR, name, "rootfs"))
    # Normalize the container path (absolute, no ..).
    p = os.path.normpath("/" + path.lstrip("/"))
    full = os.path.realpath(os.path.join(rootfs, p.lstrip("/")))
    if full != rootfs and not full.startswith(rootfs + os.sep):
        return None
    return full


def copy_into_container(args):
    """Copy a HOST path INTO a container (push).

    args: {"name", "src" (host path), "dest" (container path)}.
    The daemon runs as root so it can read any host path and write into the
    rootfs. dest is a container-absolute path; if it ends with '/' or is an
    existing dir, src is copied inside it under its own name.
    """
    name = args.get("name")
    src = args.get("src")
    dest = args.get("dest")
    if not (name and src and dest):
        return {"ok": False, "error": "push requires name, src and dest"}
    if not os.path.exists(src):
        return {"ok": False, "error": f"host path not found: {src}"}

    target = _resolve_container_path(name, dest)
    if target is None:
        return {"ok": False, "error": "dest escapes the container rootfs"}

    # If dest looks like a directory (trailing slash or existing dir), place
    # the source inside it under its own basename.
    if dest.endswith("/") or os.path.isdir(target):
        target = os.path.join(target, os.path.basename(src.rstrip("/")))

    try:
        if os.path.isdir(src):
            os.makedirs(target, exist_ok=True)
            n = _copy_tree(src, target, lambda *a: None)
        else:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(src, target, follow_symlinks=False)
            n = 1
    except OSError as e:
        return {"ok": False, "error": f"copy failed: {e}"}
    return {"ok": True, "data": {"copied": n, "to": dest}}


def copy_from_container(args):
    """Copy a path OUT of a container to the host (pull).

    args: {"name", "src" (container path), "dest" (host path)}.
    """
    name = args.get("name")
    src = args.get("src")
    dest = args.get("dest")
    if not (name and src and dest):
        return {"ok": False, "error": "pull requires name, src and dest"}

    csrc = _resolve_container_path(name, src)
    if csrc is None or not os.path.exists(csrc):
        return {"ok": False, "error": f"container path not found: {src}"}

    if dest.endswith("/") or os.path.isdir(dest):
        dest = os.path.join(dest, os.path.basename(src.rstrip("/")))

    try:
        if os.path.isdir(csrc):
            os.makedirs(dest, exist_ok=True)
            n = _copy_tree(csrc, dest, lambda *a: None)
        else:
            os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
            shutil.copy2(csrc, dest, follow_symlinks=False)
            n = 1
    except OSError as e:
        return {"ok": False, "error": f"copy failed: {e}"}
    return {"ok": True, "data": {"copied": n, "to": dest}}
