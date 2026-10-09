"""The help-page renderer: page data in, wrapped and coloured lines out.

Drawn to a width term_width decides, because help is read on a phone as often
as on a desktop: fds 2, 1 and 0 are tried in turn, then $COLUMNS, then a
fallback, clamped to [_MIN_WIDTH, _MAX_WIDTH] so a maximised window does not
produce lines nobody can follow. Below NARROW_BREAKPOINT, or whenever the
description column would come out too thin, the two-column blocks fall back to
a stacked form instead of squeezing.

Wrapping keeps long words and hyphenated words whole (flags and paths must stay
copy-pasteable), and column arithmetic counts visible characters only: colour
escapes are added after a line's width is decided, never measured.

render_page walks the dict shape pages.py defines and emits only the keys a page
actually has, so a page needs no empty sections.
"""

import os
import shutil
import sys
import textwrap

_MIN_WIDTH = 32
_MAX_WIDTH = 92
NARROW_BREAKPOINT = 60


class C:
    """ANSI colours; every field is '' when output is not a TTY."""

    enabled = sys.stderr.isatty()

    CYAN = "\033[36m" if enabled else ""
    GREEN = "\033[32m" if enabled else ""
    YELLOW = "\033[33m" if enabled else ""
    BOLD = "\033[1m" if enabled else ""
    DIM = "\033[2m" if enabled else ""
    RST = "\033[0m" if enabled else ""


_RULE = "─"
_BULLET = "▸"
_PROMPT = "$"


def term_width():
    """Return a sensible terminal width clamped to [_MIN_WIDTH, _MAX_WIDTH]."""
    for fd in (2, 1, 0):
        try:
            cols = os.get_terminal_size(fd).columns
        except (OSError, ValueError):
            continue
        if cols > 0:
            return max(_MIN_WIDTH, min(_MAX_WIDTH, cols))
    try:
        cols = int(os.environ.get("COLUMNS", "0"))
    except ValueError:
        cols = 0
    if cols > 0:
        return max(_MIN_WIDTH, min(_MAX_WIDTH, cols))
    try:
        return max(_MIN_WIDTH, min(_MAX_WIDTH, shutil.get_terminal_size((72, 24)).columns))
    except (OSError, ValueError):
        return 72


def _wrap(text, width):
    width = max(4, width)
    return textwrap.wrap(text, width=width, break_long_words=False, break_on_hyphens=False) or [""]


def _hrule(width, char=_RULE, color=None):
    return f"{color or C.CYAN}{char * width}{C.RST}"


def _header(name, usage, aliases, width):
    title = name if not aliases else f"{name} ({', '.join(aliases)})"
    print(f"{C.BOLD}{C.CYAN}{title}{C.RST}", file=sys.stderr)
    print(_hrule(width), file=sys.stderr)
    print(f"{C.DIM}Usage:{C.RST} {C.BOLD}{usage}{C.RST}", file=sys.stderr)
    print(file=sys.stderr)


def _summary(text, width):
    for para in text.split("\n\n"):
        for line in _wrap(" ".join(para.split()), width):
            print(line, file=sys.stderr)
        print(file=sys.stderr)


def _options_block(opts, width):
    if not opts:
        return
    print(f"{C.BOLD}Options:{C.RST}", file=sys.stderr)
    name_w = max(len(n) for n, _ in opts)
    stacked = width < NARROW_BREAKPOINT or name_w + 4 > width * 0.6
    if stacked:
        for name, desc in opts:
            print(f"  {C.GREEN}{name}{C.RST}", file=sys.stderr)
            for line in _wrap(desc, width - 6):
                print(f"      {line}", file=sys.stderr)
    else:
        desc_w = width - name_w - 6
        for name, desc in opts:
            lines = _wrap(desc, desc_w)
            print(f"  {C.GREEN}{name.ljust(name_w)}{C.RST}  {lines[0]}", file=sys.stderr)
            for cont in lines[1:]:
                print(f"  {' ' * name_w}  {cont}", file=sys.stderr)
    print(file=sys.stderr)


def _examples_block(examples, width):
    if not examples:
        return
    print(f"{C.BOLD}Examples:{C.RST}", file=sys.stderr)
    for ex in examples:
        print(f"  {C.YELLOW}{_PROMPT}{C.RST} {ex}", file=sys.stderr)
    print(file=sys.stderr)


def render_page(name, page, width):
    """Render one command's help page to stderr."""
    _header(name, page.get("usage", name), page.get("aliases", ()), width)
    if page.get("summary"):
        _summary(page["summary"], width)
    _options_block(page.get("options", []), width)
    _examples_block(page.get("examples", []), width)


def render_front(program, version, top_commands, width):
    """Render the top-level help (no command given, or --help)."""
    print(f"{C.BOLD}{C.CYAN}{program}{C.RST} {C.DIM}{version}{C.RST}", file=sys.stderr)
    print(_hrule(width), file=sys.stderr)
    print(
        "A chroot container tool for Android. Runs a privileged daemon via "
        "APatch so chroot and mounts work outside Android's app seccomp filter.",
        file=sys.stderr,
    )
    print(file=sys.stderr)
    print(f"{C.BOLD}Commands:{C.RST}", file=sys.stderr)
    name_w = max(len(n) for n, _ in top_commands)
    for entry in top_commands:
        name, desc = entry[0], entry[1]
        warn = entry[2] if len(entry) > 2 else None
        if width < NARROW_BREAKPOINT:
            print(f"  {C.GREEN}{name}{C.RST}", file=sys.stderr)
            for line in _wrap(desc, width - 6):
                print(f"      {line}", file=sys.stderr)
            if warn:
                print(f"      {C.YELLOW}{warn}{C.RST}", file=sys.stderr)
        else:
            desc_w = width - name_w - 6
            lines = _wrap(desc, desc_w)
            suffix = f"  {C.YELLOW}{warn}{C.RST}" if warn else ""
            print(f"  {C.GREEN}{name.ljust(name_w)}{C.RST}  {lines[0]}{suffix}", file=sys.stderr)
            for cont in lines[1:]:
                print(f"  {' ' * name_w}  {cont}", file=sys.stderr)
    print(file=sys.stderr)
    print(
        f"{C.DIM}Run '{program} <command> --help' for details on a command.{C.RST}",
        file=sys.stderr,
    )
