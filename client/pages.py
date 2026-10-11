"""The text of every help page, written by hand.

`apatch-chroot --help` and `apatch-chroot <cmd> --help` are intercepted before
argparse, so this file is the only description of a command or flag a user will
read. Adding a command without adding it here ships it undocumented.

HELP_PAGES is keyed by canonical command name. Each value is data for
render.render_page: usage, aliases, summary, options as (name, description)
pairs, examples as shell lines, and footer blocks (title/intro/bullets/examples).
Nothing here formats or wraps; widths are render.py's business.

TOP_COMMANDS is the front-page list, in the order a user meets them, with an
optional third element being a warning shown beside the command.
"""

PROGRAM_NAME = "apatch-chroot"
CANONICAL_PROGRAM_NAME = "APatch Chroot"

TOP_COMMANDS = (
    ("install", "Pull an image from a registry and install it as a container."),
    ("convert", "Import an existing chroot-distro container.", "must be unmounted first (kill or reboot)"),
    ("run", "Run a command in a container (non-interactive or one-shot)."),
    ("exec", "Run a command and capture its output (scripting)."),
    ("login", "Open an interactive shell inside a container."),
    ("list", "List installed containers."),
    ("clone", "Copy a container (snapshot before risky changes)."),
    ("push", "Copy a host file/dir into a container."),
    ("pull", "Copy a file/dir out of a container to the host."),
    ("service", "Manage a container's services from the host (start/stop/enable/status)."),
    ("kill", "Stop a container and tear down its mounts."),
    ("remove", "Delete a container and its rootfs.", "!deletes all data — no undo"),
    ("info", "Show daemon and host capability status."),
)

# Shown at the bottom of the front page — the #1 thing that confuses people.
FRONT_NOTES = (
    "After a reboot the daemon takes ~30-60s to start (it waits for Android to "
    "finish booting and Termux storage to unlock). Commands auto-wait up to 20s "
    "for it; if it is still not up, you will get a hint instead of an error.",
    "No root needed: the client talks to the privileged daemon over a unix socket.",
)

