## APatch Chroot v0.4.0.4 (subpatch)

Fixes the login shell losing `PATH`/`TERM`.

### The bug
After logging in, `ls`, `apt`, even `bash` reported **"command not found"**, and `clear` said **"TERM environment variable not set"**. Cause: `bash -l` sources `/etc/profile` + `/root/.bashrc`, which on Ubuntu can clobber or clear `PATH` and `TERM` — leaving a shell that can't find basic commands.

### The fix
A login wrapper (`daemon/login-wrapper.sh`) now runs for login shells. It sources the normal profile files (so aliases/prompt still load) but **re-asserts `PATH`/`TERM`/`HOME` afterward**, so the environment always wins no matter what the profile does.

### Get it
```
https://github.com/Developer-lfierro743/apatch-chroot/releases/download/v0.4.0.4/apatch-chroot.zip
```
Flash → reboot. After this, `apatch-chroot login` gives you a shell where `ls`, `apt`, etc. all work.
