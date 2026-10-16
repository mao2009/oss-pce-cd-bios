"""Real Geargrafx observation of the CD bus-reset path before a sector read (Issue #10, B2, diagnostic only).

The guest (src/cd_reset_probe.s) pulses $1804 bit 1 twice, waits for bus free after each pulse,
then performs READ(6) itself and copies the sector into guest RAM. Python/C++ never inject data,
service the CD or edit guest state. Reuses the #5 frontend (geargrafx_cdread_probe.cpp), the #5
Suite/judge (tools/cdread_verify.check_success) and the #5 negative helpers; only the reset-pulse
judge (tools/cdreset_bus_verify.py) is new.

This proves the low-level bus-reset register effect in Geargrafx ONLY. It does not prove the
System Card CD_RESET API (slot $01): no entry point, return, error or timing contract is measured.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from build_cdread import verify_cdread
from cdread_verify import check_success
from cdreset_bus_verify import check_reset_pulses, reset_observations
from environment import DEPENDENCIES
from geargrafx_api import compile_probe
from make_cd_image import BIN_NAME, CUE_NAME, build_image

from geargrafx_cdread import DEFAULT_REQUEST, Suite, execute, halt_confirmation_tests, judge_mutation_tests

ASSERT_RST_LOAD = b"\xa9\x02\x8d\x04\x18"   # lda #$02 / sta $1804 (the reset assertion)


def stripped_reset(payload: bytes) -> bytes:
    """Same ROM with the reset ASSERTION turned into NOPs; everything else (including the release) kept."""
    if payload.count(ASSERT_RST_LOAD) != 1:
        raise ValueError("expected exactly one reset-assertion sequence in the CD_RESET diagnostic")
    patched = bytearray(payload)
    at = payload.index(ASSERT_RST_LOAD) + 2
    patched[at:at + 3] = b"\xea\xea\xea"
    return bytes(patched)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("debug", "release"), default="debug")
    args = parser.parse_args()
    base = ROOT / "build" / args.mode / "bios-dev/dev-only-not-compatible-syscard3.pce"
    output = ROOT / "build" / args.mode / "cdreset-probe"
    rom = output / "cdreset-probe-not-bios.pce"
    payload = rom.read_bytes()
    manifest = json.loads((output / "manifest.json").read_text())
    symbols, labels = manifest["symbols"], output / "trace-labels.txt"
    digest = verify_cdread(payload, base.read_bytes(), symbols, manifest["code_sha256_excluding_request"])
    probe = compile_probe(output, "geargrafx_cdread_probe.cpp", "geargrafx-cdreset-probe")
    with tempfile.TemporaryDirectory(prefix="pce-original-cdreset-") as tmp:
        folder = Path(tmp)
        disc_manifest = build_image(folder / "disc")
        suite = Suite(probe, labels, symbols, payload, folder)

        # ---- positive: two bus resets, then a correct READ(6) through the same bus ----
        primary = suite.positive("reset_then_read_lba5", 5, 1)
        for run in primary["RUN"]:
            check_reset_pulses(run)
        suite.results["reset_then_read_lba5"]["reset_pulses"] = reset_observations(primary["RUN"][0])
        for name, lba, count in (("reset_then_read_lba0", 0, 1), ("reset_then_read_lba13_x3", 13, 3)):
            records = suite.positive(name, lba, count)
            for run in records["RUN"]:
                check_reset_pulses(run)

        # ---- negative: the same judges must reject these ----
        # selection after the resets is redirected to $1805: resets completed, the read must fail at select
        stuck = bytearray(payload)
        at = symbols["cd_select"] - 0xE000
        if stuck[at:at + 3] != bytes((0x8D, 0x00, 0x18)):
            raise ValueError("expected STA $1800 at cd_select")
        stuck[at + 1] = 0x05
        timeout = suite.failing_guest("select_after_reset_timeout", bytes(stuck), *DEFAULT_REQUEST, 2, 3, 0)
        for run in timeout["RUN"]:
            check_reset_pulses(run)  # the reset itself still worked
        # drive-side abort (media-eject notification) during the post-reset data phase: same #5 failure path
        eject = suite.failing_guest("drive_abort_after_reset_at_data_in", payload, *DEFAULT_REQUEST, 4, 5, 0,
                                    inject="eject-in-data")
        if len(eject["INJECT"]) != 2:
            raise ValueError("drive-abort injection was not applied to both cold and warm runs")
        # reset assertion removed: the reset judge MUST reject, the data path may still succeed
        no_assert = suite.run(stripped_reset(payload))
        check_success(no_assert, symbols, suite.disc, *DEFAULT_REQUEST)
        try:
            for run in no_assert["RUN"]:
                check_reset_pulses(run)
        except ValueError as error:
            if "reset not asserted" not in str(error):
                raise ValueError(f"reset-less ROM rejected for the wrong reason: {error}")
            suite.results["no_reset_assertion_rejected"] = {
                "status": "PASS_EXPECTED_FAILURE", "rejected_because": str(error),
                "observed": "data path identical without the assertion (bus already free); only the reset judge distinguishes it"}
        else:
            raise ValueError("reset judge ACCEPTED a ROM that never asserted $1804 bit 1")
        # tampered disc: the transfer completes, only the independent RAM comparison can catch it
        suite.tampered_disc("tampered_identity_byte3_after_reset", 5, 3)

        # ---- media-absent condition ----
        blocked = execute(probe, rom, None, labels)
        if not blocked["BLOCK"]:
            raise ValueError("no-media run unexpectedly executed guest code")
        suite.results["no_media_loaded"] = {
            "status": "NOT_TESTED", "reason": "Geargrafx does not execute the diagnostic ROM without media",
            "observed": blocked["BLOCK"][0]}

        judge_mutation_tests(suite, primary)
        halt_confirmation_tests(suite)

    evidence = {
        "scope": "original CD_RESET bus-reset diagnostic ONLY; System Card CD_RESET ($01) NOT_IMPLEMENTED and NOT verified",
        "rom_sha256": digest,
        "geargrafx_revision": DEPENDENCIES["geargrafx"]["revision"],
        "source_archive_sha256": DEPENDENCIES["geargrafx"]["sha256"],
        "loader": "LoadBiosFromBuffer(syscard=true) + original synthetic CUE/BIN (MODE1/2352)",
        "disc": {key: disc_manifest[key] for key in ("format", "sectors", "bin_sha256", "cue_sha256", "edc_ecc")},
        "tests": suite.results,
        "primary_observations": {"RUN": primary["RUN"], "PHASE": primary["PHASE"], "HIT": primary["HIT"]},
        "cd_reset_api_slot_01": "NOT_IMPLEMENTED", "entry_point_e003": "NOT_TESTED",
        "bios_service_abi": "UNKNOWN", "real_hardware_or_second_emulator": "NOT_TESTED",
        "busy_to_free_by_guest_reset": "NOT_TESTED (reset is issued only while the bus is already free)",
        "proprietary_content": "NONE", "status": "PASS",
    }
    (output / "cdreset-evidence.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    passed = sum(1 for r in suite.results.values() if r["status"].startswith("PASS"))
    print(f"PASS real Geargrafx CD_RESET bus-reset observation: {passed} verified scenarios "
          f"(cold+warm); CD_RESET API ($01) NOT_IMPLEMENTED, NOT verified")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"cdreset integration FAIL/BLOCKED: {error}") from error
