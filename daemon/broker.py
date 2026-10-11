#!/data/data/com.termux/files/usr/bin/python3
"""apatch-chroot broker — Termux-uid relay between the CLI and the root core.

WHY THIS EXISTS (SELinux):
The root core daemon's socket is denied to the untrusted_app domain (real
Termux):

    avc: denied { write } scontext=untrusted_app_27 tcontext=shell_data_file
         tclass=sock_file

So the CLI cannot connect to the core directly. Fix (the anland-termux
pattern for the same problem): a broker running AS THE TERMUX UID owns a
socket in Termux app storage (app_data_file — writable by the app) and relays
to the core.

Connection directions (only these two are SELinux-allowed):
    CLI  -> broker : app uid  -> app_data_file   ALLOWED
    core -> broker : root     -> app_data_file   ALLOWED
    CLI  -> core   : app uid  -> root socket     DENIED  (the wall)

So the broker LISTENS; both the CLI and the core connect IN to it. Protocol:
  - The core connects and immediately sends b"CORE\n", then holds the
    connection open as a ready relay.
  - A CLI connects and sends its JSON request. The broker pairs it with a
    ready core connection and pipes bytes both ways until the exchange ends.

The broker holds no privilege — pure byte relay. All real work stays in root.
"""

import os
import socket
import threading

BROKER_SOCKET = "/data/data/com.termux/files/usr/tmp/apatch-chroot-broker.sock"
# Separate socket the core dials to register as a ready relay. Two sockets means
# no byte-peeking (which would corrupt a CLI JSON request).
CORE_SOCKET = "/data/data/com.termux/files/usr/tmp/apatch-chroot-core.sock"

_core_queue = []
_cv = threading.Condition()


def _pipe(a, b):
    try:
        while True:
            data = a.recv(65536)
            if not data:
                break
            b.sendall(data)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                s.close()
            except OSError:
                pass


def _take_core():
    with _cv:
        if not _core_queue:
            _cv.wait(timeout=10.0)
        return _core_queue.pop() if _core_queue else None


def _handle_cli(conn):
    """CLI connection: pair with a ready core relay and pipe both ways."""
    core = _take_core()
    if core is None:
        try:
            conn.sendall(b'{"ok": false, "error": "core not connected (is the daemon running?)"}\n')
        except OSError:
            pass
        conn.close()
        return
    t = threading.Thread(target=_pipe, args=(conn, core), daemon=True)
    t.start()
    _pipe(core, conn)


def _listen(path):
    """Bind a unix socket at path and return it, ready to accept."""
    try:
        os.unlink(path)
    except OSError:
        pass
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(path)
    os.chmod(path, 0o666)
    s.listen(16)
    return s


def main():
    os.makedirs(os.path.dirname(BROKER_SOCKET), exist_ok=True)

    # Two sockets: the CLI dials BROKER_SOCKET, the core dials CORE_SOCKET.
    # No byte-peeking, so a CLI JSON request is never corrupted.
    cli_srv = _listen(BROKER_SOCKET)
    core_srv = _listen(CORE_SOCKET)
    print(
        f"broker listening: cli={BROKER_SOCKET} core={CORE_SOCKET} (uid={os.getuid()})",
        flush=True,
    )

    def accept_core():
        while True:
            conn, _ = core_srv.accept()
            with _cv:
                _core_queue.append(conn)
                _cv.notify()

    def accept_cli():
        while True:
            conn, _ = cli_srv.accept()
            threading.Thread(target=_handle_cli, args=(conn,), daemon=True).start()

    threading.Thread(target=accept_core, daemon=True).start()
    accept_cli()


if __name__ == "__main__":
    main()

