"""Real Geargrafx: DEV-ONLY internal boot path (Issue #501). NOT compatible, NOT a BIOS, NOT DEV_CALLABLE.

Guest path, all HuC6280 code in the ROM, no host injection:
  reset (vector checked in the ROM by tools/build_dev_boot_internal.py) -> cd_media_state (READ(6) LBA 0, must be MEDIA_SIGNATURE_OK) -> cd_load_exec_probe (READ(6) LBA 5,
  2048 bytes to $2800, copy 'PCEX' code to $3000, jump) -> payload marker -> cd_halt_ok.
The judge below accepts a run only when all of these hold for BOTH the cold and the warm reset.
Every negative run must be rejected by the same judge.
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
from build_cdload_exec import ERROR_NAMES, EXEC_ADDRESS, SECTOR_BYTES, build_exec_disc, encode_sector, parse_labels
from build_cdread import with_request
from cdread_verify import (PHASE_BUS_FREE, PHASE_COMMAND, PHASE_DATA_IN, PHASE_MESSAGE_IN, PHASE_STATUS,
                           check_halt_confirmation, result_block)
from environment import DEPENDENCIES
from geargrafx_api import compile_probe
from geargrafx_cdread import execute
from make_cd_image import BIN_NAME, CUE_NAME, RAW_BYTES, build_image

STEP_BUDGET = 12_000_000
DEFAULT_LBA = 5
DISC_SECTORS = 16
BUFFER_BASE = 0x2800        # the probe dumps $2800-$3FFF (6144 bytes)
MARK_OFF = 0x3F00 - BUFFER_BASE
MS_RES = 0x3F40             # cd_media_state result block for this path (src/dev_boot_internal.s)
MS_OFF = MS_RES - BUFFER_BASE
MS_STATE_OK, MS_UNRECOGNISED, MS_STAGE_DONE = 0x01, 0x02, 6
ORDER = ("dev_boot_entry", "cd_media_state", "ms_done", "cd_buffer_ready", "cd_data_done", "payload_entry", "payload_done",
         "cd_payload_return", "cd_halt_ok")
MARKER = (0xA5, 0x5A, 0xF8, 0xFF, 0x3C)  # payload writes: MARK[0], MARK[1], MARK[4], MARK[5], MARK[6]


def unrecognised_mutations(records, symbols):
    """The unrecognised-disc judge must reject the same run halted at any other PC or with any other reason."""
    mutants = {}
    other_pcs = {"cd_halt_ok": symbols["cd_halt_ok"], "cd_halt_fail": symbols["cd_halt_fail"],
                 "ms_done": symbols["ms_done"]}
    for name, pc in other_pcs.items():
        twin = copy.deepcopy(records)
        for run in twin["RUN"]:
            run["pc"] = pc
        mutants[f"halted_at_{name}"] = twin
    twin = copy.deepcopy(records)
    for run in twin["RUN"]:
        run["reason"] = "budget"
    mutants["reason_budget"] = twin
    for name, twin in mutants.items():
        try:
            judge_unrecognised(twin, symbols)
        except ValueError:
            continue
        raise ValueError(f"unrecognised judge accepted mutated observation: {name}")
    return sorted(mutants)


def check_reads(records, cycle, lba):
    """The emulator's own SCSI trace: exactly two READ(6) transfers, LBA 0 (media check) then LBA `lba`."""
    rows = [row for row in records["PHASE"] if row["cycle"] == cycle]
    reads = [(row["load_sector"], row["load_count"]) for row in rows if row["scsi_phase"] == PHASE_DATA_IN]
    if reads != [(0, 1), (lba, 1)]:
        raise ValueError(f"{cycle}: READ(6) requests {reads}, expected {[(0, 1), (lba, 1)]}")
    bus = [row["scsi_phase"] for row in rows if row["scsi_phase"] != PHASE_BUS_FREE]
    want = [PHASE_COMMAND, PHASE_DATA_IN, PHASE_STATUS, PHASE_MESSAGE_IN] * 2
    if bus != want:
        raise ValueError(f"{cycle}: SCSI phases {bus} differ from two complete READ(6) transfers")


