# APatch Chroot — Documentation

How apatch-chroot works, and why it's built the way it is.

## Start here

- **[architecture.md](architecture.md)** — the whole system: core daemon, broker, client, and the two Android walls (seccomp + SELinux) they're built to beat. Read this first.
- **[safety.md](safety.md)** — how a container runs without touching the Android host (audio, mounts, ghost mounts). The reason this tool exists.

## Topics

- **[services.md](services.md)** — the container init / mini-systemd, the `systemctl` shim, and managing services from the host.
- **[display.md](display.md)** — running GUI apps via termux-x11 (`--shared-display`).
- **[electron.md](electron.md)** — running VSCode / Chromium / Electron apps (`--no-sandbox`).
- **[desktop.md](desktop.md)** — a full LXQt desktop.
- **[storage.md](storage.md)** — install vs convert vs `--move`, and disk-space math.
- **[box64.md](box64.md)** — optional x86_64 emulation.
- **[protocol.md](protocol.md)** — the wire protocol between client, broker, and core.

## Quick reference

```sh
apatch-chroot info                      # device capabilities
apatch-chroot install ubuntu            # pull from Docker Hub
apatch-chroot login ubuntu              # interactive shell
apatch-chroot run ubuntu --init         # + service manager
apatch-chroot service ubuntu start sshd # manage services from host
apatch-chroot convert ubuntu --move     # import from chroot-distro (no copy)
apatch-chroot --help                    # all commands
apatch-chroot help display              # topic help
```

## Requirements

- Rooted Android (APatch, or Magisk/KernelSU with path tweaks)
- Termux (F-Droid build recommended)
- Python 3.10+ (ships with Termux)
- arm64 (x86_64 opt-in via box64 — see [box64.md](box64.md))
