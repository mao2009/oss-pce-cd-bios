"""Issue #518: cc65 common.a error reproducibility record (regression lock).

PR #509/#510 reported `../wrk/common/common.a: file format not recognized`
during `Prepare pinned tools from source` (single CI observation, 2026-10-23).
W09 tried 6 clean builds (-j1/-j2 x3) on ec496e9: all exit 0, NOT_REPRODUCED
for a single make invocation. The remaining hypothesis (two make processes on
the same wrk/) was never tried. This test locks in three things:

1. `tools/setup.py` still serializes the cc65 build (`-j1`), so the known
   intra-make archive race cannot be reintroduced by a parallel flag.
2. `tools/setup.py` still serializes concurrent setups with the exclusive
   `.setup.lock` (flock), so two `make setup` on one TOOLS_DIR cannot run
   the cc65 build concurrently through the supported entrypoint.
3. `docs/build-cc65-common-a-repro-518.md` records the NOT_REPRODUCED
   finding with its exact trial conditions, and explicitly lists what was
   NOT tried. No toolchain fix is claimed.

Host-only, offline, read-only: never touches the shared TOOLS_DIR, never
rebuilds tools, never needs cc65 binaries.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SETUP = ROOT / "tools" / "setup.py"
RECORD = ROOT / "docs" / "build-cc65-common-a-repro-518.md"


class Cc65CommonARepro518Tests(unittest.TestCase):
    def test_setup_serializes_cc65_build(self):
        text = SETUP.read_text(encoding="utf-8")
        # The cc65 build invocation must use the literal -j1 (PR #543 mitigation
        # for Issue #542). A parallel flag here would reintroduce the intra-make
        # archive race observed in CI (ar writing common.a while ld links it).
        # Scope to the cc65 branch inside main() (code structure, not comment
        # wording, so rewording the comment alone does not break this test).
        main_text = text[text.index("def main("):]
        branch = 'if name == "cc65":'
        self.assertIn(branch, main_text)
        cc65_block = main_text[main_text.index(branch):main_text.index(branch) + 800]
        self.assertIn('"-j1"', cc65_block,
                      "tools/setup.py must build cc65 with -j1 (serial)")
        self.assertNotIn("-j{jobs}", cc65_block,
                         "tools/setup.py must not use the parallel jobs flag for cc65")
        self.assertNotIn('"-j2"', cc65_block,
                         "tools/setup.py must not hard-code a parallel flag for cc65")
        for target in ('"ca65"', '"ld65"', '"da65"', '"cc65"'):
            self.assertIn(target, cc65_block)

    def test_setup_holds_exclusive_lock(self):
        text = SETUP.read_text(encoding="utf-8")
        # Concurrent `make setup` on one TOOLS_DIR must serialize: the supported
        # entrypoint holds LOCK_EX on .setup.lock before fetch/build. Without it,
        # two make processes could share one wrk/ (the untested hypothesis).
        self.assertIn(".setup.lock", text)
        self.assertIn("LOCK_EX", text)
        lock_pos = text.index("LOCK_EX")
        fetch_pos = text.index("for name in names:")
        self.assertLess(lock_pos, fetch_pos,
                        "exclusive lock must be acquired before any fetch/build")

    def test_repro_record_exists_and_states_not_reproduced(self):
        self.assertTrue(RECORD.is_file(),
                        "docs/build-cc65-common-a-repro-518.md must record the Issue #518 finding")
        text = RECORD.read_text(encoding="utf-8")
        for marker in ("#518", "NOT_REPRODUCED", "common.a",
                       "71746c829e77f74c2b601a7d16821170c2610df3",
                       "-j1", "-j2", "TOOLS_DIR",
                       "concurrent", "NOT tried",
                       "No toolchain change"):
            self.assertIn(marker, text, f"repro record must contain {marker!r}")
        # The record must not claim a fix or a verified root cause.
        for banned in ("root cause confirmed", "fixed by this",
                       "VERIFIED fix", "reproducible: yes"):
            self.assertNotIn(banned, text)

    def test_repro_record_cites_single_make_scope(self):
        # The NOT_REPRODUCED verdict covers only a single make invocation per
        # clean tree. Two makes sharing one wrk/ is explicitly out of scope.
        text = RECORD.read_text(encoding="utf-8")
        self.assertIn("single make", text)
        self.assertRegex(text, re.compile(r"same `?wrk", re.IGNORECASE))


if __name__ == "__main__":
    unittest.main()
