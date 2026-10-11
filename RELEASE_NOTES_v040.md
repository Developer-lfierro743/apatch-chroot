## APatch Chroot v0.4.0

A chroot-based Linux container runtime for rooted Android (APatch/Magisk/KernelSU), with its own init system, a working `systemctl`, GUI + audio support, and mount isolation that **doesn't break your Android host**.

> **Beta.** The core (daemon, broker, watchdog, OCI install, mount isolation) is verified live on-device. Newer paths (`--init`, `systemctl` shim, `convert --move`, `--shared-display`, `--shared-audio`, `--box64`) are unit-verified but not yet exercised end-to-end on all hardware. See the README's "Release status". Requires a rooted device and a privileged daemon — read [SECURITY.md](https://github.com/Developer-lfierro743/apatch-chroot/blob/main/SECURITY.md) before flashing.

### Install
Flash `apatch-chroot.zip` in APatch → Modules → Install from storage, then reboot. Wait ~30-60s, then:
```sh
apatch-chroot ping
apatch-chroot info
apatch-chroot stats
apatch-chroot install ubuntu
apatch-chroot login ubuntu
```

### What's new since v0.3.0

**Sound & GUI**
- **`--shared-audio`** (alias `--shared-sound`) — container apps play through Android audio via PulseAudio. Routes through a userspace socket, never raw ALSA — host `/dev/snd` untouched, namespace-isolated.
- **`--shared-display`** — GUI apps via termux-x11 (carried + hardened).
- Full **LXQt desktop** support (`scripts/setup-desktop.sh` + [desktop docs](https://developer-lfierro743.github.io/apatch-chroot/desktop)).

**Fixes**
- **`exec`/`run` bare-command crash fixed** — no more `No module named 'warnings'`; commands resolve against the guest's own PATH pre-chroot.
- **`/dev/shm` sized to 512M** — Chromium/Electron apps no longer crash exhausting shared memory.
- **Daemon hardened** — a buggy command handler can no longer kill a relay or hang the CLI.
- **Real login shell** — `login`/`run` exec `bash -l` (proper prompt + profile).

**New commands & detection**
- **`stats`** — per-container disk usage, running sessions, default user, source image.
- **`check-update`** — compare against the latest GitHub release.
- **APatch in-app updates** — `updateJson` in module.prop; APatch shows "update available" automatically.

**Everything from earlier releases**
- `--init` mini-systemd + `systemctl` shim + `service` command
- `setuser`/`--user`/`--root` least-privilege login
- `convert --move` (instant rename, zero extra disk)
- `suid` remount so `sudo` works
- Optional `--box64` (per-container x86_64)

### Documentation
- **Website:** https://developer-lfierro743.github.io/apatch-chroot/ (architecture, safety, services, desktop, electron, storage, box64, protocol)
- [SECURITY.md](https://github.com/Developer-lfierro743/apatch-chroot/blob/main/SECURITY.md) · [CONTRIBUTING.md](https://github.com/Developer-lfierro743/apatch-chroot/blob/main/CONTRIBUTING.md)

### Requirements
Rooted Android (APatch/Magisk/KernelSU) · Termux · Python 3.10+ · arm64 (x86_64 opt-in via box64)
