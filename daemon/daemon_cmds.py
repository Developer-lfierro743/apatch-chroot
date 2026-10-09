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


def _guest_env():
    """Return the environment dict for the chrooted exec (a copy of _GUEST_ENV)."""
    return dict(_GUEST_ENV)


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
                    mounts_mod.setup_mounts(rootfs)
                except OSError as e:
                    # Fail safe: no mounts, chroot only. Tell the user why.
                    with contextlib.suppress(OSError):
                        os.write(2, f"apatch-chroot: mount isolation unavailable ({e}); running without mounts\n".encode())
            # Give the guest DNS (Android has no /etc/resolv.conf) before chroot.
            ensure_resolv_conf(rootfs)
            os.chroot(rootfs)
            os.chdir(cwd)
            os.execvpe(command[0], command, _guest_env())
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
        if not os.path.isdir(rootfs):
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
    total = 0
    for root, dirs, files in os.walk(path):
        for f in files:
            with contextlib.suppress(OSError):
                total += os.path.getsize(os.path.join(root, f))
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
