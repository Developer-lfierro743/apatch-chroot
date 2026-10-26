# v0.5.2 — hotfixes + new README

## Fixed
- Test harnesses pointed at a stale deploy path (`/data/local/tmp/apatch-chroot/client/...`); the daemon actually deploys to `/data/apatch-chroot/`. Fixed in `test_mounts.py`, `test_client.py`, `test_alpine.py` — the live harnesses run again.
- `systemctl list-units` fallback (init down): called the status reader twice per unit; now once. Same output, half the work.

## Added
- New README: fortress hardening table, shared storage, networking + `ports`, container DNS, the Python systemctl service manager, tests, honest release-status section.

## Tests
- `test_systemctl`: 11/11 green, three consecutive runs (fixed a thread-race flake in the control-socket test by waiting for the socket to be bound).
