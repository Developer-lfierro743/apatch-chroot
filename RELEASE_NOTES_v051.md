# v0.5.1 — the init that never was

**Fixed: `--init` never actually worked in any release.** `stage_init()` was
called AFTER `os.chroot()`, where the daemon's own scripts are invisible —
so the init supervisor and the systemctl shim were never staged, the login
silently degraded to a plain exec, and the guest's real systemd systemctl
answered "Running in chroot, ignoring command". (The v0.5.0 hardening made
this visible; it was not the cause.)

## Added

- **Own Python systemctl replacement (MIT, clean-room)** —
  `daemon/systemctl.py`, staged as the guest's `systemctl` and init:
  - init/supervisor mode: parses systemd-style unit files
    (`/etc/systemd/system/*.service`), starts enabled units in dependency
    order (After/Wants/Requires, cycle-safe), reaps zombies, handles
    SIGTERM (stops services in reverse order), supports
    Type=simple/forking/oneshot, User=, Environment/EnvironmentFile,
    WorkingDirectory, Restart=no/on-failure/always (bounded).
  - command mode: `systemctl list-units / start / stop / restart / status /
    is-active` over a chroot-local unix socket; `enable / disable` create
    real `multi-user.target.wants` symlinks (like systemd) and work even
    with the init down; status-style verbs degrade to pidfile/proc reads
    when no init is running — never a chroot-refusal again.
  - logs in `/var/log/apatch-chroot/<unit>.log`, pidfiles in
    `/run/apatch-chroot/<unit>.pid` (same conventions as before).
- Staging now prefers the Python path when the guest has `python3`;
  the POSIX sh supervisor + shim remain as the no-python fallback
  (`apatch-init.sh`, `apatch-systemctl-sh`).

## Fixed

- `stage_init` called before `os.chroot()` in both entry paths
  (login/run and exec) — the shim + init finally get installed.

## Tests

- 11 new stdlib-unittest tests (`test_systemctl.py`): unit parsing,
  Environment/EnvironmentFile, dependency order + cycle handling,
  enable/disable symlink round-trip, degraded status, live process
  start/stop/restart, control-socket round trip — 11/11 green.
