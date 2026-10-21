#!/usr/bin/env python3
"""Issue #400: ADPCM DMA-from-CD guest on the pinned real core (DEVELOPMENT ONLY, synthetic CD).

src/ad_trans_dma_probe.s is a project-owned HuC6280 guest. It runs one READ(6) over the SCSI
registers and lets the ADPCM DMA ($180B) move the 2048 DATA-IN bytes of LBA 5 into ADPCM RAM at $0100.
Positive: all bytes match the synthetic sector, nothing else in ADPCM RAM changes.
Negative: the same ROM with cd_dma_enable=0 never arms the DMA; the data phase cannot drain (code 11)
and ADPCM RAM stays zero. The SAME judge accepts the positive and refuses every mutated observation.
Not BIOS, not AD_TRANS ($11), not a System Card compatibility claim; release_eligible=false.

Modes (run manually, not wired into the Makefile; TOOLS_DIR must point at the pinned tools):
  python3 tests/integration/geargrafx_ad_trans_dma.py --out build/ad-trans-dma
      Build, run both cases, save the raw probe stdout per case, write manifest.json.
  python3 tests/integration/geargrafx_ad_trans_dma.py --out build/ad-trans-dma --verify
      Reload the saved raw stdout and ROMs from --out, check every recorded SHA-256, recompute the
      verdicts and re-run the mutation tests. Makes no call to the core.
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
LBA, SECTOR = 5, 2048
DEST = 0x0100
MAX_STEPS = 30_000_000
ERR_DMA_NOT_DRAINED = 11
STAGE_DATA_WAIT = 7   # guest sets RES+1=7 right before waiting for the data phase to drain
RES_STAGE_DONE = 9    # guest sets RES+1=9 on the success path
RAM_SIZE = 0x10000
JUDGE_NAME = "ad-trans-dma-judge-v2"
SCHEMA = "ad-trans-dma-manifest-v1"
DEFAULT_OUT = ROOT / "build" / "ad-trans-dma"
CASES = (("pos-dma-sector", "pos.pce"), ("neg-dma-not-armed", "neg.pce"))
PROBE_SOURCE = "geargrafx_ad_trans_dma_probe.cpp"
GUEST_SOURCE = "src/ad_trans_dma_probe.s"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_guest(directory: Path):
    """Assemble and link the guest with the pinned cc65. Returns (rom_bytes, symbols, ca65_version)."""
    ca65, ld65 = executable("ca65"), executable("ld65")
    version = subprocess.run([ca65, "--version"], check=True, capture_output=True, text=True)
    if DEPENDENCIES["cc65"]["revision"][:9] not in version.stdout + version.stderr:
        raise ValueError("cc65 does not report the pinned build ID")
    directory.mkdir(parents=True, exist_ok=True)
    obj, bank, labels = directory / "ad_trans_dma_probe.o", directory / "bank0.bin", directory / "guest.lbl"
    subprocess.run([ca65, str(ROOT / GUEST_SOURCE), "-o", str(obj)], check=True)
    subprocess.run([ld65, "-C", str(ROOT / "src/bank0.cfg"), "-o", str(bank), "-Ln", str(labels),
                    "-m", str(directory / "guest.map"), str(obj)], check=True)
    body = bank.read_bytes()
    if len(body) != BANK_BYTES:
        raise ValueError("guest bank is not 8 KiB")
    symbols = {}
    for address, name in re.findall(r"^al ([0-9A-Fa-f]{6}) \.([A-Za-z_][A-Za-z_0-9]*)$", labels.read_text(), re.M):
        symbols[name] = int(address, 16)
    needed = ("cd_entry", "cd_cdb", "cd_dma_enable", "cd_halt_ok", "cd_halt_fail", "cd_unexpected_interrupt")
    if any(name not in symbols for name in needed) or symbols["cd_entry"] != 0xF000:
        raise ValueError("guest symbols or placement mismatch")
    (directory / "trace-labels.txt").write_text(
        "".join(f"{name} {symbols[name]:04x}\n" for name in needed), encoding="ascii")
    version_line = version.stdout.splitlines()[0] if version.stdout else version.stderr.splitlines()[0]
    return body + b"\xff" * (IMAGE_BYTES - BANK_BYTES), symbols, version_line


def with_dma_enable(rom: bytes, symbols: dict, value: int) -> bytes:
    """Test input: flip only the cd_dma_enable byte (the negative case)."""
    patched = bytearray(rom)
    patched[symbols["cd_dma_enable"] - BANK_BASE] = value
    return bytes(patched)


def run_probe(probe: Path, rom: Path, cue: Path, labels: Path) -> bytes:
    """Run the probe once and return its complete stdout bytes (the raw observation)."""
    result = subprocess.run([str(probe), str(rom), str(cue), str(labels), str(MAX_STEPS)],
                            capture_output=True, timeout=1800)
    if result.returncode != 0:
        raise ValueError(f"probe exit {result.returncode}: {result.stderr[-300:].decode('utf-8', 'replace')}")
    return result.stdout


def parse_observation(raw: bytes) -> dict:
    """The raw stdout must contain exactly one line starting with '{'; that line is the observation."""
    lines = [line for line in raw.split(b"\n") if line.startswith(b"{")]
    if len(lines) != 1:
        raise ValueError("probe stdout must contain exactly one observation line")
    return json.loads(lines[0].decode("utf-8"))


def json_line_sha256(raw: bytes) -> str:
    """Hash of the observation line alone, without its terminator: the definition used by the PR #406 body."""
    line = [line for line in raw.split(b"\n") if line.startswith(b"{")][0]
    return sha256_hex(line)


