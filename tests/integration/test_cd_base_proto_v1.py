"""Focused real-core tests for the EXPERIMENTAL CD_BASE proto V1 (Agent-A G5 P0).

Pinned Geargrafx b49ae82a + pinned cc65 71746c8. Synthetic discs only.
Asserts loader success (cold+warm) plus LBA/INDEX01/state/MPR/RAM for the
experimental contract. Genuine ABI stays UNKNOWN/BLOCKED; this file never
claims VERIFIED/DEV_CALLABLE/compatibility. release_eligible=False.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from environment import DEPENDENCIES  # noqa: E402
from build_cdload_exec import EXEC_ADDRESS, EXPECTED_MPR, encode_sector  # noqa: E402
from build_experimental_bios_boot import build_experimental_bios_boot, build_experimental_payload  # noqa: E402
from experimental_track_selection import selection_from_cue, snapshot_media, verify_media_identity  # noqa: E402
from geargrafx_api import compile_probe  # noqa: E402
from geargrafx_cdread import execute  # noqa: E402
from cdread_verify import check_halt_confirmation, result_block  # noqa: E402
import cd_base_probe  # noqa: E402
from make_cd_image import RAW_BYTES  # noqa: E402
from build_cdload_exec import SECTOR_BYTES, USER_OFFSET  # noqa: E402

MODE = "debug"
STEP_BUDGET = 12_000_000
BUFFER_BASE = 0x2800
RES_OFF = 0x3100 - BUFFER_BASE
EXPECTED_MPR_LIST = list(EXPECTED_MPR)
SEQ2 = 0xE006


def build_payload(source_name, out_dir, name, idx_lo, idx_mid, idx_hi, track):
    from environment import executable
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    src = Path(__file__).resolve().parent / source_name
    obj = out_dir / f"{name}.o"
    bin_path = out_dir / f"{name}.bin"
    lbl = out_dir / f"{name}.lbl"
    cfg = out_dir / f"{name}.cfg"
    cfg.write_text("MEMORY { RAM: start = $3000, size = $0800, file = %O; }\n"
                   "SEGMENTS { PAYLOAD: load = RAM, type = ro, start = $3000; }\n", encoding="ascii")
    defines = [f"-DSEQ2={SEQ2}", f"-DIDXLO={idx_lo}", f"-DIDXMID={idx_mid}",
               f"-DIDXHI={idx_hi}", f"-DSELTRACK={track}"]
    subprocess.run([executable("ca65"), str(src), *defines, "-o", str(obj), "-g"], check=True)
    subprocess.run([executable("ld65"), "-C", str(cfg), "-o", str(bin_path),
                    "-Ln", str(lbl), str(obj)], check=True)
    code = bin_path.read_bytes()
    if not 1 <= len(code) <= 255:
        raise ValueError(f"{name} payload {len(code)} bytes outside 1..255")
    text = lbl.read_text(encoding="utf-8")
    import re
    found = {}
    for address, label in re.findall(r"^al ([0-9A-Fa-f]{6}) \.([A-Za-z_][A-Za-z_0-9]*)$", text, re.M):
        if label in ("payload_entry", "payload_done"):
            found.setdefault(label, int(address, 16))
    if set(found) != {"payload_entry", "payload_done"}:
        raise ValueError(f"{name} payload labels missing")
    if found["payload_entry"] != EXEC_ADDRESS:
        raise ValueError(f"{name} payload_entry != ${EXEC_ADDRESS:04X}")
    return code, found


def patch_index01_sector(cue, index01, code):
    from make_cd_image import RAW_BYTES
    from build_cdload_exec import USER_OFFSET, encode_sector
    bin_name = cue.name.replace(".cue", ".bin")
    raw = bytearray((cue.parent / bin_name).read_bytes())
    sector = encode_sector(index01, code)
    # cd_base_probe fixtures: BIN frame 0 is INDEX01.
    at = 0 * RAW_BYTES + USER_OFFSET
    raw[at:at + SECTOR_BYTES] = sector
    (cue.parent / bin_name).write_bytes(bytes(raw))


def proto_results(buffer):
    if len(buffer) != 6144:
        raise ValueError(f"RAM dump {len(buffer)} bytes, expected 6144")
    res = buffer[RES_OFF:RES_OFF + 35]
    return {
        "magic": res[0],
        "a1": res[1], "a2": res[2], "a3": res[3],
        "a4": res[4], "a5": res[5], "a6": res[6], "a7": res[7],
        "p1": res[8], "p3": res[9], "p4": res[8],
        "sp": res[10],
        "state": list(res[11:19]),
        "inputs": list(res[19:27]),
        "mpr": list(res[27:35]),
    }


class ProtoV1RealCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not sys.platform.startswith("linux"):
            raise unittest.SkipTest("real-core needs Linux-pinned Geargrafx")
        try:
            from environment import executable
            executable("ca65")
        except Exception as error:
            raise unittest.SkipTest(f"cc65 unavailable: {error}")
        cls.tmp = tempfile.TemporaryDirectory(prefix="pce-cd-base-proto-")
        root = Path(cls.tmp.name)
        (root / "probe").mkdir(parents=True, exist_ok=True)
        cls.probe = compile_probe(root / "probe", "geargrafx_cdread_probe.cpp",
                                  "geargrafx-cd-base-proto")
        from build_rom import verify_dev_rom
        base = ROOT / "build" / MODE / "bios-dev" / "dev-only-not-compatible-syscard3.pce"
        if not base.exists():
            subprocess.run([sys.executable, str(ROOT / "tools" / "build_rom.py"),
                            "--mode", MODE], check=True)
        cls.base = base
        cls.cases = {}
        for fixture, build_fn, idx in (("idx150", cd_base_probe.build_fixture, 150),
                                       ("idx0", cd_base_probe.build_zero_fixture, 0)):
            for payload_name, source in (("a", "test_cd_base_proto_payload_a.s"),
                                         ("b", "test_cd_base_proto_payload_b.s")):
                case_dir = root / f"{fixture}-{payload_name}"
                case_dir.mkdir()
                # Build fixture disc (original synthetic).
                if fixture == "idx150":
                    cd_base_probe.build_fixture(case_dir)
                    cue = case_dir / cd_base_probe.CUE_NAME
                else:
                    cd_base_probe.build_zero_fixture(case_dir)
                    cue = case_dir / cd_base_probe.ZERO_CUE_NAME
                idx_lo, idx_mid, idx_hi = idx & 0xFF, (idx >> 8) & 0xFF, (idx >> 16) & 0xFF
                code, pay_syms = build_payload(source, case_dir / "pay", payload_name,
                                     idx_lo, idx_mid, idx_hi, 1)
                patch_index01_sector(cue, idx, code)
                cue = snapshot_media(cue, root / f"{fixture}-{payload_name}-mounted")
                selected = selection_from_cue(cue, cue)
                if selected["lba"] != idx or selected["track"] != 1:
                    raise ValueError(f"selection mismatch: {selected}")
                out = case_dir / "rom"
                build_experimental_bios_boot(cls.base, out, MODE, cue, code, pay_syms)
                import json
                manifest = json.loads((out / "manifest.json").read_text())
                labels = out / "trace-labels.txt"
                verify_media_identity(selected["media_identity"], cue)
                records = execute(cls.probe, out / "experimental-dev-only-not-compatible-syscard-sized.pce",
                                  cue, labels, STEP_BUDGET)
                cls.cases[(fixture, payload_name)] = (records, manifest, selected, code)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def check_loader_success(self, records, manifest, selected, code):
        self.assertEqual([r["cycle"] for r in records["RUN"]], ["cold", "warm"])
        self.assertFalse(records.get("BLOCK"))
        for run in records["RUN"]:
            self.assertEqual(run["reason"], "cd_halt_ok")
            self.assertEqual(run["pc"], manifest["trace_symbols"]["cd_halt_ok"])
            check_halt_confirmation(run)
            self.assertEqual(run["sp"], 0xFF)
            block = result_block(run)
            self.assertEqual((block["code"], block["stage"], block["bytes"]),
                             (0, 16, SECTOR_BYTES))
            buffer = bytes.fromhex(run["buffer_hex"])
            # First sector intact (results live at $3100, outside $2800-$2FFF).
            self.assertEqual(buffer[:SECTOR_BYTES], encode_sector(selected["lba"], code))

    def test_a_idx150_normal_boundary(self):
        records, manifest, selected, code = self.cases[("idx150", "a")]
        self.check_loader_success(records, manifest, selected, code)
        for run in records["RUN"]:
            res = proto_results(bytes.fromhex(run["buffer_hex"]))
            self.assertEqual(res["magic"], 0xAA)
            self.assertEqual((res["a1"], res["a2"], res["a3"]), (0, 0, 0x30))
            # HuC6280 P: bit5 is T=0 (not 6502 always-1); success Z=1/C=0, fail Z=0/C=1, I=1/D=0.
            self.assertEqual(res["p1"], 0x16)
            self.assertEqual(res["p3"], 0x15)
            self.assertEqual(res["sp"], 0xFF)
            # State: base=155 (0,0,155), gen=2, last=0x30, off=5.
            self.assertEqual(res["state"], [0, 0, 155, 2, 0x30, 0, 0, 5])
            self.assertEqual(res["inputs"], [1, 0, 0, 0x80, 149, 0, 0, 0])
            self.assertEqual(res["mpr"], EXPECTED_MPR_LIST)

    def test_a_idx0_control(self):
        records, manifest, selected, code = self.cases[("idx0", "a")]
        self.check_loader_success(records, manifest, selected, code)
        for run in records["RUN"]:
            res = proto_results(bytes.fromhex(run["buffer_hex"]))
            self.assertEqual(res["magic"], 0xAA)
            # INDEX01=0: base=0 success, base=5 success, base=$FFFFFF range error.
            self.assertEqual((res["a1"], res["a2"], res["a3"]), (0, 0, 0x21))
            self.assertEqual(res["p1"], 0x16)
            self.assertEqual(res["p3"], 0x15)
            self.assertEqual(res["sp"], 0xFF)
            # base=5 (0,0,5), gen=2, last=0x21, off=5.
            self.assertEqual(res["state"], [0, 0, 5, 2, 0x21, 0, 0, 5])
            self.assertEqual(res["inputs"], [1, 0, 0, 0x80, 0xFF, 0xFF, 0xFF, 0])
            self.assertEqual(res["mpr"], EXPECTED_MPR_LIST)

    def test_b_abnormal_taxonomy(self):
        for fixture in ("idx150", "idx0"):
            records, manifest, selected, code = self.cases[(fixture, "b")]
            self.check_loader_success(records, manifest, selected, code)
            for run in records["RUN"]:
                res = proto_results(bytes.fromhex(run["buffer_hex"]))
                self.assertEqual(res["magic"], 0xAA, fixture)
                self.assertEqual((res["a4"], res["a5"], res["a6"], res["a7"]),
                                 (0x10, 0x11, 0x12, 0x21), fixture)
                self.assertEqual(res["p4"], 0x15, fixture)
                self.assertEqual(res["sp"], 0xFF, fixture)
                # All fail: base 0, gen 0, last 0x21, off 0.
                self.assertEqual(res["state"], [0, 0, 0, 0, 0x21, 0, 0, 0], fixture)
                self.assertEqual(res["inputs"], [1, 0, 0, 0x80, 0, 0, 0x20, 0], fixture)
                self.assertEqual(res["mpr"], EXPECTED_MPR_LIST, fixture)


if __name__ == "__main__":
    unittest.main()
