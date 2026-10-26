---
title: What the flip is the new systemd?
nav_order: 11
---

So you ran `apatch-chroot login ubuntu --init` and suddenly the container
has a `systemctl`. What the flip is that thing, and why does it exist when
"systemd can't run on Android" is basically a law of nature? Fair question.
Here's the honest answer.

## The problem: real systemd is a no-go on a chroot

Real systemd needs four things Android will never give a chroot:

1. **Its own PID namespace** — so it can be PID 1. Android's PID 1 is
   init, and it stays init. We don't touch it (that's the whole deal).
2. **cgroups** — for service tracking, resource limits, slicing. Android
   owns the cgroup hierarchy; a chroot doesn't get to play.
3. **dbus** — systemctl literally talks to systemd over dbus. No dbus on
   Android, no party.
4. **A booted system** — systemd wants to BE the boot process, mount
   everything, spawn udev, the works. In a chroot, "boot" already
   happened a while ago, on someone else's watch.

So every container tool fakes it. Docker runs systemd-in-docker with
privileges we will never grant. Distrobox-ish setups hand-wave. And most
Android chroot tools just... don't have services, which is why your sshd
never starts itself and your webapp dies when you close the terminal.

## What we built instead — "Modified systemd"

Four small Python tools (MIT, clean-room, ~1,300 lines total) that give a
chroot the pieces of systemd people actually use. Not a port. Not a
shim over someone else's code. Ours.

| Real systemd | Ours | Where it lives in the container |
|---|---|---|
| systemd (PID 1) | `systemctl.py init` — the init supervisor | `/usr/local/bin/apatch-init` |
| systemctl | `systemctl.py` command mode → `/usr/bin/systemctl` (real one kept as `systemctl.real`) | |
| journald + journalctl | `journald.py` → `/usr/bin/journalctl` | |
| logind + loginctl | `loginctl.py` → `/usr/bin/loginctl` | |
| unit files + `multi-user.target.wants` | **actual systemd unit files**, real symlinks | `/etc/systemd/system/` |

The suite's source lives in the repo under [`systemd/`](https://github.com/Developer-lfierro743/apatch-chroot/tree/main/systemd)
with a component map of what's needed to "run systemd" vs what we ship vs
what's deliberately N/A (udev, cgroups, dbus, PID namespaces — Android
owns all of those, by design).

## What it actually does

```sh
apatch-chroot login ubuntu --init
```

The init supervisor (PID 1 inside the chroot, in the daemon's private
mount namespace) then:

- parses **real unit files** from `/etc/systemd/system/*.service`
- starts every **enabled** unit in **dependency order** (`After=` /
  `Wants=` / `Requires=`, cycle-safe)
- supports `Type=simple|forking|oneshot`, `User=`, `Environment=`,
  `EnvironmentFile=`, `WorkingDirectory=`, `Restart=` **and**
  `RestartSec=`
- **reaps zombies** (the classic chroot disease)
- logs every lifecycle moment to the journal
- stops services in reverse order when you log out

And while it runs, the other three tools just work:

```sh
systemctl start hermes-gateway
systemctl enable hermes-gateway      # real multi-user.target.wants symlink
systemctl status hermes-gateway
journalctl -u hermes-gateway -f      # its logs, live
loginctl list-sessions               # who's logged in
loginctl enable-linger root          # services survive logout
```

`enable`/`disable` are pure filesystem ops — they work even when the init
isn't running. `status`/`is-active` degrade to pidfile + `/proc` reads.
Nothing ever tells you "Running in chroot, ignoring command". (That used
to be the old shim's party trick. We do not miss it.)

## What it is NOT (the "don't freak out" section)

- **It can't break your phone.** The supervisor runs inside the chroot,
  in the daemon's private mount namespace, with host-dangerous
  capabilities dropped (see [safety](safety)). No cgroups, no udev, no
  kernel modules, no host sockets. Worst case, a service crashes — and
  gets restarted per its unit's `Restart=` policy, or marked failed.
- **It's not trying to be all of systemd.** No timers (yet), no mounts
  (the daemon owns mounts), no socket activation, no generators. If a
  unit file uses something exotic, we ignore that key and run the
  `ExecStart` — boring on purpose.
- **It's not a security boundary.** The fortress (capability drops, RO
  `/sys`, masked `/dev`) is the boundary. The systemd suite is a
  convenience layer that runs *inside* it.

## The guest needs python3

Staging prefers the Python suite when the container has `python3`
(Ubuntu does). Without it, you get the legacy POSIX sh supervisor +
shim — same commands, smaller brain. Both keep the real binaries as
`*.real`, so nothing from the distro is destroyed.

## First real service: the Hermes Agent gateway

`systemd/examples/hermes-gateway.service` in the repo is a complete,
real unit — the Hermes Agent gateway, `Restart=always`, `RestartSec=10`,
`WantedBy=multi-user.target`:

```sh
apatch-chroot push ubuntu systemd/examples/hermes-gateway.service \
    /etc/systemd/system/hermes-gateway.service   # the real systemd spot
apatch-chroot exec ubuntu -- systemctl enable hermes-gateway
apatch-chroot login ubuntu --init                 # boots WITH the gateway
apatch-chroot ports                               # gateway port, LAN-reachable
```

That's the flip: your agent gateway comes up with the container, restarts
itself if it crashes, logs to a real journal, and is reachable from your
laptop — and none of it required systemd to actually exist.
