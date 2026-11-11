"""Issue #719 (P04 of #473): WRAM positive + deny/allow bidirectional safety regression.

Host-only. No emulator, BIOS, ROM, reference JSR, or ./rom/ content is read.
This test pins the WRAM allow path (MPR1=$F8) against the deny path
(MPR1=$FF / I/O crossing) in both directions, without duplicating the
gap-only synthesis fixture from #718 (tests/fixtures/
huc6280_adt_synthesis.json + tests/host/test_718_*) or any driver
verdict engine.

Reuse (by call/reference, never by copy):
- tools/mpr1_stack_contract.py (#497): the allow/deny/UNDETERMINED/BLOCKED
  verdicts (check_stack_bank, check_initial_state, check_mpr1_write,
  judge_irq_window). This file calls them; it does not reimplement them.
- tests/integration/geargrafx_caller_stack_contract.py (#481): the
  CASE_RAM positive judge (judge_ram over _ram_records) and its injected
  negative. Proves the $F8 WRAM positive still passes and a stray write
  still fails, through the existing judge.
- tests/integration/geargrafx_block_write_guard.py (#470): the stackwram
  positive constants and case family (MPR $F8/$F8, STACK_WRAM_LANDED,
  restored A/X/Y). Proves the WRAM stack control is still the allow side.
- tests/integration/geargrafx_stack_s5.py / geargrafx_stack_s6.py
  (#647/#648): the driver case families cited by the ledger's `reuses`
  pointers (the S5/S6 runs own the real-core evidence; this file reruns
  nothing).
- #718 row ids appear only in `cites718` reference strings so a reviewer
  can cross-check the pairing; the 718 JSON is not copied here.

A failure means the WRAM contract drifted (an allow turned into a deny,
a deny turned into an allow, a refusal shows a device side effect, or an
unobserved state was read as safe). It is NOT evidence that any mechanism
is safe for production use.
"""
import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests" / "integration"))

import mpr1_stack_contract as C  # noqa: E402
import geargrafx_block_write_guard as W  # noqa: E402
import geargrafx_caller_stack_contract as P481  # noqa: E402
import geargrafx_stack_s5 as S5  # noqa: E402
import geargrafx_stack_s6 as S6  # noqa: E402

DOC = ROOT / "docs" / "719-wram-deny-allow.md"

