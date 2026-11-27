"""Synthetic full-ExpG real-core chain (Issue #843).

Proves the full experimental combination $33+$4A+$25+$24+$36+$35+$2C+$2B end
to end on synthetic media with the pinned core: the PUBLIC IPL boot ($02
leg) loads this payload, which seeds the shared $2502/$2503 shadow then
calls every ExpG slot through the REAL table entries ($E099 $E0DE $E06F
$E06C $E0A2 $E09F $E084 $E081, exactly 8 APICALLs at the default cap).

Coverage (OBSERVED synthetic, cold/warm pairs):
  ok chain (seeded $F4): per-call APICALL routing evidence (slots, entry
    PCs, in_table, SP delta, stack MATCH, return-matches-caller, MPR1=$F8);
    end-to-end A/X/Y/P/MPR/SP/marker/canary preservation; final shadow
    $38 with C=0; guest-recorded intermediate shadow $F4->$34->$3C->$38;
    per-call VDC entry snapshots (GetState only, non-invasive): CR high
    constant, RCR constant, AR==5 from the $4A call on, CR-low transitions
    $L0 -> ($L0&$3F) -> (($L0&$3F)|$08) with the actual initial low $L0
    recorded. B1 scaffolds ($4A/$25/$24/$36/$35) provably write no VDC
    register, so the CR evolution is the three CR heads alone.
  reject chain (unseeded $2503=$00): all 8 VDC entry snapshots IDENTICAL
    (no VDC write anywhere, AR untouched), shadow+marker untouched, final
    C=1 ($2B rejects last; B1 scaffolds preserve P on this entry class),
    registers/MPR/SP/canary preserved.
  mutation: a $2C-head WRONGBIT mutant on the full image REDs the shadow
    oracle exactly ($04 observed vs $08 correct).
  write history: the $2C/$2B writes-tracer probes run against the FULL
    ExpG image (shared tail linked once) prove containment on both paths.
  image identity: the full ExpG ROM SHA256 is pinned (drift fails here),
    the manifest slots/flags are asserted, BOOT stays at/before $FFF6,
    and the pinned Geargrafx revision is logged.

NOT a compatible System Card BIOS. NOT genuine ABI evidence (genuine slot
semantics stay UNKNOWN/BLOCKED; the observed entry states are
SIMULATED-entry). NOT DEV_CALLABLE. release_eligible=false.
G5_OBSERVED=false. Synthetic only; commercial media: NOT_RUN here (no
./rom probing in this file).
"""

import hashlib
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

CHAIN_SLOTS = (0x33, 0x4A, 0x25, 0x24, 0x36, 0x35, 0x2C, 0x2B)
CHAIN_PCS = (0xE099, 0xE0DE, 0xE06F, 0xE06C, 0xE0A2, 0xE09F, 0xE084, 0xE081)

# Local copies of the CR-family pins (canonical pins live in
# tests/host/test_build_g5_2b_host.py + test_build_g5_2c_host.py and the
# per-slot integration suites; change together). $2C mask ORA #$08, $2B
# mask AND #$FB; shared reject/tail identical for both.
D2C_HEAD_PREFIX = bytes((
    0x08, 0x48, 0xAD, 0x03, 0x25, 0xC9, 0xA5, 0xD0,
))
D2C_HEAD_MID = bytes((
    0x78, 0x43, 0x01, 0x48, 0xA9, 0xFF, 0x53, 0x01, 0xA9, 0x05, 0x8D,
    0x00, 0x00, 0xAD, 0x02, 0x25, 0x09, 0x08, 0x80,
))
D2B_HEAD_MID = bytes((
    0x78, 0x43, 0x01, 0x48, 0xA9, 0xFF, 0x53, 0x01, 0xA9, 0x05, 0x8D,
    0x00, 0x00, 0xAD, 0x02, 0x25, 0x29, 0xFB, 0x80,
))
D2F_HEAD_LEN = 29
CR_REJECT = bytes((0x68, 0x28, 0x38, 0x60))
CR_TAIL = bytes((
    0x8D, 0x02, 0x25, 0x8D, 0x02, 0x00, 0x68, 0x53, 0x01, 0x68, 0x28,
    0x18, 0x60,
))

