"""Issue #696 MINOR-2: pin the default 256 KiB IPL-loader image with an automated test.

docs/635-public-ipl-format.md stated the default (DEV) build was byte-identical
to the previous loader based on a ONE-TIME manual SHA-256 comparison, with no
automated test holding that property. This module rebuilds the 256 KiB default
image from a clean checkout and compares its SHA-256 against the pinned value
below, so a silent change to the default build fails closed.

Baseline provenance (how EXPECTED_DEFAULT_SHA256 was obtained):
- origin commit: 0f7a3257c4ee20c67400cc287438d225248d19e1 (origin/main, 2026-11-18)
- procedure: `make setup`, then `python3 tools/build_rom.py --mode debug`,
  then `build_ipl_loader_bios(original, out, sel=0x0109, mode="debug",
  variant="main", defines=())` with no extra defines (the default DEV build).
- SEL 0x0109 is the synthetic INDEX 01 LBA shared with
  tests/integration/geargrafx_ipl_loader.py (no commercial media, no BIOS bytes).
- pinned cc65 revision 71746c829e77f74c2b601a7d16821170c2610df3
  (tools/dependencies.json); the builder refuses any other toolchain.
- observed value: b70aa19e922cd111666a8aac08990eaf7605f3ae56bdfe86dd9b7abaabae0c53
  (reproduced by repeated rebuilds from the clean checkout, including fresh
  temporary directories; deterministic).

Scope notes:
- The pinned value covers the full 256 KiB image, i.e. the DEV API table plus
  the loader. An unrelated API-slot change therefore also moves this hash; that
  is intentional fail-closed behaviour, not a false alarm. Re-baseline only by
  rebuilding at a clean HEAD and recording the new commit plus procedure here;
  never edit the constant to match an unexplained build.
- NOT a compatibility claim: synthetic media only, NOT a System Card BIOS, NOT
  a BIOS ABI, release_eligible=false. API VERIFIED states are untouched.

Reference: pre-split base c41137e7 (main before PR #691) builds a different
full-image hash because the DEV API table and the loader both evolved since
(e.g. Issue #744); only the format-split commit itself kept the default build
byte-identical, as recorded in the PR. This test pins the current baseline and
guards all future default builds.
"""
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from build_dev_ipl_loader_bios import build_ipl_loader_bios  # noqa: E402
from build_rom import build as build_dev_rom  # noqa: E402
from environment import requires_cc65  # noqa: E402

SEL = 0x0109
MODE = "debug"
VARIANT = "main"

EXPECTED_DEFAULT_SHA256 = (
    "b70aa19e922cd111666a8aac08990eaf7605f3ae56bdfe86dd9b7abaabae0c53"
)

REBASELINE_HINT = (
    "default 256 KiB image hash moved; refusing to pass silently (Issue #696). "
    "If the change is intended, rebuild at a clean HEAD with "
    "`python3 tools/build_rom.py --mode debug` plus build_ipl_loader_bios("
    "original, out, sel=0x0109, mode='debug', variant='main'), record the new "
    "origin commit and procedure in this module, and update EXPECTED_DEFAULT_SHA256."
)


def _build_default_image(directory: Path, sel: int = SEL, defines=()):
    """Build the original DEV ROM and the loader image inside `directory`."""
    original = build_dev_rom(directory / "bios-dev", MODE)
    out = directory / "loader"
    rom, _symbols = build_ipl_loader_bios(original, out, sel, MODE, VARIANT, defines)
    data = rom.read_bytes()
    if len(data) != 262144:
        raise ValueError(f"loader image must be exactly 262,144 bytes, got {len(data)}")
    return data


@requires_cc65
class DefaultImageHashTest(unittest.TestCase):
    def test_default_image_matches_pinned_sha256(self):
        with tempfile.TemporaryDirectory(prefix="pce-696-default-hash-") as directory:
            data = _build_default_image(Path(directory))
            digest = hashlib.sha256(data).hexdigest()
            self.assertEqual(digest, EXPECTED_DEFAULT_SHA256, REBASELINE_HINT)

    def test_default_build_is_deterministic(self):
        with tempfile.TemporaryDirectory(prefix="pce-696-repro-a-") as first, \
                tempfile.TemporaryDirectory(prefix="pce-696-repro-b-") as second:
            digest_a = hashlib.sha256(_build_default_image(Path(first))).hexdigest()
            digest_b = hashlib.sha256(_build_default_image(Path(second))).hexdigest()
            self.assertEqual(digest_a, digest_b,
                             "same-source rebuilds must be byte-identical; "
                             "a flaky hash cannot guard the default build")
            self.assertEqual(digest_a, EXPECTED_DEFAULT_SHA256, REBASELINE_HINT)

    def test_public_build_differs_from_default_pin(self):
        # The golden must be configuration-specific: the PUBLIC format build
        # (IPL_PUBLIC) must NOT match the default pin. If this ever passes with
        # equality, the pin no longer distinguishes the default build.
        with tempfile.TemporaryDirectory(prefix="pce-696-public-") as directory:
            data = _build_default_image(Path(directory), defines=("-D", "IPL_PUBLIC"))
            self.assertEqual(len(data), 262144)
            self.assertNotEqual(hashlib.sha256(data).hexdigest(), EXPECTED_DEFAULT_SHA256,
                                "PUBLIC build must differ from the default pin")

    def test_different_sel_differs_from_default_pin(self):
        # The embedded synthetic INDEX 01 LBA is part of the image; a different
        # SEL must move the hash, proving the pin covers the embedded value.
        with tempfile.TemporaryDirectory(prefix="pce-696-sel-") as directory:
            data = _build_default_image(Path(directory), sel=0x010A)
            self.assertNotEqual(hashlib.sha256(data).hexdigest(), EXPECTED_DEFAULT_SHA256,
                                "SEL 0x010A image must differ from the SEL 0x0109 pin")


if __name__ == "__main__":
    unittest.main()