# Ledger under test: WRAM allow vs deny, each row independently asserted.
# verdict: ALLOW (may proceed) | REFUSE (denied before the side effect) |
#          BLOCKED (no evidence / unobserved; never read as safe).
# device.* are synthetic observations attached to the row: a REFUSE row with
# a device side effect is a test failure, not a "safe refusal".
LEDGER = [
    {
        "id": "719-wram-stack-allow",
        "polarity": "POSITIVE",
        "mpr1": 0xF8,
        "kind": "stack",
        "crossing": False,
        "observed": True,
        "synthetic_expect": {
            "verdict": "ALLOW",
            "resume_prohibited": False,
            "reason": "MPR1=$F8 WRAM stack: contract PASS; bytes land in WRAM, VDC untouched",
        },
        "device": {"io_reads": 0, "io_writes": 0, "vdc_changed": False},
        "cites718": ["718-tam02f8-pos", "718-xnoself-pos"],
        "reuses": ["#481 CASE_RAM judge_ram", "#470 stackwram-armed", "#497 check_stack_bank $F8"],
    },
    {
        "id": "719-unrelated-allow",
        "polarity": "POSITIVE",
        "mpr1": 0xF8,
        "kind": "unrelated",
        "crossing": False,
        "observed": True,
        "synthetic_expect": {
            "verdict": "ALLOW",
            "resume_prohibited": False,
            "reason": "unrelated (non-stack, non-transfer) op under $F8: no over-refusal",
        },
        "device": {"io_reads": 0, "io_writes": 0, "vdc_changed": False},
        "cites718": ["718-tam02f8-pos (no false positive companion)"],
        "reuses": ["#497 allow table ($F8 only, unrelated ops unaffected)"],
    },
    {
        "id": "719-io-stack-deny",
        "polarity": "NEGATIVE",
        "mpr1": 0xFF,
        "kind": "stack",
        "crossing": False,
        "observed": True,
        "synthetic_expect": {
            "verdict": "REFUSE",
            "resume_prohibited": True,
            "reason": "MPR1=$FF I/O stack page: contract FAIL; refused before the push side effect",
        },
        "device": {"io_reads": 0, "io_writes": 0, "vdc_changed": False},
        "cites718": ["718-tam02ff-neg"],
        "reuses": ["#497 check_stack_bank $FF", "#470 stackff-armed (differential: pops read VDC)"],
    },
    {
        "id": "719-io-xfer-deny",
        "polarity": "NEGATIVE",
        "mpr1": 0xF8,
        "kind": "transfer",
        "crossing": True,
        "observed": True,
        "synthetic_expect": {
            "verdict": "REFUSE",
            "resume_prohibited": True,
            "reason": "MPR1=$F8 but src/dst crosses the I/O page: A passes, T refuses pre-start; 0 bytes moved",
        },
        "device": {"io_reads": 0, "io_writes": 0, "vdc_changed": False},
        "cites718": ["718-tii-dstio-neg", "718-tin-srcio-neg", "718-tdd-down-neg", "718-tia-alt-neg"],
        "reuses": ["S6 pre-start range check (before_TransferStart, 0 bytes written)"],
    },
    {
        "id": "719-undetermined-blocked",
        "polarity": "NEGATIVE",
        "mpr1": None,
        "kind": "stack",
        "crossing": False,
        "observed": False,
        "synthetic_expect": {
            "verdict": "BLOCKED",
            "resume_prohibited": True,
            "reason": "MPR1 UNDETERMINED (reset value never trusted, no harness init): never ALLOW",
        },
        "device": {"io_reads": None, "io_writes": None, "vdc_changed": None},
        "cites718": [],
        "reuses": ["#497 RESET UNDETERMINED + check_initial_state BLOCKED"],
    },
    {
        "id": "719-unobserved-blocked",
        "polarity": "NEGATIVE",
        "mpr1": 0xF8,
        "kind": "stack",
        "crossing": False,
        "observed": False,
        "synthetic_expect": {
            "verdict": "BLOCKED",
            "resume_prohibited": True,
            "reason": "IRQ state unobserved in an MPR-changing window: missing evidence, never PASS",
        },
        "device": {"io_reads": None, "io_writes": None, "vdc_changed": None},
        "cites718": ["718-irq-pending-neg (D-owned hole discriminant)"],
        "reuses": ["#497 judge_irq_window IRQ_UNKNOWN -> BLOCKED"],
    },
]

ALLOWED_VERDICTS = {"ALLOW", "REFUSE", "BLOCKED"}


def by_id():
    return {row["id"]: row for row in LEDGER}


def judge_row(row):
    """Independent per-row judge: returns the verdict or raises ValueError.

    Rules (fail-closed):
    - unobserved (mpr1 None or observed False or device None) -> BLOCKED only.
    - ALLOW requires mpr1 == $F8, no crossing, observed, zero device effects,
      resume permitted.
    - REFUSE requires a deny ground (mpr1 != $F8, or a transfer crossing),
      zero device effects, resume prohibited.
    - ALLOW and REFUSE must agree with tools/mpr1_stack_contract on the
      bank question, except a transfer crossing which is T-owned: the bank
      check passes ($F8) while the ledger still refuses.
    """
    row = copy.deepcopy(row)
    se, dev = row["synthetic_expect"], row["device"]
    if se["verdict"] not in ALLOWED_VERDICTS:
        raise ValueError(f"{row['id']}: unknown verdict {se['verdict']!r}")
    unobserved = row["mpr1"] is None or not row["observed"] or \
        dev.get("io_reads") is None or dev.get("io_writes") is None or dev.get("vdc_changed") is None
    if unobserved:
        if se["verdict"] != "BLOCKED" or not se["resume_prohibited"]:
            raise ValueError(f"{row['id']}: unobserved state must be BLOCKED with no resume")
        return "BLOCKED"
    if row["mpr1"] is None:
        raise ValueError(f"{row['id']}: observed row with MPR1 None")
    bank_verdict, _ = C.check_stack_bank(row["mpr1"])
    if se["verdict"] == "ALLOW":
        if row["mpr1"] != 0xF8 or row["crossing"]:
            raise ValueError(f"{row['id']}: ALLOW needs $F8 without crossing")
        if bank_verdict != C.PASS:
            raise ValueError(f"{row['id']}: ALLOW disagrees with the bank contract ({bank_verdict})")
        if dev["io_reads"] != 0 or dev["io_writes"] != 0 or dev["vdc_changed"]:
            raise ValueError(f"{row['id']}: ALLOW row shows a device side effect")
        if se["resume_prohibited"]:
            raise ValueError(f"{row['id']}: ALLOW row must not prohibit resume")
        return "ALLOW"
    if se["verdict"] == "REFUSE":
        denied_by_bank = bank_verdict == C.FAIL
        denied_by_transfer = row["kind"] == "transfer" and row["crossing"]
        if not (denied_by_bank or denied_by_transfer):
            raise ValueError(f"{row['id']}: REFUSE has no deny ground (bank {bank_verdict}, crossing {row['crossing']})")
        if dev["io_reads"] != 0 or dev["io_writes"] != 0 or dev["vdc_changed"]:
            raise ValueError(f"{row['id']}: refused row shows a device side effect")
        if not se["resume_prohibited"]:
            raise ValueError(f"{row['id']}: refused row must prohibit resume")
        return "REFUSE"
    # BLOCKED with full observation is not used by this ledger; unobserved
    # BLOCKED rows are handled above.
    raise ValueError(f"{row['id']}: observed BLOCKED row is not part of this ledger")


