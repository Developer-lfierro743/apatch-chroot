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
