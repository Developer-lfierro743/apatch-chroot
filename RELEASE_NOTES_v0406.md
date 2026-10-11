## APatch Chroot v0.4.0.6 (subpatch)

**This is the one that actually fixes login.**

### The real bug (finally found)
The v0.4.0.5 wrapper set a clean PATH, then ran `exec bash -i` — but interactive bash **re-sources `~/.bashrc`**, which on converted containers runs `export PATH=/system/bin:/apex/...` **again**, re-poisoning the clean PATH. So login stayed broken even with the wrapper deployed.

### The fix
The final shell is now launched with `--norc` (skips `.bashrc` re-sourcing) and a forced `env PATH=...` (clean container PATH wins no matter what). Proven by simulation:
- old `bash -i` + poison `.bashrc` → `PATH=/system/bin:...` (broken)
- new `--norc` + `env PATH=` → `PATH=/usr/bin:...` (clean)

### Get it
```
https://github.com/Developer-lfierro743/apatch-chroot/releases/download/v0.4.0.6/apatch-chroot.zip
```
Flash → **reboot** (so the daemon reloads the new wrapper) → `apatch-chroot login ubuntu` → `ls`, `apt`, `bash` work, no spam, TERM set.