class LedgerShapeTests(unittest.TestCase):
    def test_ids_unique_and_fields_present(self):
        ids = [r["id"] for r in LEDGER]
        self.assertEqual(len(ids), len(set(ids)))
        for r in LEDGER:
            for k in ("id", "polarity", "mpr1", "kind", "crossing", "observed",
                      "synthetic_expect", "device", "cites718", "reuses"):
                self.assertIn(k, r, r["id"])
            self.assertIn(r["polarity"], {"POSITIVE", "NEGATIVE"}, r["id"])
            self.assertIn(r["kind"], {"stack", "transfer", "unrelated"}, r["id"])
            for k in ("verdict", "resume_prohibited", "reason"):
                self.assertIn(k, r["synthetic_expect"], r["id"])
            for k in ("io_reads", "io_writes", "vdc_changed"):
                self.assertIn(k, r["device"], r["id"])
            self.assertTrue(r["reuses"], r["id"])

    def test_every_row_judges_to_its_declared_verdict(self):
        for r in LEDGER:
            with self.subTest(row=r["id"]):
                self.assertEqual(judge_row(r), r["synthetic_expect"]["verdict"])

    def test_pairs_contrast_in_both_directions(self):
        table = by_id()
        allow = table["719-wram-stack-allow"]
        deny_bank = table["719-io-stack-deny"]
        deny_xfer = table["719-io-xfer-deny"]
        unrelated = table["719-unrelated-allow"]
        self.assertEqual(judge_row(allow), "ALLOW")
        self.assertEqual(judge_row(deny_bank), "REFUSE")
        self.assertEqual(judge_row(deny_xfer), "REFUSE")
        self.assertEqual(judge_row(unrelated), "ALLOW")
        # The bank deny and the transfer deny are independently grounded:
        # one fails the $F8 bank check, the other passes the bank check and
        # is refused by the crossing flag (T-owned).
        self.assertEqual(C.check_stack_bank(deny_bank["mpr1"])[0], C.FAIL)
        self.assertEqual(C.check_stack_bank(deny_xfer["mpr1"])[0], C.PASS)
        self.assertTrue(deny_xfer["crossing"])


