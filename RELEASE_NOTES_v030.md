## APatch Chroot v0.3.0

A chroot-based Linux container runtime for rooted Android (APatch/Magisk/KernelSU), with its own init system, a working `systemctl`, and mount isolation that **doesn't break your Android host**.

> **Beta.** The core (daemon, broker, watchdog, OCI install, mount isolation) is verified live on-device. Newer paths (`--init`, `systemctl` shim, `convert --move`, `--shared-display`, `--box64`) are unit-verified but not yet exercised end-to-end on all hardware. See the README's "Release status". Requires a rooted device and a privileged daemon — read [SECURITY.md](https://github.com/Developer-lfierro743/apatch-chroot/blob/main/SECURITY.md) before flashing.

### Install
Flash `apatch-chroot.zip` in APatch → Modules → Install from storage, then reboot. Wait ~30-60s, then:
```sh
apatch-chroot ping
apatch-chroot info
apatch-chroot install ubuntu
apatch-chroot login ubuntu
```

### What's new in v0.3.0

**Bug fixes**
- **`exec`/`run` bare-command crash fixed** — the recurring `exec ubuntu -- cat ...` → `No module named 'warnings'` is gone. Bare command names are now resolved to absolute paths against the guest's own PATH (pre-chroot), then exec'd directly, skipping the PATH search that crashed on stripped images.
- **Daemon hardened against handler crashes** — a bug in one command can no longer kill a relay connection (hanging the client) or shrink the relay pool. Errors are reported per-request and the daemon keeps serving.
- **Real login shell** — `login`/`run` now exec `bash -l`, so you get a proper profile-loaded prompt instead of the bare `root@localhost:/#`.

**Features (carried from the 0.2.x line, now all in one release)**
- `--shared-display` / `--shared-x11` — GUI apps via termux-x11, namespace-isolated (audio stays safe).
- `info` — a working command reporting device capabilities (mount namespace, binfmt, seccomp, container sizes).
- `setuser` / `--user` / `--root` — least-privilege login; the daemon is root but your shell doesn't have to be.
- `convert --move` — import a chroot-distro container by **renaming** it: instant, zero extra disk space (for containers bigger than your free space).
- `suid,exec` rootfs remount — `sudo` works inside containers (Android's `/data` is `nosuid`).
- Mini-systemd init (`--init`) + `systemctl` shim + host-side `service` command.
- Optional box64 (`--box64`) — per-container x86_64 emulation.

### Documentation
- Full docs **wiki** in [`docs/`](https://github.com/Developer-lfierro743/apatch-chroot/tree/main/docs): architecture, safety, services, display, storage, box64, protocol.
- [SECURITY.md](https://github.com/Developer-lfierro743/apatch-chroot/blob/main/SECURITY.md) and [CONTRIBUTING.md](https://github.com/Developer-lfierro743/apatch-chroot/blob/main/CONTRIBUTING.md).

### Requirements
Rooted Android (APatch/Magisk/KernelSU) · Termux · Python 3.10+ · arm64 (x86_64 opt-in via box64)
