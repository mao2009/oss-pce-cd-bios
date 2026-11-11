"""Issue #725: fail-closed release invariants stay mechanically verified.

Host-only. No emulator, no BIOS, no ROM, no private ``./rom/`` input is read
or executed. Real-core / commercial-media behaviour is NOT_RUN here; the only
claim is that the safety gates are still closed and that synthetic PASS,
doc-only merges and host-test PASS cannot promote anything.

Covers the #725 acceptance criteria:

* ``make release`` stays BLOCKED (static Makefile contract first; the recipe
  is executed only after the static contract already proved it is the
  ``echo BLOCKED + exit 1`` stub, so a lifted gate can never be executed
  blindly).
* ``release_eligible=false`` everywhere it is recorded.
* DEV_CALLABLE 0, owner approval 0, ABI VERIFIED 0/81, reference JSR 0/81.
* Promotion negatives: well-formed approvals, synthetic PASS markers and
  host PASS still grant no execution.
* Undecidable inputs fail closed (malformed ledgers refused, no ALLOWED path).
* Future unlocks need independent review plus Owner re-approval (documented
  in docs/725-failclosed-invariants.md; no code path unlocks today).
"""
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import failclosed_gate as gate  # noqa: E402
from release_makefile_guard import check_release_makefile  # noqa: E402

DOC = ROOT / "docs" / "725-failclosed-invariants.md"


class MakefileReleaseContract(unittest.TestCase):
    def test_static_contract_is_fail_closed(self):
        self.assertEqual(gate.check_makefile_release(), [])

    def test_release_recipe_text(self):
        text = (ROOT / "Makefile").read_text(encoding="utf-8")
        match = re.search(r"^release:\n((?:\t.*\n?)+)", text, re.M)
        self.assertIsNotNone(match, "no 'release:' target")
        recipe = match.group(1)
        self.assertIn("BLOCKED", recipe)
        self.assertRegex(recipe, r"exit\s+1\b")

    def test_make_release_refuses(self):
        # Executed only because the static contract above already proved the
        # recipe is the harmless echo+exit-1 stub. A lifted gate must fail the
        # static check first and never reach this subprocess.
        self.assertEqual(gate.check_makefile_release(), [])
        proc = subprocess.run(["make", "-C", str(ROOT), "release"],
                              capture_output=True, text=True, timeout=60)
        self.assertNotEqual(proc.returncode, 0, "make release must refuse")
        self.assertRegex(proc.stderr + proc.stdout, r"BLOCKED")


class ReleaseEligibleFalse(unittest.TestCase):
    def test_gate_check(self):
        self.assertEqual(gate.check_spec_release_eligible(), [])


class DevCallableZero(unittest.TestCase):
    def test_gate_check(self):
        self.assertEqual(gate.check_dev_callable_zero(), [])


class AbiVerifiedZero(unittest.TestCase):
    def test_gate_check(self):
        self.assertEqual(gate.check_abi_verified_zero(), [])

    def test_no_verified_in_dev_ledger_rows(self):
        ledger = json.loads((ROOT / "spec" / "dev_implementations.json").read_text(encoding="utf-8"))
        for entry in ledger["slots"]:
            self.assertNotEqual(entry.get("state"), "DEV_CALLABLE")
            contract = entry.get("abi_contract") or {}
            self.assertNotEqual(contract.get("status"), "VERIFIED")


class ReferenceJsrClosed(unittest.TestCase):
    def test_gate_check(self):
        self.assertEqual(gate.check_reference_jsr_closed(), [])

    def test_cli_reports_closed_81_of_81(self):
        proc = subprocess.run([sys.executable, str(ROOT / "tools" / "reference_jsr_gate.py"), "--check"],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 3, "JSR gate CLI must exit BLOCKED (3)")
        self.assertRegex(proc.stdout + proc.stderr, r"81/81")
        self.assertIn("jsr_count=0", proc.stdout + proc.stderr)

    def test_release_gate_cli_refuses(self):
        proc = subprocess.run([sys.executable, str(ROOT / "tools" / "build_rom.py"), "--release-gate"],
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 3)
        self.assertRegex(proc.stderr, r"^BLOCKED:")


class PromotionNegatives(unittest.TestCase):
    def test_gate_check(self):
        self.assertEqual(gate.check_promotion_negatives(), [])

    def test_synthetic_pass_markers_grant_nothing(self):
        from reference_jsr_gate import is_execution_permitted
        for marker in ("PASS", "SYNTHETIC PASS", "host PASS", "doc-only merge",
                       "MATERIALS_COMPLETE", "allowed", " ALLOWED "):
            self.assertFalse(is_execution_permitted(marker),
                             f"{marker!r} must never permit execution")


