---
title: Storage & convert
nav_order: 8
---

Three ways a container gets onto your device, and the disk-space math behind
each.

## install — pull from a registry

```sh
apatch-chroot install ubuntu
```

Downloads an OCI image from Docker Hub and extracts it. Needs free space equal
to the image size (plus a layer cache). Fresh images start small.

## convert — import from chroot-distro (copy)

```sh
apatch-chroot convert ubuntu ubuntu-apatch
```

**Copies** an existing chroot-distro container's rootfs into apatch-chroot.
Because it's a copy, it needs **free space equal to the container's size** — the
original and the copy both exist briefly.

The chroot-distro source must be **unmounted** first. If it has ghost mounts
(locked `/apex` binds), convert refuses — only a reboot clears them, and you
must not run any chroot-distro command after rebooting before converting.

## convert --move — import without copying (rename)

```sh
apatch-chroot convert ubuntu --move
```

**Renames** the rootfs on the same `/data` filesystem instead of copying. This
is the one to use when the container is bigger than your free space.

| | `convert` (copy) | `convert --move` (rename) |
|---|---|---|
| Speed | minutes for GBs | **instant** |
| Extra space needed | = container size | **zero** |
| Data | intact | intact |
| Source after | kept (a backup) | **consumed** |

`--move` works because both paths live on `/data`. It's a single `rename()`
syscall — the bytes never move, only the folder's address changes. If the paths
were on different filesystems, it falls back to a copy automatically.

The trade-off: the chroot-distro source is gone (it *became* the apatch-chroot
container). That's fine when your data lives in it.

## Check the numbers first

```sh
apatch-chroot list          # each container's size
df -h /data                 # free space
du -sh <rootfs>             # a specific container
```

Rule of thumb:
- Container **smaller than free space** → `convert` (copy) is fine, keeps a backup.
- Container **bigger than free space** → `convert --move` (rename), zero space.

## Migration from chroot-distro, end to end

1. Make sure the chroot-distro container is **unmounted**. If it has ghost
   mounts: reboot, then **don't** touch chroot-distro.
2. `apatch-chroot convert ubuntu --move`
3. `apatch-chroot login ubuntu` — verify your data is there (`ls /root`).
4. Only then remove the chroot-distro original, if anything remains.

The original is never modified by convert/move until you explicitly delete it, so
it's your safety net the whole time.