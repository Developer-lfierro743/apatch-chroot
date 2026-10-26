"""Raw mount/unshare syscalls via ctypes, for the apatch-chroot daemon.

The daemon runs as root outside the app seccomp filter, so these libc calls
succeed here. We use ctypes directly rather than the `mount`/`umount` binaries
because Termux's toybox mount cannot do propagation changes and we need precise
flag control.
"""

import ctypes
import os

# Android's Bionic libc is 'libc.so', not glibc's 'libc.so.6'. Try both.
_candidates = ["libc.so.6", "libc.so", "libc.bionic.so"]
libc = None
_last_err = None
for _name in _candidates:
    try:
        libc = ctypes.CDLL(_name, use_errno=True)
        break
    except OSError as e:
        _last_err = e
if libc is None:
    raise _last_err

# mount(2) flags
MS_RDONLY = 1
MS_NOSUID = 2
MS_NODEV = 4
MS_NOEXEC = 8
MS_REMOUNT = 32
MS_BIND = 4096
MS_MOVE = 8192
MS_REC = 16384
MS_PRIVATE = 1 << 18
MS_SLAVE = 1 << 19
MS_SHARED = 1 << 20
# Clear-flags for a suid/exec remount (turn OFF nosuid/noexec/nodav-like bits).
# We pass these to remount so setuid binaries (sudo) execute inside containers.
MS_NOSUID = 2
MS_NODEV = 4
MS_NOEXEC = 8

# unshare(2) flags
CLONE_NEWNS = 0x00020000

# prctl(2) — capability bounding-set drops for container hardening.
PR_CAPBSET_DROP = 36
libc.prctl.restype = ctypes.c_int
libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]

# Linux capability numbers (capability.h).
CAP_LINUX_IMMUTABLE = 9
CAP_NET_ADMIN = 12
CAP_SYS_MODULE = 16
CAP_SYS_RAWIO = 17
CAP_SYS_PTRACE = 19
CAP_SYS_PACCT = 20
CAP_SYS_ADMIN = 21
CAP_SYS_BOOT = 22
CAP_SYS_TIME = 25
CAP_SYS_TTY_CONFIG = 26
CAP_MKNOD = 27
CAP_AUDIT_CONTROL = 30
CAP_MAC_OVERRIDE = 32
CAP_MAC_ADMIN = 33
CAP_SYSLOG = 34
CAP_WAKE_ALARM = 35
CAP_BLOCK_SUSPEND = 36
CAP_PERFMON = 38
CAP_BPF = 39

# Capabilities a hardened container must NEVER hold: every one of these is a
# direct path to breaking the HOST (load kernel modules, raw disk I/O, change
# the host clock, reboot, reconfigure host networking, program the kernel via
# BPF, override SELinux...). Guest usability needs none of them.
HARDEN_DROP = frozenset({
    CAP_LINUX_IMMUTABLE, CAP_NET_ADMIN, CAP_SYS_MODULE, CAP_SYS_RAWIO,
    CAP_SYS_PTRACE, CAP_SYS_PACCT, CAP_SYS_BOOT, CAP_SYS_TIME,
    CAP_SYS_TTY_CONFIG, CAP_MKNOD, CAP_AUDIT_CONTROL, CAP_MAC_OVERRIDE,
    CAP_MAC_ADMIN, CAP_SYSLOG, CAP_WAKE_ALARM, CAP_BLOCK_SUSPEND,
    CAP_PERFMON, CAP_BPF,
})

libc.prctl.restype = ctypes.c_int
libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]


def drop_cap_from_bounding(cap):
    """prctl(PR_CAPBSET_DROP, cap) — one-way, inherited by every descendant.

    Once dropped from the bounding set, no setuid binary or setcap inside the
    container can ever regain this capability. Must run as root, AFTER all
    privileged setup (mounts) and BEFORE exec'ing the guest.
    """
    if libc.prctl(PR_CAPBSET_DROP, cap, 0, 0, 0) != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), f"PR_CAPBSET_DROP({cap})")

libc.mount.restype = ctypes.c_int
libc.mount.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_char_p]
libc.umount2.restype = ctypes.c_int
libc.umount2.argtypes = [ctypes.c_char_p, ctypes.c_int]
libc.unshare.restype = ctypes.c_int
libc.unshare.argtypes = [ctypes.c_int]

MNT_DETACH = 2


def mount(source, target, fstype, flags, data=None):
    """mount(2). Raises OSError on failure."""
    src = source.encode() if source else None
    tgt = target.encode()
    fst = fstype.encode() if fstype else None
    dat = data.encode() if data else None
    if libc.mount(src, tgt, fst, flags, dat) != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), target)


def umount(target, lazy=False):
    """umount2(2). Raises OSError on failure."""
    flags = MNT_DETACH if lazy else 0
    if libc.umount2(target.encode(), flags) != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), target)


def unshare(flags):
    """unshare(2). Raises OSError on failure."""
    if libc.unshare(flags) != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))