class ContractAgreementTests(unittest.TestCase):
    def test_only_f8_is_allowed(self):
        for value in range(256):
            with self.subTest(value=value):
                verdict, _ = C.check_stack_bank(value)
                self.assertEqual(verdict, C.PASS if value == 0xF8 else C.FAIL)

    def test_tam_and_direct_setmpr_paths(self):
        self.assertEqual(C.check_mpr1_write(C.PATH_TAM, 0xF8, 0)[0], C.PASS)
        self.assertEqual(C.check_mpr1_write(C.PATH_TAM, 0xFF, 0)[0], C.FAIL)
        self.assertEqual(C.check_mpr1_write(C.PATH_DIRECT_SETMPR, 0xF8, 0)[0], C.PASS)
        # Mid-run direct SetMpr is deny even for $F8 (owner-confirmed choice).
        self.assertEqual(C.check_mpr1_write(C.PATH_DIRECT_SETMPR, 0xF8, 1)[0], C.FAIL)
        self.assertEqual(C.check_mpr1_write(C.PATH_RESET, 0xF8, 0)[0], C.BLOCKED)

    def test_case_ram_positive_still_passes(self):
        failing = {c["check"] for c in P481.judge_ram(P481._ram_records()) if not c["ok"]}
        self.assertEqual(failing, set())

    def test_case_ram_injected_negative_still_fails(self):
        bad = P481._ram_records(extra_wrw=[{"step": 14, "pc": 0xE01A, "wram": 0x100, "old": 0, "new": 0xAA}])
        failing = {c["check"] for c in P481.judge_ram(bad) if not c["ok"]}
        self.assertIn("stack_writes_in_wram", failing)

    def test_stackwram_positive_is_still_the_allow_side(self):
        self.assertEqual(W._expected_mpr("stackwram"), (0xF8, 0xF8))
        self.assertEqual(W._expected_mpr("stackff"), (0xF8, 0xFF))
        self.assertEqual(W.STACK_WRAM_LANDED, [0x34, 0x12, 0x07])
        self.assertEqual((W.STACK_RESTORED_X, W.STACK_RESTORED_A, W.STACK_RESTORED_Y), (0x34, 0x12, 0x07))
        cases = set(W._cases())
        self.assertIn("tii-stackwram-armed", cases)
        self.assertIn("tii-stackff-armed", cases)


class NoSideEffectTests(unittest.TestCase):
    def test_refusal_rows_show_zero_device_effects(self):
        for r in LEDGER:
            if r["synthetic_expect"]["verdict"] != "REFUSE":
                continue
            with self.subTest(row=r["id"]):
                self.assertEqual(r["device"]["io_reads"], 0)
                self.assertEqual(r["device"]["io_writes"], 0)
                self.assertFalse(r["device"]["vdc_changed"])
                self.assertTrue(r["synthetic_expect"]["resume_prohibited"])

    def test_allow_rows_show_zero_device_effects_and_permit_resume(self):
        for r in LEDGER:
            if r["synthetic_expect"]["verdict"] != "ALLOW":
                continue
            with self.subTest(row=r["id"]):
                self.assertEqual(r["device"]["io_reads"], 0)
                self.assertEqual(r["device"]["io_writes"], 0)
                self.assertFalse(r["device"]["vdc_changed"])
                self.assertFalse(r["synthetic_expect"]["resume_prohibited"])


class UndeterminedTests(unittest.TestCase):
    def test_reset_value_is_undetermined(self):
        self.assertIsNone(C.RESET_MPR1)

    def test_undetermined_mpr1_never_allows(self):
        self.assertEqual(C.check_stack_bank(None)[0], C.BLOCKED)
        self.assertEqual(C.check_initial_state(None, None, 0)[0], C.BLOCKED)
        # Even a reset reading of $F8 without a recorded harness init is BLOCKED.
        self.assertEqual(C.check_initial_state(0xF8, None, 0)[0], C.BLOCKED)
        row = by_id()["719-undetermined-blocked"]
        self.assertEqual(judge_row(row), "BLOCKED")
        mutated = copy.deepcopy(row)
        mutated["synthetic_expect"] = dict(mutated["synthetic_expect"], verdict="ALLOW",
                                           resume_prohibited=False)
        with self.assertRaises(ValueError):
            judge_row(mutated)

    def test_unobserved_irq_never_passes(self):
        self.assertEqual(C.judge_irq_window(True, C.IRQ_UNKNOWN, C.IRQ_UNKNOWN)[0], C.BLOCKED)
        self.assertEqual(C.judge_irq_window(False, C.IRQ_UNKNOWN, C.IRQ_UNKNOWN)[0], C.BLOCKED)
        row = by_id()["719-unobserved-blocked"]
        self.assertEqual(judge_row(row), "BLOCKED")
        mutated = copy.deepcopy(row)
        mutated["synthetic_expect"] = dict(mutated["synthetic_expect"], verdict="ALLOW",
                                           resume_prohibited=False)
        mutated["device"] = {"io_reads": 0, "io_writes": 0, "vdc_changed": False}
        mutated["observed"] = True
        mutated["mpr1"] = 0xF8
        # Flipping an unobserved row to observed+ALLOW changes its identity:
        # the judge accepts the new shape, so the guard is the shape itself.
        self.assertEqual(judge_row(mutated), "ALLOW")