class UndecidableFailClosed(unittest.TestCase):
    def test_gate_check(self):
        self.assertEqual(gate.check_undecidable_fail_closed(), [])

    def test_gate_has_no_unlock_path(self):
        source = (ROOT / "tools" / "failclosed_gate.py").read_text(encoding="utf-8")
        self.assertNotIn('"release_eligible": True', source)
        self.assertNotIn("release_eligible=True", source)
        self.assertNotIn('return "ALLOWED"', source)
        # The gate reads ledgers; it never writes one.
        self.assertNotIn("write_text", source)

    def test_release_gate_module_never_passes(self):
        source = (ROOT / "tools" / "build_rom.py").read_text(encoding="utf-8")
        block = source[source.index("def release_gate"):]
        block = block[:block.index("\n\ndef ") if "\n\ndef " in block else len(block)]
        self.assertNotIn("return None", block)
        self.assertIn("raise ReleaseBlocked", block)


class FutureUnlockDocumented(unittest.TestCase):
    def test_doc_exists_and_requires_review_and_owner(self):
        self.assertTrue(DOC.is_file(), "docs/725-failclosed-invariants.md is required")
        text = DOC.read_text(encoding="utf-8")
        for required in ("independent review", "Owner", "release_eligible=false",
                         "BLOCKED", "fail-closed", "725"):
            self.assertIn(required, text)
        self.assertNotIn("release_eligible=true", text)


class MakefileEvaluationTimeSideEffects(unittest.TestCase):
    """Parse/evaluation-time command execution must fail closed (#769 MAJOR).

    Static only: mutant Makefiles live in temp dirs and are never executed.
    Each mutant appends evaluation-time shell/file/call/eval payloads to the
    real Makefile text; the shared guard must refuse all of them while the
    real tree stays GREEN.
    """
    ATTACKS = {
        "immediate-shell":
            "SIDE_EFFECT := $(shell printf SYNTHETIC_PRE_RELEASE)\n",
        "shell-assign-op":
            "SIDE_EFFECT != printf SYNTHETIC_PRE_RELEASE\n",
        "file-write":
            "SIDE_EFFECT := $(file >synthetic.txt,hello)\n",
        "call-indirect":
            "SIDE_EFFECT := $(call do_thing,arg)\n",
        "hidden-via-var":
            "HIDDEN := $(shell printf X)\nOTHER := prefix$(HIDDEN)\n",
        "prereq-position":
            "preflight: $(shell printf X)\n\t@printf ok\n",
        "deferred-shell":
            "LAZY = $(shell printf X)\n",
        "brace-form":
            "SIDE_EFFECT := ${shell printf X}\n",
        "second-expansion":
            ".SECONDEXPANSION:\n",
        "backtick-outside-recipe":
            "SIDE_EFFECT := `printf X`\n",
        "export-immediate":
            "export SIDE_EFFECT := $(shell printf X)\n",
        "override-deferred-call":
            "override SIDE_EFFECT = $(call f)\n",
        "append-immediate":
            "SIDE_EFFECT += $(shell printf X)\n",
        "cond-assign":
            "SIDE_EFFECT ?= $(shell printf X)\n",
        "eval-split-continuation":
            "X := $(ev\\\nal X)\n",
        "tab-continued-value":
            "X := foo \\\n\t$(shell printf X)\n",
        "call-in-prereq":
            "other: $(call get_deps)\n\t@true\n",
    }
    LEGIT = {
        "escaped-dollar": "MSG = price is $$5\n",
        "plain-refs": "CMD = $(PYTHON) $(MAKE) $(MODE)\n",
        "pure-funcs": ("BASE := $(strip $(VAR))\n"
                         "FILES = $(wildcard src/*.s)\n"),
        "info-output": "$(info build starting)\n",
    }

    def _check_on_mutant(self, addition):
        import tempfile
        base = (ROOT / "Makefile").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            (tmp / "Makefile").write_text(base + addition,
                                            encoding="utf-8")
            return check_release_makefile(tmp)

    def test_evaluation_time_attacks_fail_closed(self):
        for name, addition in self.ATTACKS.items():
            with self.subTest(mutant=name):
                self.assertTrue(self._check_on_mutant(addition),
                                f"evaluation-time mutant passed: {name}")

    def test_legit_non_recipe_content_stays_green(self):
        for name, addition in self.LEGIT.items():
            with self.subTest(mutant=name):
                self.assertEqual(self._check_on_mutant(addition), [],
                                 f"legit content refused: {name}")


class WholeGate(unittest.TestCase):
    def test_all_invariants_hold(self):
        ok, results = gate.run_all()
        self.assertTrue(ok, f"fail-closed invariants broken: {results}")
        self.assertEqual(gate.check_release_gate_refuses(), [])


if __name__ == "__main__":
    unittest.main()
