#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
COMPANION_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
python3 "$COMPANION_DIR/install.py" openminis "$@"
