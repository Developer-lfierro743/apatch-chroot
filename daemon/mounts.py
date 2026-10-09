"""Mount orchestration for the apatch-chroot daemon.

Runs inside a private mount namespace (unshare CLONE_NEWNS) so nothing here
ever touches the host's mount table — that is what keeps Android's audio
(/dev/snd, /dev/audio) and the rest of /dev working normally while a container
runs, and it is why the daemon does this rather than the app.

The set a login needs, in order:
  /proc        fresh procfs       (process view, /proc/self, networking bits)
  /sys         bind of host /sys  (real hardware topology)
  /dev         bind of host /dev  (null, zero, random, snd, dri, ...)
  /dev/pts     fresh devpts       (ptys; newinstance so the host's pty pool is
                                  untouched and ptmxmode=0666 lets any uid alloc)
  /dev/shm     tmpfs              (POSIX shared memory)
  /dev/mqueue  tmpfs if the host has one

Teardown unmounts in reverse order, deepest first, best-effort: the namespace
dies with the daemon child anyway, so a mount that will not come down is logged,
not fatal.
"""

import contextlib
import os

from syscalls import (
    CLONE_NEWNS,
    MS_BIND,
    MS_PRIVATE,
    MS_REC,
    mount,
    unshare,
)


def enter_private_mount_ns():
    """unshare(CLONE_NEWNS) and make '/' private, so our mounts never propagate.

    Returns True on success. On a kernel without mount-namespace support this
    raises OSError; the caller decides whether to proceed without isolation.
    """
    unshare(CLONE_NEWNS)
    # Make the new namespace's mounts private so nothing we do leaks to the host.
    with contextlib.suppress(OSError):
        mount("none", "/", None, MS_REC | MS_PRIVATE)
    return True


def _bind(src, dst):
    os.makedirs(dst, exist_ok=True)
    mount(src, dst, None, MS_BIND | MS_REC)


def setup_mounts(rootfs):
    """Mount the standard pseudo-filesystem set into *rootfs*. Returns mount list.

    Order matters: parents before children. Each entry is recorded so teardown
    can reverse it. Failures on an individual mount are logged and skipped so
    one missing piece (e.g. no /dev/mqueue) does not abort the whole login.
    """
    mounted = []

    def _try(fn, target):
        try:
            fn()
            mounted.append(target)
            return True
        except OSError:
            return False

    # procfs (fresh, so the guest sees only its own processes where the kernel
    # supports a PID namespace; on Android it shows the host's, which is fine).
    _try(lambda: mount("proc", os.path.join(rootfs, "proc"), "proc", 0), "proc")

    # sysfs: bind the host's /sys so the guest sees real hardware.
    _try(lambda: _bind("/sys", os.path.join(rootfs, "sys")), "sys")

    # /dev: bind the host's /dev so the guest gets null/zero/random/snd/dri.
    _try(lambda: _bind("/dev", os.path.join(rootfs, "dev")), "dev")

    # /dev/pts: a fresh devpts instance. newinstance keeps the host's pty pool
    # untouched; ptmxmode=0666 lets any uid allocate a pty (the fix for
    # non-root PTY allocation that a mode=620 devpts blocks).
    def _devpts():
        dst = os.path.join(rootfs, "dev", "pts")
        os.makedirs(dst, exist_ok=True)
        mount("devpts", dst, "devpts", 0, "newinstance,ptmxmode=0666,mode=620")

    _try(_devpts, "dev/pts")

    # /dev/shm: POSIX shared memory.
    def _shm():
        dst = os.path.join(rootfs, "dev", "shm")
        os.makedirs(dst, exist_ok=True)
        mount("tmpfs", dst, "tmpfs", 0, "mode=1777")

    _try(_shm, "dev/shm")

    # /dev/mqueue: only if the host has one (Android usually does not).
    def _mqueue():
        if not os.path.isdir("/dev/mqueue"):
            raise OSError("no host /dev/mqueue")
        dst = os.path.join(rootfs, "dev", "mqueue")
        os.makedirs(dst, exist_ok=True)
        mount("mqueue", dst, "mqueue", 0)

    _try(_mqueue, "dev/mqueue")

    return mounted


# No teardown function: the private mount namespace is unshared per session in
# the forked child and dies with that child on exit, so the host's mount table
# is never touched and nothing accumulates. This is the whole point of doing
# mounts in the daemon rather than the app.