def expected_ram():
    """Only [DEST, DEST+2048) holds the synthetic sector; every other byte of ADPCM RAM is zero."""
    ram = bytearray(RAM_SIZE)
    ram[DEST:DEST + SECTOR] = sector_payload(LBA)
    return bytes(ram)


def judge(case: str, obs: dict) -> str:
    """Judge one observation. The expected verdict is recomputed here; a reported reason cannot override it."""
    if type(obs) is not dict or type(obs.get("halt_confirmed")) is not bool or type(obs.get("adpcm")) is not dict:
        raise ValueError(f"{case}: malformed observation")
    result = bytes.fromhex(obs["result_hex"])
    ram = bytes.fromhex(obs["ram_hex"])
    adpcm = obs["adpcm"]
    if len(result) != 32 or len(ram) != RAM_SIZE:
        raise ValueError(f"{case}: wrong block sizes")
    if obs["ram_pre_nonzero"] != 0:
        raise ValueError(f"{case}: ADPCM RAM was not zero before the guest ran")
    if case == "pos-dma-sector":
        if obs["reason"] != "cd_halt_ok" or not obs["halt_confirmed"]:
            raise ValueError(f"{case}: guest did not reach cd_halt_ok ({obs['reason']})")
        if result[0] != 0 or result[1] != RES_STAGE_DONE or result[4] != 0 or result[5] != 0:
            raise ValueError(f"{case}: guest result block not OK (res={result[0]} stage={result[1]})")
        if ram != expected_ram():
            raise ValueError(f"{case}: ADPCM RAM differs from the expected sector image")
        if adpcm["write_address"] != DEST + SECTOR or adpcm["dma"] != 0 or result[8] != 0:
            raise ValueError(f"{case}: DMA or write pointer not drained at the end")
        return "PASS_ADPCM_DMA_SECTOR_TO_RAM"
    if case == "neg-dma-not-armed":
        if obs["reason"] != "cd_halt_fail" or not obs["halt_confirmed"]:
            raise ValueError(f"{case}: guest did not reach cd_halt_fail ({obs['reason']})")
        if result[0] != ERR_DMA_NOT_DRAINED or result[1] != STAGE_DATA_WAIT or result[7] != 0:
            raise ValueError(f"{case}: expected DMA_NOT_DRAINED at stage 7 with DMA not armed "
                             f"(res={result[0]} stage={result[1]})")
        if obs["ram_post_nonzero"] != 0 or ram != bytes(RAM_SIZE):
            raise ValueError(f"{case}: ADPCM RAM changed without the DMA being armed")
        if adpcm["write_address"] != DEST or adpcm["dma"] != 0:
            raise ValueError(f"{case}: write latch not at the programmed address or DMA still armed")
        return "NEG_DMA_NOT_ARMED_NO_TRANSFER"
    raise ValueError(f"unknown case {case}")


def _flip_ram(obs):
    ram = bytearray.fromhex(obs["ram_hex"])
    ram[DEST + 7] ^= 0x01
    obs["ram_hex"] = ram.hex()


def _shift_ram(obs):
    obs["ram_hex"] = (bytes(1) + bytes.fromhex(obs["ram_hex"])[:-1]).hex()


def _budget_stop(obs):
    obs["reason"] = "budget"
    obs["halt_confirmed"] = False


def _fake_neg_ram(obs):
    ram = bytearray(RAM_SIZE)
    ram[DEST:DEST + SECTOR] = sector_payload(LBA)
    obs["ram_hex"] = ram.hex()
    obs["ram_post_nonzero"] = SECTOR


