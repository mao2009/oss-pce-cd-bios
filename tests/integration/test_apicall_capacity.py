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
- invalid capacities (0, 65, non-int) refused fail-closed (Python ValueError;
  direct C++ invocation exits non-zero)
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


def payload_asm(ncalls):
    calls = "\n".join(["    jsr $E09F"] * ncalls)
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
        o = self.out / name
        o.mkdir(exist_ok=True)
        src = o / (name + ".s")
        src.write_text(payload_asm(ncalls), encoding="ascii")
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
                          "trunc": r["apicall_truncated"],
                          "dropped": r["apicall_dropped"],
                          "match": [c.get("stack_check") for c in calls]}
        return out

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

    def test_max_cap_boundary(self):
        self._check_full(self._run("cap20x64", 20, max_apicalls=64), 20)

    def test_invalid_caps_refused(self):
        # None means "default 8" and is valid, so it is not listed here.
        for bad in (0, -1, 65, 100, True, False, "16", 7.5, (8,)):
            with self.subTest(cap=bad):
                with self.assertRaises((ValueError, TypeError)):
                    self._run("capbad", 7, max_apicalls=bad)

    def test_direct_probe_rejects_bad_cap(self):
        # Fail-closed at the C++ boundary: non-numeric/out-of-range 7th arg.
        import tempfile
        priv = Path(tempfile.mkdtemp(prefix="pce-apicap-raw-"))
        try:
            lab = priv / "x.labels"
            lab.write_text("cd_halt_ok 3000\ncd_halt_fail 3001\n"
                           "cd_unexpected_interrupt 3002\napi_unimplemented 3003\n")
            for bad in ("0", "65", "abc", "-3"):
                with self.subTest(arg=bad):
                    r = subprocess.run(
                        [str(self.probe), str(self.rom), "-", str(lab),
                         "1000", "synthetic", bad],
                        capture_output=True, text=True, timeout=60)
                    self.assertNotEqual(r.returncode, 0, f"cap {bad} must fail")
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

    def test_truncation_invariant(self):
        # truncated is True iff dropped > 0, on every run of every shape.
        for name, ncalls, cap in (("inv7", 7, None), ("inv9", 9, None),
                                  ("inv12", 12, 16), ("inv17", 17, 16)):
            with self.subTest(name=name):
                rec = (self._run(name, ncalls) if cap is None
                       else self._run(name, ncalls, max_apicalls=cap))
                for r in rec["RUN"]:
                    self.assertEqual(r["apicall_truncated"], r["apicall_dropped"] > 0)


if __name__ == "__main__":
    unittest.main()
