# APatch Chroot

A chroot-based Linux container tool for Android, built around a **privileged APatch daemon** with a **Termux-uid broker**.

Run real Linux distributions (Ubuntu, Debian, Alpine, …) on a rooted Android device — without the seccomp crashes that plague tools doing chroot in the Termux app process, and without the SELinux wall that blocks an app from reaching a root socket.

---

## Why this exists

Two Android walls stop every naive approach:

**1. Seccomp.** Android applies a seccomp filter to every app process (installed by Zygote). It blocks `mount`, `umount2`, `chroot`, `swapon`, and more — a blocked syscall becomes `SIGSYS` and kills the process:

```
libc: Fatal signal 31 (SIGSYS), code 1 (SYS_SECCOMP), syscall 159 ...
```

**2. SELinux.** Even with root, an app (`untrusted_app` domain) is forbidden from connecting to a root-owned socket:

```
avc: denied { write } scontext=untrusted_app_27 tcontext=shell_data_file tclass=sock_file
```

**APatch Chroot solves both with two processes:**

- A **privileged core daemon** runs as root at boot (outside the app seccomp filter) and performs every `chroot`/`mount`/`openpty`.
- A **broker** runs as the *Termux uid* and owns an `app_data_file` socket the CLI can reach. The core *dials* the broker (root → app_data_file is allowed) and the broker relays bytes to the CLI.

```
┌──────────────┐   app_data_file    ┌──────────────┐   app_data_file    ┌──────────────┐
│ Termux CLI   │ ─────────────────▶ │   broker     │ ◀───────────────── │  core daemon │
│ (untrusted_  │   (ALLOWED)        │ (Termux uid) │   (root dials in,  │  (root,      │
│  app, no root)│                   │              │    ALLOWED)        │   no filter) │
└──────────────┘                    └──────────────┘                    └──────────────┘
   can't reach root socket ──────── crosses the SELinux wall ──────── does chroot/mount
```

The CLI never touches the root core's socket directly (SELinux denies that). All privileged work stays in the core; the broker is a pure byte relay.

## Requirements

- A rooted Android device with **APatch** (Magisk/KernelSU work too, with path tweaks)
- **Termux** (F-Droid build recommended)
- Python 3.10+ (ships with Termux)

## Install

### 1. Build the module zip

```sh
./build.sh          # → apatch-chroot.zip
```

### 2. Flash in APatch

**APatch → Modules → Install from storage**, pick `apatch-chroot.zip`, reboot.

### 3. Verify (wait ~30-60s after boot for the daemon)

```sh
apatch-chroot ping      # daemon alive?
apatch-chroot list      # installed containers
```

After a reboot the daemon takes ~30-60s to start (Android finishes booting, then FBE unlocks Termux storage, then the watchdog launches the core). Commands auto-wait up to 20s and print a hint if it's still not up.

## Usage

```sh
apatch-chroot install ubuntu          # pull Ubuntu from Docker Hub
apatch-chroot login ubuntu            # interactive root shell
apatch-chroot run ubuntu -- id        # run a one-off command (PTY)
apatch-chroot exec ubuntu -- id       # run a command, capture output (scripting)
apatch-chroot list                    # show installed containers
apatch-chroot clone ubuntu ubuntu-bak # snapshot a container (rollback)
apatch-chroot push ubuntu ~/f /root/  # copy host files INTO a container
apatch-chroot pull ubuntu /root/f ~/  # copy files OUT of a container
apatch-chroot kill ubuntu             # stop + unmount
apatch-chroot remove ubuntu           # delete the container (red = destructive)
apatch-chroot convert ubuntu          # import a chroot-distro container
```

Every command has a help page:

```sh
apatch-chroot --help
apatch-chroot install --help
```

## Commands

| Command | Purpose |
|---|---|
| `install IMAGE [NAME]` | Pull an OCI image from Docker Hub and install it. |
| `convert NAME [AS]` | Import an existing chroot-distro container (must be unmounted). |
| `run NAME -- CMD` | Run a command on an interactive PTY. |
| `exec NAME -- CMD` | Run a command non-interactively; capture stdout/stderr + exit code. |
| `login NAME` | Interactive login shell. |
| `list` | List containers with size + source image. |
| `clone SRC DST` | Full rootfs snapshot (rollback before risky changes). |
| `push NAME SRC DEST` | Copy host files into a container. |
| `pull NAME SRC DEST` | Copy files out of a container to the host. |
| `kill NAME` | Stop sessions + tear down mounts. |
| `remove NAME` | Delete a container and its rootfs. |
| `info` | Daemon status + host capability probes. |

## Architecture

| Path | Role |
|---|---|
| `daemon/daemon.py` | Privileged core. Root, started at boot. Dials the broker and serves requests; performs `chroot`/`mount`/`openpty`. |
| `daemon/daemon_cmds.py` | Container commands (run/exec/install/list/clone/push/pull/convert/kill/remove). |
| `daemon/broker.py` | Termux-uid relay. Owns the `app_data_file` socket the CLI reaches; pairs CLI connections with core connections. |
| `client/apatch-chroot.py` | Thin Termux CLI. No privileged syscalls. Connects to the broker, auto-starts it if down. |
| `client/pages.py` | Hand-written help text for every command. |
| `client/render.py` | Width-aware help renderer (phone-friendly, red for destructive). |
| `module/module.prop` | APatch module metadata. |
| `module/service.sh` | Starts the watchdog at boot (APatch `late_start`). |
| `module/watchdog.sh` | Atomic-lock loop: keeps the core alive, syncs client+broker into Termux lib each boot. |
| `module/customize.sh` | Installs the client into Termux's `PATH`. |

Containers live at `/data/apatch-chroot/containers/<name>/rootfs`.

### Why a broker (the SELinux wall)

`adb su <uid>` runs in the **magisk** domain and reaches anything, which is why manual testing always worked. But the real Termux CLI runs as **`untrusted_app`**, which SELinux forbids from a root-owned socket. The broker runs as the Termux uid and owns an `app_data_file` socket the app *can* reach; the root core dials the broker (root → app_data_file is allowed). This is the pattern `anland-termux` uses for the same problem.

## Protocol

Newline-delimited JSON requests over the broker's unix socket. Responses are framed so raw PTY bytes can't be confused with control messages:

```
0x01 + 4-byte big-endian length + bytes   →  PTY data
0x02 + json + \n                          →  control message
```

## Roadmap

- [x] Privileged daemon + seccomp bypass (chroot + PTY)
- [x] Termux-uid broker (crosses the SELinux socket wall)
- [x] Help system (phone-friendly, red destructive commands)
- [x] `install` — Docker Hub OCI pull (manifest → layers → extract)
- [x] `exec` — non-interactive scripting command
- [x] `clone` — rootfs snapshot
- [x] `push` / `pull` — file transfer without login
- [x] `convert` — import chroot-distro containers
- [x] Atomic-lock watchdog (single daemon, no duplicates, auto-restart)
- [x] Client auto-sync each boot (no version drift across reflashes)
- [ ] Container init/service manager (see below)
- [ ] `login` as non-root users

### Planned: container init (mini-systemd)

Android has no systemd, only `init` (PID 1), so containers start with no service manager. A future `apatch-chroot init`/`service` layer would run a tiny PID-1-style supervisor **inside each container** to start enabled services (sshd, dbus, cron) on `run`/`login` and reap zombies — a minimal, chroot-safe subset of systemd, not a port of it.

## License

MIT
