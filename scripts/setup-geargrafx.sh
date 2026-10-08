#!/usr/bin/env bash
# Source is fetched into TOOLS_DIR outside the repository; no global install.
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "${1:-}" == --desktop ]]; then
    # Separate source/build tree: desktop and libretro have incompatible defines.
    python3 "$root/tools/setup.py" geargrafx --desktop
elif [[ $# -ne 0 ]]; then
    echo 'usage: setup-geargrafx.sh [--desktop]' >&2
    exit 2
else
    python3 "$root/tools/setup.py" geargrafx
fi
