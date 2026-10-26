 ""Importable entry point for the installed `apatch-chroot` launcher.

The client lives in client/apatch-chroot.py (a hyphen, so it cannot be
imported by name). This thin wrapper is what the module's launcher imports;
it pulls in the real client module by path and calls its main(). Because the
client is imported (not run as __main__), its own __main__ guard does not
fire — so we catch its DaemonNotReady here and print the friendly hint
instead of a raw traceback.
"""

import importlib.util
import os
import sys

_CLIENT_DIR = os.path.dirname(os.path.abspath(__file__))


def main(argv=None):
    """Load client/apatch-chroot.py and run its main(), handling daemon errors."""
    path = os.path.join(_CLIENT_DIR, "apatch-chroot.py")
    spec = importlib.util.spec_from_file_location("apatch_chroot_client", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    try:
        return mod.main(argv if argv is not None else sys.argv)
    except mod.DaemonNotReady as e:
        print(f"apatch-chroot: {e.hint()}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0
