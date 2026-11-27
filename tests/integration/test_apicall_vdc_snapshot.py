"""Non-invasive VDC snapshot for the API-entry observer (Issue #55, G5 P0).

Extends `geargrafx_api_entry_probe.cpp` with an explicit opt-in
`vdc_snapshot` flag (0/1, default 0). When enabled, each APICALL record
carries `vdc_ar/vdc_cr/vdc_rcr/vdc_sr` read via
`HuC6270::GetState()` pointers only (no MMIO, no SR-clear side effect,
no extra steps). Default 6/7-arg behavior is unchanged.

Proves (only named-test kills are RED):
- off vs on: same slots/order/halt/regs/MPR/IDR/IRR/RAM (steps excluded for
  natural jitter, documented below), VDC keys present only when enabled.
- known-state exact match: synthetic guest presets distinct nonzero AR/CR/RCR
  (P1/P2) and VBLANK-pending SR (P3); the probe emits exactly those values at
  API entry, cold/warm identical, both consecutive calls identical (P3 proves
  the observation does not consume pending SR).
- invalid vdc values refused fail-closed (Python ValueError, C++ exit 2
  exactly, with valid 0/1 controls on real files).
- commercial mode stays RAM-clean with VDC keys.
- cold/warm identical order every run.
- source uses GetState, never the destructive status path for VDC.
- mutation RED only for non-int/out-of-range constants, missing keys,
  invalid flags, all-zero/wrong-register/swapped falsified records against
  known expectations, and commercial-gate bypass. In-range constants pass
  range-only checks by design (documented limitation, covered by exact-match
  tests instead); mask/I-flag helpers are literal bit assertions, not
  probe-level mutants; timing shift and extra step are NOT demonstrated as
  kills (see test names and docs/55 §6.3).

NOT a compatible System Card BIOS. Synthetic media only, pinned core only
(Geargrafx b49ae82a, cc65 71746c8). No commercial media access here
(no ./rom probing in this file).
"""

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


def payload_asm(ncalls, slot=0x35):
    entry = 0xE000 + 3 * slot
    calls = "\n".join([f"    jsr ${entry:04X}"] * ncalls)
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


def vdc_payload_asm(ar, cr, rcr, areg, xreg, yreg, pseed, wait_outer=0):
    """Synthetic guest that presets known VDC state, then JSRs $E09F twice.

    TAM is a bitmask (#$01=MPR0, #$02=MPR1); `tam #$00` is a no-op and must
    never be used. VDC ports require absolute addressing (`a:$0000` etc.):
    plain `sta $0000` assembles to zero-page (work RAM), not the hardware
    page. I stays set throughout (P bit 2), so enabled VDC IRQs stay pending
    and are never taken (no handler transition).
    """
    lines = []
    lines.append('.setcpu "huc6280"')
    lines.append('.segment "CODE"')
    lines.append('payload_entry:')
    lines.append('    cld')
    lines.append('    sei')
    lines.append('    ldx #$FF')
    lines.append('    txs')
    lines.append('    lda #$F8')
    lines.append('    tam #$02')  # MPR1 = work RAM
    lines.append('    lda #$FF')
    lines.append('    tam #$01')  # MPR0 = I/O window
    lines.append('    lda #$05')
    lines.append('    sta a:$0000')  # AR = CR
    lines.append(f'    lda #${cr & 0xFF:02X}')
    lines.append('    sta a:$0002')
    lines.append(f'    lda #${(cr >> 8) & 0xFF:02X}')
    lines.append('    sta a:$0003')
    lines.append('    lda #$06')
    lines.append('    sta a:$0000')  # AR = RCR
    lines.append(f'    lda #${rcr & 0xFF:02X}')
    lines.append('    sta a:$0002')
    lines.append(f'    lda #${(rcr >> 8) & 0xFF:02X}')
    lines.append('    sta a:$0003')
    lines.append(f'    lda #${ar:02X}')
    lines.append('    sta a:$0000')  # final AR
    lines.append('    lda #$F8')
    lines.append('    tam #$01')  # MPR0 restore
    if wait_outer > 0:
        # Spin in WRAM (no BIOS calls) so VBLANK can raise pending SR.
        lines.append(f'    ldy #${wait_outer:02X}')
        lines.append('wait2:')
        lines.append('    ldx #$FF')
        lines.append('wait3:')
        lines.append('    dex')
        lines.append('    bne wait3')
        lines.append('    dey')
        lines.append('    bne wait2')
    lines.append(f'    ldy #${yreg:02X}')
    lines.append(f'    ldx #${xreg:02X}')
    lines.append(f'    lda #${pseed:02X}')
    lines.append('    pha')
    lines.append(f'    lda #${areg:02X}')
    lines.append('    plp')
    lines.append('    jsr $E09F')
    lines.append('    jsr $E09F')
    lines.append('    lda #$AA')
    lines.append('    sta res_base')
    lines.append('payload_halt:')
    lines.append('    bra payload_halt')
    lines.append('res_base:')
    lines.append('    .res 16')
    return "\n".join(lines) + "\n"


