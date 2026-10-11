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
    ("setuser", "Set a container's default login user (least privilege)."),
    ("check-update", "Check GitHub for a newer release."),
    ("kill", "Stop a container and tear down its mounts."),
    ("remove", "Delete a container and its rootfs.", "!deletes all data — no undo"),
    ("info", "Show daemon and host capability status."),
    ("stats", "Show per-container disk usage and running sessions."),
)

# Shown at the bottom of the front page — the #1 thing that confuses people.
FRONT_NOTES = (
    "After a reboot the daemon takes ~30-60s to start (it waits for Android to "
    "finish booting and Termux storage to unlock). Commands auto-wait up to 20s "
    "for it; if it is still not up, you will get a hint instead of an error.",
    "No root needed: the client talks to the privileged daemon over a unix socket.",
    "Topics: 'apatch-chroot help display' (GUI/termux-x11) and "
    "'apatch-chroot help storage' (convert/move/disk space).",
)

# Topic help pages (not commands): reachable via 'apatch-chroot help <topic>'.
HELP_TOPICS = ("display", "storage")

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
            ("--init", "Start the container's service manager (mini-systemd)."),
            ("--box64", "Enable x86_64 emulation (needs box64 in the container)."),
            ("--shared-display", "Share termux-x11 display (alias: --shared-x11)."),
            ("--shared-audio", "Share PulseAudio sound (alias: --shared-sound)."),
            ("--user [NAME]", "Log in as this user instead of the default."),
            ("--root", "Force root even if a default user is set."),
        ],
        "examples": [
            "apatch-chroot run ubuntu -- /bin/bash",
            "apatch-chroot run ubuntu --init -- /bin/bash",
            "apatch-chroot run ubuntu --shared-display -- xterm",
            "apatch-chroot run ubuntu --box64 -- ./x86_64-app",
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
            ("--init", "Start the container's service manager (mini-systemd)."),
            ("--box64", "Enable x86_64 emulation (needs box64 in the container)."),
            ("--shared-display", "Share termux-x11 display (alias: --shared-x11)."),
            ("--shared-audio", "Share PulseAudio sound (alias: --shared-sound)."),
            ("--user [NAME]", "Log in as this user instead of the default."),
            ("--root", "Force root even if a default user is set."),
        ],
        "examples": [
            "apatch-chroot login ubuntu",
            "apatch-chroot login ubuntu --init",
            "apatch-chroot login ubuntu --shared-display",
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
        "usage": "exec [OPTIONS] NAME -- CMD [ARG...]",
        "summary": (
            "Run a command inside a container and capture its output.\n\n"
            "Unlike 'run' (an interactive PTY session), exec is non-interactive: "
            "it runs the command to completion and prints stdout/stderr, then "
            "exits with the command's exit code. Built for scripting and "
            "one-off checks.\n\n"
            "Everything after '--' is the command. The exit code of exec is the "
            "command's exit code, so it composes in shell pipelines.\n\n"
            "Note: use absolute command paths (e.g. /usr/bin/id) — bare names "
            "can hit a stripped-Python PATH lookup in minimal images."
        ),
        "options": [
            ("-h, --help", "Show this help."),
            ("--init", "Start the container's service manager (mini-systemd)."),
            ("--box64", "Enable x86_64 emulation (needs box64 in the container)."),
            ("--shared-display", "Share termux-x11 display (alias: --shared-x11)."),
            ("--shared-audio", "Share PulseAudio sound (alias: --shared-sound)."),
            ("--user [NAME]", "Run as this user instead of the default."),
            ("--root", "Force root even if a default user is set."),
        ],
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
    "setuser": {
        "usage": "setuser NAME [USER]",
        "summary": (
            "Set a container's default login user (least privilege).\n\n"
            "The daemon must run as root to chroot/mount, but your container "
            "login does not have to be root. This sets which user 'run'/'login' "
            "drop to after chroot.\n\n"
            "  setuser ubuntu luis    log in as 'luis' by default\n"
            "  setuser ubuntu root    restore root (legacy default)\n\n"
            "Per-session override: 'run'/'exec' accept --user U (log in as U "
            "just this time) or --root (force root even if a default is set). "
            "Root is opt-in, never the silent default once a user is set."
        ),
        "options": [("-h, --help", "Show this help.")],
        "examples": [
            "apatch-chroot setuser ubuntu luis",
            "apatch-chroot setuser ubuntu root",
            "apatch-chroot run ubuntu --user luis",
            "apatch-chroot run ubuntu --root",
        ],
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
    "stats": {
        "usage": "stats",
        "summary": (
            "Show per-container disk usage, running sessions, and default user.\n\n"
            "A quick overview of what's installed, how big each container is, "
            "how many sessions are currently running, and which user it logs in "
            "as by default. Handy before a big install (check free space) or to "
            "see what's active."
        ),
        "options": [("-h, --help", "Show this help.")],
        "examples": ["apatch-chroot stats"],
    },
    "check-update": {
        "usage": "check-update",
        "summary": (
            "Check GitHub for a newer release than the one you're running.\n\n"
            "Compares the installed client version against the latest GitHub "
            "release. If a newer one exists, prints the download link for the "
            "module zip. Needs network access; if GitHub is unreachable it says "
            "so and points you to the releases page manually.\n\n"
            "Alias: 'update'. Exit code 1 means an update is available."
        ),
        "options": [("-h, --help", "Show this help.")],
        "examples": ["apatch-chroot check-update"],
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
    "display": {
        "usage": "help display",
        "summary": (
            "Running GUI apps in a container (termux-x11).\n\n"
            "Android has no X server, so GUI apps need termux-x11. The "
            "--shared-display flag (alias --shared-x11) binds termux-x11's "
            "socket into the container and sets DISPLAY=:0, so xterm and other "
            "GUI apps reach the display.\n\n"
            "The socket bind lives in the container's PRIVATE mount namespace, "
            "so it cannot propagate to the host — Android's audio (/dev/snd) "
            "and other mounts are untouched. This is the safe replacement for "
            "chroot-distro's --shared-display, which broke host audio because "
            "it lacked real isolation."
        ),
        "options": [],
        "examples": [
            "# 1. Install termux-x11 (once, in Termux):",
            "pkg install x11-repo termux-x11-nightly",
            "",
            "# 2. Start the X server:",
            "termux-x11 :0 &",
            "",
            "# 3. Run a GUI app in the container:",
            "apatch-chroot login ubuntu --shared-display",
            "xterm",
            "",
            "# or one-shot:",
            "apatch-chroot run ubuntu --shared-display -- xterm",
            "",
            "# If a GUI app says 'cannot open display', check:",
            "apatch-chroot info        # binfmt/namespace status",
            "echo $DISPLAY             # should be :0 inside the container",
        ],
    },
    "storage": {
        "usage": "help storage",
        "summary": (
            "Moving and converting containers (disk space).\n\n"
            "convert makes a FULL COPY of a container, so it needs free space "
            "equal to the container's size (old + new both exist briefly). For "
            "a container bigger than your free space, use --move instead.\n\n"
            "--move RENAMES the container's rootfs on the same /data filesystem "
            "— instant, zero extra space. The trade-off: the source container "
            "is consumed (it becomes the destination). Both paths must be on "
            "the same filesystem (they are: everything is under /data).\n\n"
            "Check sizes before a big move: 'apatch-chroot list' shows each "
            "container's size; 'df -h /data' shows free space."
        ),
        "options": [],
        "examples": [
            "# See what you have and how big:",
            "apatch-chroot list",
            "",
            "# Copy a chroot-distro container in (needs free space = its size):",
            "apatch-chroot convert ubuntu ubuntu-apatch",
            "",
            "# OR move it instantly, zero extra space (consumes the source):",
            "apatch-chroot convert ubuntu --move",
            "",
            "# The chroot-distro source must be unmounted first (reboot if it",
            "# complains about ghost mounts).",
        ],
    },
}