# Full-ExpG image SHA256 pin (Issue #843 commonized build; any BOOT drift
# changes this hash). Measured from the deterministic debug build.
EXPG_IMAGE_SHA256 = "5310139318c714357278701d920e59b8d4373f433033ad276b7cf3d50a84d605"

# Payload CODE ($3000): seeds the $2502/$2503 shadow, snapshots the $20F3 /
# $20F7 mirrors pre-chain, calls the 8 ExpG slots through the real table
# entries recording the shadow after each CR head ($33/$2C/$2B), then
# records end state. MPR0=$F8 would trap I/O touches; MPR1=$F8 is the
# caller precondition. A 256-byte canary at $2900 ($5A) proves the
# work-RAM scope on the real core.
# res_base layout (32 bytes):
#   +0 ran marker ($AA)  +1..3 A/X/Y  +4 P  +5 SP  +6..13 MPR0..7
#   +14 shadow after $33  +15 shadow after $2C  +16 shadow after $2B
#   +17 marker after $2B  +18 $20F3 pre  +19 $20F7 pre
#   +20 $20F3 post  +21 $20F7 post  +22 zp $10 post
CHAIN_ASM = r"""
.setcpu "huc6280"
.segment "CODE"
payload_entry:
    cld
    ldx #$FF
    txs
    lda #$F8
    tam #$02
    lda #$F8
    tam #$01
    lda #$5A
    ldx #$00
canfill:
    sta $2900,x
    dex
    bne canfill
    lda #$A5
    sta $10
    lda #SEED_LO
    sta $2502
    .if SEED_EN
    lda #$A5
    .else
    lda #$00
    .endif
    sta $2503
    lda $20F3
    sta res_base+18
    lda $20F7
    sta res_base+19
    lda #$07
    ldx #$12
    ldy #$34
    pha
    lda #$31
    plp
    jsr $E099
    pha
    lda $2502
    sta res_base+14
    pla
    jsr $E0DE
    jsr $E06F
    jsr $E06C
    jsr $E0A2
    jsr $E09F
    jsr $E084
    pha
    lda $2502
    sta res_base+15
    pla
    jsr $E081
    pha
    lda $2502
    sta res_base+16
    lda $2503
    sta res_base+17
    pla
    sta res_base+1
    stx res_base+2
    sty res_base+3
    php
    pla
    sta res_base+4
    tsx
    txa
    sta res_base+5
    tma #$01
    sta res_base+6
    tma #$02
    sta res_base+7
    tma #$04
    sta res_base+8
    tma #$08
    sta res_base+9
    tma #$10
    sta res_base+10
    tma #$20
    sta res_base+11
    tma #$40
    sta res_base+12
    tma #$80
    sta res_base+13
    lda $10
    sta res_base+22
    lda $20F3
    sta res_base+20
    lda $20F7
    sta res_base+21
    lda #$AA
    sta res_base
payload_halt:
    bra payload_halt
res_base:
    .res 32
"""


