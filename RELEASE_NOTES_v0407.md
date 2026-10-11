## APatch Chroot v0.4.0.7 (subpatch) — login actually fixed

### Why the last two didn't work
The wrapper-script approach failed because ANY final `bash -i` re-sources `~/.bashrc`, which re-poisons PATH. We kept fighting bash and losing.

### The real fix
The daemon now execs bash **directly** with `--norc --noprofile` and a clean env:
```
execve('/bin/bash', ['/bin/bash','--norc','--noprofile','-i'], clean_env)
```
`--norc/--noprofile` mean bash **physically does not source** `~/.bashrc` or `/etc/profile` (the poison source), and the clean env forces PATH to the container's own `/usr/bin,/bin`. No script for bash to fight.

### Get it — and REBOOT
```
https://github.com/Developer-lfierro743/apatch-chroot/releases/download/v0.4.0.7/apatch-chroot.zip
```
Flash → **reboot** (daemon must reload) → `apatch-chroot login ubuntu` → `ls`, `apt`, `bash` work, no spam.
