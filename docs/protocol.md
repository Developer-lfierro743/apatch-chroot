# Wire protocol

How the client, broker, and core talk to each other. Newline-delimited JSON
requests over a Unix socket, with framed payloads so raw PTY bytes can't be
confused with control messages.

## Frames

Every message on the socket starts with a 1-byte type:

| Type | Format | Meaning |
|---|---|---|
| `0x01` | 4-byte big-endian length + bytes | **data** — raw PTY output |
| `0x02` | JSON + `\n` | **control** — requests, responses, progress |

```
0x01 <len:4> <bytes...>        →  PTY data (streamed)
0x02 {"cmd":"login",...}\n     →  control message
```

## Requests (client → broker → core)

A request is a control frame:

```json
{"cmd": "login", "args": {"name": "ubuntu", "use_init": false}}
```

`args` is command-specific. Common fields: `name`, `command`, `use_init`,
`use_box64`, `shared_display`, `user`, `as_root`.

## Responses (core → broker → client)

Two shapes.

**Simple** (most commands) — one control frame:

```json
{"ok": true, "data": {"pid": 2827}}
```
or
```json
{"ok": false, "error": "container 'ubuntu' not installed"}
```

**Streaming** (`run`, `login`, `install`, `convert`) — a mix of frames:

1. Control frames carrying progress:
   ```json
   {"phase": "layer", "detail": "1/3 abc123 (20480 bytes)"}
   ```
2. `0x01` data frames with raw PTY output (for `run`/`login`).
3. A final control frame with the result or exit code:
   ```json
   {"ok": true, "data": {"exit": 0}}
   ```

The client's `_stream_controls()` reads frames until it hits a non-`phase`
control frame, treating that as the terminal result.

## Commands

| `cmd` | args | Response |
|---|---|---|
| `ping` | — | `{uid, pid}` |
| `info` | — | host capability probes |
| `install` | `{image, name?}` | streaming progress → `{installed}` |
| `convert` | `{name, as_name?, move?}` | streaming → `{converted, moved, image}` |
| `run` | `{name, command, ...}` | streaming PTY + `{exit}` |
| `exec` | `{name, command, ...}` | `{exit, stdout, stderr}` |
| `login` | (client maps to `run` with `bash -l`) | as `run` |
| `list` | — | `{containers: [...]}` |
| `remove` | `{name}` | `{removed}` |
| `kill` | `{name}` | `{killed}` |
| `clone` | `{src, dst}` | `{cloned, entries}` |
| `push` | `{name, src, dest}` | `{copied}` |
| `pull` | `{name, src, dest}` | `{copied}` |
| `service` | `{name, verb, unit?}` | `{exit, stdout, stderr}` |
| `setuser` | `{name, user}` | `{name, user}` |
| `shutdown` | — | core exits |

## The broker's role

The broker holds no protocol logic. It accepts a CLI connection, pairs it with
one of the core's dialed relay connections, and **pipes raw bytes both ways**.
All framing and dispatch live in the client and core. The broker exists purely to
cross the SELinux socket wall (see [architecture.md](architecture.md)).

## Sockets

| Socket | Owner | Who connects |
|---|---|---|
| `.../tmp/apatch-chroot-broker.sock` | Termux uid (`app_data_file`) | the CLI |
| `.../tmp/apatch-chroot-core.sock` | Termux uid (`app_data_file`) | the core dials it |

Both live in Termux's storage because a socket under `/dev` gets a `device`
SELinux context the app can't connect to, and a root-owned socket is forbidden
outright. `app_data_file` in Termux's tree is the context the CLI *can* reach and
root *can* also reach.
