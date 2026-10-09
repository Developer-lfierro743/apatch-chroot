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

There is no teardown: the private mount namespace is unshared per session in
the forked child and dies with that child on exit, so the host's mount table
is never touched and nothing accumulates. Two guards keep it that way:
enter_private_mount_ns verifies '/' actually became private before any mount,
and setup_mounts refuses a rootfs outside CONTAINERS_DIR.
"""

import os

from syscalls import (
    CLONE_NEWNS,
    MS_BIND,
    MS_PRIVATE,
    MS_REC,
    mount,
    unshare,
)

# The only tree setup_mounts may ever mount into.
CONTAINERS_DIR = "/data/apatch-chroot/containers"


def enter_private_mount_ns():
    """unshare(CLONE_NEWNS) and make '/' private, so our mounts never propagate.

    Returns True on success. On a kernel without mount-namespace support this
    raises OSError; the caller decides whether to proceed without isolation.

    Safety: this is the load-bearing step. If '/' is NOT made private, the
    recursive /sys and /dev binds below would propagate back onto the host
    mount table — potentially re-binding host /dev onto itself and disturbing
    Android's core mounts. So we do NOT swallow a failure here: we verify the
    private remount actually succeeded (re-reading /proc/self/mountinfo for a
    'master:'/'shared:' tag on '/') and raise if it did not, so the caller
    falls back to a mount-less chroot rather than risk the host.
    """
    unshare(CLONE_NEWNS)
    mount("none", "/", None, MS_REC | MS_PRIVATE)
    if not _root_is_private():
        raise OSError("could not make / private in the new mount namespace")
    return True


def _root_is_private():
    """True when '/' in this mount namespace carries no shared/master tag.

    A private mount has neither 'shared:N' nor 'master:N' in its mountinfo
    optional fields, which means mounts under it cannot propagate to a peer
    group. We read /proc/self/mountinfo and check the line for mount point '/'.
    """
    try:
        with open("/proc/self/mountinfo") as f:
            for line in f:
                fields = line.split()
                # mountinfo: id parent major:minor root mountpoint options...
                if len(fields) >= 5 and fields[4] == "/":
                    optional = fields[6:] if len(fields) > 6 else []
                    # optional fields sit between the options '-' and the fstype.
                    if "-" in fields:
                        dash = fields.index("-")
                        optional = fields[6:dash]
                    return not any(t.startswith("shared:") or t.startswith("master:") for t in optional)
    except OSError:
        pass
    # If we cannot read mountinfo, assume NOT private and fail safe.
    return False


def _bind(src, dst):
    os.makedirs(dst, exist_ok=True)
    mount(src, dst, None, MS_BIND | MS_REC)


def _assert_safe_rootfs(rootfs):
    """Refuse to mount into a rootfs that is not one of our containers.

    Defense in depth: setup_mounts must only ever touch
    /data/apatch-chroot/containers/<name>/rootfs. A rootfs that resolves
    elsewhere (a symlink, a relative path, or a caller mistake naming '/' or
    some host directory) would have host /proc, /sys, /dev bound onto it —
    catastrophic for Android. We canonicalise and require the containers
    prefix, then refuse.
    """
    real = os.path.realpath(rootfs)
    prefix = os.path.realpath(CONTAINERS_DIR) + os.sep
    if not real.startswith(prefix):
        raise OSError(f"refusing to mount into rootfs outside {CONTAINERS_DIR}: {real}")


def setup_mounts(rootfs):
    """Mount the standard pseudo-filesystem set into *rootfs*. Returns mount list.

    Order matters: parents before children. Each entry is recorded so teardown
    can reverse it. Failures on an individual mount are logged and skipped so
    one missing piece (e.g. no /dev/mqueue) does not abort the whole login.

    Refuses outright if *rootfs* is not under CONTAINERS_DIR (see
    _assert_safe_rootfs) — this must never bind host /dev onto a host path.
    """
    _assert_safe_rootfs(rootfs)
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
