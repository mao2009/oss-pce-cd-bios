#!/usr/bin/env python3
"""Issue #300: reusable ADPCM DMA-from-CD routine on the pinned real core (DEVELOPMENT ONLY, synthetic CD).

src/adt_dma_routine.s (routine) and src/adt_dma_routine_boot.s (boot and four calls) are project-owned HuC6280
code. One routine, four calls: A positive (LBA 5 -> ADPCM $0100), B negative (DMA never armed, code 11, bus
recovered), C recovery positive (LBA 5 -> $1100 after B), D bad input (LBA $200000, code 9, no bus activity).
Not BIOS, not AD_TRANS ($11), not a System Card compatibility claim; release_eligible=false.
The internal contract is docs/research/ad-trans-routine-contract.md.

Modes (run manually, not wired into the Makefile; TOOLS_DIR must point at the pinned tools):
  python3 tests/integration/geargrafx_ad_trans_routine.py --out build/ad-trans-routine
      Build the guest (pinned cc65), run the probe once, judge the raw observation, run the mutation tests, write manifest.json.
  python3 tests/integration/geargrafx_ad_trans_routine.py --out build/ad-trans-routine --verify
      Re-judge the saved raw observation and re-run the mutation tests. Makes no call to the core.
  python3 tests/integration/geargrafx_ad_trans_routine.py --selftest
      Judge and mutation table only, on an observation built from the contract (no cc65, no core). Not evidence of the core.
The raw stdout and the core build are GPL-linked execution outputs: keep them under build/ and never publish them.
"""
import argparse
import copy
import hashlib
import json
import platform
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "tools"))
from environment import DEPENDENCIES, executable  # noqa: E402
from geargrafx_api import compile_probe  # noqa: E402
from make_cd_image import build_image, sector_payload  # noqa: E402

BANK_BASE, BANK_BYTES, IMAGE_BYTES = 0xE000, 0x2000, 256 * 1024
LBA, SECTOR, RAM_SIZE = 5, 2048, 0x10000
MAX_STEPS = 120_000_000
# Boot first: cd_entry must land at $F000 (the BOOT segment starts with it), the routine follows.
GUEST_SOURCES = ("src/adt_dma_routine_boot.s", "src/adt_dma_routine.s")
PROBE_SOURCE = "geargrafx_ad_trans_dma_probe.cpp"  # reused unchanged from PR #406 (halt labels, result dump at $2300)
PROBE_TARGET = "ad-trans-routine-probe"
JUDGE_NAME = "ad-trans-routine-judge-v1"
SCHEMA = "ad-trans-routine-manifest-v1"
DEFAULT_OUT = ROOT / "build" / "ad-trans-routine"
DEST_A, DEST_B, DEST_C, DEST_D = 0x0100, 0x0900, 0x1100, 0x1500
ERR_DMA_NOT_DRAINED, ERR_BAD_REQUEST = 11, 9
DISC_RECIPE = "tools/make_cd_image.py build_image(sectors=16); guest reads LBA 5 = sector_payload(5)"

