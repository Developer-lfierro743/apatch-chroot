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

# unshare(2) flags
CLONE_NEWNS = 0x00020000

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
