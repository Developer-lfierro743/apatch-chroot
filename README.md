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

# Service management (see "Container services" below)
apatch-chroot run ubuntu --init       # start the container's service manager
apatch-chroot service ubuntu start sshd      # manage a service from the host
apatch-chroot service ubuntu enable sshd     # auto-start on next run

# x86_64 (opt-in; needs box64 installed in the container)
apatch-chroot run ubuntu --box64      # enable x86_64 emulation for this session
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
| `service NAME VERB [UNIT]` | Manage a container's services from the host (start/stop/enable/status). |
| `info` | Daemon status + host capability probes. |

Both `run` and `exec` take optional flags: `--init` (start the container's
service manager) and `--box64` (enable x86_64 emulation for the session).

## Architecture

| Path | Role |
|---|---|
| `daemon/daemon.py` | Privileged core. Root, started at boot. Dials the broker and serves requests; performs `chroot`/`mount`/`openpty`. |
| `daemon/daemon_cmds.py` | Container commands (run/exec/install/list/clone/push/pull/convert/kill/remove/service). |
| `daemon/broker.py` | Termux-uid relay. Owns the `app_data_file` socket the CLI reaches; pairs CLI connections with core connections. |
| `daemon/init.sh` | Container init: a tiny PID-1 supervisor (mini-systemd subset) that runs inside the chroot. Starts enabled services, reaps zombies, restarts crashed ones. |
| `daemon/systemctl-shim.sh` | A drop-in `systemctl` replacement installed into the container so `systemctl start sshd` talks to the mini-init. |
| `daemon/binfmt.py` | Optional box64: registers x86_64 → box64 via binfmt_misc, scoped to the container's private mount namespace. |
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
- [x] Container init — mini-systemd (PID-1 supervisor inside the chroot)
- [x] `systemctl` hijack — drop-in shim so Ubuntu's service commands work
- [x] `service` command — manage container services from the host
- [x] Optional box64 — per-container x86_64 emulation
- [ ] `login` as non-root users

## Container services (mini-systemd)

Android has no systemd, only `init` (PID 1). A bare chroot therefore starts
with **no service manager**: sshd/dbus/cron don't auto-start, and orphaned
zombies are never reaped. APatch Chroot ships a tiny purpose-built supervisor
(not a systemd port) that runs as **PID 1 inside the container's chroot**, in
the daemon's private mount namespace — Android's own PID 1 is never touched.

Run it with `--init`:

```sh
apatch-chroot run ubuntu --init
```

It reads enabled services from `/etc/apatch-chroot/services.conf`
(`name|enabled|command` per line), starts them, reaps zombies, restarts
crashed ones (with a cooldown so a crash-looping service backs off instead of
hammering the CPU), runs your shell as a child, and stops everything on exit.

### The `systemctl` hijack

Ubuntu/Debian containers ship systemd plus unit files, and countless scripts
call `systemctl start sshd`. But systemd can't be PID 1 here (no cgroups, no
dbus, no `/run/systemd`). Instead of fighting it, `--init` installs a
**`systemctl` shim** at `/usr/bin/systemctl` (the real binary is kept as
`systemctl.real`). The shim supports `start stop restart reload status
is-active is-enabled enable disable list-units`, resolves each service's
command from the container's unit file (`ExecStart=`) or a built-in fallback
(sshd/dbus/cron/rsyslog/nginx), and honors systemd's exit-code contract
(0 = active/enabled, 3 = inactive) so scripts using `is-active` work.

So inside a `--init` container, this just works:

```sh
systemctl start sshd
systemctl enable sshd        # auto-start on the next container run
systemctl status sshd
```

### Managing services from the host

You don't have to log in. The `service` command runs the container's systemctl
shim via the exec path:

```sh
apatch-chroot service ubuntu start sshd
apatch-chroot service ubuntu enable sshd
apatch-chroot service ubuntu status sshd
apatch-chroot service ubuntu is-active sshd   # exit 0/3 — scriptable
apatch-chroot service ubuntu list-units
```

## Optional: x86_64 via box64

box64 lets an arm64 device run x86_64 binaries by JIT-translating them, routed
by the kernel through `binfmt_misc` — a magic-byte handler that claims **only**
x86_64 ELFs. arm64 ELFs (Android and everything native) match a different
magic and run untouched, so box64 is invisible to them.

The registration is mounted **inside the container's private mount namespace**,
so it exists only for that session, never in the host namespace, and vanishes
when the container stops. Enable it per session with `--box64` (box64 must be
installed inside the container, e.g. at `/usr/local/bin/box64`):

```sh
apatch-chroot run ubuntu --box64
apatch-chroot exec ubuntu --box64 -- ./my-x86_64-app
```

If the kernel lacks `binfmt_misc` or box64 isn't installed, it degrades
gracefully to native-only. It's opt-in, not default, because box64's JIT is
still unstable on multithreaded x86_64 code.

## License

MIT