class MutationTests(unittest.TestCase):
    def test_flipped_verdicts_are_rejected(self):
        for r in LEDGER:
            if r["synthetic_expect"]["verdict"] == "ALLOW":
                mutated = copy.deepcopy(r)
                mutated["synthetic_expect"] = dict(mutated["synthetic_expect"], verdict="REFUSE",
                                                   resume_prohibited=True)
                # An ALLOW-shaped row (no deny ground) cannot judge as REFUSE.
                with self.subTest(row=r["id"]):
                    with self.assertRaises(ValueError):
                        judge_row(mutated)
            elif r["synthetic_expect"]["verdict"] == "REFUSE":
                mutated = copy.deepcopy(r)
                mutated["synthetic_expect"] = dict(mutated["synthetic_expect"], verdict="ALLOW",
                                                   resume_prohibited=False)
                with self.subTest(row=r["id"]):
                    if r["mpr1"] != 0xF8 or r["crossing"]:
                        with self.assertRaises(ValueError):
                            judge_row(mutated)
                    else:  # pragma: no cover - no such REFUSE row exists in this ledger
                        self.fail(f"{r['id']}: REFUSE row without a deny ground")

    def test_injected_device_effect_is_rejected(self):
        for r in LEDGER:
            if r["device"].get("io_reads") is None:
                continue
            for field in ("io_reads", "io_writes"):
                mutated = copy.deepcopy(r)
                mutated["device"] = dict(mutated["device"], **{field: 1})
                with self.subTest(row=r["id"], field=field):
                    with self.assertRaises(ValueError):
                        judge_row(mutated)
            mutated = copy.deepcopy(r)
            mutated["device"] = dict(mutated["device"], vdc_changed=True)
            with self.subTest(row=r["id"], field="vdc_changed"):
                with self.assertRaises(ValueError):
                    judge_row(mutated)

    def test_resume_flip_is_rejected(self):
        for r in LEDGER:
            mutated = copy.deepcopy(r)
            flipped = not r["synthetic_expect"]["resume_prohibited"]
            mutated["synthetic_expect"] = dict(mutated["synthetic_expect"], resume_prohibited=flipped)
            with self.subTest(row=r["id"]):
                with self.assertRaises(ValueError):
                    judge_row(mutated)


class ReuseTests(unittest.TestCase):
    def test_reuse_pointers_match_live_driver_families(self):
        s5, s6 = set(S5.cases()), set(S6.cases())
        self.assertTrue(any("tamcode" in c for c in s5), "S5 tamcode family")
        self.assertIn("AD:tamcode-armed", s5)
        self.assertIn("AT:xself-armed", s6)
        self.assertIn("AT:xlen0-armed", s6)
        self.assertIn("AT:xtiim-armed", s6)
        wcases = set(W._cases())
        self.assertIn("tii-stackwram-armed", wcases)
        p481_cases = {c["name"] for c in P481.CASES}
        self.assertIn("ram_contract", p481_cases)
        # Every ledger row names at least one live reuse family.
        pools = {"S5": s5, "S6": s6, "stackwram": wcases, "CASE": p481_cases,
                 "#497": {"check_stack_bank"}, "S6 pre-start": {"pre-start"}}
        for r in LEDGER:
            joined = " ".join(r["reuses"])
            with self.subTest(row=r["id"]):
                self.assertTrue(any(tag in joined for tag in pools), r["id"])

    def test_no_driver_logic_duplicated(self):
        text = Path(__file__).read_text(encoding="utf-8")
        # Tokens are built by concatenation so this guard does not match its own source.
        tokens = ["def " + "check_stack_bank", "def " + "judge_ram",
                  "def " + "check_stackwram_armed", "gg" + "_verify",
                  "m_transfer" + "_state"]
        for token in tokens:
            self.assertNotIn(token, text)

    def test_doc_exists_and_states_host_only_limits(self):
        text = DOC.read_text(encoding="utf-8")
        for s in ("host-only", "NOT_RUN", "release_eligible=false", "0/81",
                  "719-wram-stack-allow", "719-io-stack-deny", "719-undetermined-blocked"):
            self.assertIn(s, text)


if __name__ == "__main__":
    unittest.main()
