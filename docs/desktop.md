---
title: Desktop (LXQt)
nav_order: 6
---

You can run a real Linux desktop inside a container on your phone. This guide
sets up **LXQt** — lightweight and Qt-based, a good fit for a phone.

## Prerequisites

On the **host** (Termux):

```sh
pkg install x11-repo termux-x11-nightly pulseaudio
```

## 1. Install the desktop (inside the container)

```sh
apatch-chroot login ubuntu
# inside:
bash setup-desktop.sh
exit
```

`setup-desktop.sh` (ships in the zip at `scripts/setup-desktop.sh`) installs
LXQt + openbox (its window manager) + qterminal (terminal) + dbus-x11 +
pcmanfm-qt (file manager) + FeatherPad (editor), and drops a `startlxqt`
launcher. It's idempotent — safe to re-run.

> If you don't have the script yet, copy it into the container or install
> manually: `apt-get install -y --no-install-recommends lxqt openbox qterminal dbus-x11 lxqt-sudo pcmanfm-qt FeatherPad`.

## 2. Start the host services

```sh
# in Termux (host):
termux-x11 :0 &
pulseaudio --start --exit-idle-time=-1
```

## 3. Launch the desktop

```sh
apatch-chroot login ubuntu --shared-display --shared-audio
# inside:
startlxqt
```

First launch is slow (LXQt builds its config). You should get the LXQt panel,
desktop, and a window manager. Open qterminal from the menu to get a shell.

## What each flag does

| Flag | Effect |
|---|---|
| `--shared-display` | binds termux-x11's socket, sets `DISPLAY=:0` (visual) |
| `--shared-audio` | binds PulseAudio's socket, sets `PULSE_SERVER` (sound) |

Both binds live in the container's private mount namespace, so the host's audio
and mounts are untouched (see [safety.md](safety.md)).

## Running apps inside the desktop

Once the desktop is up, launch apps from the LXQt menu, or from a qterminal:

```sh
pcmanfm-qt          # file manager
featherpad file.txt # editor
```

For heavier apps (VSCode, Chromium), see [electron.md](electron.md) — they need
`--no-sandbox`.

## Tips

- **Memory:** close Android apps before launching the desktop; it competes for RAM.
- **Blank screen:** confirm `termux-x11 :0` is running and `echo $DISPLAY` is `:0`
  inside the container.
- **Sound:** run `pulseaudio --start` on the host *before* logging in with
  `--shared-audio`.
- **Resolution:** termux-x11 picks a default; adjust in its app if the desktop is
  too small/large for your screen.

## Alternative: launch the desktop automatically

If you want the desktop to start on login instead of typing `startlxqt`, use
`--init` with a service, or just alias it. A simple one-liner login:

```sh
apatch-chroot run ubuntu --shared-display --shared-audio -- /bin/bash -lc startlxqt
```