"""Real Geargrafx: load code from a synthetic CD sector, copy it to RAM, execute it (Issues #14 and #5, experiment).

Stage 0 is guest HuC6280 code that reads the sector with READ(6) through $1800-$1804 (no HLE, no host
injection). The payload is run from RAM at $3000 and its marker, PC transitions, MPR and SP are
observed by the Issue #5 probe frontend (tests/integration/geargrafx_cdread_probe.cpp, unchanged).
NOT a BIOS, NOT CD_READ ($03), NOT a System Card ABI, NOT a compatibility claim.
Every negative run must be rejected by the judge in this file.
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
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_cdload_exec import (DEFAULT_LBA, DISC_SECTORS, ERROR_NAMES, EXEC_ADDRESS, EXPECTED_MPR, HDR_BYTES,
                               SECTOR_BYTES, USER_OFFSET, build_exec_disc, encode_sector, parse_labels)
from build_cdread import HALT_CONFIRM_STEPS, with_request
from cdread_verify import result_block
from environment import DEPENDENCIES
from geargrafx_api import compile_probe
from geargrafx_cdread import execute
from make_cd_image import BIN_NAME, CUE_NAME, RAW_BYTES, build_image

STEP_BUDGET = 12_000_000  # same worst-case budget as the Issue #5 harness
BUFFER_BASE = 0x2800      # probe dumps $2800-$3FFF (6144 bytes)
MARK_OFF = 0x3F00 - BUFFER_BASE
EXEC_OFF = EXEC_ADDRESS - BUFFER_BASE
ORDER = ("cd_buffer_ready", "cd_data_done", "payload_entry", "payload_done", "cd_payload_return", "cd_halt_ok")
HEADER_FAILURES = {20, 21, 22, 23}  # rejected at stage 11, after the full sector was transferred


def judge_success(records, code, sector, symbols):
    """Raise ValueError unless cold AND warm runs loaded, verified, executed and returned exactly."""
    caps = records["CAP"]
    if len(caps) != 1 or not (caps[0].get("instruction_step") and caps[0].get("cdrom_hardware")
                              and caps[0].get("cdrom_media")):
        raise ValueError("instruction stepping or CD media capability missing")
    if [run["cycle"] for run in records["RUN"]] != ["cold", "warm"]:
        raise ValueError("missing cold/warm runs")
    for run in records["RUN"]:
        cycle = run["cycle"]
        if run["reason"] != "cd_halt_ok" or run["pc"] != symbols["cd_halt_ok"]:
            raise ValueError(f"{cycle}: stopped with {run['reason']}, not at cd_halt_ok")
        if run["halt_confirmed"] is not True or run["halt_confirmation_steps"] != HALT_CONFIRM_STEPS:
            raise ValueError(f"{cycle}: halt not confirmed for {HALT_CONFIRM_STEPS} instructions")
        if run["mpr"] != EXPECTED_MPR:
            raise ValueError(f"{cycle}: MPR at halt {run['mpr']} differs from {EXPECTED_MPR}")
        if run["sp"] != 0xFF:
            raise ValueError(f"{cycle}: stack pointer {run['sp']:#x} at halt, expected 0xff")
        block = result_block(run)
        if (block["code"], block["stage"], block["bytes"], block["scsi_status"], block["scsi_message"]) \
                != (0, 16, SECTOR_BYTES, 0, 0):
            raise ValueError(f"{cycle}: result block is not a clean load+execute success: {block}")
        buffer = bytes.fromhex(run["buffer_hex"])
        if len(buffer) != 6144:
            raise ValueError(f"{cycle}: RAM dump has {len(buffer)} bytes")
        for index, (got, want) in enumerate(zip(buffer[:SECTOR_BYTES], sector)):
            if got != want:
                raise ValueError(f"{cycle}: transferred sector differs at byte {index}")
        if buffer[EXEC_OFF:EXEC_OFF + len(code)] != code:
            raise ValueError(f"{cycle}: code in RAM at {EXEC_ADDRESS:#x} differs from the payload")
        mark = buffer[MARK_OFF:MARK_OFF + 7]
        if (mark[0], mark[1], mark[4], mark[5], mark[6]) != (0xA5, 0x5A, 0xF8, 0xFF, 0x3C):
            raise ValueError(f"{cycle}: payload marker wrong: {mark.hex()}")
        hits = [h for h in records["HIT"] if h["cycle"] == cycle and h["name"] in ORDER]
        names = [h["name"] for h in hits]
        if names != list(ORDER):
            raise ValueError(f"{cycle}: label order {names} differs from {list(ORDER)}")
        steps = [h["step"] for h in hits]
        if steps != sorted(steps) or len(set(steps)) != len(steps):
            raise ValueError(f"{cycle}: label arrival steps not increasing")
        by_name = {h["name"]: h for h in hits}
        if bytes.fromhex(by_name["cd_buffer_ready"]["buffer_head_hex"]) != bytes([0x5A]) * 32:
            raise ValueError(f"{cycle}: destination was not the sentinel before the transfer")
        expected_pc = {"payload_entry": EXEC_ADDRESS, "payload_done": symbols["payload_done"],
                       "cd_payload_return": symbols["cd_payload_return"], "cd_halt_ok": symbols["cd_halt_ok"]}
        for name, pc in expected_pc.items():
            if by_name[name]["pc"] != pc:
                raise ValueError(f"{cycle}: {name} reached at PC {by_name[name]['pc']:#x}, expected {pc:#x}")


def judge_failure(records, code, stage, transferred):
    """Raise ValueError unless every run halted in cd_halt_fail with the given error and never ran the payload."""
    if [run["cycle"] for run in records["RUN"]] != ["cold", "warm"]:
        raise ValueError("missing cold/warm runs")
    for run in records["RUN"]:
        cycle = run["cycle"]
        if run["reason"] != "cd_halt_fail" or not run["halt_confirmed"]:
            raise ValueError(f"{cycle}: expected a confirmed cd_halt_fail, got {run['reason']}")
        block = result_block(run)
        got = (block["code"], block["stage"], block["bytes"])
        if got != (code, stage, transferred):
            raise ValueError(f"{cycle}: guest error {got} ({ERROR_NAMES.get(got[0], '?')}), "
                             f"expected {(code, stage, transferred)}")
        if any(h["name"] in ORDER[2:] for h in records["HIT"] if h["cycle"] == cycle):
            raise ValueError(f"{cycle}: payload or return label reached after a failed check")


class Suite:
    def __init__(self, probe, labels, symbols, code, folder, rom):
        self.probe, self.labels, self.symbols, self.code, self.folder, self.rom = probe, labels, symbols, code, folder, rom
        self.n = 0
        self.results = {}

    def run(self, rom_bytes, cue, inject=None, budget=STEP_BUDGET):
        self.n += 1
        path = self.folder / f"variant-{self.n}-not-bios.pce"
        path.write_bytes(rom_bytes)
        return execute(self.probe, path, cue, self.labels, budget, inject)

    def rom_for(self, lba):
        return self.rom if lba == DEFAULT_LBA else with_request(self.rom, self.symbols, lba, 1)

    def disc(self, name, lba, code=None, entry=0, tamper=()):
        directory = self.folder / name
        build_exec_disc(directory, lba, self.code if code is None else code, entry)
        for offset, delta in tamper:  # XOR the user-data byte at `offset` (a delta of len(code) zeroes a length byte)
            bin_path = directory / BIN_NAME
            raw = bytearray(bin_path.read_bytes())
            raw[lba * RAW_BYTES + USER_OFFSET + offset] ^= delta
            bin_path.write_bytes(raw)
        return directory / CUE_NAME

    def positive(self, name, lba):
        sector = encode_sector(lba, self.code)
        records = self.run(self.rom_for(lba), self.disc(f"disc-lba{lba}", lba))
        judge_success(records, self.code, sector, self.symbols)
        self.results[name] = {"status": "PASS", "lba": lba, "payload_bytes": len(self.code),
                              "sector_sha256": hashlib.sha256(sector).hexdigest(),
                              "guest_steps": [run["step"] for run in records["RUN"]]}
        return records

    def reject(self, name, records, **info):
        """The success judge MUST reject this observation; its reason is recorded."""
        try:
            judge_success(records, self.code, encode_sector(DEFAULT_LBA, self.code), self.symbols)
        except ValueError as error:
            self.results[name] = {"status": "PASS_EXPECTED_FAILURE", "rejected_because": str(error), **info}
        else:
            raise ValueError(f"{name}: success judge ACCEPTED a failing observation")

    def failure(self, name, rom, cue, code, stage, transferred, inject=None, **info):
        """Exact expected guest failure (error, stage, bytes), then the success judge must reject it."""
        records = self.run(rom, cue, inject=inject)
        judge_failure(records, code, stage, transferred)
        self.reject(name, records, guest_error=ERROR_NAMES.get(code, code), **info)
        return records


def mutation_tests(suite, records, sector):
    """Corrupt otherwise-good observations: the success judge must reject every one."""
    mutants = {}

    def mutated(name, edit):
        twin = copy.deepcopy(records)
        edit(twin)
        mutants[name] = twin

    for index, cycle in enumerate(("cold", "warm")):
        def ram_code(twin, i=index):
            data = bytearray(bytes.fromhex(twin["RUN"][i]["buffer_hex"]))
            data[EXEC_OFF + len(suite.code) // 2] ^= 0x80
            twin["RUN"][i]["buffer_hex"] = data.hex()

        def marker(twin, i=index):
            data = bytearray(bytes.fromhex(twin["RUN"][i]["buffer_hex"]))
            data[MARK_OFF + 4] = 0xF7  # payload observed MPR1 = $F7
            twin["RUN"][i]["buffer_hex"] = data.hex()

        def sector_byte(twin, i=index):
            data = bytearray(bytes.fromhex(twin["RUN"][i]["buffer_hex"]))
            data[HDR_BYTES + 3] ^= 1
            twin["RUN"][i]["buffer_hex"] = data.hex()

        def mpr(twin, i=index):
            twin["RUN"][i]["mpr"][1] = 0xF7

        def budget(twin, i=index):
            twin["RUN"][i]["reason"] = "budget"

        def result(twin, i=index):
            twin["RUN"][i]["result_hex"] = "09" + twin["RUN"][i]["result_hex"][2:]

        def unconfirmed(twin, i=index):
            twin["RUN"][i]["halt_confirmed"] = False

        mutated(f"{cycle}_code_in_ram_byte", ram_code)
        mutated(f"{cycle}_payload_marker_mpr", marker)
        mutated(f"{cycle}_transferred_sector_byte", sector_byte)
        mutated(f"{cycle}_mpr_at_halt", mpr)
        mutated(f"{cycle}_budget_stop", budget)
        mutated(f"{cycle}_result_block_not_success", result)
        mutated(f"{cycle}_halt_not_confirmed", unconfirmed)

        def entry_pc(twin, i=index):
            for h in twin["HIT"]:
                if h["cycle"] == cycle and h["name"] == "payload_entry":
                    h["pc"] = EXEC_ADDRESS + 1

        def no_return(twin, i=index):
            twin["HIT"] = [h for h in twin["HIT"] if not (h["cycle"] == cycle and h["name"] == "cd_payload_return")]

        mutated(f"{cycle}_payload_entry_wrong_pc", entry_pc)
        mutated(f"{cycle}_payload_never_returned", no_return)
    for name, twin in mutants.items():
        try:
            judge_success(twin, suite.code, sector, suite.symbols)
        except ValueError:
            continue
        raise ValueError(f"success judge accepted mutated observation: {name}")
    suite.results["judge_observation_mutations"] = {"status": "PASS_EXPECTED_FAILURE", "mutants": sorted(mutants)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("debug", "release"), default="debug")
    args = parser.parse_args()
    output = ROOT / "build" / args.mode / "cdload-exec"
    rom_bytes = (output / "cdload-exec-probe-not-bios.pce").read_bytes()
    manifest = json.loads((output / "manifest.json").read_text())
    symbols = manifest["symbols"]
    labels = output / "trace-labels.txt"
    code = (output / "payload.bin").read_bytes()
    if hashlib.sha256(code).hexdigest() != manifest["payload_sha256"]:
        raise ValueError("payload.bin differs from the manifest")
    if parse_labels(output / "payload.lbl").get("payload_entry") != EXEC_ADDRESS:
        raise ValueError("payload does not start at EXEC_ADDRESS")
    if hashlib.sha256(rom_bytes).hexdigest() != manifest["rom_sha256"]:
        raise ValueError("ROM differs from the manifest")
    probe = compile_probe(output, "geargrafx_cdread_probe.cpp", "geargrafx-cdload-exec-probe")
    with tempfile.TemporaryDirectory(prefix="pce-cdload-exec-") as tmp:
        suite = Suite(probe, labels, symbols, code, Path(tmp), rom_bytes)
        sector = encode_sector(DEFAULT_LBA, code)

        # ---- positive: load, verify, copy to RAM, execute, return (cold AND warm) ----
        primary = suite.positive("payload_from_lba5", 5)
        suite.positive("payload_from_lba0", 0)
        suite.positive("payload_from_last_sector_lba15", DISC_SECTORS - 1)

        # ---- negative: the same judge must reject each of these ----
        plain = Path(tmp) / "plain"
        build_image(plain, DISC_SECTORS)  # no header in the requested sector
        suite.failure("no_header_on_disc", suite.rom_for(DEFAULT_LBA), plain / CUE_NAME, 20, 11, SECTOR_BYTES)
        suite.failure("bad_magic", suite.rom, suite.disc("bad-magic", DEFAULT_LBA, tamper=[(0, 0x01)]),
                      20, 11, SECTOR_BYTES)
        suite.failure("bad_checksum_code_byte", suite.rom,
                      suite.disc("bad-checksum", DEFAULT_LBA, tamper=[(HDR_BYTES + 3, 0x01)]), 23, 11, SECTOR_BYTES)
        suite.failure("bad_entry_offset_equals_length", suite.rom,
                      suite.disc("bad-entry", DEFAULT_LBA, entry=len(code)), 21, 11, SECTOR_BYTES,
                      entry_offset=len(code), code_length=len(code))
        suite.failure("bad_length_zero", suite.rom, suite.disc("bad-length", DEFAULT_LBA, tamper=[(5, len(code))]),
                      22, 11, SECTOR_BYTES, note="length byte XOR len(code) = 0")
        suite.failure("lba16_past_end_of_disc", suite.rom_for(16), suite.disc("lba16", DEFAULT_LBA),
                      5, 5, 0, lba=16, note="ROM requests LBA 16 on a 16-sector disc")
        suite.failure("lba_max_out_of_range", with_request(suite.rom, symbols, 0x1FFFFF, 1),
                      suite.disc("lba-max", DEFAULT_LBA), 5, 5, 0, lba=0x1FFFFF)
        suite.failure("drive_abort_at_data_in", suite.rom, suite.disc("abort", DEFAULT_LBA), 4, 5, 0,
                      inject="eject-in-data", note="incomplete read: 0 of 2048 bytes before the abort")
        budget = suite.run(suite.rom, suite.disc("budget", DEFAULT_LBA), budget=1000)
        if any(run["reason"] != "budget" for run in budget["RUN"]) or any(h["name"] in ORDER[2:] for h in budget["HIT"]):
            raise ValueError("step budget run did not stop as budget before the payload")
        suite.reject("step_budget_exhausted", budget, note="budget 1000 steps, the header was never reached")

        mutation_tests(suite, primary, sector)

    evidence = {
        "scope": "load-and-execute experiment ONLY; CD_READ ($03) and every BIOS API NOT_IMPLEMENTED",
        "rom_sha256": manifest["rom_sha256"], "payload_sha256": manifest["payload_sha256"],
        "payload_bytes": len(code), "exec_address": EXEC_ADDRESS,
        "geargrafx_revision": DEPENDENCIES["geargrafx"]["revision"],
        "tests": suite.results, "primary_observations": {"RUN": primary["RUN"], "HIT": primary["HIT"]},
        "not_tested": ["partial sector read with bytes transferred > 0 (only the abort at data-in was injected)",
                       "copy-verify failure in RAM (no RAM fault injection)", "real hardware", "a second emulator"],
        "status": "PASS",
    }
    (output / "cdload-exec-evidence.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    passed = sum(1 for r in suite.results.values() if r["status"].startswith("PASS"))
    print(f"PASS real Geargrafx load+execute of CD sector code: {passed} scenarios "
          f"(cold+warm, RAM byte compare, PC order); NOT BIOS, CD_READ NOT_IMPLEMENTED")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"cdload-exec integration FAIL/BLOCKED: {error}") from error
