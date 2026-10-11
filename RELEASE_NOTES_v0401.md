## APatch Chroot v0.4.0.1

A subpatch hotfix on top of v0.4.0.

### What's in it
- **4th version slot (subpatch)** — versioning is now `major.minor.patch.subpatch`; `check-update` labels a bump MAJOR/minor/patch/subpatch.
- **First-run update banner** — after flashing and rebooting, the first `apatch-chroot ping` shows `↑ updated from v0.4.0 — welcome to v0.4.0.1!`, then stays quiet.
- **`fix-login.sh`** — silences the noisy `groups/mountpoint/lesspipe/dircolors/mount: command not found` + `/.cargo/env` errors on login. Run once inside a container: `bash fix-login.sh`.

### Update
APatch should show a **subpatch update** (versionCode 6 > 5). Tap it, reboot, and the banner confirms the new version landed.

```sh
apatch-chroot ping          # shows the update banner
apatch-chroot check-update  # confirms up to date
```

Everything else is identical to v0.4.0 (audio, GUI, stats, LXQt desktop, auto-updates). See the [v0.4.0 release notes](https://github.com/Developer-lfierro743/apatch-chroot/releases/tag/v0.4.0).
