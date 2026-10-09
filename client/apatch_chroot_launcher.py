"""Importable entry point for the installed `apatch-chroot` launcher.

The client lives in client/apatch-chroot.py (a hyphen, so it cannot be
imported by name). This thin wrapper is what the module's launcher imports;
it pulls in the real client module by path and calls its main(). Keeps the
launcher script in customize.sh trivial and the three client files together.
"""

import importlib.util
import os
import sys

_CLIENT_DIR = os.path.dirname(os.path.abspath(__file__))


def main(argv=None):
    """Load client/apatch-chroot.py and run its main()."""
    path = os.path.join(_CLIENT_DIR, "apatch-chroot.py")
    spec = importlib.util.spec_from_file_location("apatch_chroot_client", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.main(argv if argv is not None else sys.argv)
