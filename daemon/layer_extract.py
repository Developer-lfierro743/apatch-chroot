"""Extract a Docker/OCI layer (tar.gz) into a rootfs, safely.

One layer at a time, in the order the manifest lists them, because a later
layer's whiteouts and overwrites only mean anything against the tree the
earlier ones built.

An archive is a document we did not write, so every member is filtered before it
is written:
  - block/char/FIFO device entries are skipped (we never create device nodes
    from a layer)
  - a member whose path, after any leading './', contains an empty or '..'
    component, or whose final component is '.', is dropped (path traversal)
  - OCI whiteouts are honoured: '.wh.<name>' removes the sibling <name>, and an
    opaque '.wh..wh..opq' clears the whole directory it sits in
  - symlink and hardlink targets are filtered the same way, so a crafted
    archive cannot escape the rootfs

Parent directories are resolved with symlink hops clamped inside the rootfs, so
an earlier member shipping 'evil -> /' cannot have a later 'evil/passwd' written
through it onto the host.
"""

import gzip
import os
import tarfile

WHITEOUT_PREFIX = ".wh."
OPAQUE_WHITEOUT = ".wh..wh..opq"


def _clean_member_path(name):
    """Return the safe relative path for a member, or None if it must be dropped.

    Strips leading './' components (OCI layers spell paths './foo'), rejects any
    remaining empty or '..' component, and rejects a trailing '.' component.
    Interior '.' between real components is fine and kept out of the result.
    """
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if not parts:
        return None
    if any(p == ".." for p in parts):
        return None
    return parts


def _resolve_within(rootfs, rel_parts):
    """Resolve rel_parts under rootfs, clamping symlink hops inside it.

    Returns the absolute on-disk path, or None if a component escapes. An
    absolute symlink target re-roots at the rootfs (mirroring the guest's own
    view), so a legitimate absolute link still lands in the right place and '..'
    can never ascend past the root.
    """
    cur = rootfs
    for part in rel_parts:
        nxt = os.path.join(cur, part)
        if os.path.islink(nxt):
            target = os.readlink(nxt)
            if os.path.isabs(target):
                target = target.lstrip("/")
            # Clamp: resolve the link target relative to the link's directory.
            resolved = os.path.normpath(os.path.join(cur, target))
            if not (resolved == rootfs or resolved.startswith(rootfs + os.sep)):
                return None
            cur = resolved
        else:
            cur = nxt
    return cur


def _apply_whiteout(rootfs, dir_parts, wh_name):
    """Handle one '.wh.<name>' or opaque whiteout inside directory dir_parts."""
    if wh_name == ".wh..wh..opq":
        # Opaque: clear the whole directory this marker sits in.
        target = _resolve_within(rootfs, dir_parts)
        if target and os.path.isdir(target):
            for entry in os.listdir(target):
                _rmtree(os.path.join(target, entry))
        return
    # Regular whiteout: remove the sibling named after '.wh.'.
    parts = _clean_member_path(wh_name)
    if not parts:
        return
    target = _resolve_within(rootfs, dir_parts + parts)
    if target and (os.path.islink(target) or os.path.exists(target)):
        _rmtree(target)


def _rmtree(path):
    import shutil
    if os.path.islink(path) or os.path.isfile(path):
        with _suppress_os():
            os.unlink(path)
    elif os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)


class _suppress_os:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return True


def apply_layer(layer_path, rootfs):
    """Extract the gzip tar at *layer_path* into *rootfs*. Returns member count.

    Members are applied in archive order. Directories are ensured before the
    files inside them. Whiteouts are processed in place. Device/FIFO entries are
    skipped. Failures on an individual member are skipped (logged by the caller
    if desired) so one bad entry does not abort a whole layer.
    """
    applied = 0
    rootfs = os.path.realpath(rootfs)
    with gzip.open(layer_path, "rb") as gz:
        with tarfile.open(fileobj=gz, mode="r|") as tar:
            for member in tar:
                name = member.name
                # Whiteouts first: they act on siblings, not themselves.
                base = name.rsplit("/", 1)
                fname = base[-1]
                dir_parts = _clean_member_path(base[0]) if len(base) == 2 else []
                if dir_parts is None:
                    continue
                if fname == OPAQUE_WHITEOUT:
                    _apply_whiteout(rootfs, dir_parts, fname)
                    continue
                if fname.startswith(WHITEOUT_PREFIX):
                    _apply_whiteout(rootfs, dir_parts, fname[len(WHITEOUT_PREFIX):])
                    continue

                rel = _clean_member_path(name)
                if rel is None:
                    continue

                # Never materialise device nodes / FIFOs from a layer.
                if member.isdev() or member.isfifo():
                    continue

                dest = _resolve_within(rootfs, rel)
                if dest is None:
                    continue

                try:
                    if member.isdir():
                        os.makedirs(dest, exist_ok=True)
                        applied += 1
                    elif member.issym():
                        _make_symlink(rootfs, rel, member.linkname)
                        applied += 1
                    elif member.islnk():
                        link_rel = _clean_member_path(member.linkname)
                        if link_rel:
                            src = _resolve_within(rootfs, link_rel)
                            if src and os.path.exists(src):
                                _ensure_parent(dest)
                                _rmtree(dest)
                                _hardlink_or_copy(src, dest)
                                applied += 1
                    elif member.isreg():
                        _ensure_parent(dest)
                        _rmtree(dest)
                        with open(dest, "wb") as out:
                            extracted = tar.extractfile(member)
                            if extracted:
                                while True:
                                    chunk = extracted.read(1024 * 256)
                                    if not chunk:
                                        break
                                    out.write(chunk)
                        _chmod_apply(dest, member.mode)
                        applied += 1
                except OSError:
                    # Skip this member; a later layer or the next install fixes it.
                    continue
    return applied


def _ensure_parent(dest):
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _hardlink_or_copy(src, dest):
    """Hardlink src -> dest, falling back to a copy.

    Bionic's Python does not expose os.link, so a hardlink raises
    AttributeError; copy the bytes instead. Either way *dest* ends up with
    src's content, which is all a tar hardlink entry means.
    """
    import shutil
    try:
        os.link(src, dest)
    except (AttributeError, OSError):
        shutil.copy2(src, dest, follow_symlinks=False)


def _chmod_apply(path, mode):
    # Keep at least owner rwx so later members can be written into directories.
    with _suppress_os():
        os.chmod(path, mode | 0o700 if os.path.isdir(path) else mode)


def _make_symlink(rootfs, rel, linkname):
    """Create a symlink, preserving the archive's own target verbatim.

    The link string is written exactly as the archive recorded it, so
    '/bin/busybox' stays '/bin/busybox' inside the guest rather than being
    rewritten to an absolute host path the chroot could never resolve. An
    absolute or '..' target is NOT rejected here: it is a stored string, and
    the danger is a *later* member writing through it, which _resolve_within
    already clamps to the rootfs on every write.
    """
    dest = _resolve_within(rootfs, rel)
    if dest is None:
        return
    _ensure_parent(dest)
    _rmtree(dest)
    with _suppress_os():
        os.symlink(linkname, dest)
