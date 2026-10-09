import pty, os, sys, select, time

PY = "/data/data/com.termux/files/usr/bin/python3"
CL = "/data/local/tmp/apatch-chroot/client/apatch-chroot.py"

pid, fd = pty.fork()
if pid == 0:
    os.execv(PY, [PY, CL, "run", "alpine", "--", "/bin/busybox", "sh"])
else:
    time.sleep(2)
    os.write(fd, b"cat /etc/alpine-release; echo ---; /bin/busybox id; echo ---; /bin/busybox uname -m\n")
    time.sleep(3)
    out = b""
    while True:
        r, _, _ = select.select([fd], [], [], 2)
        if not r:
            break
        try:
            d = os.read(fd, 4096)
        except OSError:
            break
        if not d:
            break
        out += d
    try:
        os.write(fd, b"exit\n")
    except OSError:
        pass
    sys.stdout.write(out.decode(errors="replace"))
