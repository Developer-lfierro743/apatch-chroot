# Architecture

Three processes, two Android walls, one design goal: run real Linux containers
on Android without the host noticing.

## The two Android walls

### 1. Seccomp — apps can't do privileged syscalls

Android installs a **seccomp filter** into every app process (via Zygote). It
blocks `chroot`, `mount`, `umount2`, `swapon`, and more. A blocked syscall
becomes `SIGSYS` and **kills the process**:

```
libc: Fatal signal 31 (SIGSYS), code 1 (SYS_SECCOMP), syscall 159 ...
```

So the Termux app — even with `su` — often can't `chroot` on its own.

### 2. SELinux — apps can't reach a root socket

Even with root, SELinux confines processes by *domain*. The Termux CLI runs as
`untrusted_app`, which is **forbidden** from connecting to a root-owned socket:

```
avc: denied { write } scontext=untrusted_app tcontext=... tclass=sock_file
```

So a naive "root daemon listens on a socket, app connects to it" fails — SELinux
blocks the connection regardless of Unix permissions.

## The three-process design

```
┌──────────────┐  app_data_file   ┌──────────────┐  app_data_file   ┌──────────────┐
│  Termux CLI  │ ───────────────▶ │   broker     │ ◀─────────────── │ core daemon  │
│(untrusted_app│   (ALLOWED)      │ (Termux uid) │  (root dials in, │ (root,       │
│  no root)    │                  │              │   ALLOWED)       │  no seccomp) │
└──────────────┘                  └──────────────┘                  └──────────────┘
   reaches broker only ──────────── crosses the SELinux wall ───────── does chroot
```

**Core daemon** (`daemon/daemon.py`)
- Runs as **root**, started at boot by the APatch module.
- **Outside the app seccomp filter**, so `chroot`/`mount`/`openpty` work.
- Performs every privileged operation. Has no socket the CLI can reach (SELinux).
- **Dials the broker** (root → `app_data_file` is allowed) and serves requests
  over those connections.

**Broker** (`daemon/broker.py`)
- Runs as the **Termux uid**, so it owns an `app_data_file` socket the CLI *can*
  reach.
- A pure **byte relay**: pairs each CLI connection with one of the core's dialed
  connections and pipes bytes both ways. It holds no state and does no work.

**Client** (`client/apatch-chroot.py`)
- Thin CLI, no privileged syscalls. Connects to the broker and speaks the
  protocol. Renders help, parses flags, prints results.

The direction is the key insight: because the **CLI can't dial root** but
**root can dial the app's socket**, the core reaches *out* to the broker, and the
broker bridges the two. (This is the pattern `anland-termux` uses for the same
problem.)

## Request lifecycle

1. You run `apatch-chroot login ubuntu`.
2. Client connects to the broker's socket and sends a JSON request.
3. Broker pairs that connection with one of the core's ready relay connections
   and pipes the bytes to the core.
4. Core dispatches (`handle_request`) to the right command in `daemon_cmds.py`.
5. For `login`, the core `fork()`s a child that: unshares a **private mount
   namespace**, sets up mounts, `chroot`s, and execs `bash -l` on a PTY.
6. PTY bytes stream back through the core → broker → client → your terminal as
   length-prefixed frames; control messages (progress, exit code) are JSON.

## File map

| Path | Role |
|---|---|
| `daemon/daemon.py` | Core. Root. Dials the broker, serves requests. |
| `daemon/daemon_cmds.py` | All container commands + host probes. |
| `daemon/broker.py` | Termux-uid relay. |
| `daemon/mounts.py` | Private-namespace mount setup (proc/sys/dev/pts/shm, X11). |
| `daemon/syscalls.py` | Raw `mount`/`umount`/`unshare` via ctypes. |
| `daemon/binfmt.py` | Optional box64 registration. |
| `daemon/init.sh` | Container init (mini-systemd PID-1 supervisor). |
| `daemon/systemctl-shim.sh` | Drop-in `systemctl` for the mini-init. |
| `client/apatch-chroot.py` | Thin CLI. |
| `client/pages.py` | Help text (commands + topics). |
| `client/render.py` | Width-aware help renderer. |
| `module/*` | APatch module: boot watchdog, service handoff, installer. |

## Boot sequence

1. Android boots. APatch runs the module's `late_start` service.
2. `service.sh` waits for Termux storage to unlock (FBE), then launches the
   **watchdog** detached.
3. The **watchdog** (`watchdog.sh`) loops every 15s: if the core isn't running,
   it starts it under an atomic lock (no duplicate daemons) and re-syncs the
   client + broker into Termux's lib dir each boot.
4. The core dials the broker and registers relay connections. Ready.

This is why after a reboot the daemon takes ~30-60s to be reachable — it waits
for Android to finish booting and Termux storage to unlock.
