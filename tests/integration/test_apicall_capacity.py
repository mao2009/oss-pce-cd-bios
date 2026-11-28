"""APICALL capacity tests for the API-entry observer probe (Issue #54, G5 P0).

The entry probe (`geargrafx_api_entry_probe.cpp`) records at most 8 APICALLs
per cycle: further payload->BIOS transfers set `apicall_truncated` and are
dropped silently (no record, no dropped-event count). The commercial ExpE
chain `$02->$33->$4A->$23->$25->$24->$36->$35->$2C` needs 9 records, so the
`$2C` entry detail was truncated (RUN X=`$2C` halt still confirmed).

This suite proves a bounded configurable capacity (`max_apicalls`, default 8,
allowed 1..64) with an additive `apicall_dropped` count in RUN, without
changing the default behavior:

- fewer than 8 calls: full order, no truncation, dropped 0 (default + raised cap)
- exactly 8 calls with default cap: full order, no truncation (boundary)
- 9 calls with default cap: 8 records, truncated, dropped 1 (RED control)
- 9/12/16 calls with raised cap: full order, no truncation, dropped 0
- over-cap (17 calls, cap 16): 16 records, truncated, dropped 1
- exact/overflow at both hard bounds: 1x1 full, 2x1 drops 1, 64x64 full,
  65x64 drops 1 (cap20x64 kept as the under-cap control)
- distinct-slot order: 10-call $4A/$25/$24/$36/$35 x2 pattern proves slot+pc
  order both cycles (identical-$35 repetition cannot catch a permutation)
- invalid capacities (0, 65, non-int) refused fail-closed (Python ValueError;
  direct C++ invocation exits exactly 2, with a valid-cap control at exit 0)
- truncation invariant: truncated is True iff dropped > 0, both cycles
- cold/warm identical event order on every run
- commercial mode stays RAM-clean (assert_commercial_clean) with the new key

NOT a compatible System Card BIOS. Synthetic media only, pinned core only
(Geargrafx b49ae82a, cc65 71746c8). No commercial media access here
(no ./rom probing in this file).
"""

import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "tests" / "integration")]

SEL = 0x109
TRACK_SEL = 2
BUDGET = 20_000_000


# Distinct safe order pattern: every slot below is "experimental" (returns via
# the preservation scaffold) in the cap35 G5 ROM built in setUpClass, so a
# multi-slot payload still returns cleanly to the cd_halt_ok halt.
SAFE_ORDER_SLOTS = (0x4A, 0x25, 0x24, 0x36, 0x35)


def entry_of_slot(slot):
    return 0xE000 + slot * 3


def payload_asm(ncalls, pattern=None):
    if pattern is None:
        calls = "\n".join(["    jsr $E09F"] * ncalls)
    else:
        seq = [pattern[i % len(pattern)] for i in range(ncalls)]
        calls = "\n".join([f"    jsr ${entry_of_slot(s):04X}" for s in seq])
    return f""".setcpu "huc6280"
.segment "CODE"
payload_entry:
    cld
    ldx #$FF
    txs
    lda #$F8
    tam #$01
{calls}
    lda #$AA
    sta res_base
payload_halt:
    bra payload_halt
res_base:
    .res 16
"""


class ApicallCapacitySynthetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from unittest.mock import patch
        import build_rom
        from build_ipl_loader_g5_2base import build_ipl_loader_g5_rom, parse_labels
        from geargrafx_api import compile_probe
        cls._tmp = tempfile.TemporaryDirectory(prefix="pce-apicap-")
        cls.out = Path(cls._tmp.name)
        dev_out = Path(cls._tmp.name) / "dev"
        with patch("sys.argv", ["build_rom.py", "--mode", "debug",
                                "--output", str(dev_out)]):
            assert build_rom.main() == 0
        orig = dev_out / "dev-only-not-compatible-syscard3.pce"
        assert orig.is_file()
        cls.normal = orig.read_bytes()
        # Full-experimental G5 ROM: $E09F ($35) returns via the preservation
        # scaffold, so repeated JSRs all return and the halt is clean.
        cls.rom, cls.syms = build_ipl_loader_g5_rom(
            orig, cls.out, SEL, "debug", "cap35", ["-D", "IPL_PUBLIC"],
            track=TRACK_SEL, private_input=False,
            cd_4a="experimental", cd_25="experimental",
            cd_24="experimental", cd_36="experimental",
            cd_35="experimental")
        if orig.read_bytes() != cls.normal:
            raise ValueError("BLOCKED: G5 build mutated the normal DEV ROM input")
        body = cls.syms["ex_sprdma_entry"]
        data = cls.rom.read_bytes()
        assert tuple(data[3 * 0x35:3 * 0x35 + 3]) == (0x4C, body & 0xFF, (body >> 8) & 0xFF)
        trace = cls.out / "ipl-loader-g5-cap35.lbl"
        cls.labels = parse_labels(
            trace, ("api_unimplemented", "cd_halt_fail", "cd_unexpected_interrupt"))
        import geargrafx_ipl_loader as L
        cls.L = L
        cls.probe_dir = cls.out / "probe"
        cls.probe_dir.mkdir(exist_ok=True)
        cls.probe = compile_probe(cls.probe_dir, "geargrafx_api_entry_probe.cpp",
                                  "cap-entry-probe")

    @classmethod
    def tearDownClass(cls):
        import tempfile
        tmp = cls._tmp.name
        assert "pce-apicap-" in tmp and tmp.startswith(tempfile.gettempdir())
        cls._tmp.cleanup()

    def _run(self, name, ncalls, **kwargs):
        import environment
        from geargrafx_api_entry import run_probe
        pattern = kwargs.pop("pattern", None)
        o = self.out / name
        o.mkdir(exist_ok=True)
        src = o / (name + ".s")
        src.write_text(payload_asm(ncalls, pattern), encoding="ascii")
        cfg = ("MEMORY {\n    RAM: start = $3000, size = $0F00, file = %O;\n}\n"
               "SEGMENTS {\n    CODE: load = RAM, type = ro, start = $3000;\n}\n")
        obj, binp, cfgp, lblp = (o / (name + ".o"), o / (name + ".bin"),
                                 o / (name + ".cfg"), o / (name + ".lbl"))
        cfgp.write_text(cfg, encoding="ascii")
        ca65, ld65 = environment.executable("ca65"), environment.executable("ld65")
        subprocess.run([ca65, str(src), "-o", str(obj), "-g"], check=True)
        subprocess.run([ld65, "-C", str(cfgp), "-o", str(binp), "-Ln", str(lblp),
                        str(obj)], check=True)
        text = lblp.read_text(encoding="ascii")
        m = re.search(r"^al ([0-9A-Fa-f]{6}) \.payload_halt$", text, re.M)
        assert m, "payload_halt label missing"
        halt = int(m.group(1), 16)
        assert halt < 0x3800
        code = binp.read_bytes()
        assert 1 <= len(code) <= 2048
        body = self.L.body_for(code, 2)
        info = self.L.info_sector(2, 2, 0x3000, 0x3000, [0, 1, 2, 3, 4], 0,
                                  sum(body) & 0xFFFF)
        cue = self.L.make_disc(o, self.L.case_disc({"n": 2, "iplblk": 2}, body, info))
        lab = o / (name + ".labels")
        lab.write_text(
            f"cd_halt_ok {halt:04x}\n"
            f"cd_halt_fail {self.labels['cd_halt_fail']:04x}\n"
            f"cd_unexpected_interrupt {self.labels['cd_unexpected_interrupt']:04x}\n"
            f"api_unimplemented {self.labels['api_unimplemented']:04x}\n",
            encoding="ascii")
        mode = kwargs.pop("mode", "synthetic")
        rec = run_probe(self.probe, self.rom, cue, lab, BUDGET, mode, **kwargs)
        return rec

    def _summarize(self, rec):
        out = {}
        for cycle in ("cold", "warm"):
            calls = [c for c in rec["APICALL"] if c["cycle"] == cycle]
            runs = [r for r in rec["RUN"] if r["cycle"] == cycle]
            assert len(runs) == 1
            r = runs[0]
            assert r["reason"] == "cd_halt_ok", r
            assert r["halt_confirmed"] is True
            assert r.get("apicall_truncated") in (True, False)
            assert isinstance(r.get("apicall_dropped"), int), r
            out[cycle] = {"slots": [c["slot"] for c in calls],
                          "pcs": [c["entry_pc"] for c in calls],
                          "caller_pcs": [c["caller_pc"] for c in calls],
                          "steps": [c["step"] for c in calls],
                          "trunc": r["apicall_truncated"],
                          "dropped": r["apicall_dropped"],
                          "match": [c.get("stack_check") for c in calls]}
        return out

    def _assert_capacity(self, rec, ncalls, cap):
        """Per-cycle bound check: kept == min(ncalls, cap), dropped == ncalls - kept."""
        s = self._summarize(rec)
        want_kept = min(ncalls, cap)
        want_dropped = max(0, ncalls - cap)
        for cycle in ("cold", "warm"):
            self.assertEqual(len(s[cycle]["slots"]), want_kept, cycle)
            self.assertEqual(s[cycle]["dropped"], want_dropped, cycle)
            self.assertIs(s[cycle]["trunc"], want_dropped > 0, cycle)
            self.assertEqual(s[cycle]["trunc"], s[cycle]["dropped"] > 0, cycle)
        self.assertEqual(s["cold"]["slots"], s["warm"]["slots"])
        self.assertEqual(s["cold"]["pcs"], s["warm"]["pcs"])
        return s

    def _check_full(self, rec, ncalls):
        s = self._summarize(rec)
        for cycle in ("cold", "warm"):
            self.assertEqual(s[cycle]["slots"], [0x35] * ncalls, cycle)
            self.assertEqual(s[cycle]["pcs"], [0xE09F] * ncalls, cycle)
            self.assertIs(s[cycle]["trunc"], False, cycle)
            self.assertEqual(s[cycle]["dropped"], 0, cycle)
            self.assertEqual(s[cycle]["match"], ["MATCH"] * ncalls, cycle)
        self.assertEqual(s["cold"]["slots"], s["warm"]["slots"])

    def test_under_default_capacity(self):
        self._check_full(self._run("cap7", 7), 7)

    def test_exact_default_capacity(self):
        self._check_full(self._run("cap8", 8), 8)

    def test_over_default_truncates_with_count(self):
        # RED control: the 9th call is dropped, truncation flagged, count 1.
        rec = self._run("cap9def", 9)
        s = self._summarize(rec)
        for cycle in ("cold", "warm"):
            self.assertEqual(s[cycle]["slots"], [0x35] * 8, cycle)
            self.assertIs(s[cycle]["trunc"], True, cycle)
            self.assertEqual(s[cycle]["dropped"], 1, cycle)

    def test_raised_cap_records_ninth(self):
        self._check_full(self._run("cap9x16", 9, max_apicalls=16), 9)

    def test_raised_cap_twelve(self):
        self._check_full(self._run("cap12x16", 12, max_apicalls=16), 12)

    def test_exact_raised_cap(self):
        self._check_full(self._run("cap16x16", 16, max_apicalls=16), 16)

    def test_over_raised_cap(self):
        rec = self._run("cap17x16", 17, max_apicalls=16)
        s = self._summarize(rec)
        for cycle in ("cold", "warm"):
            self.assertEqual(s[cycle]["slots"], [0x35] * 16, cycle)
            self.assertIs(s[cycle]["trunc"], True, cycle)
            self.assertEqual(s[cycle]["dropped"], 1, cycle)

    def test_under_max_cap(self):
        # Under-cap control (renamed from cap20x64): 20 calls at cap 64 stay
        # fully recorded; the exact 64/overflow bounds are proved below.
        self._check_full(self._run("cap20x64", 20, max_apicalls=64), 20)

    def test_cap1_exact(self):
        # Lower hard bound: a single call at cap 1 is fully recorded.
        self._check_full(self._run("cap1x1", 1, max_apicalls=1), 1)

    def test_cap1_overflow(self):
        # Lower overflow: 2 calls at cap 1 keep the first, drop exactly 1.
        rec = self._run("cap2x1", 2, max_apicalls=1)
        s = self._assert_capacity(rec, 2, 1)
        for cycle in ("cold", "warm"):
            self.assertEqual(s[cycle]["slots"], [0x35], cycle)
            self.assertEqual(s[cycle]["pcs"], [0xE09F], cycle)

    def test_cap64_exact(self):
        # Upper hard bound: 64 calls at cap 64 are fully recorded.
        self._check_full(self._run("cap64x64", 64, max_apicalls=64), 64)

    def test_cap64_overflow(self):
        # Upper overflow: 65 calls at cap 64 keep 64, drop exactly 1.
        rec = self._run("cap65x64", 65, max_apicalls=64)
        s = self._assert_capacity(rec, 65, 64)
        for cycle in ("cold", "warm"):
            self.assertEqual(s[cycle]["slots"], [0x35] * 64, cycle)
            self.assertEqual(s[cycle]["pcs"], [0xE09F] * 64, cycle)

    def test_distinct_slot_order_both_cycles(self):
        # Order permutation sensitivity (preferred option A): a repeating safe
        # sequence over distinct experimental slots proves slot+pc order per
        # cycle. Identical-$35 repetition cannot catch a shuffle; this can.
        ncalls = 10
        pattern = list(SAFE_ORDER_SLOTS)
        want_slots = [pattern[i % len(pattern)] for i in range(ncalls)]
        want_pcs = [entry_of_slot(s) for s in want_slots]
        rec = self._run("caporder10x16", ncalls, max_apicalls=16,
                        pattern=pattern)
        s = self._summarize(rec)
        for cycle in ("cold", "warm"):
            self.assertEqual(s[cycle]["slots"], want_slots, cycle)
            self.assertEqual(s[cycle]["pcs"], want_pcs, cycle)
            self.assertIs(s[cycle]["trunc"], False, cycle)
            self.assertEqual(s[cycle]["dropped"], 0, cycle)
            self.assertEqual(s[cycle]["match"], ["MATCH"] * ncalls, cycle)
            # Caller PCs strictly increase inside the payload window and the
            # probe steps strictly increase: any record permutation or skip
            # breaks one of these sequences.
            cpcs, steps = s[cycle]["caller_pcs"], s[cycle]["steps"]
            self.assertEqual(len(cpcs), ncalls, cycle)
            for pc in cpcs:
                self.assertGreaterEqual(pc, 0x3000, cycle)
                self.assertLess(pc, 0x3800, cycle)
            self.assertEqual(cpcs, sorted(cpcs), cycle)
            self.assertEqual(len(set(cpcs)), ncalls, cycle)
            self.assertEqual(steps, sorted(steps), cycle)
            self.assertEqual(len(set(steps)), ncalls, cycle)
        self.assertEqual(s["cold"]["slots"], s["warm"]["slots"])
        self.assertEqual(s["cold"]["pcs"], s["warm"]["pcs"])
        self.assertEqual(s["cold"]["caller_pcs"], s["warm"]["caller_pcs"])

    def test_invalid_caps_refused(self):
        # None means "default 8" and is valid, so it is not listed here.
        for bad in (0, -1, 65, 100, True, False, "16", 7.5, (8,)):
            with self.subTest(cap=bad):
                with self.assertRaises((ValueError, TypeError)):
                    self._run("capbad", 7, max_apicalls=bad)

    def test_direct_probe_rejects_bad_cap(self):
        # Fail-closed at the C++ boundary: non-numeric/out-of-range 7th arg
        # exits exactly 2. The valid-cap control (explicit "8" and the
        # 6-arg default form) must exit 0 with 2 RUNs, proving the exit-2
        # failures are cap rejections, not unrelated setup failures.
        # (" 8" is deliberately not listed: strtol skips leading spaces.)
        import tempfile
        priv = Path(tempfile.mkdtemp(prefix="pce-apicap-raw-"))
        try:
            lab = priv / "x.labels"
            lab.write_text("cd_halt_ok 3000\ncd_halt_fail 3001\n"
                           "cd_unexpected_interrupt 3002\napi_unimplemented 3003\n")
            base = [str(self.probe), str(self.rom), "-", str(lab),
                    "1000", "synthetic"]
            for bad in ("0", "65", "abc", "-3", "", "08x", "8.0"):
                with self.subTest(arg=bad):
                    r = subprocess.run(
                        base + [bad],
                        capture_output=True, text=True, timeout=60)
                    self.assertEqual(r.returncode, 2, f"cap {bad!r} must exit 2")
            for good in (["8"], []):
                with self.subTest(arg=good):
                    r = subprocess.run(
                        base + good,
                        capture_output=True, text=True, timeout=120)
                    self.assertEqual(r.returncode, 0, f"cap {good!r} must run")
                    runs = [ln for ln in r.stdout.splitlines()
                            if ln.startswith("RUN ")]
                    self.assertEqual(len(runs), 2, f"cap {good!r} must emit 2 RUNs")
                    for ln in runs:
                        body = json.loads(ln[len("RUN "):])
                        self.assertIn(body.get("reason"), ("budget", "cd_halt_ok",
                                                           "cd_halt_fail", "cd_unexpected_interrupt",
                                                           "api_unimplemented"))
        finally:
            import shutil
            shutil.rmtree(priv, ignore_errors=True)

    def test_commercial_mode_stays_clean_with_dropped_key(self):
        # The additive apicall_dropped key is structural (a count), never a
        # RAM/stack dump: commercial clean-audit must accept it.
        rec = self._run("cap9comm", 9, max_apicalls=16, mode="commercial")
        s = self._summarize(rec)
        for cycle in ("cold", "warm"):
            self.assertEqual(s[cycle]["slots"], [0x35] * 9, cycle)
            self.assertIs(s[cycle]["trunc"], False, cycle)
            self.assertEqual(s[cycle]["dropped"], 0, cycle)
        import geargrafx_api_entry as E
        E.assert_commercial_clean(rec)
        # Evidence projection persists both keys per RUN with the right types.
        runs = E.evidence_runs(rec)
        self.assertEqual(len(runs), 2)
        for r in runs:
            self.assertIs(r["apicall_truncated"], False)
            self.assertEqual(r["apicall_dropped"], 0)
            self.assertIsInstance(r["apicall_dropped"], int)
            self.assertNotIsInstance(r["apicall_dropped"], bool)

    def test_truncation_invariant(self):
        # truncated is True iff dropped > 0, on every run of every shape.
        for name, ncalls, cap in (("inv7", 7, None), ("inv9", 9, None),
                                  ("inv12", 12, 16), ("inv17", 17, 16),
                                  ("inv1x1", 1, 1), ("inv2x1", 2, 1),
                                  ("inv64x64", 64, 64), ("inv65x64", 65, 64)):
            with self.subTest(name=name):
                rec = (self._run(name, ncalls) if cap is None
                       else self._run(name, ncalls, max_apicalls=cap))
                for r in rec["RUN"]:
                    self.assertEqual(r["apicall_truncated"], r["apicall_dropped"] > 0)

    # --- CLI --max-apicalls contract (synthetic-only, no ./rom/ access) ---

    def test_cli_help_lists_flag(self):
        r = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent /
                                  "geargrafx_api_entry.py"), "--help"],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0)
        self.assertIn("--max-apicalls", r.stdout)

    def test_cli_malformed_caps_exit_2(self):
        # Range/non-int rejections exit 2 via parser.error/argparse before any
        # ROM check, so no media or build artifact is needed here.
        entry = str(Path(__file__).resolve().parent / "geargrafx_api_entry.py")
        for bad in ("0", "65", "-3", "abc", "8.0"):
            with self.subTest(arg=bad):
                r = subprocess.run(
                    [sys.executable, entry, "--max-apicalls", bad],
                    capture_output=True, text=True, timeout=60)
                self.assertEqual(r.returncode, 2, f"--max-apicalls {bad} must exit 2")

    def _fake_probe_result(self, runs):
        import json as _json
        import unittest.mock
        lines = ["RUN " + _json.dumps(r) for r in runs]
        m = unittest.mock.MagicMock()
        m.stdout = "\n".join(lines) + "\n"
        return m

    def test_run_probe_default_is_six_args(self):
        # None keeps the historical 6-argument probe invocation unchanged.
        import unittest.mock
        import geargrafx_api_entry as E
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = list(cmd)
            return self._fake_probe_result([
                {"cycle": "cold", "reason": "budget", "halt_confirmed": False},
                {"cycle": "warm", "reason": "budget", "halt_confirmed": False}])
        with unittest.mock.patch.object(E.subprocess, "run", fake_run):
            E.run_probe("P", "R", "C", "L", 1000, "synthetic")
        self.assertEqual(len(seen["cmd"]), 6)
        self.assertEqual(seen["cmd"][:6], ["P", "R", "C", "L", "1000", "synthetic"])

    def test_run_probe_explicit_cap_is_seventh_arg(self):
        # Explicit caps become probe argv[6]; the CLI forwards this position.
        import unittest.mock
        import geargrafx_api_entry as E
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = list(cmd)
            return self._fake_probe_result([
                {"cycle": "cold", "reason": "budget", "halt_confirmed": False},
                {"cycle": "warm", "reason": "budget", "halt_confirmed": False}])
        with unittest.mock.patch.object(E.subprocess, "run", fake_run):
            E.run_probe("P", "R", "C", "L", 1000, "synthetic", max_apicalls=16)
        self.assertEqual(len(seen["cmd"]), 7)
        self.assertEqual(seen["cmd"][6], "16")

    def test_cli_forwards_cap_in_both_paths(self):
        # Static contract: both synthetic and commercial run_probe call sites
        # forward args.max_apicalls (None preserves default-8 history).
        src = (Path(__file__).resolve().parent /
               "geargrafx_api_entry.py").read_text(encoding="utf-8")
        self.assertIn("max_apicalls=max_apicalls", src)
        self.assertIn("max_apicalls=args.max_apicalls", src)
        self.assertEqual(src.count("max_apicalls=args.max_apicalls"), 3)

    def test_evidence_projection_schema_unit(self):
        # Synthetic-only schema check on fabricated structural records: both
        # keys present with bool/int types, truncation invariant holds, and
        # the commercial clean-audit accepts the additive dropped count.
        import geargrafx_api_entry as E
        rec = {"CAP": [], "HIT": [], "PHASE": [], "APICALL": [], "RUN": [
            {"cycle": "cold", "reason": "cd_halt_ok", "pc": 0x3000, "x": 0x35,
             "halt_confirmed": True, "step": 10,
             "apicall_truncated": False, "apicall_dropped": 0},
            {"cycle": "warm", "reason": "cd_halt_ok", "pc": 0x3000, "x": 0x35,
             "halt_confirmed": True, "step": 12,
             "apicall_truncated": True, "apicall_dropped": 1}]}
        E.assert_commercial_clean(rec)
        runs = E.evidence_runs(rec)
        self.assertEqual(len(runs), 2)
        self.assertIs(runs[0]["apicall_truncated"], False)
        self.assertEqual(runs[0]["apicall_dropped"], 0)
        self.assertIs(runs[1]["apicall_truncated"], True)
        self.assertEqual(runs[1]["apicall_dropped"], 1)
        for r in runs:
            self.assertIsInstance(r["apicall_truncated"], bool)
            self.assertIsInstance(r["apicall_dropped"], int)
            self.assertNotIsInstance(r["apicall_dropped"], bool)
            self.assertEqual(r["apicall_truncated"], r["apicall_dropped"] > 0)
        # Missing keys default (backward-compatible with older evidence).
        legacy = {"RUN": [{"cycle": "cold", "reason": "budget", "pc": 0,
                            "x": 0, "halt_confirmed": False, "step": 5}]}
        legacy_runs = E.evidence_runs(legacy)
        self.assertIs(legacy_runs[0]["apicall_truncated"], False)
        self.assertEqual(legacy_runs[0]["apicall_dropped"], 0)
        # Truncation stays fail-closed: any truncated trace classifies
        # INCOMPLETE, never a confirmation.
        verdict, _detail = E.classify_commercial(rec, stub_addr=None)
        self.assertEqual(verdict, "INCOMPLETE")


if __name__ == "__main__":
    unittest.main()