def judge_success(records, code, symbols, lba):
    """Raise ValueError unless cold AND warm ran the full DEV-only internal path with the expected results."""
    sector = encode_sector(lba, code)
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
        check_halt_confirmation(run)
        if run["sp"] != 0xFF:
            raise ValueError(f"{cycle}: stack pointer {run['sp']:#x} at halt, expected 0xff")
        block = result_block(run)
        if (block["code"], block["stage"], block["bytes"], block["scsi_status"], block["scsi_message"]) \
                != (0, 16, SECTOR_BYTES, 0, 0):
            raise ValueError(f"{cycle}: cd_load_exec result block is not a clean success: {block}")
        buffer = bytes.fromhex(run["buffer_hex"])
        if len(buffer) != 6144:
            raise ValueError(f"{cycle}: RAM dump has {len(buffer)} bytes")
        ms = buffer[MS_OFF:MS_OFF + 7]
        if (ms[0], ms[1], ms[2] | ms[3] << 8, ms[4], ms[5]) != (MS_STATE_OK, MS_STAGE_DONE, SECTOR_BYTES, 0, 0):
            raise ValueError(f"{cycle}: media check result at {MS_RES:#x} is not MEDIA_SIGNATURE_OK: {ms.hex()}")
        for index, (got, want) in enumerate(zip(buffer[:SECTOR_BYTES], sector)):
            if got != want:
                raise ValueError(f"{cycle}: transferred sector differs at byte {index}")
        if buffer[EXEC_ADDRESS - BUFFER_BASE:EXEC_ADDRESS - BUFFER_BASE + len(code)] != code:
            raise ValueError(f"{cycle}: code in RAM at {EXEC_ADDRESS:#x} differs from the payload")
        mark = buffer[MARK_OFF:MARK_OFF + 7]
        if (mark[0], mark[1], mark[4], mark[5], mark[6]) != MARKER:
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
        expected_pc = {"dev_boot_entry": symbols["dev_boot_entry"], "cd_media_state": symbols["cd_media_state"],
                       "ms_done": symbols["ms_done"],
                       "payload_entry": EXEC_ADDRESS, "payload_done": symbols["payload_done"],
                       "cd_payload_return": symbols["cd_payload_return"], "cd_halt_ok": symbols["cd_halt_ok"]}
        for name, pc in expected_pc.items():
            if by_name[name]["pc"] != pc:
                raise ValueError(f"{cycle}: {name} reached at PC {by_name[name]['pc']:#x}, expected {pc:#x}")
        check_reads(records, cycle, lba)


def judge_failure(records, code, stage, transferred, after_payload=False):
    """Every run halted in cd_halt_fail with the given cd_load_exec error.

    Default: the failure is before the payload, so no payload label may be reached. after_payload=True: the
    payload ran and returned through the mailbox, and the ROM's marker check then failed (no success halt)."""
    if [run["cycle"] for run in records["RUN"]] != ["cold", "warm"]:
        raise ValueError("missing cold/warm runs")
    for run in records["RUN"]:
        cycle = run["cycle"]
        if run["reason"] != "cd_halt_fail":
            raise ValueError(f"{cycle}: expected cd_halt_fail, got {run['reason']}")
        check_halt_confirmation(run)
        block = result_block(run)
        got = (block["code"], block["stage"], block["bytes"])
        if got != (code, stage, transferred):
            raise ValueError(f"{cycle}: guest error {got} ({ERROR_NAMES.get(got[0], '?')}), "
                             f"expected {(code, stage, transferred)}")
        names = {h["name"] for h in records["HIT"] if h["cycle"] == cycle}
        if after_payload:
            if not {"payload_entry", "cd_payload_return"} <= names or "cd_halt_ok" in names:
                raise ValueError(f"{cycle}: expected payload run and return before the marker failure: {sorted(names)}")
        elif {"payload_entry", "payload_done", "cd_payload_return", "cd_halt_ok"} & names:
            raise ValueError(f"{cycle}: payload or return label reached after a failed check")


