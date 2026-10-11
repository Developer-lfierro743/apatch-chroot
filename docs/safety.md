# Safety: why the Android host is never touched

This is the whole reason apatch-chroot exists. Other chroot tools break the
Android host — audio dies, mounts leak, ghost mounts accumulate. apatch-chroot
is built so none of that can happen.

## The root cause of chroot-distro's breakage

chroot-distro's own capability report admitted the problem:

```
Isolation tier: C: capability-drop only (no user namespace; container root == host root)
```

**"container root == host root"** means no real isolation. When chroot-distro
bound `/dev` and mounted devpts into it, the devpts mount **propagated back onto
the host `/dev`** — shadowing Android's real audio devices (`/dev/snd`). Its own
code comment says so:

> "or the devpts mounted into it next propagates back onto the host /dev."

And when it couldn't cleanly unmount (`EBUSY` on `system/bin`), it lazily
detached (`MNT_DETACH`), leaving **ghost mounts** that accumulated and kept
shadowing the host.

## How apatch-chroot prevents it: the private mount namespace

Every container session runs in its **own private mount namespace**. The
load-bearing steps in `enter_private_mount_ns()`:

1. `unshare(CLONE_NEWNS)` — create a new mount namespace.
2. `mount("/", MS_REC | MS_PRIVATE)` — make `/` private.
3. **`_root_is_private()` verifies it** — re-reads `/proc/self/mountinfo` and
   confirms `/` has no `shared:` or `master:` tag. If it does, it **raises**
   rather than proceeding.

That verification is the guarantee. A truly private namespace **cannot propagate
mounts to the host** — physically impossible. That's why the code refuses to
swallow a failure here:

> "If '/' is NOT made private, the recursive /sys and /dev binds below would
> propagate back onto the host mount table... So we do NOT swallow a failure."

## No teardown, nothing accumulates

The private namespace is unshared **per session, in the forked child**, and
**dies with the child on exit**. There is no teardown step, so:

- Nothing propagates to the host.
- Nothing accumulates (no ghost mounts — there's nothing left behind to become
  one).
- Android's `/dev/snd`, `/apex`, and system mounts are never unmounted or
  modified.

`apatch-chroot` only ever *adds* mounts inside the throwaway namespace. It never
removes anything Android owns.

## What's mounted, and where

Inside the private namespace only:

| Mount | Purpose |
|---|---|
| `proc` | fresh procfs (process view) |
| `sys` | bind of host `/sys` (hardware) |
| `dev` | bind of host `/dev` (null/zero/random/**snd**) |
| `dev/pts` | fresh devpts, `newinstance` (host pty pool untouched) |
| `dev/shm` | tmpfs (POSIX shared memory) |
| `tmp/.X11-unix` | termux-x11 socket, only with `--shared-display` |
| rootfs itself | remounted `suid,exec` so `sudo` works (see below) |

## The `nosuid` remount (why `sudo` works)

Android mounts `/data` with `nosuid` as anti-root hardening, so a setuid `sudo`
sitting in a container silently runs as the caller (not root). apatch-chroot
**remounts the rootfs `suid,exec` inside the private namespace**, so setuid
binaries elevate in-container while the host `/data` mount is untouched.

## The `--shared-display` safety (the chroot-distro trap)

chroot-distro's `--shared-display`/`--shared-x11` is the exact code path that
broke audio — it lacked isolation. apatch-chroot's `--shared-display` binds the
termux-x11 socket into the **same private namespace**, so it cannot propagate.
Same guarantee as every other mount.

## What you can rely on

- Host audio (`/dev/snd`) stays intact during and after a session.
- No ghost mounts accumulate.
- Clean session exit — no `EBUSY`, no `MNT_DETACH`, no leftovers.
- Android's own mounts (`/apex`, `/system`, `/dev/snd`) are never unmounted.

The one thing that's **not** apatch-chroot's to fix: ghost mounts left behind by
*chroot-distro* itself. Those are locked in the host namespace and only a reboot
clears them. That's a one-time cleanup before you migrate, not an ongoing cost.