# Result slots: 8 bytes each at $2300 + offset. Fields: code, stage, scsi status, scsi msg, dma, stat, bus_ok, arm.
SLOTS = {"A": 0, "B": 8, "C": 16, "D": 24}
BUS_UNTOUCHED = 0xFF
EXPECTED = {
    #      code  stage  sst msg dma  bus_ok           arm
    "A": (0, 9, 0, 0, 0, 1, 1),
    "B": (ERR_DMA_NOT_DRAINED, 7, 0, 0, 0, 1, 0),
    "C": (0, 9, 0, 0, 0, 1, 1),
    "D": (ERR_BAD_REQUEST, 0, 0, 0, 0, BUS_UNTOUCHED, 1),
}
WRITE_ADDRESS_END = DEST_C + SECTOR


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_guest(directory: Path):
    """Assemble and link the routine and the boot with the pinned cc65. Returns (rom_bytes, symbols, ca65_version)."""
    ca65, ld65 = executable("ca65"), executable("ld65")
    version = subprocess.run([ca65, "--version"], check=True, capture_output=True, text=True)
    if DEPENDENCIES["cc65"]["revision"][:9] not in version.stdout + version.stderr:
        raise ValueError("cc65 does not report the pinned build ID")
    directory.mkdir(parents=True, exist_ok=True)
    objects = []
    for source in GUEST_SOURCES:
        obj = directory / (Path(source).stem + ".o")
        subprocess.run([ca65, str(ROOT / source), "-o", str(obj)], check=True)
        objects.append(str(obj))
    bank, labels = directory / "bank0.bin", directory / "guest.lbl"
    subprocess.run([ld65, "-C", str(ROOT / "src/bank0.cfg"), "-o", str(bank), "-Ln", str(labels),
                    "-m", str(directory / "guest.map"), *objects], check=True)
    body = bank.read_bytes()
    if len(body) != BANK_BYTES:
        raise ValueError("guest bank is not 8 KiB")
    symbols = {}
    for address, name in re.findall(r"^al ([0-9A-Fa-f]{6}) \.([A-Za-z_][A-Za-z_0-9]*)$", labels.read_text(), re.M):
        symbols[name] = int(address, 16)
    needed = ("cd_entry", "cd_halt_ok", "cd_halt_fail", "cd_unexpected_interrupt", "adt_dma_sector")
    if any(name not in symbols for name in needed) or symbols["cd_entry"] != 0xF000:
        raise ValueError("guest symbols or placement mismatch")
    (directory / "trace-labels.txt").write_text(
        "".join(f"{name} {symbols[name]:04x}\n" for name in needed), encoding="ascii")
    version_line = version.stdout.splitlines()[0] if version.stdout else version.stderr.splitlines()[0]
    return body + b"\xff" * (IMAGE_BYTES - BANK_BYTES), symbols, version_line


def run_probe(probe: Path, rom: Path, cue: Path, labels: Path) -> bytes:
    """Run the probe once and return its complete stdout bytes (the raw observation)."""
    result = subprocess.run([str(probe), str(rom), str(cue), str(labels), str(MAX_STEPS)],
                            capture_output=True, timeout=3600)
    if result.returncode != 0:
        raise ValueError(f"probe exit {result.returncode}: {result.stderr[-300:].decode('utf-8', 'replace')}")
    return result.stdout


def parse_observation(raw: bytes) -> dict:
    """The raw stdout must contain exactly one line starting with '{'; that line is the observation."""
    lines = [line for line in raw.split(b"\n") if line.startswith(b"{")]
    if len(lines) != 1:
        raise ValueError("probe stdout must contain exactly one observation line")
    return json.loads(lines[0].decode("utf-8"))


class CheckError(ValueError):
    """A judge refusal that names the check which fired. The mutation test asserts this exact id."""

    def __init__(self, check: str, message: str):
        super().__init__(message)
        self.check = check


def _require(condition: bool, check: str, message: str) -> None:
    if not condition:
        raise CheckError(check, message)


