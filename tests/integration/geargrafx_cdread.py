"""Actual Geargrafx CD-ROM2 sector read by independent guest HuC6280 code (Issue #5, diagnostic-only).

The guest drives the emulated CD controller through $1800-$1804 and copies the sector
into guest RAM itself. Python/C++ never inject data, service the CD, or edit guest
state; they only read it. No firmware HLE, proprietary BIOS, or third-party code is used.
Every negative run must be rejected by the SAME judge (cdread_verify.check_success) that
accepts the positive runs.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from build_cdread import SECTOR_BYTES, verify_cdread, with_request
from cdread_verify import SENTINEL, check_success, describe_failure
from environment import DEPENDENCIES
from geargrafx_api import compile_probe
from make_cd_image import BIN_NAME, CUE_NAME, DEFAULT_SECTORS, MAGIC, audio_frame, build_image, sector_payload

STEP_BUDGET = 12_000_000  # longest legitimate path: ~0.8M (seek) + ~5.8M (one 24-bit-poll timeout) guest instructions
DEFAULT_REQUEST = (5, 1)


def execute(probe, rom, cue, labels, budget=STEP_BUDGET, inject=None):
    result = subprocess.run([str(probe), str(rom), str(cue) if cue else "-", str(labels), str(budget),
                             *([inject] if inject else [])],
                            check=True, capture_output=True, text=True, timeout=900)
    records = {"CAP": [], "HIT": [], "PHASE": [], "RUN": [], "BLOCK": [], "INJECT": []}
    for line in result.stdout.splitlines():
        tag, _, body = line.partition(" ")
        if tag in records:
            records[tag].append(json.loads(body))
    if not records["BLOCK"] and len(records["RUN"]) != 2:
        raise ValueError("probe did not complete cold and warm runs")
    records["rom_sha256"] = hashlib.sha256(Path(rom).read_bytes()).hexdigest()
    return records


def check_ram_transition(records, expected):
    """RAM before the transfer is the guest's sentinel; after it, the sector bytes."""
    for cycle in ("cold", "warm"):
        before = [h for h in records["HIT"] if h["cycle"] == cycle and h["name"] == "cd_buffer_ready"]
        after = [h for h in records["HIT"] if h["cycle"] == cycle and h["name"] == "cd_data_done"]
        if len(before) != 1 or len(after) != 1:
            raise ValueError(f"{cycle}: missing RAM before/after checkpoints")
        if bytes.fromhex(before[0]["buffer_head_hex"]) != bytes([SENTINEL]) * 32:
            raise ValueError(f"{cycle}: destination RAM was not the sentinel before the transfer")
        if bytes.fromhex(after[0]["buffer_head_hex"]) != expected[:32]:
            raise ValueError(f"{cycle}: destination RAM head wrong when the transfer finished")