HELP_PAGES = {
    "convert": {
        "usage": "convert NAME [AS_NAME]",
        "summary": (
            "Import an existing chroot-distro container into apatch-chroot.\n\n"
            "NAME is the container as chroot-distro knows it; AS_NAME (optional) "
            "renames it on import. The rootfs is copied as-is and the source "
            "image reference is preserved, so 'list' still shows where it came "
            "from.\n\n"
            "The chroot-distro container must be unmounted first — run "
            "'chroot-distro kill NAME' (or reboot) — or the copy is refused, "
            "since stale mounts under the rootfs would be copied as empty "
            "directories."
        ),
        "options": [("-h, --help", "Show this help.")],
        "examples": [
            "chroot-distro kill ubuntu && apatch-chroot convert ubuntu",
            "apatch-chroot convert ubuntu my-ubuntu",
        ],
    },
    "install": {
        "usage": "install [OPTIONS] IMAGE [AS_NAME]",
        "summary": (
            "Pull an image from a registry (Docker Hub by default) and "
            "install it as a local container.\n\n"
            "IMAGE is a registry reference such as 'ubuntu', 'ubuntu:24.04', "
            "'alpine:latest', or 'docker.io/library/debian:bookworm'. Without "
            "a tag, ':latest' is assumed. AS_NAME overrides the container name "
            "(default: the image name without its tag).\n\n"
            "Layers are downloaded and extracted into "
            "/data/apatch-chroot/containers/<name>/rootfs. The pull happens in "
            "the daemon, so it runs as root outside Android's app seccomp filter."
        ),
        "options": [
            ("-h, --help", "Show this help."),
            ("--arch [ARCH]", "Target architecture (default: host). Accepts aarch64, arm, x86_64, etc."),
            ("--no-pull", "Do not contact the registry; install from the local cache only."),
        ],
        "examples": [
            "apatch-chroot install ubuntu",
            "apatch-chroot install ubuntu:24.04",
            "apatch-chroot install alpine my-alpine",
        ],
    },
    "run": {
        "usage": "run [OPTIONS] NAME [-- COMMAND [ARG...]]",
        "summary": (
            "Run a command inside an installed container.\n\n"
            "NAME is the container name. COMMAND defaults to the container's "
            "default shell. Everything after '--' is passed to the container "
            "verbatim.\n\n"
            "The command runs chrooted on a PTY, forked by the privileged "
            "daemon. Standard input and output are streamed to your terminal."
        ),
        "options": [
            ("-h, --help", "Show this help."),
            ("--cwd [DIR]", "Working directory inside the container (default: /)."),
        ],
        "examples": [
            "apatch-chroot run ubuntu -- /bin/bash",
            "apatch-chroot run ubuntu -- id",
            "apatch-chroot run alpine -- sh -c 'echo hi && uname -a'",
        ],
    },
    "login": {
        "usage": "login [OPTIONS] NAME",
        "summary": (
            "Open an interactive login shell inside a container.\n\n"
            "This is 'run NAME -- <default shell>' with a controlling terminal, "
            "so you get job control, Ctrl-C, and a real prompt. Exit the shell "
            "to return to Termux.\n\n"
            "The PTY and chroot are set up by the daemon in a privileged "
            "context, so this works where a plain chroot in the Termux app "
            "would be blocked by Android's seccomp filter."
        ),
        "options": [
            ("-h, --help", "Show this help."),
            ("--user [NAME]", "Log in as this user instead of root."),
        ],
        "examples": [
            "apatch-chroot login ubuntu",
        ],
    },
    "list": {
        "usage": "list",
        "summary": "List every installed container with its size and source image.",
        "options": [("-h, --help", "Show this help.")],
        "examples": ["apatch-chroot list"],
    },
    "kill": {
        "usage": "kill NAME",
        "summary": (
            "Stop a container's running sessions and unmount its filesystems.\n\n"
            "Signals the container's processes, then sweeps the bind mounts and "
            "pseudo-filesystems the daemon set up. Mounts locked by a dead "
            "namespace are retried cross-namespace; anything that still will "
            "not come down is reported, and a device reboot is the last resort."
        ),
        "options": [("-h, --help", "Show this help.")],
        "examples": ["apatch-chroot kill ubuntu"],
    },
    "exec": {
        "usage": "exec NAME -- CMD [ARG...]",
        "summary": (
            "Run a command inside a container and capture its output.\n\n"
            "Unlike 'run' (an interactive PTY session), exec is non-interactive: "
            "it runs the command to completion and prints stdout/stderr, then "
            "exits with the command's exit code. Built for scripting and "
            "one-off checks.\n\n"
            "Everything after '--' is the command. The exit code of exec is the "
            "command's exit code, so it composes in shell pipelines."
        ),
        "options": [("-h, --help", "Show this help.")],
        "examples": [
            "apatch-chroot exec ubuntu -- cat /etc/os-release",
            "apatch-chroot exec ubuntu -- apt list --installed",
            "apatch-chroot exec alpine -- sh -c 'uname -a && nproc'",
        ],
    },
    "clone": {
        "usage": "clone SRC DST",
        "summary": (
            "Copy a container to a new one — a full rootfs snapshot.\n\n"
            "DST must not already exist. The entire rootfs is copied, along with "
            "the source image/arch metadata, so the clone 'list's identically to "
            "the source. Use it to snapshot before a risky change (a big apt "
            "upgrade, an experiment) so you can roll back by removing the clone "
            "or swapping names.\n\n"
            "This copies every file, so it uses as much disk as the source."
        ),
        "options": [("-h, --help", "Show this help.")],
        "examples": [
            "apatch-chroot clone ubuntu ubuntu-backup",
            "apatch-chroot clone ubuntu ubuntu-pre-upgrade",
        ],
    },
    "push": {
        "usage": "push NAME HOST_SRC CONTAINER_DEST",
        "summary": (
            "Copy a host file or directory INTO a container, no login needed.\n\n"
            "HOST_SRC is a path on the Android/Termux side; CONTAINER_DEST is an "
            "absolute path inside the container. If CONTAINER_DEST ends in '/' or "
            "is an existing directory, HOST_SRC is copied inside it under its own "
            "name. The daemon runs as root, so it can read any host path.\n\n"
            "Paths cannot escape the container rootfs."
        ),
        "options": [("-h, --help", "Show this help.")],
        "examples": [
            "apatch-chroot push ubuntu ~/backup.tar /root/",
            "apatch-chroot push ubuntu ./myscript.sh /usr/local/bin/",
        ],
    },
    "pull": {
        "usage": "pull NAME CONTAINER_SRC HOST_DEST",
        "summary": (
            "Copy a file or directory OUT of a container to the host.\n\n"
            "CONTAINER_SRC is an absolute path inside the container; HOST_DEST is "
            "a path on the Android/Termux side. If HOST_DEST ends in '/' or is an "
            "existing directory, the item is copied inside it under its own name. "
            "The reverse of 'push'."
        ),
        "options": [("-h, --help", "Show this help.")],
        "examples": [
            "apatch-chroot pull ubuntu /root/backup.tar ~/",
            "apatch-chroot pull ubuntu /var/log/ ./logs/",
        ],
    },
    "remove": {
        "usage": "remove NAME",
        "summary": (
            "Delete a container and its rootfs from disk.\n\n"
            "The container is killed first. This permanently deletes all data "
            "inside it; there is no undo."
        ),
        "options": [("-h, --help", "Show this help.")],
        "examples": ["apatch-chroot remove ubuntu"],
    },
    "info": {
        "usage": "info",
        "summary": (
            "Show daemon status and host capability probes.\n\n"
            "Reports whether the daemon is reachable, and which kernel features "
            "this device has (mount namespace, devpts, tmpfs, and so on), so a "
            "missing feature is explained rather than guessed."
        ),
        "options": [("-h, --help", "Show this help.")],
        "examples": ["apatch-chroot info"],
    },
    "service": {
        "usage": "service NAME VERB [UNIT]",
        "summary": (
            "Manage a container's services from the host, without logging in.\n\n"
            "This runs the container's systemctl shim (installed when the "
            "container is run with --init) with the given VERB and UNIT. Android "
            "has no systemd, so this shim talks to the container's mini-init "
            "instead. The command is resolved from the container's unit file "
            "(ExecStart=) or a built-in fallback.\n\n"
            "VERBS: start stop restart reload status enable disable is-active "
            "is-enabled list-units\n\n"
            "Exit codes follow systemd's contract: 0 = active/enabled, "
            "3 = inactive, so scripts using 'is-active' in conditionals work."
        ),
        "options": [("-h, --help", "Show this help.")],
        "examples": [
            "apatch-chroot service ubuntu start sshd",
            "apatch-chroot service ubuntu enable sshd",
            "apatch-chroot service ubuntu status sshd",
            "apatch-chroot service ubuntu is-active sshd",
            "apatch-chroot service ubuntu list-units",
        ],
    },
}
