## APatch Chroot v0.4.0.2

A subpatch fixing update detection + adding a reliable self-update path.

### Fixes
- **Update detection actually works for subpatches** — v0.4.0 couldn't see v0.4.0.1 (a 3-slot parser dropped the 4th number). Now `check-update` correctly reports `v0.4.0 → v0.4.0.2` as a **subpatch update**.

### New
- **`self-update`** — downloads the latest release zip straight to `/sdcard/Download/apatch-chroot.zip` and prints the flash steps. Works even when APatch's own update polling misses a release (which is what bit us). It does **not** flash or reboot — you do that, since only you should reboot the device.

```sh
apatch-chroot self-update
# → downloads the newest zip, then:
#   APatch → Modules → Install from storage → reboot
```

### Note
If APatch's Modules list isn't showing updates, `self-update` is the reliable path — it talks to GitHub directly instead of waiting for APatch to poll `update.json`.
