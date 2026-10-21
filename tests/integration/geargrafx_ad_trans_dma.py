#!/usr/bin/env python3
"""Issue #400: ADPCM DMA-from-CD guest on the pinned real core (DEVELOPMENT ONLY, synthetic CD).

src/ad_trans_dma_probe.s is a project-owned HuC6280 guest. It runs one READ(6) over the SCSI
registers and lets the ADPCM DMA ($180B) move the 2048 DATA-IN bytes of LBA 5 into ADPCM RAM at $0100.
Positive: all bytes match the synthetic sector, nothing else in ADPCM RAM changes.
Negative: the same ROM with cd_dma_enable=0 never arms the DMA; the data phase cannot drain (code 11)
and ADPCM RAM stays zero. The SAME judge accepts the positive and refuses every mutated observation.
Not BIOS, not AD_TRANS ($11), not a System Card compatibility claim; release_eligible=false.
Run manually (not wired into the Makefile): python3 tests/integration/geargrafx_ad_trans_dma.py
"""
import copy
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "tools"))
from environment import DEPENDENCIES, executable  # noqa: E402
from geargrafx_api import compile_probe  # noqa: E402
from make_cd_image import BIN_NAME, build_image, sector_payload  # noqa: E402

BANK_BASE, BANK_BYTES, IMAGE_BYTES = 0xE000, 0x2000, 256 * 1024
LBA, SECTOR = 5, 2048
DEST = 0x0100
MAX_STEPS = 30_000_000
HALTS = ("cd_halt_ok", "cd_halt_fail", "cd_unexpected_interrupt")
ERR_DMA_NOT_DRAINED = 11
RES_STAGE_DONE = 9
RAM_SIZE = 0x10000
JUDGE_NAME = "ad-trans-dma-judge-v1"


def build_guest(directory: Path):
    """Assemble and link the guest with the pinned cc65. Returns (rom_bytes, symbols)."""
    ca65, ld65 = executable("ca65"), executable("ld65")
    version = subprocess.run([ca65, "--version"], check=True, capture_output=True, text=True)
    if DEPENDENCIES["cc65"]["revision"][:9] not in version.stdout + version.stderr:
        raise ValueError("cc65 does not report the pinned build ID")
    directory.mkdir(parents=True, exist_ok=True)
    obj, bank, labels = directory / "ad_trans_dma_probe.o", directory / "bank0.bin", directory / "guest.lbl"
    subprocess.run([ca65, str(ROOT / "src/ad_trans_dma_probe.s"), "-o", str(obj)], check=True)
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
    return body + b"\xff" * (IMAGE_BYTES - BANK_BYTES), symbols


def with_dma_enable(rom: bytes, symbols: dict, value: int) -> bytes:
    """Test input: flip only the cd_dma_enable byte (the negative case)."""
    patched = bytearray(rom)
    patched[symbols["cd_dma_enable"] - BANK_BASE] = value
    return bytes(patched)


def run_probe(probe: Path, rom: Path, cue: Path, labels: Path):
    """Returns (observation dict, sha256 of the raw observation line)."""
    result = subprocess.run([str(probe), str(rom), str(cue), str(labels), str(MAX_STEPS)],
                            capture_output=True, text=True, timeout=1800)
    if result.returncode != 0:
        raise ValueError(f"probe exit {result.returncode}: {result.stderr[-300:]}")
    lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
    if len(lines) != 1:
        raise ValueError("probe must emit exactly one observation line")
    obs = json.loads(lines[0])
    return obs, hashlib.sha256(lines[0].encode("utf-8")).hexdigest()


def expected_ram():
    """Only [DEST, DEST+2048) holds the synthetic sector; every other byte of ADPCM RAM is zero."""
    ram = bytearray(RAM_SIZE)
    ram[DEST:DEST + SECTOR] = sector_payload(LBA)
    return bytes(ram)


def judge(case: str, obs: dict) -> str:
    """Judge one observation. The expected verdict is recomputed here; a reported reason cannot override it."""
    if type(obs) is not dict or type(obs.get("halt_confirmed")) is not bool:
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
        if result[0] != ERR_DMA_NOT_DRAINED or result[7] != 0:
            raise ValueError(f"{case}: expected DMA_NOT_DRAINED with DMA not armed (res={result[0]})")
        if obs["ram_post_nonzero"] != 0 or ram != bytes(RAM_SIZE):
            raise ValueError(f"{case}: ADPCM RAM changed without the DMA being armed")
        if adpcm["write_address"] != DEST:
            raise ValueError(f"{case}: write latch not at the programmed address")
        return "NEG_DMA_NOT_ARMED_NO_TRANSFER"
    raise ValueError(f"unknown case {case}")


