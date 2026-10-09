# APatch Chroot

A chroot-based Linux container tool for Android, built around a **privileged APatch daemon**.

Run real Linux distributions (Ubuntu, Debian, Alpine, …) on a rooted Android device — without the seccomp crashes that plague tools doing chroot in the Termux app process.

---

## Why this exists

Android applies a **seccomp filter to every app process** (installed by the Zygote). It blocks `mount`, `umount2`, `chroot`, `swapon`, and more — a blocked syscall becomes `SIGSYS` and kills the process.

Tools like `chroot-distro` run `chroot`/`mount`/`openpty` inside the Termux app, so on modern Android (especially Android 15) they crash:

```
libc: Fatal signal 31 (SIGSYS), code 1 (SYS_SECCOMP), syscall 159 ...
```

**APatch Chroot solves this with a daemon.** An APatch module starts a small root daemon at boot. The daemon lives *outside* the app sandbox and its seccomp filter, so every privileged syscall succeeds there. The Termux client is a thin CLI that just sends commands to the daemon over a unix socket — it does no privileged work itself.

```
┌─────────────────────┐         unix socket          ┌──────────────────────┐
│  Termux app         │  ─────────────────────────▶  │  APatch daemon       │
│  (client, no root)  │   {"cmd":"run",...}          │  (root, boot)        │
│  apatch-chroot      │  ◀─────────────────────────  │  chroot / mount /    │
└─────────────────────┘   framed PTY + control        │  openpty here        │
                                                       └──────────────────────┘
        app seccomp filter applies here          privileged context — no filter
```

## Requirements

- A rooted Android device with **APatch** (Magisk/KernelSU work too, with path tweaks)
- **Termux** (F-Droid build recommended)
- Python 3.10+ (ships with Termux)

## Install

### 1. Build the module zip

From a clone of this repo:

```sh
cd module
zip -r ../apatch-chroot.zip .
```

### 2. Flash in APatch

Open **APatch → Modules → Install from storage**, pick `apatch-chroot.zip`, reboot.

### 3. Verify

```sh
apatch-chroot info      # daemon reachable + host capabilities
apatch-chroot ping      # quick daemon liveness check
```

## Usage

```sh
apatch-chroot install ubuntu          # pull Ubuntu from Docker Hub
apatch-chroot login ubuntu            # interactive root shell
apatch-chroot run ubuntu -- id        # run a one-off command
apatch-chroot list                    # show installed containers
apatch-chroot kill ubuntu             # stop + unmount
apatch-chroot remove ubuntu           # delete the container
```

Every command has a help page:

```sh
apatch-chroot --help
apatch-chroot install --help
```

## Architecture

| Path | Role |
|---|---|
| `daemon/daemon.py` | Privileged core. Root, started at boot by `service.sh`. Owns the mount namespace and performs every `chroot`/`mount`/`openpty`. Unix-socket server. |
| `daemon/daemon_cmds.py` | Container commands (fork chroot + PTY, stream I/O over the socket). |
| `client/apatch-chroot.py` | Thin Termux CLI. No privileged syscalls. Talks to the daemon. |
| `client/pages.py` | Hand-written help text for every command. |
| `client/render.py` | Width-aware help renderer (phone-friendly, stacked on narrow screens). |
| `module/module.prop` | APatch module metadata. |
| `module/service.sh` | Starts the daemon at boot (APatch `late_start`). |
| `module/customize.sh` | Installs the client into Termux's `PATH`. |

Containers live at `/data/apatch-chroot/containers/<name>/rootfs`.

## Protocol

Newline-delimited JSON requests over the unix socket `/dev/apatch-chroot.sock`. Responses are framed so raw PTY bytes can't be confused with control messages:

```
0x01 + 4-byte big-endian length + bytes   →  PTY data
0x02 + json + \n                          →  control message
```

## Roadmap

- [x] Privileged daemon + socket + chroot + PTY (the seccomp bypass)
- [x] Help system
- [ ] `install` — Docker Hub OCI pull (manifest → layers → extract)
- [ ] Mount orchestration (`/dev`, `/proc`, `/sys`, devpts)
- [ ] `list`, `kill`, `remove`, `info`
- [ ] `login` as non-root users
- [ ] Multi-arch images via binfmt

## License

MIT
