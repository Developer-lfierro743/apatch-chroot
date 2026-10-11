# Optional: x86_64 via box64

box64 lets an arm64 device run x86_64 binaries by JIT-translating them to arm64
on the fly. It's **opt-in**, not default, because box64's JIT is still unstable
on heavily multithreaded x86_64 code.

## How it works

The kernel routes `exec()` through **binfmt_misc** — a magic-byte handler. box64
is registered to claim **only** x86_64 ELFs (matched by the `\x7fELF...x86-64`
magic). arm64 ELFs — Android and everything native — match a *different* magic
and run untouched, so box64 is invisible to them.

apatch-chroot mounts binfmt_misc and registers box64 **inside the container's
private mount namespace**, so:
- The registration exists only for that session.
- Android's PID 1 namespace never sees it.
- It vanishes when the container stops.

## Prerequisites

box64 must be installed **inside the container**, e.g. at
`/usr/local/bin/box64` (or `/usr/bin/box64`, `/opt/box64/box64`). Verify:

```sh
apatch-chroot exec ubuntu -- /usr/local/bin/box64 --version
# Box64 arm64 v0.4.x ... with Dynarec
```

(Use the absolute path — a bare `box64` name can hit a stripped-Python PATH
lookup in minimal images.)

## Usage

```sh
apatch-chroot run ubuntu --box64 -- ./x86_64-app
apatch-chroot exec ubuntu --box64 -- /path/to/x86_64-tool
apatch-chroot login ubuntu --box64
```

**You'll know Dynarec engaged** when box64 prints its banner:
```
box64 with Dynarec ... running: <app>
```
before the program runs. Without `--box64`, an x86_64 binary fails with
`Exec format error`.

## What works

| Category | Verdict |
|---|---|
| CLI tools, single/lightly-threaded apps | ✅ great |
| Many games | ✅ box64's main use case |
| SDKs/toolchains, media tools | ✅ usually |
| Heavily multithreaded x86_64 apps | ⚠️ may segfault (JIT limitation) |
| Java/JVM | ❌ not an ELF box64 can translate |

## If it doesn't engage

- Run `apatch-chroot info` — check `binfmt_misc: yes`. If `no`, your kernel
  lacks `CONFIG_BINFMT_MISC` and box64 can't work at all.
- Confirm box64 is at a path `find_box64()` checks (see above).
- A segfault on a multithreaded app is box64's JIT, not apatch-chroot — the
  registration and isolation are sound.
