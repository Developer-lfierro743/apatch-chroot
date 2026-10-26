# Modified systemd — our custom systemd for Android chroots

**What is this?** A from-scratch, MIT-licensed suite that gives an Android
chroot the systemd pieces people actually use — without systemd itself
(no PID namespace, no cgroups, no dbus on Android). Every file is written
from systemd's documented behavior + POSIX primitives; no upstream code.

```
┌──────────────────────────────────────────────────────────────────┐
│  login --init  →  apatch-init (systemctl.py init) = PID 1        │
│                   ├── starts enabled units (dependency order)    │
│                   ├── reaps zombies, Restart= + RestartSec=      │
│                   ├── control socket → systemctl verbs           │
│                   ├── registers sessions → loginctl              │
│                   └── lifecycle events → journal (journald.py)   │
└──────────────────────────────────────────────────────────────────┘
```

## The component map: what's needed to "run systemd", and what we ship

| Real systemd component | What it does | Ours | Status |
|---|---|---|---|
| **PID 1 init** (`systemd`) | start boot target, reap zombies, stop services | `systemctl.py init` (via the staged `apatch-init` wrapper) | ✅ live |
| **systemctl** | unit-file control: start/stop/status/enable/... | `systemctl.py` command mode + `/usr/bin/systemctl` wrapper (real → `.real`) | ✅ live |
| **journald + journalctl** | service logs, queryable journal | `journald.py` — service stdout/stderr logs + `journal.jsonl` lifecycle events, queried by `journalctl` (`-u -f -n -r -S -U`) | ✅ live |
| **logind + loginctl** | sessions, seats, linger | `loginctl.py` — filesystem session registry, terminate/kill, linger (services survive logout) | ✅ live |
| unit files + `multi-user.target.wants` | service definitions + enablement | real systemd unit files in `/etc/systemd/system/`, enable = real symlinks | ✅ live |
| `user@.service` (per-user managers) | user-level services | not needed yet (container users are few; services are system-level) | ➖ deferred |
| udevd | device events | N/A — Android owns devices; the fortress masks what's dangerous | ➖ N/A |
| timedatectl / hostnamectl | clock/hostname | N/A — host-owned (and deliberately so: `CAP_SYS_TIME` is dropped) | ➖ N/A |
| dbus-daemon | IPC bus | not required — our IPC is the chroot-local control socket + pidfiles | ➖ deferred |
| cgroups | resource control | N/A on Android (cgroupfs is host-managed) | ➖ N/A |
| PID namespaces | true container init | N/A — Android's PID 1 stays ours-by-contract: we run in a private mount ns, not a PID ns | ➖ by design |
| `.target` units, generators, fstab, mount units | boot orchestration | simplified: enabled services start in dependency order at `--init` boot | ➖ subset |

**Bottom line:** what a chroot needs from systemd is a service manager, a
systemctl, a journal, and a login/session story. All four exist here, in
~1,300 lines of readable Python, and none of it can break the host.

## Files

| File | Role |
|---|---|
| `systemctl.py` | init supervisor (PID 1 inside the chroot) + systemctl client; unit parsing, dependency order, `Restart=`/`RestartSec=`, control socket, journald events |
| `loginctl.py` | sessions (`/run/apatch-chroot/sessions/*.json`), terminate/kill, linger |
| `journald.py` | the journal: `journal.jsonl` events + service logs; the `journalctl` CLI |
| `examples/hermes-gateway.service` | a real unit file — the Hermes Agent gateway, our first service |

## Where things live in the guest

```
/etc/systemd/system/*.service            unit files (yours)
/etc/systemd/system/multi-user.target.wants/   enabled services (symlinks)
/usr/local/bin/apatch-systemctl.py       the suite (staged at every --init)
/usr/local/bin/apatch-loginctl.py
/usr/local/bin/apatch-journald.py
/usr/bin/{systemctl,loginctl,journalctl} wrappers (real binaries → *.real)
/var/log/apatch-chroot/<unit>.log        service stdout/stderr
/var/log/apatch-chroot/journal.jsonl     lifecycle events
/run/apatch-chroot/<unit>.pid            pidfiles
/run/apatch-chroot/control.sock          systemctl control socket
/run/apatch-chroot/sessions/             login session records
```

No python3 in the guest? The legacy POSIX sh supervisor + shim
(`daemon/init.sh`, `daemon/systemctl-shim.sh`) run instead — same
commands, smaller feature set.

## First real service: the Hermes Agent gateway

`examples/hermes-gateway.service` boots the Hermes Agent gateway inside
the container at `--init` (it is enabled, so it starts automatically):

```sh
apatch-chroot push ubuntu examples/hermes-gateway.service \
    /etc/systemd/system/hermes-gateway.service   # into the real spot
apatch-chroot login ubuntu --init                 # it starts on boot
systemctl status hermes-gateway                   # inside
journalctl -u hermes-gateway -n 20                # its logs
apatch-chroot ports                               # its port, LAN-reachable
```

Paths inside the unit follow the standard Hermes Agent layout
(`/root/.hermes/hermes-agent/venv/bin/hermes`); adjust `ExecStart=`/
`WorkingDirectory=` if your install lives elsewhere.

## License

MIT — original code, clean-room (see the repo LICENSE). Unit files in
`examples/` are configuration, not code.
