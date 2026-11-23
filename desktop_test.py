"""Safe tests for the LXQt profile; no real mount, APatch or Android writes."""

from contextlib import ExitStack, closing
import importlib.util
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / "daemon"), str(ROOT / "client")]
core = importlib.import_module("daemon")
commands = importlib.import_module("daemon_cmds")
mounts = importlib.import_module("mounts")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


client = load("desktop_client", "client/apatch-chroot.py")
launcher = load("lxqt_launcher", "scripts/apatch-startlxqt.py")


class DesktopCommandTests(unittest.TestCase):
    def test_below_threshold_does_not_open_pty_or_launch(self):
        with patch.object(commands.os.path, "isdir", return_value=True), \
             patch.object(commands, "_mem_available_kib", return_value=767 * 1024), \
             patch.object(commands.pty, "openpty") as openpty:
            result = core.handle_request({"cmd": "desktop", "args": {"name": "ubuntu", "as_root": True}}, None)
        self.assertFalse(result["ok"])
        self.assertIn("768 MiB", result["error"])
        self.assertIn("767 MiB", result["error"])
        openpty.assert_not_called()

    def test_missing_memavailable_fails_closed(self):
        with patch.object(commands.os.path, "isdir", return_value=True), \
             patch.object(commands, "_mem_available_kib", return_value=None), \
             patch.object(commands, "run_container") as run:
            result = commands.desktop_container({"name": "ubuntu", "as_root": True}, None)
        self.assertFalse(result["ok"])
        self.assertIn("MemAvailable", result["error"])
        run.assert_not_called()

    def test_desktop_wire_request_uses_fixed_command_and_guest_user(self):
        with patch.object(commands.os.path, "isdir", return_value=True), \
             patch.object(commands, "read_default_user", return_value="guest"), \
             patch.object(commands, "_mem_available_kib", return_value=768 * 1024), \
             patch.object(commands, "run_container") as run:
            core.handle_request({"cmd": "desktop", "args": {"name": "ubuntu", "check": True,
                                "shared_audio": True, "command": ["evil"], "hardened": False}}, None)
        params, _conn = run.call_args.args
        self.assertEqual(params["command"], ["/usr/local/bin/apatch-startlxqt", "--check"])
        self.assertEqual(params["user"], "guest")
        self.assertTrue(params["shared_audio"])
        self.assertTrue(run.call_args.kwargs["desktop"])
        self.assertNotIn("hardened", params)

    def test_desktop_cannot_silently_run_as_root(self):
        with patch.object(commands.os.path, "isdir", return_value=True), \
             patch.object(commands, "read_default_user", return_value="root"), \
             patch.object(commands, "run_container") as run:
            result = commands.desktop_container({"name": "ubuntu"}, None)
        self.assertFalse(result["ok"])
        self.assertIn("--root", result["error"])
        run.assert_not_called()

    def test_memavailable_uses_available_not_free_or_total(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "meminfo"
            path.write_text("MemTotal: 4096000 kB\nMemFree: 12345 kB\nMemAvailable: 827392 kB\n")
            self.assertEqual(commands._mem_available_kib(path), 827392)
            path.write_text("MemAvailable: broken kB\nMemFree: 2000 kB\n")
            self.assertIsNone(commands._mem_available_kib(path))

    def test_pty_environment_delivered_to_absolute_command(self):
        for display, audio in ((True, True), (False, False)):
            with self.subTest(display=display, audio=audio), \
                 patch.object(commands.os, "execve") as execve, \
                 patch.dict(os.environ, {"PULSE_SERVER": "tcp:127.0.0.1:4713", "DISPLAY": ":99"}):
                commands._exec_guest(["/usr/bin/test"], commands._guest_env(display=display, audio=audio))
                path, argv, env = execve.call_args.args
                self.assertEqual(path, "/usr/bin/test")
                self.assertEqual(argv, [path])
                self.assertEqual(env.get("DISPLAY"), ":0" if display else None)
                self.assertEqual(env.get("PULSE_SERVER"), "unix:/var/run/pulse/native" if audio else None)

    def test_desktop_hardening_explicitly_removes_mount_privilege(self):
        syscalls = importlib.import_module("syscalls")
        with patch.object(syscalls, "drop_cap_from_bounding") as drop, patch.object(commands.os, "write"):
            commands._apply_hardening({"keep_sysadmin": True}, required=True)
        drop.assert_any_call(syscalls.CAP_SYS_ADMIN)

    def test_missing_namespace_fails_the_pty_check_without_legacy_mounts(self):
        parent, peer = socket.socketpair()
        with parent, peer, patch.object(commands.os.path, "isdir", return_value=True), \
             patch.object(commands, "_mem_available_kib", return_value=4096 * 1024), \
             patch.object(mounts, "enter_private_mount_ns", side_effect=OSError("no private mounts")), \
             patch.object(mounts, "setup_mounts", side_effect=AssertionError("legacy mounts were used")):
            # Actual PTY, fork and protocol; the mount seam is forced to fail.
            peer.settimeout(2)
            self.assertIsNone(commands.desktop_container({"name": "ubuntu", "as_root": True,
                                                           "check": True}, parent))
            pid_frame = client.read_control_line(peer)
            assert pid_frame is not None
            self.assertTrue(pid_frame["ok"])
            exit_frame = client.read_control_line(peer)
            assert exit_frame is not None
            self.assertEqual(exit_frame["data"]["exit"], 127)

    def test_noninteractive_exec_keeps_display_and_audio_env(self):
        with patch.object(commands.os.path, "isdir", return_value=True), \
             patch.object(commands.os, "chroot"), patch.object(commands, "ensure_resolv_conf"), \
             patch.object(mounts, "enter_private_mount_ns"), patch.object(mounts, "setup_mounts"):
            result = commands.exec_container({"name": "ubuntu", "as_root": True, "hardened": False,
                                              "command": ["/usr/bin/env"], "shared_display": True,
                                              "shared_audio": True}, None)
        self.assertEqual(result["data"]["exit"], 0)
        env = dict(line.split("=", 1) for line in result["data"]["stdout"].splitlines())
        self.assertEqual(env["DISPLAY"], ":0")
        self.assertEqual(env["PULSE_SERVER"], "unix:/var/run/pulse/native")

    def test_client_sends_desktop_payload_with_checked_flags(self):
        with patch.object(client, "_run_pty_request", return_value=19) as run:
            rc = client.main(["apatch-chroot", "desktop", "ubuntu", "--check", "--shared-audio",
                              "--user", "guest", "--min-free-mib", "900"])
        self.assertEqual(rc, 19)
        self.assertEqual(run.call_args.args[0], {"cmd": "desktop", "args": {
            "name": "ubuntu", "check": True, "shared_audio": True, "as_root": False,
            "user": "guest", "min_free_mib": 900}})


class DesktopMountTests(unittest.TestCase):
    def test_fails_before_any_mount_without_private_namespace(self):
        with patch.object(mounts, "_root_is_private", return_value=False), \
             patch.object(mounts, "mount") as mount:
            with self.assertRaisesRegex(OSError, "private"):
                mounts.setup_desktop_mounts("/not/a/container")
        mount.assert_not_called()

    def test_target_walker_rejects_parent_symlink_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            rootfs = Path(tmp) / "guest"
            rootfs.mkdir()
            outside = Path(tmp) / "outside"
            outside.mkdir()
            (rootfs / "tmp").symlink_to(outside)
            with self.assertRaises(OSError):
                with mounts._desktop_target(str(rootfs), ("tmp", "X0"), directory=False):
                    self.fail("accepted a symlinked mount target")
            self.assertEqual(list(outside.iterdir()), [])

    def test_bind_requires_a_real_unix_socket_and_creates_file_target(self):
        with tempfile.TemporaryDirectory() as tmp, closing(socket.socket(socket.AF_UNIX)) as sock:
            # Unix sockaddr paths are limited; use short names under the tool scratch dir.
            rootfs = Path(tmp) / "g"
            rootfs.mkdir()
            src = Path(tmp) / "s"
            src.touch()
            mount = Mock()
            with patch.object(mounts, "mount", mount):
                with self.assertRaises(OSError):
                    mounts._desktop_bind(str(rootfs), str(src), ("tmp", "X0"))
                mount.assert_not_called()
                src.unlink()
                sock.bind(str(src))
                mounts._desktop_bind(str(rootfs), str(src), ("tmp", "X0"))
            self.assertTrue((rootfs / "tmp/X0").is_file())
            self.assertEqual(mount.call_args.args[3], mounts.MS_BIND)


class DesktopLauncherTests(unittest.TestCase):
    def test_active_window_manager_refuses_duplicate(self):
        active = "_NET_SUPPORTING_WM_CHECK(WINDOW): window id # 0xabc\n"
        with patch.object(launcher, "run_probe", side_effect=[Mock(returncode=0),
                          Mock(returncode=0, stdout=active), Mock(returncode=0, stdout=active)]):
            with self.assertRaisesRegex(RuntimeError, "already has a window manager"):
                launcher.require_display({"DISPLAY": ":0"})

    def test_mask_files_are_scoped_and_dont_overwrite_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            normal = home / ".config/autostart"
            normal.mkdir(parents=True)
            normal_entry = normal / "lxqt-policykit-agent.desktop"
            normal_entry.write_text("user config\n")
            profile = launcher.prepare_profile(home)
            hidden = profile / "autostart/lxqt-policykit-agent.desktop"
            self.assertIn("Hidden=true", hidden.read_text())
            hidden.write_text("my profile override\n")
            launcher.prepare_profile(home)
            self.assertEqual(hidden.read_text(), "my profile override\n")
            self.assertEqual(normal_entry.read_text(), "user config\n")

    @unittest.skipUnless(shutil.which("dbus-daemon") and shutil.which("dbus-send"), "needs D-Bus tools")
    def test_private_buses_answer_and_disappear_on_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            with ExitStack() as stack:
                for kind in ("session", "system"):
                    addr = stack.enter_context(launcher.private_bus(runtime, kind, os.environ.copy()))
                    launcher.check_bus(addr, os.environ.copy())
                    self.assertTrue((runtime / f"{kind}-bus").is_socket())
                self.assertNotIn("standard_system_servicedirs", (runtime / "system.conf").read_text())
            self.assertFalse((runtime / "session-bus").exists())
            self.assertFalse((runtime / "system-bus").exists())


@unittest.skipUnless(os.getenv("APATCH_TEST_XVFB") == "1", "opt-in authenticated Xvfb smoke test")
class DesktopSmoke(unittest.TestCase):
    def test_panel_window_manager_and_clean_exit(self):
        for binary in ("Xvfb", "xauth", "xwininfo", "openbox", "startlxqt", "lxqt-panel"):
            self.assertIsNotNone(shutil.which(binary), f"missing {binary}")
        with tempfile.TemporaryDirectory(prefix="lxqt-test-") as tmp:
            work = Path(tmp)
            xauth = work / "xauth"
            xauth.touch(mode=0o600)
            display = f":{100 + secrets.randbelow(900)}"
            subprocess.run(["xauth", "-f", str(xauth)], input=f"add {display} . {secrets.token_hex(16)}\n",
                           text=True, check=True, capture_output=True, timeout=5)
            env = {"PATH": "/usr/bin:/bin", "HOME": str(work / "home"), "LANG": "C.UTF-8",
                   "TMPDIR": tmp, "DISPLAY": display, "XAUTHORITY": str(xauth)}
            (work / "home").mkdir(mode=0o700)
            with (work / "xvfb.log").open("w+") as xlog, (work / "desktop.log").open("w+") as log, \
                 ExitStack() as stack:
                # GLX startup stalls on some Android-hosted Xvfb builds; the
                # smoke test exercises the desktop, not host GPU or SysV IPC.
                x = subprocess.Popen(["Xvfb", display, "-nolisten", "tcp", "-screen", "0", "640x480x24",
                                      "-auth", str(xauth), "-noreset", "-extension", "GLX",
                                      "-extension", "MIT-SHM"], env=env, stdout=xlog,
                                     stderr=subprocess.STDOUT, start_new_session=True)
                stack.callback(launcher.stop_child, x)
                until = time.monotonic() + 6
                while True:
                    try:
                        if launcher.run_probe(["xdpyinfo"], env).returncode == 0:
                            break
                    except subprocess.TimeoutExpired:
                        pass
                    if x.poll() is not None or time.monotonic() > until:
                        xlog.seek(0)
                        self.fail(f"isolated Xvfb did not become reachable:\n{xlog.read()}")
                    time.sleep(0.1)
                check = subprocess.run([sys.executable, str(ROOT / "scripts/apatch-startlxqt.py"), "--check"],
                                       env=env, text=True, capture_output=True, timeout=12)
                self.assertEqual(check.returncode, 0, check.stderr)
                self.assertIn("both guest D-Bus", check.stdout)
                desktop = subprocess.Popen([sys.executable, str(ROOT / "scripts/apatch-startlxqt.py")],
                                           env=env, stdout=log, stderr=subprocess.STDOUT)
                stack.callback(launcher.stop_child, desktop)
                until = time.monotonic() + 15
                while True:
                    tree = launcher.run_probe(["xwininfo", "-root", "-tree"], env).stdout.lower()
                    wm = launcher.run_probe(["xprop", "-root", "_NET_SUPPORTING_WM_CHECK"], env).stdout
                    if ("lxqt-panel" in tree or "lxqt panel" in tree) and "window id # 0x" in wm:
                        print(f"Verified LXQt panel and window manager on isolated {display}")
                        break
                    if desktop.poll() is not None or time.monotonic() > until:
                        log.seek(0)
                        self.fail(f"LXQt did not draw a panel with a window manager:\n{log.read()}\n{tree}")
                    time.sleep(0.2)
                # Only signal the test process we created. It owns its session group.
                desktop.terminate()
                self.assertEqual(desktop.wait(timeout=9), 143)
                self.assertFalse(list(work.glob("aplx-*")), "private D-Bus/runtime directory leaked")


if __name__ == "__main__":
    unittest.main()
