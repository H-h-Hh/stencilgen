#!/usr/bin/env bash
# macOS / Linux launcher.  No arguments = GUI, with arguments = command line.
set -e
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
exec "$PY" -m stencilgen "$@"
