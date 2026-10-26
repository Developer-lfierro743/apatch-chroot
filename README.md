# APatch Chroot

A chroot-based Linux container tool for Android, built around a **privileged APatch daemon** with a **Termux-uid broker** — now with a **hardened containment wall** so what happens inside a container cannot break the host.

Run real Linux distributions (Ubuntu, Debian, Alpine, …) on a rooted Android device — without the seccomp crashes that plague tools doing chroot in the Termux app process, without the SELinux wall that blocks an app from reaching a root socket, and (since v0.5.0) with the container fenced off from the host kernel's dangerous knobs.

> 📚 **Documentation website:** https://developer-lfierro743.github.io/apatch-chroot/ (architecture, safety, services, desktop, box64, protocol). Source in [docs/](docs/).
>
> ⚠️ This runs a **root daemon** — read [SECURITY.md](SECURITY.md) before installing. Contributing? See [CONTRIBUTING.md](CONTRIBUTING.md).

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

## What's new in v0.5.x — fortress + doors

- **Hardened containment (default ON)** — the container, even as root inside, cannot: see host processes, write sysfs, touch block devices or Android control sockets, load kernel modules, reconfigure host networking, change the host clock, or reboot the host. All namespace-scoped; Android's own mount table is untouched. See [The fortress](#the-fortress-host-protection).
- **`--shared-storage`** — your Android files inside the container at `/mnt/sdcard` (`/sdcard` symlink).
- **Own Python `systemctl`** — a clean-room MIT replacement that parses real systemd unit files, starts enabled services in dependency order, and gives you `systemctl start/status/enable/...` inside the container.
- **Container-owned DNS** — Google (8.8.8.8/8.8.4.4) + Cloudflare (1.1.1.1/1.0.0.1) written to the container's `/etc/resolv.conf` on every login.
- **`apatch-chroot ports`** — shows the phone's LAN IP and every listening port, so a webapp inside the container is reachable from your LAN with zero forwarding.
- **Fixed: `--init` staging** — the init supervisor + systemctl shim were silently never installed in earlier releases (staged after `chroot`, where the daemon dir is invisible). Now staged before, and covered by 11 unit tests.

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

