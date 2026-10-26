#!/usr/bin/env python3
"""apatch-chroot journald / journalctl replacement — MIT, clean-room.

The journal for containers without systemd, following the same pattern as
systemctl.py and loginctl.py: staged into the container, written from
systemd's documented behavior + POSIX primitives.

Storage is plain files (no daemon needed — the verbs are stateless):

    /var/log/apatch-chroot/<unit>.log    raw stdout/stderr of the service
    /var/log/apatch-chroot/journal.jsonl structured lifecycle events

The init supervisor (systemctl.py init) appends lifecycle events here via
log_event(): started / exited / stopped / restarted / failed, one JSON
object per line with an epoch timestamp.

journalctl reads BOTH and merges them into a single timeline per unit:

    journalctl                          everything, newest last
    journalctl -u hermes-gateway        one unit (repeatable: -u A -u B)
    journalctl -u ssh -f                follow (tail -f behavior)
    journalctl -u ssh -n 50             last 50 lines (default 10)
    journalctl -u ssh -r                newest first
    journalctl -u ssh -S "10 min ago"   since
    journalctl -u ssh -U "2026-10-27"   until
    journalctl -e                       jump to the end (last 10 lines)
    journalctl --list-boots             single-entry boot list (no PID ns)
    --no-pager and -o short             accepted, we never page anyway
"""

import argparse
import json
import os
import sys
import time

LOGDIR = os.environ.get("APATCH_LOGDIR", "/var/log/apatch-chroot")
JOURNAL = os.path.join(LOGDIR, "journal.jsonl")
BOOT_ID = "1"


# ------------------------------------------------------------- writing

def log_event(unit, event, pid=None, code=None, **extra):
    """Append one structured lifecycle event (called by the supervisor)."""
    rec = {"t": time.time(), "unit": unit, "event": event}
    if pid is not None:
        rec["pid"] = pid
    if code is not None:
        rec["code"] = code
    rec.update(extra)
    os.makedirs(LOGDIR, exist_ok=True)
    with open(JOURNAL, "a") as fh:
        fh.write(json.dumps(rec) + "\n")


# -------------------------------------------------------------- reading

def _parse_since(val):
    """Accept epoch, ISO-ish 'YYYY-MM-DD[ HH:MM[:SS]]', 'N min/hour(s) ago'."""
    if not val:
        return None
    val = val.strip()
    try:
        return float(val)  # epoch
    except ValueError:
        pass
    low = val.lower()
    import re
    m = re.match(r"^(\d+)\s*(min|minute|minutes|m|hour|hours|h|day|days|d)\s*ago$", low)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        mult = {"m": 60, "min": 60, "minute": 60, "minutes": 60,
                "h": 3600, "hour": 3600, "hours": 3600,
                "d": 86400, "day": 86400, "days": 86400}[unit]
        return time.time() - n * mult
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return time.mktime(time.strptime(val, fmt))
        except ValueError:
            continue
    return None