def judge_unrecognised(records, symbols):
    """Media check did not accept the disc: the run must stop at dev_media_halt (not anywhere else), with no
    READ(6) of the payload sector, no payload and no success halt."""
    if [run["cycle"] for run in records["RUN"]] != ["cold", "warm"]:
        raise ValueError("missing cold/warm runs")
    for run in records["RUN"]:
        cycle = run["cycle"]
        if run["reason"] != "dev_media_halt" or run["pc"] != symbols["dev_media_halt"]:
            raise ValueError(f"{cycle}: stopped with {run['reason']} at PC {run['pc']:#x}, "
                             f"expected dev_media_halt at {symbols['dev_media_halt']:#x}")
        check_halt_confirmation(run)
        ms = bytes.fromhex(run["buffer_hex"])[MS_OFF:MS_OFF + 7]
        if ms[0] != MS_UNRECOGNISED:
            raise ValueError(f"{cycle}: media check state {ms[0]:#x}, expected MEDIA_UNRECOGNISED")
        names = {h["name"] for h in records["HIT"] if h["cycle"] == cycle}
        if {"cd_buffer_ready", "payload_entry", "cd_halt_ok"} & names:
            raise ValueError(f"{cycle}: payload-path label reached after an unrecognised disc: {sorted(names)}")
        rows = [row for row in records["PHASE"] if row["cycle"] == cycle and row["scsi_phase"] == PHASE_DATA_IN]
        if [(row["load_sector"], row["load_count"]) for row in rows] != [(0, 1)]:
            raise ValueError(f"{cycle}: READ(6) beyond the media check: {rows}")


class Suite:
    def __init__(self, probe, labels, symbols, code, folder, rom):
        self.probe, self.labels, self.symbols, self.code, self.folder, self.rom = probe, labels, symbols, code, folder, rom
        self.n = 0
        self.results = {}

    def run(self, rom_bytes, cue, inject=None, budget=STEP_BUDGET):
        self.n += 1
        path = self.folder / f"variant-{self.n}-not-compatible.pce"
        path.write_bytes(rom_bytes)
        return execute(self.probe, path, cue, self.labels, budget, inject)

    def rom_for(self, lba, count=1):
        return self.rom if (lba, count) == (DEFAULT_LBA, 1) else with_request(self.rom, self.symbols, lba, count)

    def disc(self, name, lba, code=None, tamper=()):
        directory = self.folder / name
        build_exec_disc(directory, lba, self.code if code is None else code)
        for offset, delta in tamper:
            bin_path = directory / BIN_NAME
            raw = bytearray(bin_path.read_bytes())
            raw[lba * RAW_BYTES + 16 + offset] ^= delta
            bin_path.write_bytes(raw)
        return directory / CUE_NAME

    def positive(self, name, lba):
        records = self.run(self.rom_for(lba), self.disc(f"disc-lba{lba}", lba))
        judge_success(records, self.code, self.symbols, lba)
        self.results[name] = {"status": "PASS", "lba": lba, "payload_bytes": len(self.code),
                              "sector_sha256": hashlib.sha256(encode_sector(lba, self.code)).hexdigest(),
                              "reads": "LBA0 media check, then LBA %d" % lba}
        return records

    def expect_reject(self, name, records, lba, code, **info):
        try:
            judge_success(records, code, self.symbols, lba)
        except ValueError as error:
            self.results[name] = {"status": "PASS_EXPECTED_FAILURE", "rejected_because": str(error), **info}
        else:
            raise ValueError(f"{name}: success judge ACCEPTED a failing observation")

    def failure(self, name, rom, cue, code, stage, transferred, after_payload=False, **info):
        records = self.run(rom, cue)
        judge_failure(records, code, stage, transferred, after_payload)
        self.expect_reject(name, records, DEFAULT_LBA, self.code,
                           guest_error=f"{ERROR_NAMES.get(code, code)} (code {code}) at stage {stage}, "
                                       f"{transferred} bytes", **info)