# Known-state patterns (all values distinct within and across patterns so a
# single fixed value can never satisfy more than one).
P1 = dict(ar=2, cr=0x10, rcr=0xAA, sr=0x00,
          areg=0x3C, xreg=0x11, yreg=0x22, pseed=0x07, wait_outer=0)
P2 = dict(ar=7, cr=0x20, rcr=0x155, sr=0x00,
          areg=0x55, xreg=0x33, yreg=0x44, pseed=0x04, wait_outer=0)
P3 = dict(ar=3, cr=0x08, rcr=0x00, sr=0x20,
          areg=0x3C, xreg=0x11, yreg=0x22, pseed=0x07, wait_outer=0x10)


def check_vdc_record(call):
    """Range/type sanity for one APICALL with VDC snapshot.

    This checks only that each VDC field is a non-bool int inside the pinned
    core's register domain (AR 5-bit, CR 13-bit, RCR 10-bit, SR 7-bit status).
    It does NOT prove semantic accuracy: in-range constants (including
    all-zero) and CR/SR transpositions of in-range values PASS this check by
    design. Semantic accuracy is proved by the exact-match tests below
    (`test_exact_known_state_*`), which assert the probe emits the preset
    fixture values.
    """
    for key in ("vdc_ar", "vdc_cr", "vdc_rcr", "vdc_sr"):
        if key not in call:
            raise ValueError(f"missing {key}")
        v = call[key]
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError(f"{key} must be int")
    if not 0 <= call["vdc_ar"] <= 31:
        raise ValueError(f"vdc_ar out of range: {call['vdc_ar']}")
    if not 0 <= call["vdc_cr"] <= 0x1FFF:
        raise ValueError(f"vdc_cr out of range: {call['vdc_cr']}")
    if not 0 <= call["vdc_rcr"] <= 0x3FF:
        raise ValueError(f"vdc_rcr out of range: {call['vdc_rcr']}")
    if not 0 <= call["vdc_sr"] <= 0x7F:
        raise ValueError(f"vdc_sr out of range: {call['vdc_sr']}")
    return True


def assert_exact_vdc(testcase, call, exp, what):
    """Assert a record carries exactly the fixture's preset values."""
    testcase.assertEqual(call.get("vdc_ar"), exp["ar"], f"{what} vdc_ar")
    testcase.assertEqual(call.get("vdc_cr"), exp["cr"], f"{what} vdc_cr")
    testcase.assertEqual(call.get("vdc_rcr"), exp["rcr"], f"{what} vdc_rcr")
    testcase.assertEqual(call.get("vdc_sr"), exp["sr"], f"{what} vdc_sr")
    testcase.assertEqual(call.get("slot"), 0x35, f"{what} slot")
    testcase.assertEqual(call.get("entry_pc"), 0xE09F, f"{what} entry_pc")
    testcase.assertEqual(call.get("caller_a"), exp["areg"], f"{what} A")
    testcase.assertEqual(call.get("caller_x"), exp["xreg"], f"{what} X")
    testcase.assertEqual(call.get("caller_y"), exp["yreg"], f"{what} Y")
    testcase.assertEqual(call.get("caller_p"), exp["pseed"], f"{what} P")
    testcase.assertEqual(call.get("sp_delta"), 2, f"{what} sp_delta")
    testcase.assertEqual(call.get("stack_check"), "MATCH", f"{what} stack")


class VdcSnapshotSynthetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from unittest.mock import patch
        import build_rom
        from build_ipl_loader_g5_2base import build_ipl_loader_g5_rom, parse_labels
        from geargrafx_api import compile_probe
        cls._tmp = tempfile.TemporaryDirectory(prefix="pce-vdc-snap-")
        cls.out = Path(cls._tmp.name)
        dev_out = Path(cls._tmp.name) / "dev"
        with patch("sys.argv", ["build_rom.py", "--mode", "debug",
                                "--output", str(dev_out)]):
            assert build_rom.main() == 0
        orig = dev_out / "dev-only-not-compatible-syscard3.pce"
        assert orig.is_file()
        cls.normal = orig.read_bytes()
        cls.rom, cls.syms = build_ipl_loader_g5_rom(
            orig, cls.out, SEL, "debug", "vdcsnap", ["-D", "IPL_PUBLIC"],
            track=TRACK_SEL, private_input=False,
            cd_4a="experimental", cd_25="experimental",
            cd_24="experimental", cd_36="experimental",
            cd_35="experimental")
        if orig.read_bytes() != cls.normal:
            raise ValueError("BLOCKED: G5 build mutated the normal DEV ROM input")
        trace = cls.out / "ipl-loader-g5-vdcsnap.lbl"
        cls.labels = parse_labels(
            trace, ("api_unimplemented", "cd_halt_fail", "cd_unexpected_interrupt"))
        import geargrafx_ipl_loader as L
        cls.L = L
        cls.probe_dir = cls.out / "probe"
        cls.probe_dir.mkdir(exist_ok=True)
        cls.probe = compile_probe(cls.probe_dir, "geargrafx_api_entry_probe.cpp",
                                  "vdc-snap-probe")

    @classmethod
    def tearDownClass(cls):
        import tempfile
        tmp = cls._tmp.name
        assert "pce-vdc-snap-" in tmp and tmp.startswith(tempfile.gettempdir())
        cls._tmp.cleanup()

    def _build_case_files(self, name, asm_text):
        """Assemble payload + synthetic disc + probe labels; no probe run."""
        import environment
        o = self.out / name
        o.mkdir(exist_ok=True)
        src = o / (name + ".s")
        src.write_text(asm_text, encoding="ascii")
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
        return halt, cue, lab

    def _run(self, name, ncalls, **kwargs):
        from geargrafx_api_entry import run_probe
        halt, cue, lab = self._build_case_files(name, payload_asm(ncalls))
        assert halt < 0x3800
        mode = kwargs.pop("mode", "synthetic")
        rec = run_probe(self.probe, self.rom, cue, lab, BUDGET, mode, **kwargs)
        return rec

    def _run_vdc(self, name, pattern, **kwargs):
        """Run the known-state VDC fixture; returns (records, payload_halt)."""
        from geargrafx_api_entry import run_probe
        asm_text = vdc_payload_asm(pattern["ar"], pattern["cr"], pattern["rcr"],
                                   pattern["areg"], pattern["xreg"], pattern["yreg"],
                                   pattern["pseed"], pattern.get("wait_outer", 0))
        halt, cue, lab = self._build_case_files(name, asm_text)
        mode = kwargs.pop("mode", "synthetic")
        rec = run_probe(self.probe, self.rom, cue, lab, BUDGET, mode, **kwargs)
        return rec, halt

    def _strip(self, call):
        return {k: v for k, v in call.items() if k not in ("step", "vdc_ar", "vdc_cr", "vdc_rcr", "vdc_sr")}

    # --- exact known-state accuracy (MEDIUM-1 core) ---

    def _check_known_pattern(self, name, pattern):
        rec, halt = self._run_vdc(name, pattern, max_apicalls=16, vdc_snapshot=True)
        for cycle in ("cold", "warm"):
            calls = [c for c in rec["APICALL"] if c["cycle"] == cycle]
            # Two JSR $E09F per cycle; both must carry the exact preset state.
            self.assertEqual(len(calls), 2, f"{name} {cycle} count")
            for c in calls:
                self.assertTrue(check_vdc_record(c))
                assert_exact_vdc(self, c, pattern, f"{name} {cycle}")
            runs = [r for r in rec["RUN"] if r["cycle"] == cycle]
            self.assertEqual(len(runs), 1, cycle)
            self.assertEqual(runs[0].get("reason"), "cd_halt_ok", cycle)
            self.assertTrue(runs[0].get("halt_confirmed"), cycle)
            self.assertEqual(runs[0].get("pc"), halt, cycle)
            self.assertFalse(runs[0].get("apicall_truncated"), cycle)
            self.assertEqual(runs[0].get("apicall_dropped"), 0, cycle)
        cold = [c["slot"] for c in rec["APICALL"] if c["cycle"] == "cold"]
        warm = [c["slot"] for c in rec["APICALL"] if c["cycle"] == "warm"]
        self.assertEqual(cold, warm)
        return rec

    def test_exact_known_state_p1(self):
        self._check_known_pattern("known_p1", P1)

    def test_exact_known_state_p2_distinct(self):
        # Different AR/CR/RCR/regs from P1: one fixed value cannot pass both.
        self._check_known_pattern("known_p2", P2)

    def test_pending_sr_exact_and_persists_p3(self):
        # VBLANK-pending SR must read exactly 0x20 on BOTH consecutive calls:
        # a destructive (SR-clearing) observation would show 0x00 on the 2nd.
        rec = self._check_known_pattern("known_p3", P3)
        for cycle in ("cold", "warm"):
            calls = [c for c in rec["APICALL"] if c["cycle"] == cycle]
            self.assertEqual([c.get("vdc_sr") for c in calls], [0x20, 0x20],
                             f"P3 {cycle} SR persists (non-destructive)")

    def test_allzero_falsified_rejected_against_known(self):
        # RED: an all-zero VDC record does not match the known nonzero P1/P3
        # expectations, so a probe emitting constant zeros is caught.
        fabric = {"vdc_ar": 0, "vdc_cr": 0, "vdc_rcr": 0, "vdc_sr": 0,
                  "slot": 0x35, "entry_pc": 0xE09F,
                  "caller_a": P1["areg"], "caller_x": P1["xreg"],
                  "caller_y": P1["yreg"], "caller_p": P1["pseed"],
                  "sp_delta": 2, "stack_check": "MATCH"}
        with self.assertRaises(AssertionError, msg="all-zero vs P1 must fail"):
            assert_exact_vdc(self, fabric, P1, "allzero-vs-P1")
        fabric_p3 = dict(fabric, caller_a=P3["areg"], caller_x=P3["xreg"],
                         caller_y=P3["yreg"], caller_p=P3["pseed"])
        with self.assertRaises(AssertionError, msg="all-zero vs P3 must fail"):
            assert_exact_vdc(self, fabric_p3, P3, "allzero-vs-P3")

    def test_swapped_falsified_rejected_against_known(self):
        # RED: CR/SR-transposed records do not match known expectations.
        swapped_p1 = {"vdc_ar": P1["ar"], "vdc_cr": P1["sr"], "vdc_rcr": P1["rcr"],
                      "vdc_sr": P1["cr"], "slot": 0x35, "entry_pc": 0xE09F,
                      "caller_a": P1["areg"], "caller_x": P1["xreg"],
                      "caller_y": P1["yreg"], "caller_p": P1["pseed"],
                      "sp_delta": 2, "stack_check": "MATCH"}
        with self.assertRaises(AssertionError, msg="swapped vs P1 must fail"):
            assert_exact_vdc(self, swapped_p1, P1, "swapped-vs-P1")
        swapped_p3 = {"vdc_ar": P3["ar"], "vdc_cr": P3["sr"], "vdc_rcr": P3["rcr"],
                      "vdc_sr": P3["cr"], "slot": 0x35, "entry_pc": 0xE09F,
                      "caller_a": P3["areg"], "caller_x": P3["xreg"],
                      "caller_y": P3["yreg"], "caller_p": P3["pseed"],
                      "sp_delta": 2, "stack_check": "MATCH"}
        with self.assertRaises(AssertionError, msg="swapped vs P3 must fail"):
            assert_exact_vdc(self, swapped_p3, P3, "swapped-vs-P3")

    def test_missing_vdc_key_rejected(self):
        base = {"vdc_ar": P1["ar"], "vdc_cr": P1["cr"],
                "vdc_rcr": P1["rcr"], "vdc_sr": P1["sr"]}
        for key in ("vdc_ar", "vdc_cr", "vdc_rcr", "vdc_sr"):
            broken = dict(base)
            del broken[key]
            with self.assertRaises(ValueError, msg=f"missing {key}"):
                check_vdc_record(broken)

    def test_range_check_limitation_inrange_swap(self):
        # Documents that range-only validation ACCEPTS an in-range CR/SR
        # transposition (P1 CR=0x10/SR=0x00 swapped stays in range). This is why
        # exact-match tests above exist; range checks alone are insufficient.
        swapped = {"vdc_ar": P1["ar"], "vdc_cr": P1["sr"], "vdc_rcr": P1["rcr"],
                   "vdc_sr": P1["cr"]}
        self.assertTrue(check_vdc_record(swapped))

    # --- basic behavior ---

    def test_off_has_no_vdc_keys(self):
        rec = self._run("novdc", 7)
        for c in rec["APICALL"]:
            for k in ("vdc_ar", "vdc_cr", "vdc_rcr", "vdc_sr"):
                self.assertNotIn(k, c, f"VDC key {k} must be absent when disabled")

    def test_on_has_valid_vdc_keys(self):
        rec = self._run("withvdc", 7, max_apicalls=16, vdc_snapshot=True)
        self.assertGreater(len(rec["APICALL"]), 0)
        for c in rec["APICALL"]:
            self.assertTrue(check_vdc_record(c))

    def test_off_vs_on_identical_except_steps_and_vdc(self):
        # Strict: the same known-state fixture must reach the normal clean
        # halt in both modes (equal abnormal outcomes cannot pass), and all
        # guest-visible state must match. Steps are excluded: independent
        # identical runs show ±2k-step natural jitter with no systematic +N
        # per APICALL, so step equality cannot prove (or disprove) the absence
        # of extra emulator steps; non-invasiveness rests on the GetState-only
        # source gate plus identical halt/order/regs/RAM below.
        off, off_halt = self._run_vdc("cmp_off", P1, max_apicalls=16)
        on, on_halt = self._run_vdc("cmp_on", P1, max_apicalls=16, vdc_snapshot=True)
        self.assertEqual(off_halt, on_halt)
        for cycle in ("cold", "warm"):
            off_calls = [c for c in off["APICALL"] if c["cycle"] == cycle]
            on_calls = [c for c in on["APICALL"] if c["cycle"] == cycle]
            # Expected normal path: exactly 2 table JSRs to $E09F.
            for label, calls in (("off", off_calls), ("on", on_calls)):
                self.assertEqual(len(calls), 2, f"{label} {cycle} count")
                self.assertEqual([c["slot"] for c in calls], [0x35, 0x35],
                                 f"{label} {cycle} slots")
                self.assertEqual([c["entry_pc"] for c in calls], [0xE09F, 0xE09F],
                                 f"{label} {cycle} entries")
            for a, b in zip(off_calls, on_calls):
                self.assertEqual(self._strip(a), self._strip(b), cycle)
            off_runs = [r for r in off["RUN"] if r["cycle"] == cycle]
            on_runs = [r for r in on["RUN"] if r["cycle"] == cycle]
            self.assertEqual(len(off_runs), 1, cycle)
            self.assertEqual(len(on_runs), 1, cycle)
            for runs, label in ((off_runs, "off"), (on_runs, "on")):
                self.assertEqual(runs[0].get("reason"), "cd_halt_ok", f"{label} {cycle}")
                self.assertTrue(runs[0].get("halt_confirmed"), f"{label} {cycle}")
                self.assertEqual(runs[0].get("pc"), off_halt, f"{label} {cycle} halt pc")
                self.assertFalse(runs[0].get("apicall_truncated"), f"{label} {cycle}")
                self.assertEqual(runs[0].get("apicall_dropped"), 0, f"{label} {cycle}")
            for key in ("reason", "halt_confirmed", "pc", "x", "p", "sp",
                        "apicall_truncated", "apicall_dropped", "result_hex", "buffer_hex"):
                self.assertEqual(off_runs[0].get(key), on_runs[0].get(key), f"{cycle} {key}")
            self.assertEqual(off_runs[0].get("mpr"), on_runs[0].get("mpr"), f"{cycle} mpr")
            # The enabled run must carry exactly the P1 preset state.
            for c in on_calls:
                assert_exact_vdc(self, c, P1, f"on {cycle}")

    def test_cold_warm_identical_order_with_vdc(self):
        rec = self._run("cwvdc", 9, max_apicalls=16, vdc_snapshot=True)
        cold = [c["slot"] for c in rec["APICALL"] if c["cycle"] == "cold"]
        warm = [c["slot"] for c in rec["APICALL"] if c["cycle"] == "warm"]
        self.assertEqual(cold, warm)
        self.assertEqual(len(cold), 9)

    def test_invalid_vdc_refused(self):
        for bad in (2, -1, "1", 1.0, (1,), True, False):
            # True/False are bool (subclass of int) — Python API accepts bool
            # as 1/0 by design; only non-bool invalids are refused here.
            if isinstance(bad, bool):
                continue
            with self.subTest(cap=bad):
                with self.assertRaises((ValueError, TypeError)):
                    self._run("badvdc", 7, vdc_snapshot=bad)

    def test_direct_probe_rejects_bad_vdc(self):
        # Exact exit-2 usage refusal on real files (not a load failure), plus
        # valid 0/1 controls that must complete the run (exit 0).
        halt, cue, lab = self._build_case_files("vdcarg", payload_asm(2))
        base = [str(self.probe), str(self.rom), str(cue), str(lab),
                "20000000", "synthetic", "16"]
        for bad in ("2", "-1", "abc", ""):
            with self.subTest(arg=bad):
                r = subprocess.run(base + [bad],
                                   capture_output=True, text=True, timeout=120)
                self.assertEqual(r.returncode, 2, f"vdc {bad!r} must exit 2")
        for good in ("0", "1"):
            with self.subTest(arg=good):
                r = subprocess.run(base + [good],
                                   capture_output=True, text=True, timeout=300)
                self.assertEqual(r.returncode, 0, f"vdc {good!r} must run")

    def test_commercial_mode_stays_clean_with_vdc(self):
        # Positive clean audit: a normal commercial-mode run passes.
        rec = self._run("commvdc", 7, max_apicalls=16, mode="commercial", vdc_snapshot=True)
        import geargrafx_api_entry as E
        E.assert_commercial_clean(rec)
        for c in rec["APICALL"]:
            self.assertTrue(check_vdc_record(c))

    def test_commercial_gate_rejects_forbidden_fields(self):
        # Negative gate: injected RAM-dump / raw-stack fields are rejected.
        import geargrafx_api_entry as E
        dirty = {"APICALL": [{"cycle": "cold", "vdc_ar": 1, "vdc_cr": 2,
                              "vdc_rcr": 3, "vdc_sr": 4}],
                 "RUN": [], "HIT": [], "PHASE": [], "CAP": []}
        dirty["APICALL"][0]["buffer_hex"] = "aa"
        with self.assertRaises(ValueError, msg="buffer_hex must be rejected"):
            E.assert_commercial_clean(dirty)
        dirty["APICALL"][0].pop("buffer_hex")
        dirty["APICALL"][0]["stack_b0"] = 1
        with self.assertRaises(ValueError, msg="stack_b0 must be rejected"):
            E.assert_commercial_clean(dirty)

    def test_truncation_invariant_with_vdc(self):
        rec = self._run("truncvdc", 17, max_apicalls=16, vdc_snapshot=True)
        for r in rec["RUN"]:
            self.assertEqual(r["apicall_truncated"], r["apicall_dropped"] > 0)

    def test_source_uses_getstate_not_mmio(self):
        src = (ROOT / "tests" / "integration" / "geargrafx_api_entry_probe.cpp").read_text()
        self.assertIn("GetHuC6270_1", src)
        self.assertIn("GetState()", src)
        self.assertIn("vdc_ar", src)
        # VDC path must never use destructive MMIO reads or writes.
        self.assertNotIn("ReadRegister", src)
        self.assertNotIn("WriteRegister", src)
        # No guest writes (same gate as existing observer).
        for forbidden in ("Memory::Write", "SetMpr", "->Write("):
            self.assertNotIn(forbidden, src, f"probe must not write guest state: {forbidden}")

    def test_mutation_fixed_value_rejected(self):
        base = {"vdc_ar": 0, "vdc_cr": 0, "vdc_rcr": 0, "vdc_sr": 0}
        # A validator that accepted any constant without range/type checks would
        # pass a broken record; ours must reject non-int/out-of-range.
        # (In-range constants pass this range check by design; they are caught
        # by the exact-match tests above instead.)
        for key in ("vdc_ar", "vdc_cr", "vdc_rcr", "vdc_sr"):
            broken = dict(base, **{key: "0"})
            with self.assertRaises(ValueError, msg=f"fixed string {key}"):
                check_vdc_record(broken)
            broken = dict(base, **{key: 99999})
            with self.assertRaises(ValueError, msg=f"out-of-range {key}"):
                check_vdc_record(broken)

    def test_bit_expression_helpers_not_probe_level(self):
        # Literal bit assertions only (helper scope, not probe mutants):
        # IE mask bit3 (0x08 VBLANK) vs bit2 (0x04 RCR), I flag = P bit 2.
        self.assertEqual(0x08 & 0x08, 0x08)
        self.assertEqual(0x04 & 0x08, 0x00)
        for p, expect_i in ((0x06, 1), (0x05, 1), (0x04, 1), (0x00, 0)):
            self.assertEqual((p >> 2) & 1, expect_i, f"P={p:02x}")
        self.assertNotEqual((0x06 >> 0) & 1, (0x06 >> 2) & 1)


if __name__ == "__main__":
    unittest.main()