def mutation_test(pos_obs: dict, neg_obs: dict) -> None:
    """Falsified observations must be refused by judge(); a single flipped byte is enough."""
    flipped = copy.deepcopy(pos_obs)
    ram = bytearray.fromhex(flipped["ram_hex"])
    ram[DEST + 7] ^= 0x01
    flipped["ram_hex"] = ram.hex()
    shifted = copy.deepcopy(pos_obs)
    shifted["ram_hex"] = bytes(1) + bytes.fromhex(pos_obs["ram_hex"])[:-1]
    shifted["ram_hex"] = shifted["ram_hex"].hex()
    stopped = copy.deepcopy(pos_obs)
    stopped["reason"] = "budget"
    stopped["halt_confirmed"] = False
    faked = copy.deepcopy(neg_obs)
    ram = bytearray(RAM_SIZE)
    ram[DEST:DEST + SECTOR] = sector_payload(LBA)
    faked["ram_hex"] = ram.hex()
    faked["ram_post_nonzero"] = SECTOR
    for name, bad_case, bad in (("flip", "pos-dma-sector", flipped), ("shift", "pos-dma-sector", shifted),
                                ("budget", "pos-dma-sector", stopped), ("faked-neg", "neg-dma-not-armed", faked)):
        try:
            judge(bad_case, bad)
        except ValueError:
            continue
        raise ValueError(f"mutation test FAILED: {name} observation accepted")


def main():
    with tempfile.TemporaryDirectory(prefix="pce-ad-trans-dma-") as tmp:
        directory = Path(tmp)
        rom_pos, symbols = build_guest(directory / "guest")
        rom_neg = with_dma_enable(rom_pos, symbols, 0)
        if sum(a != b for a, b in zip(rom_pos, rom_neg)) != 1:
            raise ValueError("positive and negative ROMs must differ in exactly one byte")
        disc = build_image(directory / "disc", sectors=16)
        cue = directory / "disc" / "oss-pce-cd-test.cue"
        probe = compile_probe(directory, source_name="geargrafx_ad_trans_dma_probe.cpp",
                              target_name="ad-trans-dma-probe")
        rom_pos_path, rom_neg_path = directory / "pos.pce", directory / "neg.pce"
        rom_pos_path.write_bytes(rom_pos)
        rom_neg_path.write_bytes(rom_neg)
        labels = directory / "guest" / "trace-labels.txt"
        observations = {}
        for case, rom_path in (("pos-dma-sector", rom_pos_path), ("neg-dma-not-armed", rom_neg_path)):
            obs, obs_sha = run_probe(probe, rom_path, cue, labels)
            verdict = judge(case, obs)
            observations[case] = obs
            print("EVIDENCE " + json.dumps({
                "case": case, "verdict": verdict, "observation_kind": "REAL_CORE_DEVICE_LAYER_PROJECT_GUEST",
                "geargrafx_revision": DEPENDENCIES["geargrafx"]["revision"],
                "geargrafx_source_sha256": DEPENDENCIES["geargrafx"]["sha256"],
                "rom_sha256": hashlib.sha256(rom_path.read_bytes()).hexdigest(),
                "disc_bin_sha256": disc["bin_sha256"], "disc": "SYNTHETIC",
                "observation_sha256": obs_sha, "judge": JUDGE_NAME, "release_eligible": False,
                "reason": obs["reason"], "steps": obs["steps"], "adpcm": obs["adpcm"],
                "result_hex": obs["result_hex"]}, sort_keys=True))
        mutation_test(observations["pos-dma-sector"], observations["neg-dma-not-armed"])
    print("PASS issue-400 ADPCM DMA-from-CD guest (pinned core, synthetic CD, project-owned guest)")
    print("  positive: 2048 bytes of LBA 5 land at ADPCM RAM $0100 byte-for-byte; all other RAM zero")
    print("  negative: DMA not armed -> DMA_NOT_DRAINED, ADPCM RAM zero; mutations refused")
    print("LIMIT: device-layer observation of the pinned core only; not AD_TRANS ($11), not BIOS; release_eligible=false.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.SubprocessError, KeyError) as error:
        print(f"FAIL issue-400 ADPCM DMA guest: {error}", file=sys.stderr)
        raise SystemExit(1) from error