def judge(obs: dict) -> str:
    """Judge the one observation. The expected values are recomputed here from the contract, not read from the guest."""
    _require(type(obs) is dict and type(obs.get("halt_confirmed")) is bool and type(obs.get("adpcm")) is dict,
             "malformed", "malformed observation")
    result = bytes.fromhex(obs["result_hex"])
    ram = bytes.fromhex(obs["ram_hex"])
    adpcm = obs["adpcm"]
    _require(len(result) == 32 and len(ram) == RAM_SIZE, "block_size", "wrong block sizes")
    _require(obs["ram_pre_nonzero"] == 0, "ram_pre_zero", "ADPCM RAM was not zero before the guest ran")
    _require(obs["reason"] == "cd_halt_ok" and obs["halt_confirmed"], "halt_ok",
             f"guest did not reach cd_halt_ok ({obs['reason']})")
    for name, offset in SLOTS.items():
        slot = result[offset:offset + 8]
        _require(slot[0] == EXPECTED[name][0], f"slot_{name.lower()}_code",
                 f"call {name}: code {slot[0]} != {EXPECTED[name][0]}")
        _require(slot[1] == EXPECTED[name][1], f"slot_{name.lower()}_stage",
                 f"call {name}: stage {slot[1]} != {EXPECTED[name][1]}")
        _require(slot[2] == EXPECTED[name][2] and slot[3] == EXPECTED[name][3], f"slot_{name.lower()}_scsi",
                 f"call {name}: SCSI status/message differs")
        _require(slot[4] == EXPECTED[name][4], f"slot_{name.lower()}_dma",
                 f"call {name}: ADPCM_DMA at return is {slot[4]}")
        _require(slot[6] == EXPECTED[name][5], f"slot_{name.lower()}_bus_ok",
                 f"call {name}: bus_ok {slot[6]} != {EXPECTED[name][5]}")
        _require(slot[7] == EXPECTED[name][6], f"slot_{name.lower()}_arm",
                 f"call {name}: arm echo {slot[7]} != {EXPECTED[name][6]}")
    sector = sector_payload(LBA)
    _require(ram[DEST_A:DEST_A + SECTOR] == sector, "ram_sector_a",
             "call A: ADPCM RAM at $0100 differs from the sector image")
    _require(ram[DEST_C:DEST_C + SECTOR] == sector, "ram_sector_c",
             "call C: ADPCM RAM at $1100 differs from the sector image")
    _require(ram[:DEST_A] == bytes(DEST_A) and ram[DEST_A + SECTOR:DEST_C] == bytes(DEST_C - DEST_A - SECTOR),
             "ram_outside_zero", "ADPCM RAM changed outside the sectors of calls A and C")
    _require(ram[DEST_C + SECTOR:] == bytes(RAM_SIZE - DEST_C - SECTOR), "ram_tail_zero",
             "ADPCM RAM changed after the sector of call C")
    _require(adpcm["write_address"] == WRITE_ADDRESS_END, "adpcm_write_address",
             f"write pointer {adpcm['write_address']:#x} is not {WRITE_ADDRESS_END:#x}")
    _require(adpcm["dma"] == 0, "adpcm_dma_off", "ADPCM DMA still armed at the end")
    return "PASS_ADT_ROUTINE_FOUR_CALLS"


def _patch_slot(index, value):
    def mutate(obs):
        block = bytearray.fromhex(obs["result_hex"])
        block[index] = value
        obs["result_hex"] = block.hex()
    return mutate


def _flip_ram(offset):
    def mutate(obs):
        ram = bytearray.fromhex(obs["ram_hex"])
        ram[offset] ^= 0x01
        obs["ram_hex"] = ram.hex()
    return mutate


def _set_ram(offset, value):
    def mutate(obs):
        ram = bytearray.fromhex(obs["ram_hex"])
        ram[offset] = value
        obs["ram_hex"] = ram.hex()
    return mutate


def _set_field(field, value):
    def mutate(obs):
        obs["adpcm"][field] = value
    return mutate


def _set_reason(value):
    def mutate(obs):
        obs["reason"] = value
        obs["halt_confirmed"] = False
    return mutate


# (name, forgery, check id that must refuse it). Each forgery must change the observation and be refused by exactly that check.
MUTATIONS = (
    ("ram-sector-a-flip", _flip_ram(DEST_A + 5), "ram_sector_a"),
    ("ram-sector-c-flip", _flip_ram(DEST_C + 7), "ram_sector_c"),
    ("ram-leak-after-negative", _set_ram(DEST_B, 0x5A), "ram_outside_zero"),
    ("slot-b-code-forged-ok", _patch_slot(SLOTS["B"] + 0, 0), "slot_b_code"),
    ("slot-c-code-forged-fail", _patch_slot(SLOTS["C"] + 0, ERR_DMA_NOT_DRAINED), "slot_c_code"),
    ("slot-d-bus-forged-free", _patch_slot(SLOTS["D"] + 6, 1), "slot_d_bus_ok"),
    ("slot-d-code-forged-ok", _patch_slot(SLOTS["D"] + 0, 0), "slot_d_code"),
    ("slot-b-dma-forged-on", _patch_slot(SLOTS["B"] + 4, 1), "slot_b_dma"),
    ("write-address-forged", _set_field("write_address", DEST_C), "adpcm_write_address"),
    ("halt-forged-budget", _set_reason("budget"), "halt_ok"),
)