class Suite:
    def __init__(self, probe, labels, symbols, payload, folder):
        self.probe, self.labels, self.symbols, self.payload, self.folder = probe, labels, symbols, payload, folder
        self.results = {}
        self.disc = [sector_payload(i) for i in range(DEFAULT_SECTORS)]
        self.cue = folder / "disc" / CUE_NAME
        self.n = 0

    def run(self, rom_bytes, cue=None, inject=None, budget=STEP_BUDGET):
        self.n += 1
        path = self.folder / f"variant-{self.n}-not-bios.pce"
        path.write_bytes(rom_bytes)
        return execute(self.probe, path, cue or self.cue, self.labels, budget, inject)

    def variant(self, lba, count):
        return with_request(self.payload, self.symbols, lba, count)

    def positive(self, name, lba, count):
        rom = self.payload if (lba, count) == DEFAULT_REQUEST else self.variant(lba, count)
        records = self.run(rom)
        check_success(records, self.symbols, self.disc, lba, count)
        expected = b"".join(self.disc[lba:lba + count])
        check_ram_transition(records, expected)
        self.results[name] = {"status": "PASS", "lba": lba, "count": count, "bytes": len(expected),
                              "data_sha256": hashlib.sha256(expected).hexdigest(),
                              "guest_steps": [run["step"] for run in records["RUN"]]}
        return records

    def must_be_rejected(self, name, records, lba, count, match=None, **expected):
        """The shared judge MUST raise; `expected` pins how the guest/emulator actually failed."""
        try:
            check_success(records, self.symbols, self.disc, lba, count)
        except ValueError as error:
            if match and match not in str(error):
                raise ValueError(f"{name}: rejected for the wrong reason: {error}")
            reason = str(error)
        else:
            raise ValueError(f"{name}: judge ACCEPTED an invalid run")
        self.results[name] = {"status": "PASS_EXPECTED_FAILURE", "rejected_because": reason,
                              "guest": describe_failure(records), **expected}

    def tampered_disc(self, name, lba, offset, delta=0x01):
        disc_dir = self.folder / name
        build_image(disc_dir)
        bin_path = disc_dir / BIN_NAME
        raw = bytearray(bin_path.read_bytes())
        raw[lba * 2352 + 16 + offset] ^= delta  # user data starts 16 bytes into the raw frame
        bin_path.write_bytes(raw)
        records = self.run(self.payload, cue=disc_dir / CUE_NAME)
        # the transfer itself succeeds: only the independent comparison can catch it
        if any(run["reason"] != "cd_halt_ok" for run in records["RUN"]):
            raise ValueError(f"{name}: tampered disc should still be readable by the guest")
        self.must_be_rejected(name, records, *DEFAULT_REQUEST, match=f"at byte {offset} ",
                              tampered_offset=offset)

    def failing_guest(self, name, rom, lba, count, code, stage, transferred, **kw):
        records = self.run(rom, **kw)
        for run, (reason, got_code, got_stage, got_bytes) in zip(records["RUN"], describe_failure(records)):
            if (reason, got_code, got_stage, got_bytes) != ("cd_halt_fail", code, stage, transferred):
                raise ValueError(f"{name}: guest ended {(reason, got_code, got_stage, got_bytes)}, "
                                 f"expected {('cd_halt_fail', code, stage, transferred)}")
            buffer = bytes.fromhex(run["buffer_hex"])
            if any(b != SENTINEL for b in buffer[transferred:]):
                raise ValueError(f"{name}: RAM beyond the transferred bytes was modified")
        self.must_be_rejected(name, records, lba, count, match="did not stop at the success checkpoint",
                              error_code=code, transferred_bytes=transferred)
        return records


