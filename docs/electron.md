---
title: Electron / Chromium
nav_order: 7
---

VSCode, Discord, and many modern apps are **Electron** — a bundled Chromium.
They run in apatch-chroot, but Chromium's security model fights a chroot in two
specific ways. This guide covers both.

## The two problems

### 1. Chromium's sandbox won't work in a chroot

Chromium sandboxes its renderer processes using Linux namespaces + seccomp. In a
chroot (and especially under Android's seccomp), that sandbox setup fails and the
app won't start, or crashes on launch.

**Fix: run with `--no-sandbox`.** This disables Chromium's process sandbox. The
app runs, but without that layer of isolation.

> **Security trade-off, be honest with yourself:** `--no-sandbox` means a
> compromised renderer isn't contained. For a **single-user, local container**
> that already runs as root inside its namespace, this is usually an acceptable
> trade — but it's a real reduction in isolation. Don't run untrusted web content
> in a `--no-sandbox` Chromium and assume you're protected.

### 2. `chrome-sandbox` needs setuid root

Chromium's sandbox helper (`chrome-sandbox`) is a setuid-root binary. On Android,
`/data` is mounted `nosuid`, so it can't elevate.

apatch-chroot **remounts the container's rootfs `suid,exec`** inside the private
namespace, so setuid binaries *do* work in-container. That handles this — you
don't need to `chmod` anything.

## How to run an Electron app

### VSCode (arm64)

```sh
# inside the container:
apatch-chroot login ubuntu --shared-display --shared-audio

# install the arm64 .deb (Microsoft ships code for arm64):
apt-get install -y wget
wget -O /tmp/code.deb "https://code.visualstudio.com/sha/download?build=stable&os=linux-deb-arm64"
apt-get install -y /tmp/code.deb

# run it — note the --no-sandbox flags:
code --no-sandbox --disable-gpu-sandbox
```

### Generic Electron app

Most Electron apps accept the same flags:

```sh
yourapp --no-sandbox --disable-gpu-sandbox
```

Some read flags from an env var instead:

```sh
export ELECTRON_DISABLE_SANDBOX=1
yourapp
```

## Recommended flags

| Flag | Why |
|---|---|
| `--no-sandbox` | disables the namespace-based renderer sandbox (required in chroot) |
| `--disable-gpu-sandbox` | the GPU process has its own sandbox; disable it too |
| `--disable-dev-shm-usage` | `/dev/shm` is small/limited; avoids crashes on shared memory |
| `--in-process-gpu` | fallback if GPU crashes (software rendering) |

A reliable combo:

```sh
yourapp --no-sandbox --disable-gpu-sandbox --disable-dev-shm-usage
```

## GPU / rendering

Chromium tries to use the GPU. In a container that often fails. Two paths:

- **Software rendering** (reliable, slower):
  ```sh
  LIBGL_ALWAYS_SOFTWARE=1 yourapp --no-sandbox
  ```
- **Let it try the GPU** first; if you get a blank window or crash, fall back to
  software.

## Memory reality check

Chromium + Electron is **memory-hungry**. On a phone (limited RAM, and the
container competes with Android), expect:

- VSCode: workable but not snappy.
- Chromium with many tabs: will swap/struggle.
- Close other apps before launching heavy Electron stuff.

If it OOMs, that's the phone's memory limit, not apatch-chroot — nothing to fix
in the tool.

## Summary

```sh
# host:
termux-x11 :0 &
pulseaudio --start

# container:
apatch-chroot login ubuntu --shared-display --shared-audio
code --no-sandbox --disable-gpu-sandbox --disable-dev-shm-usage
```

Electron/Chromium apps **work**, the trade is `--no-sandbox` (documented, not
hidden) and memory pressure on a phone. The setuid/`suid` remount apatch-chroot
already does covers `chrome-sandbox`, so you don't fight that.