def mutation_test(base: dict) -> dict:
    """Judge the unmodified observation, then require each forgery to be refused by its declared check id."""
    expected = [check for _, _, check in MUTATIONS]
    if len(set(expected)) != len(expected):
        raise ValueError("mutation test invalid: declared check ids are not distinct")
    judge(copy.deepcopy(base))  # sanity: the unmodified observation must pass
    table = {}
    for name, forge, check in MUTATIONS:
        forged = copy.deepcopy(base)
        forge(forged)
        if forged == base:
            raise ValueError(f"mutation test invalid: {name} did not change the observation")
        try:
            judge(forged)
        except CheckError as error:
            if error.check != check:
                raise ValueError(f"mutation test FAILED: {name} refused by {error.check}, expected {check}") from error
            table[name] = check
            continue
        raise ValueError(f"mutation test FAILED: {name} observation accepted")
    return table


def contract_observation() -> dict:
    """Observation built from the contract, for --selftest only. It is not core output."""
    sector = sector_payload(LBA)
    result = bytearray(32)
    for name, offset in SLOTS.items():
        code, stage, sst, msg, dma, bus_ok, arm = EXPECTED[name]
        result[offset:offset + 8] = bytes([code, stage, sst, msg, dma, 0, bus_ok, arm])
    ram = bytearray(RAM_SIZE)
    ram[DEST_A:DEST_A + SECTOR] = sector
    ram[DEST_C:DEST_C + SECTOR] = sector
    return {"reason": "cd_halt_ok", "halt_confirmed": True, "steps": 0, "pc": 0, "ram_pre_nonzero": 0,
            "ram_post_nonzero": 2 * SECTOR, "adpcm": {"control": 0, "dma": 0, "status": 0, "playing": 0,
                                                    "address": 0, "write_address": WRITE_ADDRESS_END,
                                                    "read_address": 0, "length": 0},
            "result_hex": bytes(result).hex(), "ram_hex": bytes(ram).hex()}


def selftest() -> int:
    base = contract_observation()
    verdict = judge(base)
    table = mutation_test(base)
    print(f"SELFTEST PASS {verdict}: judge accepts the contract observation; {len(table)} forgeries refused by their declared checks")
    print("LIMIT: judge and mutation table only; no core run, not evidence of the guest.")
    return 0