def _set_result(index, value):
    def mutate(obs):
        block = bytearray.fromhex(obs["result_hex"])
        block[index] = value
        obs["result_hex"] = block.hex()
    return mutate


def _set_adpcm(field, value):
    def mutate(obs):
        obs["adpcm"][field] = value
    return mutate


# (name, case whose observation is forged, forgery). Each forgery must change the observation and be refused.
MUTATIONS = (
    ("pos-ram-flip", "pos-dma-sector", _flip_ram),
    ("pos-ram-shift", "pos-dma-sector", _shift_ram),
    ("pos-budget-stop", "pos-dma-sector", _budget_stop),
    ("pos-result-hex-res-forged", "pos-dma-sector", _set_result(0, ERR_DMA_NOT_DRAINED)),
    ("pos-result-hex-stage-forged", "pos-dma-sector", _set_result(1, STAGE_DATA_WAIT)),
    ("pos-adpcm-dma-forged", "pos-dma-sector", _set_adpcm("dma", 1)),
    ("pos-adpcm-write-address-forged", "pos-dma-sector", _set_adpcm("write_address", DEST)),
    ("neg-ram-faked", "neg-dma-not-armed", _fake_neg_ram),
    ("neg-result-hex-res-forged-ok", "neg-dma-not-armed", _set_result(0, 0)),
    ("neg-result-hex-stage-forged", "neg-dma-not-armed", _set_result(1, RES_STAGE_DONE)),
    ("neg-adpcm-dma-forged", "neg-dma-not-armed", _set_adpcm("dma", 1)),
    ("neg-adpcm-write-address-forged", "neg-dma-not-armed", _set_adpcm("write_address", DEST + SECTOR)),
)


def mutation_test(base: dict) -> None:
    """Judge the unmodified observations, then refuse every forgery. base maps case -> observation."""
    for case, obs in base.items():
        judge(case, copy.deepcopy(obs))  # sanity: the unmodified observation must pass
    for name, case, forge in MUTATIONS:
        forged = copy.deepcopy(base[case])
        forge(forged)
        if forged == base[case]:
            raise ValueError(f"mutation test invalid: {name} did not change the observation")
        try:
            judge(case, forged)
        except ValueError:
            continue
        raise ValueError(f"mutation test FAILED: {name} observation accepted")


def check_raw(case: str, raw_path: Path, expected_sha: str):
    """Reload one saved raw observation, check its SHA-256, and return (observation, verdict)."""
    raw = raw_path.read_bytes()
    if sha256_hex(raw) != expected_sha:
        raise ValueError(f"{case}: raw observation does not match its recorded sha256 (modified?)")
    obs = parse_observation(raw)
    return obs, judge(case, obs)


