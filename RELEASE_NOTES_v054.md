# v0.5.4 — Modified systemd: the journal + our first service

The systemd suite now lives in its own folder (`systemd/`) — component
map included: what's needed to "run systemd" vs what we ship (and what's
deliberately N/A on Android). Same MIT clean-room pattern.

## Added

- **journald.py / journalctl** — the container journal, stateless:
  - service stdout/stderr (`/var/log/apatch-chroot/<unit>.log`) merged
    with structured lifecycle events (`journal.jsonl`: started / exited /
    stopped / restarted / failed, written by the init supervisor)
  - `journalctl -u UNIT [-f] [-n N|all] [-r] [-S "10 min ago"] [-U ...]`,
    positional units, `--list-boots`, `--no-pager`/`-o short` accepted,
    real binaries kept as `journalctl.real`
- **RestartSec=** — units now honor the delay (pending-restart queue in
  the supervisor; the Hermes gateway unit uses RestartSec=10).
- **`systemd/examples/hermes-gateway.service`** — the first real service:
  the Hermes Agent gateway as a proper unit (User/WorkingDirectory/
  Environment/ExecStart/Restart=always/RestartSec=10, WantedBy=
  multi-user.target). Push it into `/etc/systemd/system/` and it starts
  on every `--init` boot.
- **Help page 2** — `apatch-chroot --help --page 2` (session flags,
  hardening escape hatches, the in-container systemd suite). Front page
  hints at it.
- **Client VERSION fixed** — was frozen at v0.4.0.8 since that release
  (help banner + check-update now track the real version).

## Changed

- `systemctl.py` + `loginctl.py` moved `daemon/` → `systemd/` (the
  Modified systemd suite). Paths updated in the daemon + tests.

## Tests

- 9 new journald tests. **31/31 green** (systemctl 11 + loginctl 11 +
  journald 9), three consecutive runs.
