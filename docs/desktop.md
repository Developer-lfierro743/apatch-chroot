---
title: Desktop (LXQt)
nav_order: 6
---

`apatch-chroot desktop` runs one Ubuntu LXQt session on an existing Termux:X11
server. It doesn't start a second X server, a display manager, or a full init.
Use a non-root guest user when possible.

> Requires APatch Chroot **v0.5.6 or newer**. Install the module ZIP from
> [GitHub Releases](https://github.com/Developer-lfierro743/apatch-chroot/releases/latest)
> and reboot before following the guest setup below. Updating guest scripts
> alone does not update the Android-side daemon.

## Install or upgrade the guest launcher

From Termux with the updated module client and daemon running:

```sh
apatch-chroot push ubuntu "$(command -v setup-desktop.sh)" /tmp/setup-desktop.sh
apatch-chroot push ubuntu "$(command -v apatch-startlxqt.py)" /tmp/apatch-startlxqt.py
apatch-chroot exec ubuntu --root -- /bin/sh /tmp/setup-desktop.sh --launcher-only
```

If LXQt isn't installed yet, omit `--launcher-only` to install LXQt Core,
Openbox, QTerminal, and the supporting tools. For a source checkout, the two
files are in `scripts/`. This does not replace Ubuntu's `startlxqt` or change
the existing `systemctl` shim. The APatch profile has its own settings under
`~/.config/apatch-chroot/lxqt`.

## Start the desktop

```sh
# In Termux, if Termux:X11 isn't already running:
termux-x11 :0 &

# If your chosen guest user already exists:
apatch-chroot setuser ubuntu YOUR_GUEST_USER
apatch-chroot desktop ubuntu --check
apatch-chroot desktop ubuntu
```

If you only have a root account, pass `--root` explicitly, including for
`--check`. An existing X11 window manager on `:0` should be closed normally
before launching another desktop. Individual GUI apps can share an existing
X11 session without starting another desktop (see [Display](display.md)).

### Optional sound

Add `--shared-audio` to both the check and launch. If a Termux PulseAudio server
is already running, keep using it. APatch looks for its native Unix socket in
Termux's standard runtime directories. To start a **new** server at a known
path, in Termux:

```sh
PULSE_RUNTIME_PATH="$PREFIX/var/run/pulse" pulseaudio --start
```

PulseAudio still enforces its normal authentication. If it denies the guest,
configure the guest desktop user's PulseAudio cookie through a trusted local
channel. The launcher doesn't copy cookies or loosen permissions. The desktop
starts without sound if the endpoint is missing or inaccessible. It never
starts another sound server or routes to raw Android audio devices.

## What is isolated

- The desktop sees a minimal device tree and private `/tmp` and `/dev/shm`.
  The only host service sockets deliberately shared are Termux:X11 `:0` and,
  with `--shared-audio`, PulseAudio.
- The launcher starts real D-Bus session and system buses in a mode-`0700`
  runtime directory. The system bus does not activate host or guest hardware
  services. LXQt uses the session bus for desktop IPC; private D-Bus alone does
  not provide logind, an authentication session, host power controls, disk
  mounting or printer discovery. The lean profile skips those optional
  autostarts along with XScreenSaver and Qlipper. The lock screen is not a
  supported security boundary; lock the phone itself.
- A shared X server is **not** an input or window security boundary. Only run
  clients you trust on the same display. APatch desktop mode does not provide
  PID, network or user namespace isolation.

## Memory on Android

By default, desktop launch needs at least 768 MiB of Android `MemAvailable`.
`--min-free-mib N` changes the threshold. `--check` tests it along with display
access, `/dev/shm`, and D-Bus. This is **not a hard cap** and it samples only at
startup. Android's [low-memory killer](https://source.android.com/docs/core/perf/lmkd)
can still act later. Browsers and other heavy apps often use more RAM than
LXQt itself. A `/dev/shm` size ceiling is not an upfront RAM reservation and
does not supply System V IPC on kernels that lack it.