Releases are also attached to [GitHub Releases](https://github.com/Developer-lfierro743/apatch-chroot/releases) — the module's built-in updater (`updateJson`) checks `releases/latest/download/apatch-chroot.zip`, so future upgrades are one tap from the APatch app.

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

# Login user (least privilege — see "Login user" below)
apatch-chroot setuser ubuntu luis     # log in as 'luis' by default
apatch-chroot login ubuntu --root     # force root just this session

# Services (see "Container services" below)
apatch-chroot login ubuntu --init     # boot with the init supervisor
apatch-chroot service ubuntu start sshd      # manage services from the host
apatch-chroot service ubuntu enable sshd     # auto-start on next --init run

# Doors (opt-in per session)
apatch-chroot login ubuntu --shared-storage   # Android storage at /mnt/sdcard
apatch-chroot login ubuntu --shared-display   # termux-x11 display
apatch-chroot login ubuntu --shared-audio     # host audio
apatch-chroot ports                   # LAN IP + listening TCP ports

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
| `setuser NAME [USER]` | Set a container's default login user (least privilege). |
| `ports` | Show the phone's LAN IP + every listening TCP port. |
| `info` | Daemon status + host capability probes. |

`run`, `login`, and `exec` take optional flags: `--init`, `--box64`,
`--shared-display` (alias `--shared-x11`), `--shared-audio`,
`--shared-storage`, `--user U`, `--root`, plus the containment escape
hatches `--no-harden` and `--keep-sysadmin` (see below).

## The fortress: host protection

Everything below is **namespace-scoped** — done inside the container's private mount namespace, invisible to Android, gone on exit. The host mount table, kernel knobs, and devices are never touched by the container.

| Threat (root inside the container) | Defense |
|---|---|
| See / kill host processes (`/proc`) | `/proc` mounted with `subset=pid` (hidepid=2 fallback) — only the container's own process tree exists |
| Unbind drivers, flip kernel knobs (`/sys`) | `/sys` bind-mounted then **read-only** remounted |
| `dd` a raw block device → brick | `/dev/block` masked with an empty read-only tmpfs |
| Talk to Android's control sockets (set host **system properties** via `/dev/socket/property_service`, logd…) | `/dev/socket` masked |
| Load host kernel modules (`CAP_SYS_MODULE`) | capability **bounding-set drop** before exec |
| Reconfigure **host** networking (shared netns!) (`CAP_NET_ADMIN`) | dropped |
| Reboot host (`CAP_SYS_BOOT`), change host clock (`CAP_SYS_TIME`) | dropped |
| Create device nodes bypassing the masks (`CAP_MKNOD`) | dropped |
| `ptrace`, raw I/O, syslog/audit/SELinux/BPF access | dropped (`CAP_SYS_PTRACE`, `SYS_RAWIO`, `SYSLOG`, `AUDIT_*`, `MAC_*`, `BPF`, `PERFMON`…) |

Capability drops are **one-way** (bounding set, inherited by every descendant — `setuid`/`setcap` inside can never regain them) and cover ~20 host-dangerous capabilities. Guest usability needs none of them.

Escape hatches:

```sh
apatch-chroot login ubuntu --no-harden      # skip all of the above (legacy behavior)
apatch-chroot login ubuntu --keep-sysadmin  # keep CAP_SYS_ADMIN (FUSE/AppImage users)
```

## Shared storage

```sh
apatch-chroot login ubuntu --shared-storage
ls /mnt/sdcard        # your Android storage (also /sdcard)
```

The bind prefers Android's raw backing store `/data/media/0` (no FUSE recursion, stable ownership) and falls back to `/storage/emulated/0`. The mount is recursive, so per-user FUSE submounts come along; files the guest writes appear to Android apps through the FUSE view. Cosmetic `/sdcard → /mnt/sdcard` symlink included.

## Networking + `ports`

The container **shares the kernel network namespace** with Android — that's the feature: WiFi/data just work, no NAT, no forwarding. A service binding `0.0.0.0:<port>` inside the container is reachable **from your LAN** on the phone's WiFi address:

```sh
# inside the container (with --init):
systemctl start ssh          # or: python3 -m http.server 8080

# on the host:
apatch-chroot ports
# phone LAN IP: 192.168.1.23
# from outside:  http://192.168.1.23:<port>   (chroot binds 0.0.0.0)
# 4 listening TCP socket(s): ...
```

Example: Hermes' webapp inside the container, opened from a laptop on the same WiFi.

> **VPN caveat:** container traffic follows the host routing table and bypasses per-app VPN routing (the daemon runs as root, not in any VPN app's list). Documented; policy-routing integration is future work.

## Container DNS

Android keeps no `/etc/resolv.conf` (netd owns DNS via system properties), so fresh images fail with "Temporary failure resolving". Every login now installs a **managed** `/etc/resolv.conf`:

```
# managed by apatch-chroot - do not edit (override: /etc/resolv.conf.d/local.conf)
nameserver 8.8.8.8
nameserver 8.8.4.4
nameserver 1.1.1.1
nameserver 1.0.0.1
```

- Marker present → rewritten fresh on every login.
- Your own config (working nameservers, no marker) → respected, never touched.
- Want different servers? Write them to `/etc/resolv.conf.d/local.conf` (future hook) or drop the marker — the file becomes yours.

## Container services

Android has no systemd — a bare chroot starts with **no service manager**: sshd/dbus/cron don't auto-start, and orphaned zombies are never reaped. Since v0.5.1, `--init` boots the container with a **Python init supervisor + systemctl replacement** (`daemon/systemctl.py`, clean-room, MIT):

```sh
apatch-chroot login ubuntu --init
```

- **Init mode** (PID 1 inside the chroot, in the daemon's private mount namespace — Android's own PID 1 is never touched): parses systemd-style unit files from `/etc/systemd/system/*.service`, starts **enabled** units in dependency order (`After=`/`Wants=`/`Requires=`, cycle-safe), supports `Type=simple|forking|oneshot`, `User=`, `Environment=`/`EnvironmentFile=`, `WorkingDirectory=`, `Restart=no|on-failure|always` (bounded), reaps zombies, and stops services in reverse order on SIGTERM/shell exit.
- **Command mode** — the same binary installed as `/usr/bin/systemctl` (real binary kept as `systemctl.real`):

```sh
systemctl list-units
systemctl start ssh
systemctl status ssh
systemctl is-active ssh        # systemd exit-code contract (0/3) — scriptable
systemctl enable ssh           # real multi-user.target.wants symlink, like systemd
systemctl disable ssh
```

- `enable`/`disable` are pure filesystem ops and work even when the init isn't running; `status`/`list-units`/`is-active` degrade to pidfile//proc reads when no init is up — **never** a "Running in chroot, ignoring" refusal again.
- Logs: `/var/log/apatch-chroot/<unit>.log`. Pidfiles: `/run/apatch-chroot/<unit>.pid`.
- Guest needs `python3` for the Python path; without it, the legacy POSIX sh supervisor + shim (`daemon/init.sh`, `daemon/systemctl-shim.sh`) run instead — same commands, smaller feature set.

### Managing services from the host

You don't have to log in. The `service` command drives the same machinery via the exec path:

```sh
apatch-chroot service ubuntu start sshd
apatch-chroot service ubuntu enable sshd
apatch-chroot service ubuntu status sshd
apatch-chroot service ubuntu is-active sshd   # exit 0/3 — scriptable
apatch-chroot service ubuntu list-units
```

## Architecture

| Path | Role |
|---|---|
| `daemon/daemon.py` | Privileged core. Root, started at boot. Dials the broker and serves requests; performs `chroot`/`mount`/`openpty`. |
| `daemon/daemon_cmds.py` | Container commands (run/exec/install/list/clone/push/pull/convert/kill/remove/service), init staging, DNS manager, capability-drop policy, `ports`. |
| `daemon/mounts.py` | The fortress: private mount namespace, `/proc subset=pid`, RO `/sys`, `/dev` masks, shared-storage binds, teardown ordering. |
| `daemon/syscalls.py` | ctypes mount/unshare/prctl helpers, capability constants + `HARDEN_DROP` set. |
| `daemon/broker.py` | Termux-uid relay. Owns the `app_data_file` socket the CLI reaches; pairs CLI connections with core connections. |
| `daemon/systemctl.py` | Python init supervisor + systemctl replacement (unit files, dependency order, control socket). MIT, clean-room. |
| `daemon/init.sh` | Legacy POSIX sh supervisor (fallback when the guest has no python3). |
| `daemon/systemctl-shim.sh` | Legacy sh systemctl shim (same fallback path). |
| `daemon/binfmt.py` | Optional box64: registers x86_64 → box64 via binfmt_misc, scoped to the container's private mount namespace. |
| `daemon/registry.py` | Container registry (name → rootfs/image metadata). |
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

Inside a `--init` container, `systemctl` talks to the init over a chroot-local unix socket (`/run/apatch-chroot/control.sock`) with one-line JSON requests/responses.

## Tests

```sh
python3 -m unittest test_systemctl -v   # 11 tests: parsing, dependency order +
                                        # cycles, enable/disable, degraded status,
                                        # live process start/stop, control socket
python3 test_mounts.py                  # live on-device harness (needs the
python3 test_client.py                  #   deployed daemon + a container)
python3 test_alpine.py                  #   — run AFTER flashing, on the phone
```

`test_systemctl` is stdlib-only (no pytest needed on-device).

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
- [x] Container init supervisor + `systemctl` replacement (Python, unit files)
- [x] `service` command — manage container services from the host
- [x] Optional box64 — per-container x86_64 emulation
- [x] `login` as non-root users (`setuser` + `--user`/`--root`)
- [x] **Fortress hardening** — subset=pid /proc, RO /sys, /dev masks, capability drops
- [x] `--shared-storage` — Android storage at `/mnt/sdcard`
- [x] Container-owned DNS (Google + Cloudflare, marker-based)
- [x] `ports` — LAN IP + listening ports (no-forwarding reachability)
- [x] `--init` staging fix (was silently dead since introduction; 11 unit tests)
- [ ] Live on-device pass: `--init` boot + services + hardening sweep (v0.5.1 flash → reboot → verify)
- [ ] Policy-routing so container traffic follows VPN apps
- [ ] `/etc/resolv.conf.d/local.conf` override hook

## Release status

- **Verified live on-device:** core daemon, broker, watchdog, OCI `install`, `exec`/`clone`/`push`/`pull`, mount isolation, `login` (bash -l), hardening banner + capability drops, `ports`.
- **Unit-verified (11/11 green):** the Python systemctl — unit parsing, Environment/EnvironmentFile, dependency order + cycle handling, enable/disable symlink round-trip, degraded status, live process start/stop, control-socket round trip.
- **New in v0.5.1, pending the live pass:** the full `--init` boot (unit-file services running under the Python supervisor) — flash v0.5.1, reboot, then `login <name> --init` → `systemctl list-units`.
- `convert` and `--box64` degrade safely; end-to-end runs depend on the source tool's quirks (chroot-distro ghost mounts) and kernel binfmt_misc support.

## License

MIT — all code in this repository is original and MIT-licensed. The `systemctl` replacement was written from systemd's documented unit-file semantics and POSIX primitives (clean-room; no third-party implementation code was copied).
