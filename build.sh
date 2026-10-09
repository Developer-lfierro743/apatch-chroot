#!/data/data/com.termux/files/usr/bin/bash
# Build a flashable APatch module zip.
#
# The zip root must contain module.prop (APatch reads it there), plus the
# daemon/ and client/ trees that service.sh and customize.sh copy into place.
# We assemble into a staging dir so the zip layout is exactly:
#
#   module.prop
#   service.sh
#   customize.sh
#   daemon/*.py
#   client/*.py
#
# Usage: ./build.sh [output.zip]   (default: apatch-chroot.zip)

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${1:-$HERE/apatch-chroot.zip}"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

# Module glue at the zip root.
cp "$HERE/module/module.prop" "$STAGE/"
cp "$HERE/module/service.sh" "$STAGE/"
cp "$HERE/module/customize.sh" "$STAGE/"

# Daemon + client trees (no __pycache__).
mkdir -p "$STAGE/daemon" "$STAGE/client"
for f in "$HERE"/daemon/*.py; do
    cp "$f" "$STAGE/daemon/"
done
for f in "$HERE"/client/*.py; do
    cp "$f" "$STAGE/client/"
done

# Zip from inside the staging dir so paths are relative (no leading ./).
cd "$STAGE"
rm -f "$OUT"
zip -r -X "$OUT" . -x '*.pyc' '*__pycache__*' >/dev/null

echo "built: $OUT"
unzip -l "$OUT" | sed -n '1,40p'