def mutation_tests(suite, records):
    """Corrupt otherwise-good observations: the success judge must reject every one."""
    mutants = {}
    for index, cycle in enumerate(("cold", "warm")):
        def mutated(name, edit, i=index):
            twin = copy.deepcopy(records)
            edit(twin, i)
            mutants[f"{cycle}_{name}"] = twin

        def ram_code(twin, i):
            data = bytearray(bytes.fromhex(twin["RUN"][i]["buffer_hex"]))
            data[EXEC_ADDRESS - BUFFER_BASE + len(suite.code) // 2] ^= 0x80
            twin["RUN"][i]["buffer_hex"] = data.hex()

        def ms_state(twin, i):
            data = bytearray(bytes.fromhex(twin["RUN"][i]["buffer_hex"]))
            data[MS_OFF] = MS_UNRECOGNISED
            twin["RUN"][i]["buffer_hex"] = data.hex()

        def marker(twin, i):
            data = bytearray(bytes.fromhex(twin["RUN"][i]["buffer_hex"]))
            data[MARK_OFF + 4] = 0xF7
            twin["RUN"][i]["buffer_hex"] = data.hex()

        def drop_media_read(twin, i):
            cyc = twin["RUN"][i]["cycle"]
            seen = 0
            kept = []
            for p in twin["PHASE"]:
                if p["cycle"] == cyc and p["scsi_phase"] == PHASE_DATA_IN:
                    seen += 1
                    if seen == 1:
                        continue
                kept.append(p)
            twin["PHASE"] = kept

        def no_media_label(twin, i):
            twin["HIT"] = [h for h in twin["HIT"] if not (h["cycle"] == twin["RUN"][i]["cycle"] and h["name"] == "ms_done")]

        def wrong_entry_pc(twin, i):
            for h in twin["HIT"]:
                if h["cycle"] == twin["RUN"][i]["cycle"] and h["name"] == "payload_entry":
                    h["pc"] = EXEC_ADDRESS + 1

        def budget(twin, i):
            twin["RUN"][i]["reason"] = "budget"

        mutated("ram_code_byte", ram_code)
        mutated("media_state_not_ok", ms_state)
        mutated("payload_marker_mpr", marker)
        mutated("media_read_missing", drop_media_read)
        mutated("media_label_missing", no_media_label)
        mutated("payload_entry_wrong_pc", wrong_entry_pc)
        mutated("budget_stop", budget)
    for name, twin in mutants.items():
        try:
            judge_success(twin, suite.code, suite.symbols, DEFAULT_LBA)
        except ValueError:
            continue
        raise ValueError(f"success judge accepted mutated observation: {name}")
    suite.results["judge_observation_mutations"] = {"status": "PASS_EXPECTED_FAILURE", "mutants": sorted(mutants)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("debug", "release"), default="debug")
    args = parser.parse_args()
    output = ROOT / "build" / args.mode / "dev-boot-internal"
    rom_bytes = (output / "dev-only-not-compatible-dev-boot-internal.pce").read_bytes()
    manifest = json.loads((output / "manifest.json").read_text())
    symbols = manifest["symbols"]
    if manifest.get("dev_callable") is not False or manifest.get("release_eligible") is not False:
        raise ValueError("manifest must state dev_callable=false and release_eligible=false")
    code = (output / "payload.bin").read_bytes()
    if hashlib.sha256(code).hexdigest() != manifest["payload_sha256"]:
        raise ValueError("payload.bin differs from the manifest")
    if parse_labels(output / "payload.lbl").get("payload_entry") != EXEC_ADDRESS:
        raise ValueError("payload does not start at EXEC_ADDRESS")
    if hashlib.sha256(rom_bytes).hexdigest() != manifest["rom_sha256"]:
        raise ValueError("ROM differs from the manifest")
    probe = compile_probe(output, "geargrafx_cdread_probe.cpp", "geargrafx-dev-boot-internal-probe")
    with tempfile.TemporaryDirectory(prefix="pce-dev-boot-internal-") as tmp:
        suite = Suite(probe, output / "trace-labels.txt", symbols, code, Path(tmp), rom_bytes)

        # ---- positive: media check, READ(6) of the payload sector, RAM copy, jump, return (cold AND warm) ----
        primary = suite.positive("payload_from_lba5", 5)
        suite.positive("payload_from_last_sector_lba15", DISC_SECTORS - 1)

        # ---- negative: the same judge must reject each of these ----
        # (a) ROM asks for 2 sectors: the ROM's own request check fails before any transfer (code 9 = INVALID_REQUEST)
        suite.failure("request_two_sectors", suite.rom_for(DEFAULT_LBA, 2), suite.disc("count2", DEFAULT_LBA),
                      9, 1, 0, count=2, note="sector count mismatch rejected by the guest")
        # (b) disc carries the payload at LBA 0: the media check must refuse it and never READ LBA 5
        bad_media = Path(tmp) / "bad-media-lba0"
        build_exec_disc(bad_media, 0, code)
        records = suite.run(rom_bytes, bad_media / CUE_NAME, budget=STEP_BUDGET)
        judge_unrecognised(records, symbols)
        suite.results["unrecognised_judge_mutations"] = {"status": "PASS_EXPECTED_FAILURE",
                                                         "mutants": unrecognised_mutations(records, symbols)}
        suite.results["disc_unrecognised_at_media_check"] = {
            "status": "PASS", "stopped_at": "dev_media_halt", "media_state": "MEDIA_UNRECOGNISED (0x02)",
            "note": "LBA 0 without the synthetic magic; halted at dev_media_halt; no LBA 5 read"}
        # (c) a payload whose marker byte is wrong: the ROM's own marker check after the return rejects it
        #     (PAYLOAD_MARKER_MISMATCH = 30 at stage 15); the success judge must also reject the observation
        wrong = bytearray(code)
        at = bytes(wrong).index(b"\xa9\x3c")  # lda #$3C (payload marker value)
        wrong[at + 1] = 0x3D
        suite.failure("payload_marker_wrong", suite.rom_for(DEFAULT_LBA),
                      suite.disc("wrong-marker", DEFAULT_LBA, code=bytes(wrong)), 30, 15, SECTOR_BYTES,
                      after_payload=True, note="marker byte $3C -> $3D")
        # (d) transfer tampered on the disc: the guest copies what it reads; the judge compares every byte
        records = suite.run(suite.rom_for(DEFAULT_LBA), suite.disc("tampered", DEFAULT_LBA, tamper=[(300, 0x01)]))
        suite.expect_reject("transfer_byte_tampered", records, DEFAULT_LBA, code, note="user byte 300 XOR 1")
        # (e) bad header checks still stop the path before the payload (from the Issue #14/#5 set)
        suite.failure("bad_magic", suite.rom, suite.disc("bad-magic", DEFAULT_LBA, tamper=[(0, 0x01)]),
                      20, 11, SECTOR_BYTES)

        mutation_tests(suite, primary)

    evidence = {
        "scope": "DEV-ONLY internal boot path (Issue #501); NOT compatible, NOT BIOS, NOT DEV_CALLABLE",
        "rom_sha256": manifest["rom_sha256"], "payload_sha256": manifest["payload_sha256"],
        "payload_bytes": len(code), "exec_address": EXEC_ADDRESS, "ms_result_address": MS_RES,
        "geargrafx_revision": DEPENDENCIES["geargrafx"]["revision"],
        "tests": suite.results, "primary_observations": {"RUN": primary["RUN"], "HIT": primary["HIT"],
                                                          "PHASE": primary["PHASE"]},
        "not_tested": ["real hardware", "a second emulator", "RAM fault injection in the copy",
                       "partial read with bytes transferred > 0 on the media check"],
        "status": "PASS",
    }
    (output / "dev-boot-internal-evidence.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    passed = sum(1 for r in suite.results.values() if r["status"].startswith("PASS"))
    print(f"PASS DEV-only internal boot via real Geargrafx: {passed} scenarios "
          f"(cold+warm, guest READ(6) x2, RAM byte compare, PC order); NOT compatible, NOT BIOS, NOT DEV_CALLABLE")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"dev-boot-internal integration FAIL/BLOCKED: {error}") from error
