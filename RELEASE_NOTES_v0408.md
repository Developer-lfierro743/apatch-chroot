## APatch Chroot v0.4.0.8 (subpatch)

A permanent, working `~/.bashrc` on login — nice prompt + aliases, no more `bash-5.2#`.

### What's new
Login now execs `bash --rcfile /root/.bashrc`, so your **own** `.bashrc` loads every time (colored `user@host:cwd$` prompt, `ll`/`la`/`l` aliases, history, bash-completion) — while the chroot-distro poison files (`/etc/profile.d/chroot-profile.sh`) **never** run.

Containers with no `.bashrc` get a sensible default (never overwrites an existing one). Edit `~/.bashrc` freely; it's yours.

### Get it — REBOOT
```
https://github.com/Developer-lfierro743/apatch-chroot/releases/download/v0.4.0.8/apatch-chroot.zip
```
Flash → **reboot** (daemon must reload) → `apatch-chroot login ubuntu` → clean PATH **and** a proper prompt.
