---
title: Home
nav_order: 1
layout: home
permalink: /docs/
---

# APatch Chroot

Run real Linux distributions (Ubuntu, Debian, Alpine, …) on a rooted Android device — with its own init system, a working `systemctl`, and mount isolation that **doesn't break your Android host**.

A chroot-based container runtime built around a **privileged APatch daemon** and a **Termux-uid broker**, solving the two Android walls that stop naive approaches: **seccomp** (apps can't call `chroot`/`mount`) and **SELinux** (an app can't reach a root socket).

## Start here

- **[Architecture](architecture.md)** — the three-process design and the two Android walls. Read this first.
- **[Safety](safety.md)** — how a container runs without touching the Android host (audio, mounts). Why this tool exists.

## Topics

- **[Services](services.md)** — mini-systemd init, the `systemctl` shim, managing services from the host.
- **[Display](display.md)** — GUI apps via Termux:X11 (`--shared-display`).
- **[LXQt desktop](desktop.md)** — run a lean desktop with private D-Bus and a RAM startup check.
- **[Electron apps](electron.md)** — VSCode / Chromium (`--no-sandbox`).
- **[Storage](storage.md)** — install vs convert vs `--move`, and disk-space math.
- **[box64](box64.md)** — optional x86_64 emulation.
- **[Protocol](protocol.md)** — the wire protocol between client, broker, and core.

## Quick reference

```sh
apatch-chroot info                      # device capabilities
apatch-chroot check-update              # is a newer release out?
apatch-chroot install ubuntu            # pull from Docker Hub
apatch-chroot login ubuntu              # interactive shell
apatch-chroot login ubuntu --init       # + service manager
apatch-chroot service ubuntu start sshd # manage services from host
apatch-chroot convert ubuntu --move     # import from chroot-distro (no copy)
apatch-chroot desktop ubuntu --check    # LXQt preflight (main branch)
apatch-chroot --help                    # all commands
```

## Requirements

- Rooted Android (APatch, or Magisk/KernelSU with path tweaks)
- Termux (F-Droid build recommended)
- Python 3.10+ (ships with Termux)
- arm64 (x86_64 opt-in via box64 — see [box64](box64.md))

## Get it

Flash the module zip from the [latest release](https://github.com/Developer-lfierro743/apatch-chroot/releases/latest) in APatch → Modules → Install from storage, then reboot.

> ⚠️ This runs a **root daemon** — read the [safety notes](safety.md) before installing.
