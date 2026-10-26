# Changelog

Highlights per release. Full notes live in the tagged
`RELEASE_NOTES_v*.md` files and on the
[releases page](https://github.com/Developer-lfierro743/apatch-chroot/releases).

## v0.5.4 — Modified systemd: journal + first service (versionCode 19)

- **Modified systemd suite** now lives in `systemd/` (component map in its README)
- **journald.py / journalctl**: service logs + lifecycle events merged into a
  queryable journal (`-u -f -n -r -S -U`)
- **RestartSec=** support (pending-restart queue in the supervisor)
- `systemd/examples/hermes-gateway.service` — the Hermes Agent gateway as a real unit
- Help **page 2** (`--help --page 2`); client VERSION unfrozen (was stuck at v0.4.0.8)
- Packaging fix: `systemd/` ships in the zip + deploys at boot

## v0.5.3 — loginctl (versionCode 18)

- `loginctl`: session registry, list/show/terminate/kill, **linger** (services
  survive logout), auto-prune of dead sessions
- Init supervisor registers its login session; honors linger

## v0.5.2 — hotfixes + new README (versionCode 17)

- Test harnesses: fixed stale deploy paths; list-units fallback de-duplicated
- README rewritten (fortress, doors, honest release status)

## v0.5.1 — the init that never was (versionCode 16)

- **Fixed: `--init` never actually worked** — staging ran after `chroot()`, so
  the init supervisor + systemctl shim were never installed; the guest's real
  systemctl answered "Running in chroot, ignoring command"
- Own **Python systemctl replacement** (unit files, dependency order,
  Type/User/Environment/Restart, control socket), sh suite kept as fallback
- 11 new tests

## v0.5.0 — fortress + doors (versionCode 15)

- **Hardening (default ON):** `/proc subset=pid`, read-only `/sys`,
  `/dev/block` + `/dev/socket` masks, ~20 host-dangerous capability drops
  (`--no-harden` / `--keep-sysadmin` escape hatches)
- `--shared-storage` (Android storage at `/mnt/sdcard`)
- Container-owned DNS (Google + Cloudflare, marker-based)
- `ports` — LAN IP + listening sockets (chroot webapps, no forwarding)
- Broker SIGTERM cleanup; orphaned-pid reaping
