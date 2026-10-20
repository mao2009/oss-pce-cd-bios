"""Shared paths and pinned dependencies; no shell evaluation or global installs."""
import json
import os
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
DEPENDENCIES = json.loads((ROOT / "tools/dependencies.json").read_text())


def tools_dir():
    path = Path(os.environ.get("TOOLS_DIR", f"/tmp/oss-pce-cd-bios-tools-{os.getuid()}"))
    path = path.resolve()
    if path == ROOT or ROOT in path.parents:
        raise ValueError("TOOLS_DIR must be outside this repository (external source isolation)")
    return path


def dependency_dir(name):
    return tools_dir() / f"{name}-{DEPENDENCIES[name]['revision']}"


def executable(name):
    defaults = {
        "ca65": dependency_dir("cc65") / "bin/ca65",
        "ld65": dependency_dir("cc65") / "bin/ld65",
        "da65": dependency_dir("cc65") / "bin/da65",
        "cc65": dependency_dir("cc65") / "bin/cc65",
        "actionlint": dependency_dir("actionlint") / "actionlint",
    }
    requested = os.environ.get(name.upper(), str(defaults[name]))
    found = shutil.which(requested)
    if not found:
        raise ValueError(f"missing tool {name}: {requested}; run make setup")
    return found


def cc65_skip_reason():
    """None when the pinned cc65 tools are usable, else a SKIP/BLOCKED reason for unittest skips."""
    for name in ("ca65", "ld65", "cc65", "da65"):
        try:
            executable(name)
        except Exception as error:  # e.g. os.getuid missing on Windows: still a reasoned skip, not an import crash
            return f"SKIP/BLOCKED (cc65 unavailable, run make setup): {type(error).__name__}: {error}"
    return None


def core_path():
    path = Path(os.environ.get("GEARGRAFX_CORE", str(
        dependency_dir("geargrafx") / "platforms/libretro/geargrafx_libretro.so")))
    if not path.is_file():
        raise ValueError(f"missing Geargrafx core: {path}; run make setup")
    return path.resolve()
