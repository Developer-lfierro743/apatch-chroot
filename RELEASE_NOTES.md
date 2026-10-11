## APatch Chroot v0.2.0 — your own Linux containers on Android

A chroot-based Linux container runtime for rooted Android (APatch/Magisk/KernelSU). Run real Ubuntu/Debian/Alpine on your phone — with its own init system, a working `systemctl`, and mount isolation that **doesn't break your Android host**.

> **Beta.** The core (daemon, broker, watchdog, OCI install, mount isolation) is verified live on-device. The newer service/init/convert paths are unit-verified but not yet exercised end-to-end on hardware. See the README's "Release status" section. Requires a rooted device and a privileged daemon — read before flashing.

### Install
Flash `apatch-chroot.zip` in APatch → Modules → Install from storage, then reboot. Wait ~30-60s after boot, then:
```sh
apatch-chroot ping
apatch-chroot install ubuntu
apatch-chroot login ubuntu
```

### Why this exists
Two Android walls break naive container tools: **seccomp** (apps can't call `chroot`/`mount` — instant `SIGSYS`) and **SELinux** (an app can't even reach a root-owned socket). APatch Chroot solves both with a privileged root daemon plus a Termux-uid **broker** that relays across the SELinux wall — the core dials the broker (allowed), the CLI reaches the broker (allowed).

### What's new in v0.2.0
- **Mini-systemd init** (`--init`) — a tiny PID-1 supervisor that runs *inside* the container (never touching Android's PID 1): starts enabled services, reaps zombies, restarts crashed ones with a cooldown.
- **`systemctl` hijack** — a drop-in shim so Ubuntu's own `systemctl start sshd` works, resolved from the container's unit files. Honors systemd's exit-code contract.
- **`service` command** — manage container services from the host without logging in.
- **Least-privilege login** — `setuser` + `--user`/`--root`. The daemon is root, but your shell doesn't have to be. Root is opt-in once a default user is set.
- **`sudo` works** — remounts the rootfs `suid,exec` in the container's private namespace (Android's `/data` is `nosuid`), so setuid binaries elevate without touching the host mount.
- **Optional box64** (`--box64`) — per-container x86_64 emulation via `binfmt_misc`, namespace-scoped and invisible to Android's arm64 apps.

### Everything is namespace-isolated
Every mount (including the suid remount and box64 registration) lives in the container's **private mount namespace** and dies with the session. Android's `/dev/snd`, `/apex`, and system mounts are never unmounted or modified — this is the fix for the audio/ghost-mount breakage that affects other chroot tools.

### Commands
`install` · `convert` · `run` · `login` · `exec` · `list` · `clone` · `push` · `pull` · `kill` · `remove` · `service` · `setuser` · `info` — every one has a phone-friendly `--help` page.

### Requirements
Rooted Android (APatch/Magisk/KernelSU) · Termux · Python 3.10+ · arm64 (x86_64 opt-in via box64)

**Full details, architecture, and the honest release-status breakdown are in the [README](https://github.com/Developer-lfierro743/apatch-chroot#readme).**
