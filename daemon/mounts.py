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
    MS_REMOUNT,
    MS_RDONLY,
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


def _remount_suid_exec(rootfs):
    """Remount the rootfs with suid+exec so setuid binaries (sudo) work.

    Android mounts /data (where our container rootfs lives) with nosuid (and
    often noexec) as anti-root hardening. Those flags make setuid-root binaries
    like sudo silently run as the caller (or refuse to run at all) — so sudo
    appears "broken" inside a container for no visible reason.

    We bind the rootfs onto itself, then remount it clearing nosuid/noexec,
    INSIDE the container's private mount namespace. This change exists only in
    this namespace — the host /data mount and Android's hardening are untouched
    (same isolation guarantee as every other mount we make here).
    """
    mount(rootfs, rootfs, None, MS_BIND | MS_REC)
    mount(rootfs, rootfs, None, MS_REMOUNT | MS_BIND)


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


def setup_mounts(rootfs, x11=False, audio=False, shared_storage=False):
    """Mount the standard pseudo-filesystem set into *rootfs*. Returns mount list.

    Order matters: parents before children. Each entry is recorded so teardown
    can reverse it. Failures on an individual mount are logged and skipped so
    one missing piece (e.g. no /dev/mqueue) does not abort the whole login.

    When *x11* is set, also bind the host's termux-x11 socket into the
    container's /tmp/.X11-unix so GUI apps reach the display. This bind lives
    in the SAME private mount namespace as everything else here, so it cannot
    propagate to the host (the whole reason this is safe where chroot-distro's
    --shared-display was not: we are in a verified-private namespace).

    When *audio* is set, bind the host's PulseAudio socket into the container
    and the client sets PULSE_SERVER, so container apps play through Android's
    audio. Same private-namespace safety as x11 — the host's /dev/snd is never
    touched (we route through PulseAudio, not raw ALSA).

    When *shared_storage* is set, bind Android's shared storage (the raw
    /data/media/0 backing store, FUSE /storage/emulated/0 as fallback) at
    /mnt/sdcard inside the container, with a /sdcard convenience symlink.

    Containment hardening (v0.5.0), all namespace-scoped so the host mount
    table is never touched:
      - /proc mounted with subset=pid (+hidepid=2 fallback): the guest — even
        as root — sees only its own process tree, never host processes.
      - /sys bound READ-ONLY: guest root cannot write sysfs (no driver
        unbinds, no kernel knob changes).
      - /dev/block and /dev/socket masked with an empty read-only tmpfs:
        no block devices (no bricking via dd) and no Android property/logd
        control sockets reachable from inside.

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

    # First: remount the rootfs itself with suid+exec so setuid binaries (sudo)
    # execute inside the container. /data is nosuid on Android; without this,
    # sudo silently fails to elevate. Namespace-scoped, host untouched.
    _try(lambda: _remount_suid_exec(rootfs), "rootfs")

    # procfs (fresh). Containment ladder, best first:
    #   subset=pid    — the mount shows ONLY the mounting process's tree, even
    #                   to root inside. The guest cannot see host processes.
    #   hidepid=2     — non-root guests see only their own processes (older
    #                   kernels; root still sees everything, accepted fallback).
    #   plain proc    — last resort (very old kernels).
    def _proc(opts):
        mount("proc", os.path.join(rootfs, "proc"), "proc", 0, opts)

    if not _try(lambda: _proc("subset=pid"), "proc"):
        if not _try(lambda: _proc("hidepid=2"), "proc"):
            _try(lambda: _proc(""), "proc")

    # sysfs: bind the host's /sys so the guest sees real hardware — READ-ONLY.
    # A read-write sysfs would let guest root unbind drivers or flip kernel
    # knobs on the live host. The RO remount is namespace-scoped.
    def _sys_ro():
        _bind("/sys", os.path.join(rootfs, "sys"))
        mount("/sys", os.path.join(rootfs, "sys"), None,
              MS_BIND | MS_REMOUNT | MS_RDONLY | MS_REC)

    _try(_sys_ro, "sys")

    # /dev: bind the host's /dev so the guest gets null/zero/random/snd/dri.
    _try(lambda: _bind("/dev", os.path.join(rootfs, "dev")), "dev")

    # Danger masks (read-only empty tmpfs over the hostile subtrees):
    #   /dev/block   — every host block device lives here; writable from guest
    #                  root it is a one-command brick (dd to mmcblk0).
    #   /dev/socket  — Android's control sockets (property_service lets root
    #                  set HOST system properties, logd, netd...).
    # The guest cannot create its way around these: mknod and SYS_ADMIN are
    # dropped from the capability bounding set before exec (see daemon_cmds).
    def _mask(rel):
        dst = os.path.join(rootfs, "dev", rel)
        os.makedirs(dst, exist_ok=True)
        mount("tmpfs", dst, "tmpfs", MS_RDONLY, "mode=000,size=4k")

    def _mask_block():
        if os.path.isdir(os.path.join(rootfs, "dev", "block")):
            _mask("block")

    def _mask_socket():
        if os.path.isdir(os.path.join(rootfs, "dev", "socket")):
            _mask("socket")

    _try(_mask_block, "dev/block (masked)")
    _try(_mask_socket, "dev/socket (masked)")

    # /dev/pts: a fresh devpts instance. newinstance keeps the host's pty pool
    # untouched; ptmxmode=0666 lets any uid allocate a pty (the fix for
    # non-root PTY allocation that a mode=620 devpts blocks).
    def _devpts():
        dst = os.path.join(rootfs, "dev", "pts")
        os.makedirs(dst, exist_ok=True)
        mount("devpts", dst, "devpts", 0, "newinstance,ptmxmode=0666,mode=620")

    _try(_devpts, "dev/pts")

    # /dev/shm: POSIX shared memory. Sized generously (512M) because
    # Chromium/Electron apps exhaust a small default tmpfs and crash — with
    # room here they use real shared memory instead of needing
    # --disable-dev-shm-usage. Namespace-scoped, host untouched.
    def _shm():
        dst = os.path.join(rootfs, "dev", "shm")
        os.makedirs(dst, exist_ok=True)
        mount("tmpfs", dst, "tmpfs", 0, "mode=1777,size=512M")

    _try(_shm, "dev/shm")

    # /dev/mqueue: only if the host has one (Android usually does not).
    def _mqueue():
        if not os.path.isdir("/dev/mqueue"):
            raise OSError("no host /dev/mqueue")
        dst = os.path.join(rootfs, "dev", "mqueue")
        os.makedirs(dst, exist_ok=True)
        mount("mqueue", dst, "mqueue", 0)

    _try(_mqueue, "dev/mqueue")

    # X11 (termux-x11): bind the host's X11 socket into the container so GUI
    # apps reach the display. Lives in this private namespace — cannot leak to
    # the host. Skipped silently if termux-x11 isn't running (no socket yet).
    if x11:
        def _x11():
            # termux-x11's socket dir (Termux prefix tmp).
            src = "/data/data/com.termux/files/usr/tmp/.X11-unix"
            if not os.path.isdir(src):
                raise OSError("no host X11 socket (is termux-x11 running?)")
            dst = os.path.join(rootfs, "tmp", ".X11-unix")
            os.makedirs(dst, exist_ok=True)
            mount(src, dst, None, MS_BIND | MS_REC)

        _try(_x11, "tmp/.X11-unix")

    # Audio (PulseAudio): bind the host's PulseAudio socket into the container
    # so apps play through Android's audio. We route through PulseAudio (a
    # userspace socket), NEVER raw ALSA — so the host's /dev/snd is untouched.
    # Same private-namespace safety as x11. Skipped silently if PulseAudio
    # isn't running (no socket yet).
    if audio:
        def _audio():
            # Termux PulseAudio socket (native protocol).
            src = "/data/data/com.termux/files/usr/var/run/pulse/native"
            if not os.path.exists(src):
                raise OSError("no host PulseAudio socket (is pulseaudio running?)")
            dst_dir = os.path.join(rootfs, "var", "run", "pulse")
            os.makedirs(dst_dir, exist_ok=True)
            dst = os.path.join(dst_dir, "native")
            mount(src, dst, None, MS_BIND)

        _try(_audio, "var/run/pulse/native")

    # Shared storage: Android's shared storage at /mnt/sdcard (+ /sdcard
    # symlink). Source ladder prefers the RAW backing store /data/media/0
    # (no FUSE recursion, stable ownership) and falls back to the FUSE view
    # /storage/emulated/0. Bind is recursive so per-user FUSE submounts come
    # along. Read-write: files created by the guest appear to Android apps
    # through the FUSE view's synthesized ownership.
    if shared_storage:
        def _sdcard():
            src = None
            for cand in ("/data/media/0", "/storage/emulated/0", "/sdcard"):
                if os.path.isdir(cand):
                    src = cand
                    break
            if src is None:
                raise OSError("no Android shared storage found")
            dst = os.path.join(rootfs, "mnt", "sdcard")
            os.makedirs(dst, exist_ok=True)
            mount(src, dst, None, MS_BIND | MS_REC)
            link = os.path.join(rootfs, "sdcard")
            if not os.path.lexists(link):
                try:
                    os.symlink("/mnt/sdcard", link)
                except OSError:
                    pass  # symlink is cosmetic; the bind is what matters

        _try(_sdcard, "mnt/sdcard")

    return mounted


# No teardown function: the private mount namespace is unshared per session in
# the forked child and dies with that child on exit, so the host's mount table
# is never touched and nothing accumulates. This is the whole point of doing
# mounts in the daemon rather than the app.
