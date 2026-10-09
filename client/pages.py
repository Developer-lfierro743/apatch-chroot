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
    ("convert", "Import an existing chroot-distro container."),
    ("run", "Run a command in a container (non-interactive or one-shot)."),
    ("login", "Open an interactive shell inside a container."),
    ("list", "List installed containers."),
    ("kill", "Stop a container and tear down its mounts."),
    ("remove", "Delete a container and its rootfs."),
    ("info", "Show daemon and host capability status."),
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
}
