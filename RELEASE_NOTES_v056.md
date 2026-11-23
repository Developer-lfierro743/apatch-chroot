# APatch Chroot v0.5.6 — LXQt desktop

Requires a rooted Android device with APatch and Termux. The LXQt feature is opt-in.

- **`apatch-chroot desktop`** reuses Termux:X11 and optionally an existing Termux PulseAudio server. LXQt runs with private session and system D-Bus instances, a private `/dev/shm`, and no raw Android audio devices. The existing `systemctl` shim is unchanged.
- **RAM preflight** refuses startup below 768 MiB of Android `MemAvailable` by default. `--check` checks prerequisites without opening the desktop. This is not a hard RAM cap and does not change Android’s low-memory killer.
- **Display and sound flags** now keep their guest environment on direct `run` and `exec` commands.
- **Documentation**: the [docs homepage](https://developer-lfierro743.github.io/apatch-chroot/docs/) and [LXQt guide](https://developer-lfierro743.github.io/apatch-chroot/docs/desktop.html) are live.

In APatch, install the attached `apatch-chroot.zip` as a module and reboot. Then follow the LXQt guide to install or upgrade the guest launcher before using `apatch-chroot desktop ubuntu --check`. Use a non-root guest account when possible.

**Verification:** The LXQt panel and Openbox were smoke-tested on a separate authenticated Xvfb display. Installing this module and using live Termux:X11 or PulseAudio passthrough on Android have not been verified for this release.
