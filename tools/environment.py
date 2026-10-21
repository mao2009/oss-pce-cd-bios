"""Shared paths and pinned dependencies; no shell evaluation or global installs."""
import functools
import json
import os
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
DEPENDENCIES = json.loads((ROOT / "tools/dependencies.json").read_text())


def tools_dir():
    path = Path(os.environ.get("TOOLS_DIR") or f"/tmp/oss-pce-cd-bios-tools-{getattr(os, 'getuid', lambda: 0)()}")
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


CC65_TOOLS = ("ca65", "ld65", "cc65", "da65")
REQUIRE_CC65_ENV = "OSS_PCE_REQUIRE_CC65"


def cc65_skip_reason():
    """None when the pinned cc65 tools are usable, else a reason naming the first missing tool."""
    for name in CC65_TOOLS:
        try:
            executable(name)
        except Exception as error:  # e.g. os.getuid missing on Windows: still a reasoned skip, not an import crash
            return f"cc65 unavailable ({name}: {type(error).__name__}: {error}); run make setup"
    return None


def cc65_required():
    """True in required CI (set by `make test`, and by the `integration-test` target that `make test-real-core` runs): a missing cc65 is a FAILURE, never a SKIP."""
    return os.environ.get(REQUIRE_CC65_ENV) == "1"


def _cc65_gate():
    reason = cc65_skip_reason()
    if reason is None:
        return
    if cc65_required():
        raise AssertionError(f"REQUIRED tool missing (not skipped, {REQUIRE_CC65_ENV}=1): {reason}")
    import unittest  # imported here so the build tools never need unittest at import time
    raise unittest.SkipTest(f"SKIP cc65-dependent: {reason}")


def requires_cc65(target):
    """Gate one cc65-dependent test method, or a whole TestCase class (per-test SKIP/FAIL, never silent).

    For a class the fixture is not built when cc65 is missing, so only tests that need it are affected;
    each test then reports its own skip (or failure in required mode) instead of one class-level skip.
    """
    if not isinstance(target, type):
        @functools.wraps(target)
        def gated(*args, **kwargs):
            _cc65_gate()
            return target(*args, **kwargs)
        return gated

    setup, teardown = target.setUpClass, target.tearDownClass

    def setUpClass(cls):
        if cc65_skip_reason() is None:
            setup.__func__(cls)

    def tearDownClass(cls):
        if cc65_skip_reason() is None:
            teardown.__func__(cls)

    target.setUpClass = classmethod(setUpClass)
    target.tearDownClass = classmethod(tearDownClass)
    for name, value in list(vars(target).items()):
        if name.startswith("test") and callable(value):
            setattr(target, name, requires_cc65(value))
    return target


def core_path():
    path = Path(os.environ.get("GEARGRAFX_CORE", str(
        dependency_dir("geargrafx") / "platforms/libretro/geargrafx_libretro.so")))
    if not path.is_file():
        raise ValueError(f"missing Geargrafx core: {path}; run make setup")
    return path.resolve()