def judge_mutation_tests(suite, records):
    """Corrupt otherwise-good observations: the judge must reject every one."""
    mutants = {}
    for index, cycle in enumerate(("cold", "warm")):
        for offset in (0, 3, 24, 1023, 2047):
            twin = copy.deepcopy(records)
            data = bytearray(bytes.fromhex(twin["RUN"][index]["buffer_hex"]))
            data[offset] ^= 0x80
            twin["RUN"][index]["buffer_hex"] = data.hex()
            mutants[f"{cycle}_ram_byte_{offset}"] = twin
        twin = copy.deepcopy(records)
        data = bytearray(bytes.fromhex(twin["RUN"][index]["buffer_hex"]))
        data[SECTOR_BYTES] = 0  # overrun beyond the requested length
        twin["RUN"][index]["buffer_hex"] = data.hex()
        mutants[f"{cycle}_overrun_byte"] = twin
        twin = copy.deepcopy(records)
        twin["RUN"][index]["reason"] = "budget"
        mutants[f"{cycle}_budget_exhausted"] = twin
        twin = copy.deepcopy(records)
        twin["RUN"][index]["mpr"][1] = 0xF7
        mutants[f"{cycle}_mpr_changed"] = twin
    twin = copy.deepcopy(records)
    twin["PHASE"] = [row for row in twin["PHASE"] if row["scsi_phase"] != 4]
    mutants["no_data_in_phase_observed"] = twin
    twin = copy.deepcopy(records)
    for row in twin["PHASE"]:
        if row["scsi_phase"] == 4:
            row["load_sector"] = 6
    mutants["emulator_saw_other_lba"] = twin
    for name, twin in mutants.items():
        try:
            check_success(twin, suite.symbols, suite.disc, *DEFAULT_REQUEST)
        except ValueError:
            continue
        raise ValueError(f"judge accepted mutated observation: {name}")
    suite.results["judge_observation_mutations"] = {"status": "PASS_EXPECTED_FAILURE", "mutants": sorted(mutants)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("debug", "release"), default="debug")
    args = parser.parse_args()
    base = ROOT / "build" / args.mode / "bios-dev/dev-only-not-compatible-syscard3.pce"
    output = ROOT / "build" / args.mode / "cdread-probe"
    rom = output / "cdread-probe-not-bios.pce"
    payload = rom.read_bytes()
    manifest = json.loads((output / "manifest.json").read_text())
    symbols, labels = manifest["symbols"], output / "trace-labels.txt"
    digest = verify_cdread(payload, base.read_bytes(), symbols, manifest["code_sha256_excluding_request"])
    probe = compile_probe(output, "geargrafx_cdread_probe.cpp", "geargrafx-cdread-probe")
    with tempfile.TemporaryDirectory(prefix="pce-original-cdread-") as tmp:
        folder = Path(tmp)
        disc_manifest = build_image(folder / "disc")
        suite = Suite(probe, labels, symbols, payload, folder)

        # ---- positive: the guest reads real sectors into guest RAM ----
        primary = suite.positive("single_sector_lba5", 5, 1)
        suite.positive("first_sector_lba0", 0, 1)
        suite.positive("last_sector_lba15", 15, 1)
        suite.positive("three_sectors_lba5", 5, 3)
        suite.positive("disc_end_boundary_lba13_x3", 13, 3)

        # ---- negative: the identical judge must reject each of these ----
        suite.tampered_disc("tampered_identity_byte3", 5, 3)
        suite.tampered_disc("tampered_last_byte2047", 5, 2047, 0x80)
        wrong = suite.run(suite.variant(6, 1))
        suite.must_be_rejected("wrong_sector_requested_lba6", wrong, 5, 1, match="at byte 24 ")
        suite.failing_guest("invalid_sector_lba16", suite.variant(16, 1), 16, 1, 5, 5, 0)
        suite.failing_guest("invalid_sector_lba_max", suite.variant(0x1FFFFF, 1), 0x1FFFFF, 1, 5, 5, 0)
        past_end = suite.failing_guest("read_past_disc_end_lba14_x3", suite.variant(14, 3), 14, 3, 5, 5,
                                       2 * SECTOR_BYTES)
        got = bytes.fromhex(past_end["RUN"][0]["buffer_hex"])[:2 * SECTOR_BYTES]
        if got != suite.disc[14] + suite.disc[15]:
            raise ValueError("data read before the end-of-disc error is wrong")
        suite.failing_guest("zero_sector_count", suite.variant(5, 0), 5, 0, 9, 1, 0)
        suite.failing_guest("oversized_sector_count", suite.variant(5, 4), 5, 4, 9, 1, 0)
        # Timeout: selection is redirected to $1805 (a CD register this guest otherwise never touches), so the target never answers.
        stuck = bytearray(payload)
        at = symbols["cd_select"] - 0xE000
        if stuck[at:at + 3] != bytes((0x8D, 0x00, 0x18)):
            raise ValueError("expected STA $1800 at cd_select")
        stuck[at + 1] = 0x05
        try:
            verify_cdread(bytes(stuck), base.read_bytes(), symbols, manifest["code_sha256_excluding_request"])
        except ValueError:
            pass
        else:
            raise ValueError("fixture verifier accepted the select-redirected ROM")
        suite.failing_guest("selection_never_answered_timeout", bytes(stuck), *DEFAULT_REQUEST, 2, 3, 0)
        eject = suite.failing_guest("drive_abort_at_data_in_entry", payload, *DEFAULT_REQUEST, 4, 5, 0,
                                    inject="eject-in-data")
        if len(eject["INJECT"]) != 2:
            raise ValueError("drive-abort injection was not applied to both cold and warm runs")

        # ---- data track vs CD-DA: what Geargrafx does when READ(6) targets an audio LBA ----
        mixed = folder / "mixed"
        build_image(mixed, DEFAULT_SECTORS, 8)
        data_ok = suite.run(suite.variant(15, 1), cue=mixed / CUE_NAME)
        check_success(data_ok, symbols, suite.disc, 15, 1)  # an audio track does not disturb data reads
        audio = suite.run(suite.variant(16, 1), cue=mixed / CUE_NAME)
        got = bytes.fromhex(audio["RUN"][0]["buffer_hex"])[:SECTOR_BYTES]
        if describe_failure(audio)[0] != ("cd_halt_ok", 0, 9, SECTOR_BYTES) or got != audio_frame(0)[16:16 + SECTOR_BYTES]:
            raise ValueError("unexpected Geargrafx behaviour for READ(6) on an audio-track LBA")
        if got[:len(MAGIC)] == MAGIC:
            raise ValueError("audio data was mistaken for the data-track pattern")
        suite.results["read6_on_audio_track_lba"] = {
            "status": "OBSERVED_EMULATOR_BEHAVIOUR",
            "observation": "Geargrafx returns GOOD status and 2048 raw PCM bytes taken 16 bytes into the frame (as if MODE1); real-hardware behaviour UNKNOWN",
            "consequence": "firmware must check the track type (READ TOC $DE mode 2) itself; this diagnostic does not"}

        # ---- media-absent conditions the harness can express ----
        blocked = execute(probe, rom, None, labels)
        if not blocked["BLOCK"]:
            raise ValueError("no-media run unexpectedly executed guest code")
        suite.results["no_media_loaded"] = {
            "status": "NOT_TESTED", "reason": "Geargrafx does not execute the System Card ROM without media",
            "observed": blocked["BLOCK"][0]}
        empty = folder / "empty"
        empty.mkdir()
        (empty / BIN_NAME).write_bytes(b"")
        (empty / CUE_NAME).write_bytes((folder / "disc" / CUE_NAME).read_bytes())
        try:
            execute(probe, rom, empty / CUE_NAME, labels)
        except subprocess.CalledProcessError as error:
            suite.results["empty_disc_image"] = {"status": "NOT_TESTED", "probe_exit": error.returncode,
                                                  "reason": "Geargrafx rejects a zero-sector image at load time"}
        else:
            raise ValueError("empty disc image unexpectedly loaded")

        judge_mutation_tests(suite, primary)

    evidence = {
        "scope": "original CD sector-read hardware diagnostic ONLY; CD_READ ($03) and every BIOS API NOT_IMPLEMENTED",
        "rom_sha256": digest,
        "geargrafx_revision": DEPENDENCIES["geargrafx"]["revision"],
        "source_archive_sha256": DEPENDENCIES["geargrafx"]["sha256"],
        "loader": "LoadBiosFromBuffer(syscard=true) + original synthetic CUE/BIN (MODE1/2352)",
        "disc": {key: disc_manifest[key] for key in ("format", "sectors", "bin_sha256", "cue_sha256", "edc_ecc")},
        "success_judgement": "guest halt at cd_halt_ok + result block + full RAM compare vs independent generator "
                             "+ emulator-observed SCSI READ(6) lba/count + SCSI phase order, cold AND warm",
        "tests": suite.results,
        "primary_observations": {"RUN": primary["RUN"], "PHASE": primary["PHASE"], "HIT": primary["HIT"]},
        "cd_read_api_slot_03": "NOT_IMPLEMENTED", "bios_service_abi": "UNKNOWN",
        "interrupt_driven_transfer": "NOT_TESTED", "cd_da_track_behaviour": "OBSERVED_EMULATOR_BEHAVIOUR only (tests.read6_on_audio_track_lba); TOC type check NOT_TESTED",
        "real_hardware_or_second_emulator": "NOT_TESTED", "proprietary_content": "NONE",
        "status": "PASS",
    }
    (output / "cdread-evidence.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    passed = sum(1 for r in suite.results.values() if r["status"].startswith("PASS"))
    skipped = sum(1 for r in suite.results.values() if r["status"] == "NOT_TESTED")
    print(f"PASS real Geargrafx CD read by guest HuC6280: {passed} verified scenarios "
          f"(cold+warm, full-byte RAM compare), {skipped} NOT_TESTED; NOT BIOS, CD_READ API NOT_IMPLEMENTED")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"cdread integration FAIL/BLOCKED: {error}") from error
