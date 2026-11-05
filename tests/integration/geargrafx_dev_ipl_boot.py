"""Real Geargrafx: DEV-only IPL boot from RESET (Issue #668). Synthetic media only. NOT compatible, NOT a BIOS ABI.

One ROM, built once from the host selection record only (no payload bytes embedded). Then, on the pinned core:
  RESET -> init -> host-selected LBA -> READ(6) -> guest PCEX validation -> copy to $3000 -> execute -> return.
Two different synthetic IPLs on the SAME ROM must produce different observed results. Every negative (except the
step-budget timeout) must be rejected without copying or executing. The ROM bytes are re-verified after all runs.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import geargrafx_cdload_exec as T  # judges and synthetic payload helpers, shared with #5/#14 and PR #658
from build_cdload_exec import HDR_BYTES, SECTOR_BYTES, USER_OFFSET, encode_sector
from build_experimental_bios_boot import PAYLOAD_LABELS, build_experimental_payload
from build_ipl_boot import build_ipl_rom
from experimental_track_selection import SelectionError, assembler_defines, selection_from_cue
from geargrafx_api import compile_probe
from geargrafx_cdread import execute
from make_cd_image import BIN_NAME, CUE_NAME, RAW_BYTES, build_image

STEP_BUDGET = T.STEP_BUDGET
SECTORS = 300         # synthetic BIN length: must contain the selected sector
TARGET_LBA = 0x000109  # 265 = 00:03:40 MSF; MID byte 0x01 and LO byte 0x09 non-zero, HI byte zero (see host check)


def set_index01(cue, lba):
    """Move the data track's INDEX 01 to `lba` frames so the selected LBA is `lba` (synthetic layout only)."""
    text = cue.read_text(encoding="ascii")
    if text.count("INDEX 01 00:00:00\n") != 1:
        raise ValueError("unexpected synthetic CUE layout")
    mm, ss, ff = lba // (60 * 75), (lba // 75) % 60, lba % 75
    text = text.replace("INDEX 01 00:00:00\n",
                        f"INDEX 00 00:00:00\n    INDEX 01 {mm:02d}:{ss:02d}:{ff:02d}\n", 1)
    cue.write_text(text, encoding="ascii")
    return cue


def make_disc(directory, lba, code=None, entry=0):
    """Synthetic MODE1 data track of SECTORS sectors; optionally write a PCEX sector at `lba` (BIN frame index)."""
    build_image(directory, SECTORS)
    cue = directory / CUE_NAME
    if code is not None:
        bin_path = directory / BIN_NAME
        raw = bytearray(bin_path.read_bytes())
        at = lba * RAW_BYTES + USER_OFFSET
        raw[at:at + SECTOR_BYTES] = encode_sector(lba, code, entry)
        bin_path.write_bytes(raw)
    return cue


def tamper_bin(cue, lba, edits):
    bin_path = cue.parent / BIN_NAME
    raw = bytearray(bin_path.read_bytes())
    for offset, delta in edits:
        raw[lba * RAW_BYTES + USER_OFFSET + offset] ^= delta
    bin_path.write_bytes(raw)


