"""Issue #862 L5: loader-staging interaction run on the pinned synthetic core.

Synthetic media only; no ./rom access in any form; no commercial bytes. NOT a
compatible System Card BIOS, NOT a BIOS ABI, release_eligible=false.

The TEST-ONLY loader variant (IPL_L5_SNAPSHOT, tests/integration/
ipl_loader_rom.s) snapshots BUF offsets $0302-$0304 (the CR-shadow cells
$2502/$2503/$2504) into RB spare after the $5A fill, the INFO read, the
probe_last read, and the first/last BODY reads. The TEST-ONLY payload
(tests/integration/ipl_payload_l5.s) reports the same three cells plus the
entry MPR0/MPR1 at IPLJMP. Production loader bytes are unchanged by the
snapshot code (assembled with the define off, the ROM hash equals the
committed build; pinned by tests/host/test_862_loader_ownership.py).

Expected staging trace (n=5, DEV, IPLBLK=2, STA=$3000):
  FILL  = (5A,5A,5A)  -- post-$5A-fill, every read_sector
  INFO  = (D0,D1,D2)  -- INFO sector bytes at offsets $0302-$0304
  PRE   = (C4,C5,C6)  -- probe_last reads the FINAL body sector
  FIRST = (C0,C1,C2)  -- BODY sector 0
  LAST  = (C4,C5,C6)  -- BODY sector n-1 (last-sector-wins)
  RPT   = (C4,C5,C6) + MPR0=$FF + MPR1=$F8 at IPLJMP
Both cold and warm cycles must agree (staging determinism on synthetic
media); the shadow cells therefore hold last-sector bytes at IPLJMP
regardless of the pre-staging residual (loader-ownership-period).
"""
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(HERE))

from build_cdload_exec import parse_labels  # noqa: E402
from environment import requires_cc65  # noqa: E402
from geargrafx_api import compile_probe  # noqa: E402
from geargrafx_cdread import execute  # noqa: E402
from geargrafx_ipl_loader import (  # noqa: E402
    BUDGET,
    PAY_ORIGINS,
    RB,
    SEL,
    apply_edits,
    body_for,
    build_loader,
    case_disc,
    info_sector,
    judge,
    make_disc,
    require_cold_warm_cycles,
)

MODE = "debug"
INFO_TRIPLE = (0xD0, 0xD1, 0xD2)
BODY_TRIPLE = tuple((0xC0 + i, 0xC1 + i, 0xC2 + i) for i in range(5))
CASE = dict(name="l5_n5_last_sector_wins", pay="A", n=5, iplblk=2, sta=0x3000,
            entry=0x3000, mprs=[0x80, 0x81, 0x82, 0x83, 0x84], openmode=0,
            reason="cd_halt_ok", code=0, copied=5, payload_ran=True,
            expect_cdb_last=[0x08, 0x00, 0x01, 0x0F, 0x01, 0x00])

L5_FILL = RB + 0x24
L5_INFO = RB + 0x27
L5_PRE = RB + 0x2A
L5_FIRST = RB + 0x2D
L5_LAST = RB + 0x30
L5_RPT = RB + 0x34


def build_l5_payload(out: Path, origin: int, tag: int):
    """Assemble the L5 reporting payload (TEST-ONLY, synthetic harness only)."""
    import subprocess
    sys.path.insert(0, str(ROOT / "tools"))
    from environment import executable
    ca65, ld65 = executable("ca65"), executable("ld65")
    name = f"ipl_payload_l5_{origin:04x}_{tag:02x}"
    obj, bin_path, lbl, cfg = (out / f"{name}.o", out / f"{name}.bin",
                               out / f"{name}.lbl", out / f"{name}.cfg")
    cfg.write_text(f"MEMORY {{\n    RAM: start = ${origin:04X}, size = $0F00, file = %O;\n}}\n"
                   f"SEGMENTS {{\n    CODE: load = RAM, type = ro, start = ${origin:04X};\n}}\n",
                   encoding="ascii")
    subprocess.run([ca65, str(HERE / "ipl_payload_l5.s"), "-D", f"PAY_TAG=${tag:02X}",
                    "-o", str(obj), "-g"], check=True)
    subprocess.run([ld65, "-C", str(cfg), "-o", str(bin_path), "-Ln", str(lbl),
                    str(obj)], check=True)
    code = bin_path.read_bytes()
    symbols = parse_labels(lbl)
    if symbols.get("payload_entry") != origin or not 1 <= len(code) <= 255:
        raise ValueError("L5 payload must start at its origin and be 1..255 bytes")
    return code, symbols["payload_halt"]


def with_triple(buf: bytearray, triple):
    buf[0x0302:0x0305] = bytes(triple)
    return buf


