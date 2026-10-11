# Security Policy

apatch-chroot runs a **privileged root daemon** and performs `chroot`, `mount`,
and other privileged operations. That power is the point — and the reason this
document exists. Read it before deploying.

## Supported versions

Only the latest release line receives fixes. This project is pre-1.0 and moves
fast; always run the newest tagged release.

## What apatch-chroot does with privilege

- Runs a **root daemon** (started at boot by the APatch/Magisk/KernelSU module),
  outside the Android app seccomp filter.
- `chroot`s and mounts inside a **private mount namespace** per session.
- Listens on a **Unix socket in Termux's storage**, chowned to the app uid.

## Trust model — read this

**Installing apatch-chroot is equivalent to granting root to whatever can reach
its socket.** Specifically:

- The module runs as **root at boot**. Flashing it is a root-granting act, same
  as installing any root solution.
- The broker socket is reachable by the **Termux app uid**. Anything running as
  that uid (any Termux process, any app that can execute in Termux's context)
  can drive the daemon. There is no authentication beyond the socket's uid
  ownership and SELinux context.
- **Do not** expose the socket, the module, or Termux storage to untrusted apps.

This is the same trust posture as the root solution itself — if you trust APatch,
you trust the daemon it launches. If you don't, don't install this.

## Hardening already in place

- **Private mount namespace per session**, verified private before any mount
  (`_root_is_private()`). Mounts cannot propagate to the host — audio and system
  mounts stay intact. See [docs/safety.md](docs/safety.md).
- **Socket in Termux storage**, not `/dev` (a `/dev` socket gets a `device`
  SELinux context the app can't connect to, and a root-owned socket is forbidden
  outright). Chowned to the app uid so only that uid reaches it.
- **Atomic watchdog lock** — exactly one core daemon, no duplicate-daemon races.
- **Path guards** — mounts are refused outside `CONTAINERS_DIR`; `push` guards
  against path escape; layer extraction clamps symlink traversal and honors
  whiteouts.
- **Least privilege inside containers** — logins drop to a non-root user when a
  default user is set; `sudo` works only because the rootfs is remounted
  `suid,exec` *inside the namespace*, never on the host.

## Reporting a vulnerability

**Do not open a public issue for a security problem.** Instead:

1. Use GitHub's private **Security Advisories** ("Report a vulnerability") on the
   repo, or
2. Contact the maintainer privately via the email/profile on the GitHub account
   `Developer-lfierro743`.

Include: what you did, what happened, the device/kernel, and a proof of concept
if you have one. You'll get an acknowledgement, and credit in the fix if you
want it.

## Known limitations (not bugs, design facts)

- **No authentication on the socket.** Anyone at the Termux uid can drive the
  daemon. This is by design (it's a local, single-user tool) but it means
  "Termux uid == root" for this tool.
- **The daemon must be root.** The seccomp bypass requires it. There is no
  unprivileged mode.
- **Ghost mounts from chroot-distro** are not apatch-chroot's to clean; only a
  reboot clears them. This is a migration caveat, not an ongoing risk.
- **box64 is opt-in** and its JIT can be unstable on multithreaded x86_64 code —
  a robustness concern for x86_64 workloads, not a host-safety issue.

## Scope

In scope: privilege escalation *past the intended trust model* (an app that
isn't the Termux uid reaching the socket), host mount/audio corruption from a
normal session, path-escape bugs in `push`/`pull`/layer extraction.

Out of scope: anything that requires the attacker to already be the Termux uid
or already have root (that's the trust model, not a break of it), and box64's
own JIT bugs.
