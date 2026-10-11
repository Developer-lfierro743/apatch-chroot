## APatch Chroot v0.4.0.3 (subpatch)

Fixes update completeness + moves updateJson to a dedicated branch.

### The fix that matters
A module flash did **not** fully update before:
- `service.sh` copied a hardcoded list of 7 daemon files, so newer daemon files (`binfmt.py`, `init.sh`, `systemctl-shim.sh`) were **never deployed** on update — the daemon ran stale/missing code. Now it copies everything in the daemon dir.
- Helper scripts (`fix-login.sh`, `setup-desktop.sh`) couldn't be run after an update (module dir is unreadable by the app). Now deployed to Termux bin at flash **and** re-synced each boot.

So after this flash, updates are actually complete — every file lands.

### Also
- `updateJson` now points at a dedicated `update` branch (like revanced-magisk-module), bumped independently of `main`.
- Bundles `self-update` + the subpatch-aware `check-update` from v0.4.0.2.

```sh
apatch-chroot self-update    # future updates: one command
```
