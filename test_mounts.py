import pty, os, sys, select, time

PY = "/data/data/com.termux/files/usr/bin/python3"
CL = "/data/local/tmp/apatch-chroot/client/apatch-chroot.py"

pid, fd = pty.fork()
if pid == 0:
    os.execv(PY, [PY, CL, "run", "alpine", "--", "/bin/busybox", "sh"])
else:
    time.sleep(2)
    cmds = (
        b"echo P1; /bin/busybox ls /proc/self >/dev/null && echo PROC-OK; "
        b"echo P2; /bin/busybox ls /dev/pts && echo PTS-OK; "
        b"echo P3; /bin/busybox cat /etc/alpine-release; "
        b"echo P4; /bin/busybox ls /dev/snd 2>&1 | head -1; "
        b"echo DONE\n"
    )
    os.write(fd, cmds)
    time.sleep(4)
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