def run(out: Path, argv: list) -> int:
    raw_dir, rom_dir = out / "raw", out / "rom"
    raw_dir.mkdir(parents=True, exist_ok=True)
    rom_dir.mkdir(parents=True, exist_ok=True)
    rom_pos, symbols, cc65_version = build_guest(out / "guest")
    rom_neg = with_dma_enable(rom_pos, symbols, 0)
    if sum(a != b for a, b in zip(rom_pos, rom_neg)) != 1:
        raise ValueError("positive and negative ROMs must differ in exactly one byte")
    disc = build_image(out / "disc", sectors=16)
    cue = out / "disc" / "oss-pce-cd-test.cue"
    (out / "probe").mkdir(parents=True, exist_ok=True)
    probe = compile_probe(out / "probe", source_name=PROBE_SOURCE, target_name="ad-trans-dma-probe")
    labels = out / "guest" / "trace-labels.txt"
    roms = {"pos-dma-sector": rom_pos, "neg-dma-not-armed": rom_neg}
    for case, rom_name in CASES:
        (rom_dir / rom_name).write_bytes(roms[case])
    cases, base = {}, {}
    for case, rom_name in CASES:
        raw = run_probe(probe, rom_dir / rom_name, cue, labels)
        raw_path = raw_dir / f"{case}.stdout"
        raw_path.write_bytes(raw)
        obs, verdict = check_raw(case, raw_path, sha256_hex(raw))
        base[case] = obs
        cases[case] = {
            "verdict": verdict, "reason": obs["reason"], "steps": obs["steps"],
            "raw_file": f"raw/{case}.stdout", "raw_sha256": sha256_hex(raw),
            "json_line_sha256": json_line_sha256(raw),
            "rom_file": f"rom/{rom_name}", "rom_sha256": sha256_hex(roms[case]),
        }
    mutation_test(base)
    manifest = {
        "schema": SCHEMA, "judge": JUDGE_NAME, "release_eligible": False,
        "observation_kind": "REAL_CORE_DEVICE_LAYER_PROJECT_GUEST", "disc": "SYNTHETIC",
        "geargrafx_revision": DEPENDENCIES["geargrafx"]["revision"],
        "geargrafx_source_sha256": DEPENDENCIES["geargrafx"]["sha256"],
        "probe_binary_sha256": hashlib.sha256(probe.read_bytes()).hexdigest(),
        "probe_source_sha256": sha256_hex((ROOT / "tests/integration" / PROBE_SOURCE).read_bytes()),
        "guest_source_sha256": sha256_hex((ROOT / GUEST_SOURCE).read_bytes()),
        "driver_sha256": sha256_hex(Path(__file__).read_bytes()),
        "cc65": cc65_version,
        "disc_recipe": "tools/make_cd_image.py build_image(sectors=16); guest reads LBA 5 = sector_payload(5)",
        "disc_bin_sha256": disc["bin_sha256"],
        "cd_dma_enable_patch": {"symbol": "cd_dma_enable", "pos": 1, "neg": 0},
        "max_steps": MAX_STEPS,
        "command": "python3 tests/integration/geargrafx_ad_trans_dma.py " + " ".join(argv),
        "environment": {"python": sys.version.split()[0], "platform": platform.platform()},
        "cases": cases,
        "mutations": len(MUTATIONS),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    for case, _ in CASES:
        entry = cases[case]
        print("EVIDENCE " + json.dumps({
            "case": case, "verdict": entry["verdict"], "raw_sha256": entry["raw_sha256"],
            "json_line_sha256": entry["json_line_sha256"], "rom_sha256": entry["rom_sha256"],
            "reason": entry["reason"], "steps": entry["steps"], "adpcm": base[case]["adpcm"],
            "result_hex": base[case]["result_hex"], "disc_bin_sha256": disc["bin_sha256"],
            "geargrafx_revision": manifest["geargrafx_revision"], "judge": JUDGE_NAME,
            "release_eligible": False}, sort_keys=True))
    print(f"MANIFEST {out / 'manifest.json'} sha256={sha256_hex((out / 'manifest.json').read_bytes())}")
    print("PASS issue-400 ADPCM DMA-from-CD guest (pinned core, synthetic CD, project-owned guest)")
    print("  positive: 2048 bytes of LBA 5 land at ADPCM RAM $0100 byte-for-byte; all other RAM zero")
    print("  negative: DMA not armed -> DMA_NOT_DRAINED at stage 7, ADPCM RAM zero; "
          f"{len(MUTATIONS)} forgeries refused")
    print("LIMIT: device-layer observation of the pinned core only; not AD_TRANS ($11), not BIOS; release_eligible=false.")
    return 0


def verify(out: Path) -> int:
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != SCHEMA or manifest.get("judge") != JUDGE_NAME:
        raise ValueError("manifest schema or judge mismatch")
    if manifest.get("release_eligible") is not False:
        raise ValueError("manifest must keep release_eligible=false")
    base, roms = {}, {}
    for case, rom_name in CASES:
        entry = manifest["cases"][case]
        rom = (out / "rom" / rom_name).read_bytes()
        if sha256_hex(rom) != entry["rom_sha256"]:
            raise ValueError(f"{case}: ROM does not match its recorded sha256")
        roms[case] = rom
        obs, verdict = check_raw(case, out / "raw" / f"{case}.stdout", entry["raw_sha256"])
        if verdict != entry["verdict"] or json_line_sha256((out / "raw" / f"{case}.stdout").read_bytes()) \
                != entry["json_line_sha256"]:
            raise ValueError(f"{case}: recomputed verdict or line hash differs from the manifest")
        base[case] = obs
    if sum(a != b for a, b in zip(roms["pos-dma-sector"], roms["neg-dma-not-armed"])) != 1:
        raise ValueError("positive and negative ROMs must differ in exactly one byte")
    mutation_test(base)
    print(f"VERIFY PASS {out / 'manifest.json'}: {len(CASES)} raw observations re-judged, "
          f"{len(MUTATIONS)} forgeries refused")
    return 0


def main(argv=None):
    args_list = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description="Issue #400 ADPCM DMA-from-CD real-core driver")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="artifact directory (default build/ad-trans-dma)")
    parser.add_argument("--verify", action="store_true", help="re-judge saved raw observations; no core run")
    args = parser.parse_args(args_list)
    out = args.out.resolve()
    if args.verify:
        return verify(out)
    return run(out, args_list)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.SubprocessError, KeyError, json.JSONDecodeError) as error:
        print(f"FAIL issue-400 ADPCM DMA guest: {error}", file=sys.stderr)
        raise SystemExit(1) from error