def assert_not_copied(records, code):
    """No run may leave the IPL bytes at $3000 (copy destination): the rejection must happen before the copy."""
    for run in records["RUN"]:
        buffer = bytes.fromhex(run["buffer_hex"])
        if len(buffer) != 6144:
            raise ValueError(f"{run['cycle']}: RAM dump has {len(buffer)} bytes, expected 6144")
        if buffer[T.EXEC_OFF:T.EXEC_OFF + len(code)] == code:
            raise ValueError(f"{run['cycle']}: IPL bytes were copied to RAM before the rejection")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("debug", "release"), default="debug")
    args = parser.parse_args()
    mode = args.mode
    out = ROOT / "build" / mode / "dev-ipl-boot"
    original = ROOT / "build" / mode / "bios-dev" / "dev-only-not-compatible-syscard3.pce"
    if not original.is_file():
        raise SystemExit("BLOCKED: run make dev-rom first")
    out.mkdir(parents=True, exist_ok=True)
    probe = compile_probe(out, "geargrafx_cdread_probe.cpp", "geargrafx-dev-ipl-probe")
    results = {}

    with tempfile.TemporaryDirectory(prefix="pce-dev-ipl-") as tmp:
        tmp = Path(tmp)
        layout_dir = tmp / "layout"
        layout_cue = set_index01(make_disc(layout_dir, TARGET_LBA), TARGET_LBA)
        selected = selection_from_cue(layout_cue, layout_cue)
        lba = selected["lba"]
        if lba != TARGET_LBA:
            raise ValueError(f"expected the layout to select LBA {TARGET_LBA}, got {lba}")

        # Host-side encoding of the full 21-bit LBA (the real core cannot use a file this large here).
        wide = dict(selected, lba=0x010203)
        defines = assembler_defines(wide)
        for expected in ("EXP_SELECTED_LBA_HI=$01", "EXP_SELECTED_LBA_MID=$02", "EXP_SELECTED_LBA_LO=$03"):
            if expected not in defines:
                raise ValueError(f"host encoding of LBA 0x010203 is wrong, missing {expected}: {defines}")
        results["host_lba_encoding_0x010203"] = {"status": "PASS", "scope": "host encoding only, not real-core"}

        rom_path, rom_symbols = build_ipl_rom(original, out, mode, layout_cue)
        rom_bytes = rom_path.read_bytes()
        rom_sha = hashlib.sha256(rom_bytes).hexdigest()
        results["rom"] = {"name": rom_path.name, "sha256": rom_sha, "bytes": len(rom_bytes), "selected_lba": lba}

        code_a, payload_symbols = build_experimental_payload(out / "payload", mode)
        code_b = T.payload_variant(code_a, T.VARIANT_TRAILER)
        if code_a in rom_bytes or code_b in rom_bytes:
            raise ValueError("an IPL payload was found inside the ROM image")
        results["rom"]["payload_bytes_found_in_rom"] = False

        symbols = dict(rom_symbols)
        symbols.update({name: payload_symbols[name] for name in PAYLOAD_LABELS})
        labels = out / "trace-labels.txt"
        labels.write_text("".join(f"{name} {value:04x}\n" for name, value in symbols.items()), encoding="ascii")

        def disc(name, code, entry=0, edits=(), blank=False):
            directory = tmp / name
            cue = set_index01(make_disc(directory, lba, None if blank else code, entry), lba)
            mounted = selection_from_cue(cue, cue)
            if (mounted["track"], mounted["lba"], mounted["kind"]) != (selected["track"], lba, selected["kind"]):
                raise ValueError(f"{name}: mounted disc selects a different record than the ROM was built for")
            tamper_bin(cue, lba, edits)
            return cue

        def run(cue, inject=None, budget=STEP_BUDGET):
            records = execute(probe, rom_path, cue, labels, budget, inject)
            if records["rom_sha256"] != rom_sha:
                raise ValueError("probe did not execute the single IPL ROM")
            return records

        # ---- positive: IPL A, cold and warm ----
        records_a = run(disc("ipl-a", code_a))
        T.judge_success(records_a, code_a, symbols, lba, 1)
        results["ipl_a"] = {"status": "PASS", "lba": lba, "payload_bytes": len(code_a),
                            "sector_sha256": hashlib.sha256(encode_sector(lba, code_a)).hexdigest()}

        # ---- positive: IPL B on the same ROM must produce a different observed result ----
        records_b = run(disc("ipl-b", code_b))
        T.judge_marker_mismatch(records_b, code_b, symbols, lba, T.VARIANT_TRAILER)
        try:
            T.judge_success(records_b, code_b, symbols, lba, 1)
        except ValueError as error:
            results["ipl_b"] = {"status": "PASS_EXPECTED_DIFFERENT_RESULT", "guest_result": "PAYLOAD_MARKER_MISMATCH(30)",
                                "success_judge_rejected_because": str(error)}
        else:
            raise ValueError("success judge accepted IPL B")

        # ---- negatives: rejected before copy/execution (expectations from the #5/#14 judges) ----
        negatives = [
            ("bad_magic", dict(edits=[(0, 0x01)]), 20, 11, SECTOR_BYTES),
            ("bad_checksum_code_byte", dict(edits=[(HDR_BYTES + 3, 0x01)]), 23, 11, SECTOR_BYTES),
            ("bad_length_zero", dict(edits=[(5, len(code_a))]), 22, 11, SECTOR_BYTES),
            ("bad_entry_offset_equals_length", dict(entry=len(code_a)), 21, 11, SECTOR_BYTES),
            ("no_header_on_disc", dict(blank=True), 20, 11, SECTOR_BYTES),
        ]
        for name, kwargs, code, stage, transferred in negatives:
            records = run(disc(name, code_a, **kwargs))
            T.judge_failure(records, code, stage, transferred)
            assert_not_copied(records, code_a)
            results[name] = {"status": "PASS_EXPECTED_FAILURE", "guest_error": code, "stage": stage, "bytes": transferred}

        records = run(disc("abort", code_a), inject="eject-in-data")
        T.judge_failure(records, 4, 5, 0)
        assert_not_copied(records, code_a)
        results["drive_abort_at_data_in"] = {"status": "PASS_EXPECTED_FAILURE", "guest_error": 4, "stage": 5, "bytes": 0}

        records = run(disc("budget", code_a), budget=1000)
        if len(records["RUN"]) != 2 or any(run_["reason"] != "budget" for run_ in records["RUN"]) or any(
                h["name"] in T.ORDER[2:] for h in records["HIT"]):
            raise ValueError("step-budget run did not stop before the IPL was entered")
        assert_not_copied(records, code_a)
        results["step_budget_exhausted"] = {"status": "PASS_EXPECTED_FAILURE", "budget": 1000,
                                            "note": "timeout by step budget, not a guest rejection"}

        # ---- host refusal: audio-only medium never yields a selection record ----
        audio = tmp / "audio"
        audio.mkdir()
        (audio / "audio.bin").write_bytes(b"\x00" * (2352 * 16))
        audio_cue = audio / CUE_NAME
        audio_cue.write_text('FILE "audio.bin" BINARY\n  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n', encoding="ascii")
        try:
            selection_from_cue(audio_cue, audio_cue)
        except SelectionError as error:
            message = str(error)
            if message != "synthetic layout has no MODE1/2352 data track":
                raise ValueError(f"audio-only refusal has an unexpected reason: {message}")
            results["audio_only_medium"] = {"status": "PASS_EXPECTED_FAILURE", "host_refusal": message}
        else:
            raise ValueError("audio-only medium produced a selection record")

    # ROM must be byte-identical after every run (no rebuild per IPL).
    if hashlib.sha256(rom_path.read_bytes()).hexdigest() != rom_sha:
        raise ValueError("ROM changed during the test")
    results["rom"]["unchanged_after_all_runs"] = True
    passed = sum(1 for value in results.values() if isinstance(value, dict) and str(value.get("status", "")).startswith("PASS"))
    evidence = {"scope": "DEV-only IPL boot, synthetic media only; NOT compatible, NOT a BIOS ABI, NOT commercial",
                "mode": mode, "results": results, "status": "PASS",
                "limitation": "real-core LBA is 0x000109 (MID and LO bytes); the HI byte is covered only by the host encoding check"}
    (out / "dev-ipl-boot-evidence.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(f"PASS DEV-only IPL boot from RESET ({passed} scenarios; one ROM, two synthetic IPLs; cold+warm); "
          f"NOT compatible, NOT BIOS, NOT DEV_CALLABLE")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, SelectionError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"dev-ipl-boot integration FAIL/BLOCKED: {error}") from error