@requires_cc65
class LoaderL5StagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        out = ROOT / "build" / MODE / "ipl-loader-l5"
        out.mkdir(parents=True, exist_ok=True)
        cls.out = out
        cls.loader, cls.loader_labels = build_loader(out, SEL, "l5",
                                                     ["-D", "IPL_L5_SNAPSHOT"])
        cls.loader_sha = hashlib.sha256(cls.loader.read_bytes()).hexdigest()
        origin, tag = PAY_ORIGINS[CASE["pay"]]
        cls.pay_code, cls.pay_halt = build_l5_payload(out, origin, tag)
        cls.probe = compile_probe(out, "geargrafx_cdread_probe.cpp",
                                  "geargrafx-ipl-loader-l5-probe")

    def test_l5_staging_trace_cold_warm(self):
        n = CASE["n"]
        body = body_for(self.pay_code, n)
        body_buf = bytearray(body)
        for i in range(n):
            body_buf[i * 2048 + 0x0302:i * 2048 + 0x0305] = bytes(BODY_TRIPLE[i])
        body_written = bytes(body_buf)
        cksum = sum(body_written) & 0xFFFF
        info = info_sector(CASE["iplblk"], n, CASE["sta"], CASE["entry"],
                           CASE["mprs"], CASE["openmode"], cksum)
        with_triple(info, INFO_TRIPLE)
        with tempfile.TemporaryDirectory(prefix="pce-ipl-l5-") as tmp:
            tmp = Path(tmp)
            frames = {}
            for i in range(n):
                frames[SEL + CASE["iplblk"] + i] = body_written[i * 2048:(i + 1) * 2048]
            frames[SEL + 1] = bytes(info)
            disc = tmp / CASE["name"]
            cue = make_disc(disc, frames)
            labels = tmp / f"{CASE['name']}.labels"
            labels.write_text(
                f"cd_halt_ok {self.pay_halt:04x}\n"
                f"cd_halt_fail {self.loader_labels['cd_halt_fail']:04x}\n"
                f"cd_unexpected_interrupt {self.loader_labels['cd_unexpected_interrupt']:04x}\n",
                encoding="ascii")
            records = execute(self.probe, self.loader, f"card:{cue}", labels,
                              BUDGET, None)
        # Standard loader contract first (result block, load area, payload).
        judge(records, CASE, body_written, PAY_ORIGINS[CASE["pay"]][1])
        runs = require_cold_warm_cycles(records, CASE["name"])
        seen = {}
        for run in runs:
            cycle = run["cycle"]
            buf = bytes.fromhex(run["buffer_hex"])
            if len(buf) != 6144:
                raise ValueError(f"{CASE['name']} {cycle}: dump has {len(buf)} bytes")
            self.assertTrue(run.get("halt_confirmed"))
            snap = {name: tuple(buf[off:off + 3]) for name, off in
                    (("FILL", L5_FILL), ("INFO", L5_INFO), ("PRE", L5_PRE),
                     ("FIRST", L5_FIRST), ("LAST", L5_LAST))}
            rpt = tuple(buf[L5_RPT:L5_RPT + 5])
            self.assertEqual(snap["FILL"], (0x5A, 0x5A, 0x5A),
                             f"{cycle}: post-fill snapshot")
            self.assertEqual(snap["INFO"], INFO_TRIPLE, f"{cycle}: INFO snapshot")
            self.assertEqual(snap["PRE"], BODY_TRIPLE[-1],
                             f"{cycle}: pre-check reads the final body sector")
            self.assertEqual(snap["FIRST"], BODY_TRIPLE[0],
                             f"{cycle}: first BODY snapshot")
            self.assertEqual(snap["LAST"], BODY_TRIPLE[-1],
                             f"{cycle}: last BODY snapshot (last-sector-wins)")
            self.assertNotEqual(snap["FIRST"], snap["LAST"],
                                f"{cycle}: sectors must be distinguishable")
            # IPLJMP handoff: shadow cells hold the last body sector's bytes;
            # MPR1=$F8 proves the cells were observed through work RAM.
            self.assertEqual(rpt[0:3], BODY_TRIPLE[-1],
                             f"{cycle}: IPLJMP shadow triple")
            self.assertEqual(rpt[3], 0xFF, f"{cycle}: entry MPR0")
            self.assertEqual(rpt[4], 0xF8, f"{cycle}: entry MPR1 (work RAM)")
            seen[cycle] = (snap, rpt)
        # Cold/warm determinism: staging overwrites every residual.
        self.assertEqual(seen["cold"], seen["warm"])
        if hashlib.sha256(self.loader.read_bytes()).hexdigest() != self.loader_sha:
            raise ValueError("L5 loader ROM changed during the test")


if __name__ == "__main__":
    unittest.main()
