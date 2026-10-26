# v0.5.0 — fortress + doors

The containment overhaul plus the quality-of-life doors users asked for.
Upgrading is safe: existing containers keep working, no layout changes.

## Added

- **`--shared-storage`** — Android shared storage inside the container at
  `/mnt/sdcard` (with a `/sdcard` symlink). Prefers the raw backing store
  `/data/media/0` (no FUSE recursion, stable ownership) and falls back to
  `/storage/emulated/0`. Files the guest writes show up for Android apps.
- **`apatch-chroot ports`** — prints the phone's LAN IP and every listening
  TCP port. The container shares the kernel network namespace with Android,
  so a webapp binding `0.0.0.0:<port>` inside the chroot is reachable from
  the LAN with zero port forwarding (e.g. Hermes' webapp).
- **Container-owned DNS** — `/etc/resolv.conf` is now a managed file:
  Google (8.8.8.8, 8.8.4.4) + Cloudflare (1.1.1.1, 1.0.0.1), rewritten on
  every login. Your own config (no marker, working nameservers) is respected.
- **`--no-harden` / `--keep-sysadmin`** — escape hatches for the new
  containment (see below). `--keep-sysadmin` re-adds CAP_SYS_ADMIN for
  FUSE/AppImage users at the cost of mount containment.

## Changed — containment hardening (default ON)

The whole point: what happens inside the chroot **cannot** break the host
Android system. All namespace-scoped, host mount table untouched:

- **/proc `subset=pid`** (hidepid=2 fallback) — the guest, even as root,
  sees only its own process tree. Host processes are invisible.
- **/sys mounted READ-ONLY** — no sysfs writes from inside (no driver
  unbinds, no kernel knobs).
- **/dev/block + /dev/socket masked** — no block devices reachable (no
  `dd`-to-mmcblk0 bricking) and no Android control sockets (the
  property_service socket would let root set HOST system properties).
- **Capability bounding-set drops** (one-way, inherited by every
  descendant, setuid/setcap can't regain): SYS_MODULE, SYS_RAWIO,
  SYS_PTRACE, SYS_ADMIN*, NET_ADMIN, SYS_BOOT, SYS_TIME, SYS_TTY_CONFIG,
  MKNOD, SYSLOG, AUDIT_CONTROL, MAC_*, BPF, PERFMON and friends. The guest
  root can no longer load host kernel modules, reconfigure HOST networking
  (shared netns!), change the host clock, reboot the host, or create device
  nodes that bypass the masks.
  *--keep-sysadmin re-adds SYS_ADMIN only.

- Broker: clean SIGTERM shutdown that unlinks stale sockets (no more
  EADDRINUSE after an abrupt Android shutdown); watchdog/kill reap
  orphaned session pids so a recycled pid is never signalled.

## Notes

- Hardened guests lose: FUSE/AppImage mounts and host-network
  reconfiguration (by design). Use `--keep-sysadmin` / `--no-harden`
  when you truly need them.
- VPN caveat: container traffic follows the host routing table and bypasses
  per-app VPN routing (root UID is not in any VPN app list). Documented;
  policy-routing integration is future work.
- 11 crypto/container tests + live login verified on-device.