class Expg843FullChain(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from unittest.mock import patch
        import build_rom
        from build_ipl_loader_g5_2base import build_ipl_loader_g5_rom, parse_labels
        from geargrafx_api import compile_probe
        cls._tmp = tempfile.TemporaryDirectory(prefix="pce-expg843-")
        cls.out = Path(cls._tmp.name)
        dev_out = Path(cls._tmp.name) / "dev"
        with patch("sys.argv", ["build_rom.py", "--mode", "debug",
                                "--output", str(dev_out)]):
            assert build_rom.main() == 0
        orig = dev_out / "dev-only-not-compatible-syscard3.pce"
        assert orig.is_file()
        cls.normal = orig.read_bytes()
        cls.sel = SEL
        cls.track_sel = TRACK_SEL
        # Full ExpG image (all seven experimentals).
        cls.rom, cls.syms = build_ipl_loader_g5_rom(
            orig, cls.out, cls.sel, "debug", "expg", ["-D", "IPL_PUBLIC"],
            track=cls.track_sel, private_input=False,
            cd_4a="experimental", cd_25="experimental",
            cd_24="experimental", cd_36="experimental",
            cd_35="experimental", cd_2c="experimental",
            cd_2b="experimental")
        cls.rom_stub, cls.syms_stub = build_ipl_loader_g5_rom(
            orig, cls.out, cls.sel, "debug", "expg-stub", ["-D", "IPL_PUBLIC"],
            track=cls.track_sel, private_input=False)
        if orig.read_bytes() != cls.normal:
            raise ValueError("BLOCKED: G5 build mutated the normal DEV ROM input")
        data = cls.rom.read_bytes()
        # Every ExpG slot entry is wired to its linked body through the
        # real table redirect.
        for slot, entry_name, pc in (("$33", "experimental_slot_33", 0xE099),
                                    ("$4A", "ex_memopen_entry", 0xE0DE),
                                    ("$25", "ex_scrmod_entry", 0xE06F),
                                    ("$24", "ex_dotmod_entry", 0xE06C),
                                    ("$36", "ex_satclr_entry", 0xE0A2),
                                    ("$35", "ex_sprdma_entry", 0xE09F),
                                    ("$2C", "ex_irqon_entry", 0xE084),
                                    ("$2B", "ex_rcroff_entry", 0xE081)):
            addr = cls.syms[entry_name]
            tab = data[pc - 0xE000:pc - 0xE000 + 3]
            assert tuple(tab) == (0x4C, addr & 0xFF, (addr >> 8) & 0xFF), (slot, addr)
        # CR-family pins on the full image: both 29-byte heads reach the
        # shared reject/tail, exact bytes in BOOT.
        for entry_name, mid in (("ex_irqon_entry", D2C_HEAD_MID),
                                ("ex_rcroff_entry", D2B_HEAD_MID)):
            addr = cls.syms[entry_name]
            assert 0xF000 <= addr <= 0xFFF6 - D2F_HEAD_LEN, (entry_name, hex(addr))
            off = addr - 0xE000
            assert data[off:off + 8] == D2C_HEAD_PREFIX, (entry_name,)
            assert data[off + 9:off + 28] == mid, (entry_name,)
            assert data[off + 8] == (cls.syms["cr_reject"] - (addr + 9)) & 0xFF, (entry_name,)
            assert data[off + 28] == (cls.syms["cr_tail_publish"] - (addr + 29)) & 0xFF, (
                entry_name,)
        tail, reject = cls.syms["cr_tail_publish"], cls.syms["cr_reject"]
        assert 0xF000 <= reject <= 0xFFF6 - 4, hex(reject)
        assert 0xF000 <= tail <= 0xFFF6 - 13, hex(tail)
        assert data[reject - 0xE000:reject - 0xE000 + 4] == CR_REJECT
        assert data[tail - 0xE000:tail - 0xE000 + 13] == CR_TAIL
        # Stub image keeps every opt-in table entry on its fallback. Slot
        # $33 is the exception: the $33 head is always wired (glue links
        # the CR-family unit unconditionally), so $E099 points at the
        # NEED_CR33 head even here.
        stub = cls.rom_stub.read_bytes()
        addr33 = cls.syms_stub["experimental_slot_33"]
        assert tuple(stub[0xE099 - 0xE000:0xE099 - 0xE000 + 3]) == (
            0x4C, addr33 & 0xFF, (addr33 >> 8) & 0xFF), ("stub $33", hex(addr33))
        for pc, fb_name in ((0xE0DE, "api_slot_4A"),
                            (0xE06F, "api_slot_25"), (0xE06C, "api_slot_24"),
                            (0xE0A2, "api_slot_36"), (0xE09F, "api_slot_35"),
                            (0xE084, "api_slot_2C"), (0xE081, "api_slot_2B")):
            fb = cls.syms_stub[fb_name]
            assert tuple(stub[pc - 0xE000:pc - 0xE000 + 3]) == (
                0x4C, fb & 0xFF, (fb >> 8) & 0xFF), (hex(pc), fb_name)
        man = json.loads((cls.out / "manifest-expg-g5.json").read_text())
        assert man["experimental_slots"] == [
            "$02", "$03", "$33", "$4A", "$25", "$24",
            "$36", "$35", "$2C", "$2B"], man["experimental_slots"]
        assert man["release_eligible"] is False
        assert man["dev_callable"] is False
        assert man.get("private_media_used") is False
        trace_full = cls.out / "ipl-loader-g5-expg.lbl"
        cls.labels_full = parse_labels(
            trace_full, ("api_unimplemented", "cd_halt_fail", "cd_unexpected_interrupt"))
        cls.probe = compile_probe(cls.out, "geargrafx_api_return_probe.cpp",
                                  "expg-probe")
        cls.eprobe = compile_probe(cls.out, "geargrafx_api_entry_probe.cpp",
                                   "expg-entry-probe")
        cls.writes_probe_2c = compile_probe(
            cls.out, "geargrafx_irqon2c_writes_probe.cpp", "expg-writes-2c")
        cls.writes_probe_2b = compile_probe(
            cls.out, "geargrafx_rcroff2b_writes_probe.cpp", "expg-writes-2b")
        import geargrafx_ipl_loader as L
        cls.L = L
        deps = json.loads((ROOT / "tools" / "dependencies.json").read_text())
        cls.geargrafx_rev = deps["geargrafx"]["revision"]
        print(f"GEARGRAFX_PINNED_REVISION={cls.geargrafx_rev}")

    @classmethod
    def tearDownClass(cls):
        import tempfile
        tmp = cls._tmp.name
        assert "pce-expg843-" in tmp and tmp.startswith(tempfile.gettempdir())
        cls._tmp.cleanup()

    # ---- image identity ----

    def test_expg_image_sha_pinned(self):
        # Full-ExpG image byte identity: any BOOT drift changes this hash.
        import hashlib
        data = self.rom.read_bytes()
        self.assertEqual(len(data), 262144)
        print(f"GEARGRAFX_PINNED_REVISION={self.geargrafx_rev}")
        self.assertEqual(hashlib.sha256(data).hexdigest(), EXPG_IMAGE_SHA256,
                         "full-ExpG image differs from the Issue #843 pin")

    def test_expg_allowlist_is_the_11_window_union(self):
        # The API-area diff of a full-ExpG image is exactly the
        # $4A/$25/$24/$36/$35/$2C/$2B table windows on top of the base
        # three windows (same helper the host suite pins).
        import sys
        sys.path[:0] = [str(ROOT / "tools")]
        import build_experimental_bios_boot as beb
        data = self.rom.read_bytes()
        api_diff = {i for i in range(0x1000) if data[i] != self.normal[i]}
        wall = beb.unified_api_windows_4a_25_24_36_35_2c_2b(
            self.syms["api_slot_02"])
        # The diff must stay inside the allowlist (some allowed windows
        # may carry identical bytes; same subset discipline as the host
        # full-ExpG test).
        self.assertLessEqual(api_diff, wall)

    # ---- chain harness ----

    def _check_canary(self, buf):
        self.assertEqual(buf[0x100:0x200], bytes((0x5A,) * 256))

    def _buf(self, rec):
        self.assertIn("RUN", rec)
        runs = rec["RUN"]
        self.assertIsInstance(runs, list)
        self.assertEqual(len(runs), 2)
        cycles = [r.get("cycle") for r in runs]
        self.assertEqual(sorted(cycles), ["cold", "warm"])
        bufs = {}
        pcs = []
        for r in runs:
            self.assertIn("cycle", r)
            self.assertIn("reason", r)
            self.assertIn("pc", r)
            self.assertIn("halt_confirmed", r)
            self.assertIn("buffer_hex", r)
            self.assertEqual(r.get("reason"), "cd_halt_ok")
            self.assertIs(r.get("halt_confirmed"), True)
            self.assertIsInstance(r.get("pc"), int)
            self.assertIs(r.get("apicall_truncated"), False)
            # The entry probe has no return-tracer; the return probe
            # records ret_truncated. Accept either schema, fail closed.
            if "ret_truncated" in r:
                self.assertIs(r.get("ret_truncated"), False)
            buf = bytes.fromhex(r["buffer_hex"])
            self.assertGreaterEqual(len(buf), 2048)
            bufs[r["cycle"]] = buf
            pcs.append(r["pc"])
        self.assertEqual(pcs[0], pcs[1], "clean-halt cold/warm pc differ")
        return bufs["cold"], bufs["warm"]

    def _run_chain(self, name, seed_lo, seed_en, rom=None):
        # Build the chain payload with SEED_LO/SEED_EN, deliver it through
        # the PUBLIC boot disc path, run on the given (default full ExpG)
        # image with per-APICALL VDC snapshots (GetState only,
        # non-invasive).
        import environment
        rom = self.rom if rom is None else rom
        o = self.out / ("chain-" + name)
        o.mkdir(exist_ok=True)
        src = o / "chain.s"
        text = CHAIN_ASM.replace("SEED_LO", f"${seed_lo:02X}").replace(
            "SEED_EN", "1" if seed_en else "0")
        text = text.replace("POPEN", "$07").replace("XONE", "$12").replace(
            "YONE", "$34").replace("SPINIT", "$FF")
        src.write_text(text, encoding="ascii")
        CFG = ("MEMORY {\n    RAM: start = $3000, size = $0F00, file = %O;\n}\n"
               "SEGMENTS {\n    CODE: load = RAM, type = ro, start = $3000;\n}\n")
        obj, binp, cfgp, lblp = (o / "chain.o", o / "chain.bin",
                                 o / "chain.cfg", o / "chain.lbl")
        cfgp.write_text(CFG, encoding="ascii")
        ca65, ld65 = environment.executable("ca65"), environment.executable("ld65")
        subprocess.run([ca65, str(src), "-o", str(obj), "-g"], check=True)
        subprocess.run([ld65, "-C", str(cfgp), "-o", str(binp), "-Ln", str(lblp),
                        str(obj)], check=True)
        text = lblp.read_text(encoding="ascii")
        syms = {}
        for line in text.splitlines():
            m = re.match(r"^al ([0-9A-Fa-f]{6}) \.(.+)$", line)
            if m:
                syms[m.group(2)] = int(m.group(1), 16)
        base = syms["res_base"] - 0x2800
        code = binp.read_bytes()
        body = self.L.body_for(code, 2)
        info = self.L.info_sector(2, 2, 0x3000, 0x3000, [0, 1, 2, 3, 4], 0,
                                  sum(body) & 0xFFFF)
        cue = self.L.make_disc(o, self.L.case_disc({"n": 2, "iplblk": 2}, body, info))
        m = re.search(r"^al ([0-9A-Fa-f]{6}) \.payload_halt$", text, re.M)
        halt = int(m.group(1), 16)
        lab = o / "chain.labels"
        lab.write_text(
            f"cd_halt_ok {halt:04x}\ncd_halt_fail {self.labels_full['cd_halt_fail']:04x}\n"
            f"cd_unexpected_interrupt {self.labels_full['cd_unexpected_interrupt']:04x}\n"
            f"api_unimplemented {self.labels_full['api_unimplemented']:04x}\n", encoding="ascii")
        import geargrafx_api_entry as Eentry
        rec = Eentry.run_probe(self.eprobe, rom, cue, lab, BUDGET, "synthetic",
                               vdc_snapshot=True)
        cold, warm = self._buf(rec)
        self.assertEqual(cold, warm)
        return rec, cold, warm, base

    def _check_routing(self, rec):
        # Per-call routing evidence on both cycles: exact slot/pc order
        # through the real table, balanced stacks, MPR1=$F8.
        for cycle in ("cold", "warm"):
            calls = [c for c in rec["APICALL"] if c["cycle"] == cycle]
            self.assertEqual([c["slot"] for c in calls], list(CHAIN_SLOTS), cycle)
            self.assertEqual([c["entry_pc"] for c in calls], list(CHAIN_PCS), cycle)
            for c in calls:
                self.assertTrue(c["in_table"], (cycle, c))
                self.assertEqual(c["sp_delta"], 2, (cycle, c))
                self.assertEqual(c.get("stack_check"), "MATCH", (cycle, c))
                self.assertTrue(c.get("return_matches_caller"), (cycle, c))
                self.assertEqual(c["mpr"][1], 0xF8, (cycle, c))
                for key in ("vdc_ar", "vdc_cr", "vdc_rcr", "vdc_sr"):
                    self.assertIn(key, c, (cycle, key))

    def _check_end_state(self, cold, base, seed_lo, seed_en, exp_p, exp_shadow):
        # End-to-end preservation: registers, SP, MPRs, marker, canary,
        # mirrors unchanged; final P/shadow per path.
        self.assertEqual(cold[base], 0xAA)
        self.assertEqual((cold[base + 1], cold[base + 2], cold[base + 3]),
                         (0x31, 0x12, 0x34))
        self.assertEqual(cold[base + 4] & ~0x10, exp_p)
        self.assertEqual(cold[base + 5], 0xFF)
        self.assertEqual(list(cold[base + 6:base + 14]),
                         [0xF8, 0xF8, 0x80, 0x81, 0x82, 0x83, 0x84, 0x00])
        self.assertEqual(cold[base + 14], exp_shadow[0])
        self.assertEqual(cold[base + 15], exp_shadow[1])
        self.assertEqual(cold[base + 16], exp_shadow[2])
        self.assertEqual(cold[base + 17], 0xA5 if seed_en else 0x00)
        self.assertEqual((cold[base + 18], cold[base + 19]),
                         (cold[base + 20], cold[base + 21]),
                         "mirrors $20F3/$20F7 changed mid-chain")
        self.assertEqual(cold[base + 22], 0xA5)
        self._check_canary(cold)

    # ---- ok chain (seeded): synchronized CR/shadow proof ----

    def test_chain_ok_synchronized(self):
        # Seeded $F4: $33 -> $34, $2C -> $3C, $2B -> $38. The per-call VDC
        # entry snapshots (GetState, non-invasive) prove the CR evolution;
        # the guest shadow recordings prove the publish order. The CR
        # heads write the SHADOW image (seed-derived), never the entry CR:
        # post-$33 CR low is $F4&$3F regardless of the recorded initial $L0.
        rec, cold, warm, base = self._run_chain("ok", 0xF4, 1)
        self._check_routing(rec)
        for cycle in ("cold", "warm"):
            calls = [c for c in rec["APICALL"] if c["cycle"] == cycle]
            self.assertEqual(len(calls), 8, cycle)
            h0 = calls[0]["vdc_cr"] >> 8
            rcr0 = calls[0]["vdc_rcr"]
            # CR high untouched by every ExpG body; RCR untouched.
            for c in calls:
                self.assertEqual(c["vdc_cr"] >> 8, h0, (cycle, c))
                self.assertEqual(c["vdc_rcr"], rcr0, (cycle, c))
            # $33 leaves AR=5; no B1/CR body touches AR afterwards except
            # the $2C/$2B ST0 (still 5).
            for c in calls[1:]:
                self.assertEqual(c["vdc_ar"], 5, (cycle, c))
            # CR-low evolution from the seed: $33 publishes $F4&$3F=$34
            # (visible at the $4A..$2C entries); $2C publishes $34|$08=$3C
            # (visible at the $2B entry).
            for c in calls[1:7]:
                self.assertEqual(c["vdc_cr"] & 0xFF, 0x34, (cycle, c))
            self.assertEqual(calls[7]["vdc_cr"] & 0xFF, 0x3C, (cycle, c))
            # Entry-state propagation: caller regs exact at every call;
            # C=0 from the $33 ok path on (family convention). The guest
            # pha/lda $2502/pla between calls clears Z (A=$31 nonzero),
            # so calls[1:] observe P=0x04 (I=1,Z=0,C=0), not 0x06.
            for c in calls:
                self.assertEqual((c["caller_a"], c["caller_x"], c["caller_y"]),
                                 (0x31, 0x12, 0x34), (cycle, c))
            self.assertEqual(calls[0]["caller_p"] & ~0x10, 0x07, cycle)
            for c in calls[1:]:
                self.assertEqual(c["caller_p"] & ~0x10, 0x04, (cycle, c))
            self.assertEqual(calls[0]["mpr"][1], 0xF8, cycle)
        # Guest-recorded intermediate shadow: $F4->$34->$3C->$38.
        self.assertEqual(cold[base + 14], 0x34)
        self.assertEqual(cold[base + 15], 0x3C)
        self.assertEqual(cold[base + 16], 0x38)
        self._check_end_state(cold, base, 0xF4, 1, 0x04, (0x34, 0x3C, 0x38))
        self.assertEqual(cold[base + 4] & 0x01, 0x00)
        self._check_canary(warm)

    # ---- reject chain (unseeded): zero-disturbance proof ----

    def test_chain_reject_no_write(self):
        # Unseeded ($2503=$00): every CR head rejects; all 8 VDC entry
        # snapshots are IDENTICAL (no VDC write, no ST0 anywhere), the
        # shadow/marker are untouched, final C=1 ($2B rejects last; B1
        # scaffolds preserve P on this entry class, as the ExpF chain
        # proves for C=0).
        rec, cold, warm, base = self._run_chain("rej", 0xF0, 0)
        self._check_routing(rec)
        for cycle in ("cold", "warm"):
            calls = [c for c in rec["APICALL"] if c["cycle"] == cycle]
            self.assertEqual(len(calls), 8, cycle)
            first = calls[0]
            for c in calls:
                self.assertEqual(c["vdc_cr"], first["vdc_cr"], (cycle, c))
                self.assertEqual(c["vdc_ar"], first["vdc_ar"], (cycle, c))
                self.assertEqual(c["vdc_rcr"], first["vdc_rcr"], (cycle, c))
                self.assertEqual(c["vdc_sr"], first["vdc_sr"], (cycle, c))
                self.assertEqual((c["caller_a"], c["caller_x"], c["caller_y"]),
                                 (0x31, 0x12, 0x34), (cycle, c))
            # C=1 preserved from entry through every reject + B1 scaffold.
            # Same guest Z-clobber as the ok chain: first call P=0x07,
            # later calls P=0x05 (I=1,Z=0,C=1).
            self.assertEqual(calls[0]["caller_p"] & ~0x10, 0x07, cycle)
            for c in calls[1:]:
                self.assertEqual(c["caller_p"] & ~0x10, 0x05, (cycle, c))
        self.assertEqual(cold[base + 14], 0xF0)
        self.assertEqual(cold[base + 15], 0xF0)
        self.assertEqual(cold[base + 16], 0xF0)
        self._check_end_state(cold, base, 0xF0, 0, 0x05, (0xF0, 0xF0, 0xF0))
        self.assertEqual(cold[base + 4] & 0x01, 0x01)
        self._check_canary(warm)

    # ---- mutation: the oracle fires on the full image ----

    def test_chain_mutant_wrongbit_red(self):
        # $2C-head WRONGBIT mutant (ORA #$08 -> ORA #$04) on the full ExpG
        # image, seeded $00: correct $08 vs mutant $04 after $2C; the $2B
        # AND #$FB mask then clears mutant bit 2, so final is $00
        # ($04 & $FB = $00), proving mask precision.
        data = bytearray(self.rom.read_bytes())
        off = (self.syms["ex_irqon_entry"] & 0xFFFF) - 0xE000
        data[off + 26] = 0x04
        tmp = self.out / "mutwrong.pce"
        tmp.write_bytes(bytes(data))
        rec, cold, warm, base = self._run_chain("mutwrong", 0x00, 1, rom=tmp)
        # The mutant ran: $04 observed where $08 is correct.
        self.assertEqual(cold[base + 15], 0x04)
        self.assertEqual(cold[base + 16], 0x00)
        with self.assertRaises(AssertionError):
            self._check_end_state(cold, base, 0x00, 1, 0x04, (0x00, 0x08, 0x08))

    # ---- write history on the full image (shared tail linked once) ----

    def _run_writes_on_full(self, name, probe, spinit, seed_lo, seed_en):
        o = self.out / ("writes-" + name)
        o.mkdir(exist_ok=True)
        (o / "d.bin").write_bytes(bytes(2352 * 32))
        cue = o / "d.cue"
        cue.write_text('FILE "d.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        r = subprocess.run([str(probe), str(self.rom.resolve()),
                            str(cue.resolve()), "4096", str(spinit),
                            str(seed_lo), str(seed_en)],
                           capture_output=True, text=True, timeout=120, check=True)
        recs = [json.loads(line[7:]) for line in r.stdout.splitlines()
                if line.startswith("RESULT ")]
        self.assertEqual(len(recs), 2, "write tracer must report cold+warm")
        cycles = sorted(x["cycle"] for x in recs)
        self.assertEqual(cycles, ["cold", "warm"])
        return recs

    def _check_writes_contained(self, recs, spinit, seed_en):
        # Same containment formula as the per-slot suites (change together
        # with test_g5_irqon_2c._allowed_2c / test_g5_rcroff_2b): transient
        # stack + guest-seed shadow addresses only; callee window publishes
        # $2502 on ok, nothing on reject.
        depth = 4 if seed_en else 3
        transient = set(range(0x100 + spinit - depth, 0x100 + spinit + 1))
        allowed = transient | {0x502, 0x503}
        callee_allowed = transient | ({0x502} if seed_en else set())
        for res in recs:
            self.assertTrue(res["halted"])
            self.assertFalse(res["truncated"])
            seen = set()
            for s, e in res["written"]:
                seen.update(range(s, e + 1))
            cseen = set()
            for s, e in res.get("written_callee", []):
                cseen.update(range(s, e + 1))
            self.assertLessEqual(seen, allowed, f"out-of-scope: {sorted(seen - allowed)}")
            self.assertTrue(transient <= seen)
            self.assertLessEqual(cseen, callee_allowed,
                                 f"callee out-of-scope: {sorted(cseen - callee_allowed)}")
            self.assertTrue(transient <= cseen)

    def test_writes_2c_full_image_ok(self):
        recs = self._run_writes_on_full("2c-ok", self.writes_probe_2c,
                                        0xFF, 0x00, 1)
        self._check_writes_contained(recs, 0xFF, 1)

    def test_writes_2c_full_image_reject(self):
        recs = self._run_writes_on_full("2c-rej", self.writes_probe_2c,
                                        0xFF, 0xF0, 0)
        self._check_writes_contained(recs, 0xFF, 0)

    def test_writes_2b_full_image_ok(self):
        recs = self._run_writes_on_full("2b-ok", self.writes_probe_2b,
                                        0xFF, 0x00, 1)
        self._check_writes_contained(recs, 0xFF, 1)

    def test_writes_2b_full_image_reject(self):
        recs = self._run_writes_on_full("2b-rej", self.writes_probe_2b,
                                        0xFF, 0xF0, 0)
        self._check_writes_contained(recs, 0xFF, 0)


if __name__ == "__main__":
    unittest.main()
