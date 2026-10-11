## APatch Chroot v0.4.0.5 (subpatch)

Fixes the login shell's broken PATH + profile noise.

### The bug (diagnosed on-device)
After `apatch-chroot login`, the container's PATH was:
```
/system/bin:/apex/...:/data/data/com.termux/...   ← Android HOST paths, no /usr/bin
```
So `ls`, `apt`, even `bash` said "command not found" — though the binaries exist at `/usr/bin`. Plus `groups/mountpoint/lesspipe/dircolors/mount: command not found` + `/.cargo/env` spam on every login.

**Cause:** the converted container's profile exports a host-leaked PATH, and `bash --login` re-sources it *after* our env was set.

### The fix
`login-wrapper.sh` now sets HOME first, stubs the missing tools, sources the profile with errors suppressed, then sets a **clean container PATH that replaces** the polluted one (no host paths), and execs `bash -i` (not `--login`, so profile isn't re-sourced and PATH can't get re-polluted).

Verified: simulated the exact broken PATH → comes out clean with `/usr/bin`.

### Get it
```
https://github.com/Developer-lfierro743/apatch-chroot/releases/download/v0.4.0.5/apatch-chroot.zip
```
Flash → reboot → `apatch-chroot login ubuntu` → `ls`, `apt`, `bash` all work, no spam.
