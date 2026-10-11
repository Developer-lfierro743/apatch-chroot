# Contributing

Thanks for your interest in apatch-chroot. This is a small, focused project — a
chroot container runtime for rooted Android — and contributions are welcome.

## Read this first

- **[docs/architecture.md](docs/architecture.md)** — how the three processes fit
  together and why. Don't start coding before you understand the broker.
- **[docs/safety.md](docs/safety.md)** — the non-negotiable safety model. **Any
  change to mount handling must preserve host isolation.** If a PR risks
  propagating a mount to the host, it won't be merged, full stop.
- **[SECURITY.md](SECURITY.md)** — the trust model and how to report issues.

## Ground rules

1. **Host safety is sacred.** Never unbind or modify Android's own mounts
   (`/apex`, `/system`, `/dev/snd`). Only add mounts inside the private
   namespace. `_root_is_private()` must stay a hard failure, not a warning.
2. **The daemon stays root, but the login doesn't have to be.** New session
   paths should respect the `--user`/`--root` least-privilege model.
3. **No secrets in the repo.** No IPs, pairing codes, keys, tokens.
4. **Match the existing style.** The code is deliberately small and commented
   with *why*, not *what*. Keep that.

## Setting up

```sh
git clone https://github.com/Developer-lfierro743/apatch-chroot.git
cd apatch-chroot
./build.sh          # builds apatch-chroot.zip
```

Python 3.10+ for the daemon and client. The container init and systemctl shim
are POSIX sh (no bashisms — they run in minimal containers).

## Testing

- Unit-level checks can run on a desktop (no device needed): the flag parser,
  the help renderer, the init supervisor logic, the systemctl shim.
- Mount/namespace and `--shared-display` behavior **must be tested on a real
  rooted device** — desktop Linux won't reproduce Android's seccomp/SELinux.
- If you change mount handling, verify host audio survives:
  `ls /dev/snd` before and after a session.

## Making a change

1. Fork and branch (`feat/…` or `fix/…`).
2. Keep the commit message format: a short imperative subject, then a body
   explaining *why* (see `git log` for examples).
3. Update docs — if you add a command or flag, update `client/pages.py` (help),
   `README.md`, and the relevant `docs/*.md` page.
4. Open a PR describing what and why, and whether it was tested on-device.

## What's most wanted

- Live on-device verification of the newer paths (`--init`, `systemctl` shim,
  `convert --move`, `--shared-display`, `--box64`) — reports of what works and
  what breaks on different devices/kernels are gold.
- Compatibility with other root solutions (KernelSU path tweaks).
- More services in the systemctl shim fallback map.

## Code of conduct

Be decent. This is a hobby project built in the open; keep it welcoming.
