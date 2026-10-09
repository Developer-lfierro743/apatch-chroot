import pty, os, sys, select, time

CLIENT = "/data/local/tmp/apatch-chroot/client/apatch-chroot.py"
PY = "/data/data/com.termux/files/usr/bin/python3"

pid, fd = pty.fork()
if pid == 0:
    os.execv(PY, [PY, CLIENT, "run", "ubuntu", "--", "/bin/bash"])
else:
    time.sleep(1.5)
    for cmd in [b"echo HELLO-FROM-CHROOT\n", b"id\n", b"exit\n"]:
        try:
            os.write(fd, cmd)
        except OSError:
            break
        time.sleep(1.2)
    out = b""
    try:
        while True:
            r, _, _ = select.select([fd], [], [], 2)
            if not r:
                break
            d = os.read(fd, 4096)
            if not d:
                break
            out += d
    except OSError:
        pass
    sys.stdout.write(out.decode(errors="replace"))
