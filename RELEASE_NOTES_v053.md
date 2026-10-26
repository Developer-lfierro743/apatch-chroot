# v0.5.3 — loginctl (same pattern as systemctl)

## Added

- **`loginctl` replacement (MIT, clean-room)** — `daemon/loginctl.py`,
  staged into the container as `/usr/bin/loginctl` (real binary kept as
  `loginctl.real`):
  - filesystem session registry (`/run/apatch-chroot/sessions/*.json`);
    every login registers itself — plain logins inline, `--init` logins
    via the init supervisor — and dead-leader records are pruned
    automatically (no cleanup handshake)
  - verbs: list-sessions / list-users / list-seats, show-session,
    session-status, show-user, user-status, terminate-session,
    terminate-user, kill-session, kill-user, enable-linger,
    disable-linger
  - **linger with real behavior**: `enable-linger USER` → when the login
    shell exits, background services keep running (sshd/webapp survive
    logout); the next `--init` login adopts them via pidfiles;
    `apatch-chroot kill` still stops everything. Default (no linger) is
    unchanged: exit = stop services.
- Init supervisor (`systemctl.py init`) now registers its login session
  and honors linger on shutdown.

## Tests

- 11 new tests (`test_loginctl.py`): registry round-trip, auto-prune of
  dead sessions, find/filter, linger round-trip, terminate (real
  process), output formats, unknown-verb rc. 22/22 green total.