def run(out: Path, argv: list) -> int:
    raw_dir, rom_dir = out / "raw", out / "rom"
    raw_dir.mkdir(parents=True, exist_ok=True)
    rom_dir.mkdir(parents=True, exist_ok=True)
    rom, symbols, cc65_version = build_guest(out / "guest")
    disc = build_image(out / "disc", sectors=16)
    cue = out / "disc" / "oss-pce-cd-test.cue"
    (out / "probe").mkdir(parents=True, exist_ok=True)
    probe = compile_probe(out / "probe", source_name=PROBE_SOURCE, target_name=PROBE_TARGET)
    labels = out / "guest" / "trace-labels.txt"  # name hex pairs, as the probe reads them
    (rom_dir / "routine.pce").write_bytes(rom)
    raw = run_probe(probe, rom_dir / "routine.pce", cue, labels)
    raw_path = raw_dir / "routine.stdout"
    raw_path.write_bytes(raw)
    obs = parse_observation(raw)
    verdict = judge(obs)
    mutation_checks = mutation_test(obs)
    manifest = {
        "schema": SCHEMA, "judge": JUDGE_NAME, "release_eligible": False,
        "observation_kind": "REAL_CORE_DEVICE_LAYER_PROJECT_GUEST", "disc": "SYNTHETIC",
        "verdict": verdict, "reason": obs["reason"], "steps": obs["steps"],
        "raw_file": "raw/routine.stdout", "raw_sha256": sha256_hex(raw),
        "rom_file": "rom/routine.pce", "rom_sha256": sha256_hex(rom),
        "geargrafx_revision": DEPENDENCIES["geargrafx"]["revision"],
        "geargrafx_source_sha256": DEPENDENCIES["geargrafx"]["sha256"],
        "probe_binary_sha256": hashlib.sha256(probe.read_bytes()).hexdigest(),
        "probe_source_sha256": sha256_hex((ROOT / "tests/integration" / PROBE_SOURCE).read_bytes()),
        "guest_sources_sha256": {src: sha256_hex((ROOT / src).read_bytes()) for src in GUEST_SOURCES},
        "driver_sha256": sha256_hex(Path(__file__).read_bytes()),
        "cc65": cc65_version, "disc_recipe": DISC_RECIPE, "disc_bin_sha256": disc["bin_sha256"],
        "max_steps": MAX_STEPS,
        "command": "python3 tests/integration/geargrafx_ad_trans_routine.py " + " ".join(argv),
        "verify_command": "python3 tests/integration/geargrafx_ad_trans_routine.py --out build/ad-trans-routine --verify",
        "environment": {"python": sys.version.split()[0], "platform": platform.platform()},
        "mutations": len(MUTATIONS), "mutation_checks": mutation_checks,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print("EVIDENCE " + json.dumps({"verdict": verdict, "raw_sha256": manifest["raw_sha256"],
                                     "reason": obs["reason"], "steps": obs["steps"],
                                     "result_hex": obs["result_hex"], "release_eligible": False}, sort_keys=True))
    print(f"MANIFEST {out / 'manifest.json'} sha256={sha256_hex((out / 'manifest.json').read_bytes())}")
    print(f"PASS issue-300 reusable ADPCM DMA routine: {len(mutation_checks)} forgeries refused by their declared checks")
    print("LIMIT: device-layer observation of the pinned core only; not AD_TRANS ($11), not BIOS; release_eligible=false.")
    return 0


def verify(out: Path) -> int:
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != SCHEMA or manifest.get("judge") != JUDGE_NAME or manifest.get("release_eligible") is not False:
        raise ValueError("manifest schema, judge or release_eligible mismatch")
    if manifest["driver_sha256"] != sha256_hex(Path(__file__).read_bytes()):
        raise ValueError("driver_sha256 differs from the working tree")
    for src, digest in manifest["guest_sources_sha256"].items():
        if digest != sha256_hex((ROOT / src).read_bytes()):
            raise ValueError(f"{src} differs from the recorded source")
    if sha256_hex((out / manifest["rom_file"]).read_bytes()) != manifest["rom_sha256"]:
        raise ValueError("ROM does not match its recorded sha256")
    if sha256_hex((out / "probe" / PROBE_TARGET).read_bytes()) != manifest["probe_binary_sha256"]:
        raise ValueError("probe binary does not match its recorded sha256")
    raw = (out / manifest["raw_file"]).read_bytes()
    if sha256_hex(raw) != manifest["raw_sha256"]:
        raise ValueError("raw observation does not match its recorded sha256 (modified?)")
    obs = parse_observation(raw)
    if judge(obs) != manifest["verdict"]:
        raise ValueError("recomputed verdict differs from the manifest")
    mutation_test(obs)
    print(f"VERIFY PASS {out / 'manifest.json'}: sources and raw observation match, verdict re-judged, "
          f"{len(MUTATIONS)} forgeries refused by their declared checks; no core call")
    return 0


def main(argv=None):
    args_list = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description="Issue #300 reusable ADPCM DMA routine real-core driver")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="artifact directory (default build/ad-trans-routine)")
    parser.add_argument("--verify", action="store_true", help="re-judge saved raw observation; no core run")
    parser.add_argument("--selftest", action="store_true", help="judge and mutation table only; no cc65, no core")
    args = parser.parse_args(args_list)
    if args.selftest:
        return selftest()
    out = args.out.resolve()
    if args.verify:
        return verify(out)
    return run(out, args_list)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.SubprocessError, KeyError, json.JSONDecodeError) as error:
        print(f"FAIL issue-300 ADPCM DMA routine: {error}", file=sys.stderr)
        raise SystemExit(1) from error
