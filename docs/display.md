---
title: Display (termux-x11)
nav_order: 5
---

Android has no X server, so GUI apps (xterm, editors, browsers) need one. On
Android that's **termux-x11**, an X server that runs as an app.

## Setup

```sh
# 1. Install termux-x11 (once, in Termux):
pkg install x11-repo
pkg install termux-x11-nightly

# 2. Start the X server:
termux-x11 :0 &
```

## Run a GUI app in a container

```sh
apatch-chroot login ubuntu --shared-display
# inside:
xterm
```

or one-shot:

```sh
apatch-chroot run ubuntu --shared-display -- xterm
```

`--shared-display` (alias `--shared-x11`) does two things:
1. Binds termux-x11's socket (`/data/data/com.termux/files/usr/tmp/.X11-unix`)
   into the container's `/tmp/.X11-unix`.
2. Sets `DISPLAY=:0` in the guest environment.

## Why it's safe

This is the feature that **broke host audio in chroot-distro**. Its
`--shared-display` lacked mount-namespace isolation, so the X11/devpts mounts
propagated onto the host `/dev`.

apatch-chroot's `--shared-display` binds the socket into the **same private
mount namespace** as every other mount. It **cannot propagate** to the host —
Android's `/dev/snd` and everything else stay untouched. Same guarantee as the
core mount isolation. See [safety.md](safety.md).

## Troubleshooting

**"cannot open display"**
- Is termux-x11 actually running? Start it: `termux-x11 :0 &`
- Check `DISPLAY` inside the container: `echo $DISPLAY` → should be `:0`.
- Run `apatch-chroot info` — confirms namespace/binfmt status.
- The socket only exists while termux-x11 is up; start it before logging in.

**Window doesn't appear / crashes**
- Make sure you launched termux-x11 (it's a separate app/session).
- Some GPU apps need more than a socket; software rendering (`LIBGL_ALWAYS_SOFTWARE=1`)
  is a fallback.

## What works

- X11 apps that use the classic X protocol (xterm, most Tk/Qt/GTK apps with
  software rendering).
- WebGL/GPU-accelerated apps may not — those need more than a socket.