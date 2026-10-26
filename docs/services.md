---
title: Services (mini-systemd)
nav_order: 4
---

> **v0.5.x replaced this whole design.** The sh supervisor + `services.conf`
> are now the no-python3 fallback; the real deal is the Modified systemd
> suite (Python init supervisor, unit files, systemctl/loginctl/journalctl).
> Read **[What the flip is the new systemd?](new-systemd)** — this page
> stays for the legacy path.

Android has no systemd — only `init` (PID 1), which boots the phone. A bare
chroot therefore has **no service manager**: sshd/dbus/cron don't auto-start,
and orphaned zombies are never reaped. apatch-chroot ships a tiny, purpose-built
supervisor to fix that.

## What it is (and isn't)

It's **not a systemd port**. It's a small POSIX-sh supervisor (`daemon/init.sh`)
that runs as **PID 1 inside the container's chroot** — in the daemon's private
mount namespace. Android's own PID 1 is never involved.

What it does:
- Reads enabled services from `/etc/apatch-chroot/services.conf`
  (`name|enabled|command` per line)
- Starts them, tracks PIDs, **reaps zombies**
- **Restarts crashed services** with a cooldown (a crash-looping service backs
  off instead of hammering the CPU)
- Runs your shell as a child; stops all services on exit

## Using it

```sh
apatch-chroot login ubuntu --init
```

Define services in `/etc/apatch-chroot/services.conf`:
```
sshd|1|/usr/sbin/sshd -D
dbus|1|/usr/bin/dbus-daemon --system --nofork
```

## The `systemctl` shim

Ubuntu/Debian ship systemd + unit files, and countless scripts call
`systemctl start sshd`. But systemd can't be PID 1 here (no cgroups, no dbus, no
`/run/systemd`). So `--init` installs a **`systemctl` shim** at
`/usr/bin/systemctl` (the real binary is kept as `systemctl.real`).

The shim supports: `start stop restart reload status is-active is-enabled
enable disable list-units`. It resolves each service's command by:
1. the manifest, then
2. the container's unit file (`ExecStart=`), then
3. a built-in fallback (sshd/dbus/cron/rsyslog/nginx).

It honors systemd's **exit-code contract** (0 = active/enabled, 3 = inactive),
so scripts using `systemctl is-active foo && ...` work.

```sh
# inside a --init container:
systemctl start sshd
systemctl enable sshd     # auto-start next run
systemctl status sshd
```

## Managing services from the host

You don't have to log in. The `service` command runs the container's systemctl
shim via the exec path:

```sh
apatch-chroot service ubuntu start sshd
apatch-chroot service ubuntu enable sshd
apatch-chroot service ubuntu status sshd
apatch-chroot service ubuntu is-active sshd   # exit 0/3 — scriptable
apatch-chroot service ubuntu list-units
```

## Tuning

Environment variables (set inside the container, or exported before login):

| Var | Default | Meaning |
|---|---|---|
| `APATCH_CHECK_INTERVAL` | `2` | Seconds between supervision passes |
| `APATCH_RESPAWN_COOLDOWN` | `5` | Min seconds before restarting a dead service |

## Limitations

- It's a subset, not systemd: no cgroups, no unit dependencies, no socket
  activation. It starts listed services and keeps them alive — that's it.
- `--init` always runs as root (the supervisor is PID 1); the `--user` least-
  privilege drop applies only to plain `run`/`exec`, not `--init`.