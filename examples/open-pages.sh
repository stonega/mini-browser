#!/usr/bin/env sh
set -eu
PROJECT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
# Each URL gets its own window. A quoted search also works.
exec "$PROJECT_DIR/scripts/run" https://www.gnome.org https://webkitgtk.org