def read_events():
    """Structured events from journal.jsonl (tolerates partial lines)."""
    out = []
    try:
        with open(JOURNAL, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and "t" in rec:
                    out.append(rec)
    except OSError:
        pass
    return out


def read_unit_logs(unit=None):
    """Raw service output: [(timestamp, unit, line)] from <unit>.log files.

    Log lines have no timestamps of their own, so the file's mtime is used
    as the best available ordering signal (second-granular on some
    filesystems — lifecycle events carry exact times and win ties).
    """
    out = []
    try:
        names = os.listdir(LOGDIR)
    except OSError:
        return out
    for name in names:
        if not name.endswith(".log"):
            continue
        unit_name = name[:-len(".log")]
        if unit and unit_name != unit:
            continue
        try:
            mtime = os.path.getmtime(os.path.join(LOGDIR, name))
            with open(os.path.join(LOGDIR, name),
                      encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    out.append((mtime, unit_name, line.rstrip("\n")))
        except OSError:
            continue
    return out


def _fmt_ts(ts):
    return time.strftime("%b %d %H:%M:%S", time.localtime(ts))


def collect(units=None, since=None, until=None, reverse=False):
    """Merged timeline: structured events + raw output, oldest first."""
    rows = []
    for rec in read_events():
        if units and rec.get("unit") not in units:
            continue
        if since and rec["t"] < since:
            continue
        if until and rec["t"] > until:
            continue
        ev = rec.get("event", "?")
        extra = ""
        if rec.get("pid") is not None:
            extra += f" pid={rec['pid']}"
        if rec.get("code") is not None:
            extra += f" code={rec['code']}"
        rows.append((rec["t"], rec.get("unit", "-"),
                     f"[{ev}]{extra}"))
    for ts, unit, line in read_unit_logs(None):
        if units and unit not in units:
            continue
        if since and ts < since:
            continue
        if until and ts > until:
            continue
        rows.append((ts, unit, line))
    rows.sort(key=lambda r: r[0])
    if reverse:
        rows.reverse()
    return rows


def print_rows(rows, n=None):
    if n is not None:
        rows = rows[-n:]
    for ts, unit, line in rows:
        print(f"{_fmt_ts(ts)} {unit}: {line}")


def follow(units, n=10):
    """Tail the merged timeline until interrupted."""
    rows = collect(units=units)
    for ts, unit, line in rows[-n:]:
        print(f"{_fmt_ts(ts)} {unit}: {line}")
    seen = {(r[0], r[1], r[2]) for r in rows}
    pos = {name: 0 for name in _watched_files(units)}
    for name, off in pos.items():
        pos[name] = _file_size(name)
    try:
        while True:
            for name in _watched_files(units):
                size = _file_size(name)
                if size > pos[name]:
                    with open(name, encoding="utf-8", errors="replace") as fh:
                        fh.seek(pos[name])
                        for line in fh:
                            unit = os.path.basename(name)
                            if unit == "journal.jsonl":
                                try:
                                    rec = json.loads(line)
                                    u = rec.get("unit", "-")
                                    if units and u not in units:
                                        continue
                                    ev = rec.get("event", "?")
                                    extra = ""
                                    if rec.get("pid") is not None:
                                        extra += f" pid={rec['pid']}"
                                    if rec.get("code") is not None:
                                        extra += f" code={rec['code']}"
                                    line = f"[{ev}]{extra}"
                                    print(f"{_fmt_ts(rec.get('t', 0))} {u}: {line}", flush=True)
                                    continue
                                except ValueError:
                                    continue
                            if unit.endswith(".log"):
                                unit = unit[:-len(".log")]
                                if units and unit not in units:
                                    continue
                                print(f"{_fmt_ts(time.time())} {unit}: {line.rstrip()}", flush=True)
                    pos[name] = size
                elif size < pos[name]:
                    pos[name] = size  # truncated/rotated
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0


def _watched_files(units):
    files = [JOURNAL]
    try:
        for name in os.listdir(LOGDIR):
            if name.endswith(".log"):
                unit = name[:-len(".log")]
                if not units or unit in units:
                    files.append(os.path.join(LOGDIR, name))
    except OSError:
        pass
    return files


def _file_size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


# ----------------------------------------------------------------- CLI

def main(argv=None):
    p = argparse.ArgumentParser(
        prog="journalctl", add_help=True,
        description="Query the apatch-chroot journal (own creation, MIT).")
    p.add_argument("-u", "--unit", action="append", default=[],
                   help="show this unit (repeatable)")
    p.add_argument("-f", "--follow", action="store_true",
                   help="keep printing new lines")
    p.add_argument("-n", "--lines", default="10",
                   help="number of lines to show ('all' for everything)")
    p.add_argument("-e", "--pager-end", action="store_true",
                   help="jump to the end (same as the default -n 10)")
    p.add_argument("-r", "--reverse", action="store_true",
                   help="newest first")
    p.add_argument("-S", "--since", help="epoch, ISO date, or '10 min ago'")
    p.add_argument("-U", "--until", help="epoch, ISO date, or '10 min ago'")
    p.add_argument("--list-boots", action="store_true",
                   help="list boots (single entry: no PID namespace here)")
    p.add_argument("--no-pager", action="store_true",
                   help="accepted for compatibility; output is never paged")
    p.add_argument("-o", "--output", default="short",
                   help="accepted for compatibility (short)")
    p.add_argument("units_positional", nargs="*",
                   help="unit names without -u (like journalctl ssh)")
    args = p.parse_args(argv if argv is not None else sys.argv[1:])

    if args.list_boots:
        print("BOOT ID  FIRST ENTRY                  LAST ENTRY")
        print(f"{BOOT_ID:<8} (container session start)     (now)")
        return 0

    units = set(args.unit) | set(args.units_positional) or None
    since = _parse_since(args.since)
    until = _parse_until(args.until)

    if args.follow:
        return follow(units, n=_n(args.lines) or 10)

    rows = collect(units=units, since=since, until=until, reverse=args.reverse)
    if args.reverse:
        n = _n(args.lines)
        if n is not None:
            rows = rows[:n]
        for ts, unit, line in rows:
            print(f"{_fmt_ts(ts)} {unit}: {line}")
    else:
        n = _n(args.lines)
        if n is not None:
            rows = rows[-n:]
        for ts, unit, line in rows:
            print(f"{_fmt_ts(ts)} {unit}: {line}")
    return 0


def _parse_until(val):
    return _parse_since(val)


def _n(val):
    if val is None or val == "all":
        return None
    try:
        return max(0, int(val))
    except ValueError:
        return 10


if __name__ == "__main__":
    sys.exit(main())
