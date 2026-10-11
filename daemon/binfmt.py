"""binfmt_misc + box64 support for apatch-chroot containers (optional).

WHY: box64 lets an arm64 device run x86_64 binaries by JIT-translating them.
The kernel routes exec() to box64 via binfmt_misc — a magic-byte handler that
ONLY claims x86_64 ELFs. arm64 ELFs (Android, everything native) match a
different magic and go down the native path, so box64 is invisible to them.

SCOPE / SAFETY: binfmt_misc is a mount-namespace resource. We mount it fresh
INSIDE the container's private mount namespace (the same one enter_private_
mount_ns created) and register box64 there. That registration exists ONLY in
this namespace — Android's PID 1 namespace never sees it, and it vanishes when
the container's session ends. Zero host bleed. This is the whole reason it's
safe: register per-namespace, never in the shared/root namespace.

box64 is OPT-IN per container: pass use_box64 in the run/exec args and have
box64 installed inside the rootfs (e.g. at /usr/local/bin/box64). If the
kernel lacks binfmt_misc or box64 is missing, we log and continue without it —
the container still works, just native-only.

The x86_64 ELF magic and the 'F' (fix-binary) flag are the standard box64
registration (see the box64 project's install instructions).
"""

import os

from syscalls import mount

# binfmt_misc virtual filesystem mount point (standard Linux path).
BINFMT_MISC_PATH = "/proc/sys/fs/binfmt_misc"

# Where we expect box64 inside the container rootfs (override per container).
DEFAULT_BOX64_PATH = "/usr/local/bin/box64"

# The x86_64 ELF magic: 7f 45 4c 46 02 01 01 00 00 00 00 00 00 00 00 00 02 00 3e 00
#   \x7fELF .. class=2(64bit) data=1(LE) .. type=2(EXEC/DYN) machine=0x3e(x86-64)
# Encoded with \x escapes as the kernel expects in the register file.
X86_64_MAGIC = (
    r"\x7f\x45\x4c\x46\x02\x01\x01\x00"
    r"\x00\x00\x00\x00\x00\x00\x00\x00"
    r"\x02\x00\x3e\x00"
)
# Mask: don't care about the padding zero bytes, match class/data/type/machine.
X86_64_MASK = (
    r"\xff\xff\xff\xff\xff\xff\xff\x00"
    r"\x00\x00\x00\x00\x00\x00\x00\x00"
    r"\xff\xff\xff\xff"
)


def binfmt_available():
    """True if the kernel supports binfmt_misc.

    We test by attempting to mount it (inside whatever namespace we're in).
    A kernel without CONFIG_BINFMT_MISC fails the mount; we report False
    rather than raise, so callers degrade gracefully.
    """
    try:
        os.makedirs(BINFMT_MISC_PATH, exist_ok=True)
        mount("binfmt_misc", BINFMT_MISC_PATH, "binfmt_misc", 0)
        return os.path.exists(os.path.join(BINFMT_MISC_PATH, "status"))
    except OSError:
        return False


def find_box64(rootfs):
    """Return the in-container path to box64 if present, else None.

    Checks the default location, then PATH-ish spots. We look INSIDE the
    container rootfs (the host path), not the chrooted view.
    """
    for rel in (DEFAULT_BOX64_PATH.lstrip("/"), "usr/bin/box64", "opt/box64/box64"):
        p = os.path.join(rootfs, rel)
        if os.path.isfile(p):
            return "/" + rel  # path as seen from inside the chroot
    return None


def register_box64(rootfs, interpreter_path=None):
    """Mount binfmt_misc in the current (private) namespace and register box64.

    Must be called AFTER enter_private_mount_ns() and BEFORE chroot, so the
    registration lands in the container's namespace and the interpreter path
    is the in-chroot path.

    Returns True on success (box64 registered), False if unavailable/missing
    (the container should continue native-only). Never raises — box64 is an
    optional enhancement, never a hard requirement.
    """
    if not binfmt_available():
        return False
    box64 = interpreter_path or find_box64(rootfs)
    if not box64:
        return False

    register = os.path.join(BINFMT_MISC_PATH, "register")
    if not os.path.exists(register):
        return False

    # Flags: 'F' = fix-binary (open the interpreter at registration time and
    # keep it loaded), so the interpreter is resolved once, not per-exec.
    entry = f":box64:M::{X86_64_MAGIC}:{X86_64_MASK}:{box64}:F\n"
    try:
        with open(register, "w") as f:
            f.write(entry)
        return True
    except OSError:
        return False
