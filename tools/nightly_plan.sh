#!/usr/bin/env bash
# main-only successor to the legacy feature-branch/diff planner.
set -euo pipefail
export SOURCE_SHA="$(git rev-parse HEAD)"
python3 tools/nightly.py plan
