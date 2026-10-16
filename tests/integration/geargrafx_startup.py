"""Actual Geargrafx System Card startup checkpoint (Issue #4, diagnostic-only).

Reads public CPU state from the pinned debugger-enabled core. No firmware HLE,
proprietary BIOS, production boot, or third-party code enters the ROM.
"""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from build_startup import EXPECTED_MPR, MARKER, SYMBOL_OFFSETS, verify_startup
from environment import DEPENDENCIES
from startup_media import classify_startup_media
from geargrafx_api import compile_probe
from geargrafx_contracts import execute, trace

CD_INIT_STATE = 1  # startup_cd_state value (non-zero, distinct from power-on RAM); $2205 observed in OBS work RAM


def check_evidence(records, symbols):
    if records["OBS"][0]["pc"] != symbols["startup_entry"]:
        raise ValueError("guest did not load the original startup reset vector")
    for row in records["OBS"][1:]:
        if (row["pc"] != symbols["startup_checkpoint"] or row["x"] != 0xb0
                or row["sp"] != 0xff or row["mpr"] != EXPECTED_MPR
                or bytes.fromhex(row["work_ram_0200_hex"])[:len(MARKER)] != MARKER):
            raise ValueError("actual System Card startup PC/X/SP/MPR/RAM checkpoint mismatch")
        if not (row["p"] & 4) or row["p"] & 8:
            raise ValueError("startup interrupt/decimal flags differ")
        # $2205 is inside the observed 64-byte window at $2200; it must hold the CD init state.
        if bytes.fromhex(row["work_ram_0200_hex"])[5] != CD_INIT_STATE:
            raise ValueError("observed CD init state byte differs from startup record")
    for cycle in ("cold", "warm"):
        ready = trace(records, cycle, "startup_mpr_ready")
        stack = trace(records, cycle, "startup_stack_ready")
        write = trace(records, cycle, "startup_marker_write")
        checkpoint = trace(records, cycle, "startup_checkpoint")
        if ready["mpr"] != EXPECTED_MPR or stack["sp"] != 0xff:
            raise ValueError("guest never reached explicit MPR/stack setup")
        if write["a"] != 0x42 or write["mpr"] != EXPECTED_MPR:
            raise ValueError("guest marker write not observed with expected mapping")
        if checkpoint["pc"] != symbols["startup_checkpoint"]:
            raise ValueError("guest never reached startup checkpoint")
        if any(row["cycle"] == cycle and row["phase"] == "startup_unexpected_interrupt"
               for row in records["TRACE"]):
            raise ValueError("unexpected interrupt diagnostic was executed")


def check_dispatched(records, symbols, bit):
    """Real IRQ taken through a startup vector: X=$EE trap, SP $FF->$FC, I set, stacked P (I clear)/PC."""
    halt = symbols["startup_unexpected_interrupt"] + 2  # LDX #$EE then BRA @halt
    for row in records["OBS"][1:]:
        stack = bytes.fromhex(row["stack_01f8_hex"])
        if ((row["pc"], row["x"], row["sp"]) != (halt, 0xee, 0xfc) or row["mpr"] != EXPECTED_MPR
                or not row["p"] & 4 or stack[5] & 4
                or int.from_bytes(stack[6:8], "little") != symbols["startup_checkpoint"]):
            raise ValueError("startup IRQ dispatch PC/X/SP/P/stack/MPR mismatch")
    for cycle in ("cold", "warm"):
        before = trace(records, cycle, "irq_asserted")
        entry = trace(records, cycle, "startup_unexpected_interrupt")
        if before["p"] & 4 or before["sp"] != 0xff or not entry["p"] & 4 or entry["sp"] != 0xfc:
            raise ValueError("startup IRQ I-flag/stack transition missing")
        if not entry["irr"] & bit:
            raise ValueError("requested interrupt source not pending at startup vector entry")


def run_interrupts(probe, payload, original, cue, output, folder, symbols):
    """Execute the startup ROM's IRQ1/IRQ2 vectors in the real core; timer/NMI stay NOT_TESTED."""
    labels = folder / "startup-irq-labels.txt"  # the harness asserts the line when PC reaches irq_wait
    labels.write_text((output / "trace-labels.txt").read_text()
                      + f"irq_wait {symbols['startup_checkpoint']:04x}\n", encoding="ascii")
    at = symbols["startup_checkpoint"] - 0xe000 - 2
    if payload[at:at + 2] != b"\xa2\xb0":
        raise ValueError("expected LDX #$B0 before startup_checkpoint")
    cli = bytearray(payload)
    cli[at:at + 2] = b"\x58\xea"  # CLI; NOP — test-only variant so the asserted line can be taken
    try:
        verify_startup(bytes(cli), original)
    except ValueError:
        pass
    else:
        raise ValueError("CLI variant passed fixture verifier")
    variant = folder / "startup-cli-not-bios.pce"
    variant.write_bytes(cli)
    results = {}
    for irq, bit in (("irq1", 2), ("irq2", 1)):
        masked = execute(probe, output / "startup-probe-not-bios.pce", cue, labels, irq)
        check_evidence(masked, symbols)  # unmodified ROM keeps I set: still the startup checkpoint
        if not all(row["irr"] & bit and row["p"] & 4 for row in masked["OBS"][1:]):
            raise ValueError(f"{irq} asserted at the I=1 checkpoint was not left pending")
        taken = execute(probe, variant, cue, labels, irq)
        check_dispatched(taken, symbols, bit)
        try:
            check_evidence(taken, symbols)
        except ValueError as error:
            rejected = str(error)
        else:
            raise ValueError(f"{irq} dispatch to the X=$EE trap passed the startup check")
        results[irq] = {"masked_at_checkpoint": "PASS", "dispatched_cli_variant": "PASS",
                        "startup_check_rejects_dispatch": rejected,
                        "observations": {"masked": masked, "dispatched": taken}}
    results["timer"] = "NOT_TESTED (startup ROM never programs the HuC6280 timer)"
    results["nmi"] = "NOT_TESTED (pinned Geargrafx HuC6280 has no NMI input or $FFFC dispatch)"
    results["cli_variant_sha256"] = hashlib.sha256(cli).hexdigest()
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("debug", "release"), default="debug")
    args = parser.parse_args()
    original_path = ROOT / "build" / args.mode / "bios-dev/dev-only-not-compatible-syscard3.pce"
    output = ROOT / "build" / args.mode / "startup-probe"
    rom = output / "startup-probe-not-bios.pce"
    original, payload = original_path.read_bytes(), rom.read_bytes()
    digest = verify_startup(payload, original)
    manifest = json.loads((output / "manifest.json").read_text())
    symbols = manifest["startup_symbols"]
    if symbols != {name: 0xf000 + offset for name, offset in SYMBOL_OFFSETS.items()}:
        raise ValueError("startup symbol manifest is inconsistent")
    probe = compile_probe(output)
    with tempfile.TemporaryDirectory(prefix="pce-original-startup-") as tmp:
        folder = Path(tmp)
        (folder / "original.bin").write_bytes(bytes(2352 * 32))
        cue = folder / "original.cue"
        cue.write_text('FILE "original.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        records = execute(probe, rom, cue, output / "trace-labels.txt")
        check_evidence(records, symbols)
        # Deterministic negative: redirect the second store to $2200, so 'O'
        # overwrites 'B' even if uninitialized work RAM happened to contain 'B'.
        damaged = bytearray(payload)
        marker_offset = symbols["startup_marker_write"] - 0xe000
        if damaged[marker_offset:marker_offset+3] != b"\x8d\x00\x22":
            raise ValueError("symbol-qualified STA $2200 opcode mismatch")
        second_store = marker_offset + 5  # STA $2200 (3) then LDA #$4F (2)
        if damaged[second_store:second_store+3] != b"\x8d\x01\x22":
            raise ValueError("expected second marker store STA $2201")
        damaged[second_store+1] = 0  # STA $2200 with A='O', not 'B'
        invalid = folder / "wrong-startup-marker-not-bios.pce"
        invalid.write_bytes(damaged)
        try:
            verify_startup(damaged, original)
        except ValueError:
            pass
        else:
            raise ValueError("invalid marker ROM passed fixture verifier")
        negative = execute(probe, invalid, cue, output / "trace-labels.txt")
        try:
            check_evidence(negative, symbols)
        except ValueError:
            pass
        else:
            raise ValueError("mutated startup ROM unexpectedly passed actual Geargrafx")
        # More real-core negatives: each mutated ROM must make the SAME evidence check fail.
        # (name, offset into STARTUP_CODE, expected original byte, replacement byte)
        # The CD-state store must be proven by a non-zero value: power-on RAM reads 0, so a removed
        # or redirected store would otherwise look identical to a correct one.
        mutations = (("mpr5_value", 24, b"\x03", b"\x05"), ("stack_pointer", 36, b"\xff", b"\xfe"),
                     ("cd_init_state_value", 62, b"\x01", b"\x00"),       # value equals power-on zero
                     ("cd_store_removed", 63, b"\x8d\x05\x22", b"\xea\xea\xea"),
                     ("cd_store_redirected", 64, b"\x05", b"\x06"))       # STA $2206 leaves $2205 at 0
        mutation_results = {}
        for name, offset, original_bytes, replacement in mutations:
            mutated = bytearray(payload)
            at = symbols["startup_entry"] - 0xe000 + offset
            if bytes(mutated[at:at + len(original_bytes)]) != original_bytes:
                raise ValueError(f"{name}: unexpected original bytes at startup+{offset}")
            mutated[at:at + len(replacement)] = replacement
            try:
                verify_startup(bytes(mutated), original)
            except ValueError:
                pass
            else:
                raise ValueError(f"{name}: mutated ROM passed fixture verifier")
            path = folder / f"{name}-not-bios.pce"
            path.write_bytes(mutated)
            observed = execute(probe, path, cue, output / "trace-labels.txt")
            try:
                check_evidence(observed, symbols)
            except ValueError as error:
                # CD mutations must fail on the CD-state byte itself, not on an unrelated trace gap.
                if name.startswith("cd_") and "CD init state" not in str(error):
                    raise ValueError(f"{name}: failed for an unrelated reason: {error}") from error
                mutation_results[name] = "PASS_EXPECTED_FAILURE"
            else:
                raise ValueError(f"{name}: mutated startup ROM unexpectedly passed actual Geargrafx")
        # ---- #142: same ROM, CD hardware on, with and without a disc. Four facts recorded separately. ----
        labels = output / "trace-labels.txt"
        checkpoint = symbols["startup_checkpoint"]
        with_disc = classify_startup_media(records, checkpoint)
        nodisc = execute(probe, rom, "-", labels, media=False)
        check_evidence(nodisc, symbols)  # no-media branch runs the same startup CPU/RAM checkpoint
        without_disc = classify_startup_media(nodisc, checkpoint)
        if (with_disc["media"], without_disc["media"]) != ("MEDIA_PRESENT", "NO_MEDIA"):
            raise ValueError("media/no-media harness branches were not labelled as expected (guest does not detect media)")
        media_negatives = {}
        # (1) a no-media run cannot be submitted as the normal-disc run
        try:
            execute(probe, rom, "-", labels)
        except ValueError:
            media_negatives["no_media_as_disc_run"] = "PASS_EXPECTED_FAILURE"
        else:
            raise ValueError("no-media run was accepted as a disc run")
        # (2) forged flags on real records are rejected
        for name, source, patch in (("no_media_claims_media", nodisc, {"cdrom_media": True, "disc_given": True}),
                                    ("disc_claims_no_media", records, {"cdrom_media": False, "disc_given": False})):
            forged = {**source, "CAP": [{**source["CAP"][0], **patch}]}
            try:
                classify_startup_media(forged, checkpoint)
            except ValueError:
                media_negatives[name] = "PASS_EXPECTED_FAILURE"
            else:
                raise ValueError(f"{name}: forged media flags were accepted")
        # (3) boundary: 4 instructions never reach the checkpoint, so the real run is not a verdict
        short = execute(probe, rom, "-", labels, media=False, steps=4)
        try:
            classify_startup_media(short, checkpoint)
        except ValueError:
            media_negatives["step_budget_too_small"] = "PASS_EXPECTED_FAILURE"
        else:
            raise ValueError("a run that stopped before the checkpoint was classified")
        interrupt_results = run_interrupts(probe, payload, original, cue, output, folder, symbols)
    evidence = {
        "scope": "original startup diagnostic ONLY; production BIOS/ABI/CD boot NOT_IMPLEMENTED",
        "rom_sha256": digest,
        "geargrafx_revision": DEPENDENCIES["geargrafx"]["revision"],
        "source_archive_sha256": DEPENDENCIES["geargrafx"]["sha256"],
        "loader": "LoadBiosFromBuffer(syscard=true) + original synthetic 32-sector CD",
        "status": "PASS",
        "observations": records, "wrong_marker_negative": {
            "status": "PASS_EXPECTED_FAILURE",
            "rom_sha256": hashlib.sha256(damaged).hexdigest(),
            "observations": negative,
        },
        "mutation_negatives": mutation_results,
        "cd_init_state_observed": CD_INIT_STATE, "media_branches": {"guest_media_detection": "NOT_PERFORMED", "verdict_meaning": "harness load branch, not guest detection",
                           "MEDIA_PRESENT": with_disc, "NO_MEDIA": without_disc,
                           "no_media_loader": "SetCDROMType(STANDARD)+LoadHuCardFromBuffer; real hardware UNKNOWN",
                           "negatives": media_negatives, "no_media_observations": nodisc},
        "bios_service_abi": "UNKNOWN", "cd_boot": "NOT_TESTED",
        "interrupt_vector_execution": interrupt_results,
        "proprietary_content": "NONE",
    }
    (output / "startup-evidence.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print("PASS real Geargrafx System Card startup: cold/warm PC/SP/MPR/RAM, marker/MPR/stack negatives; "
          "IRQ1/IRQ2 masked at checkpoint and dispatched to X=$EE trap in CLI variant; timer/NMI NOT_TESTED; NOT BIOS boot")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        raise SystemExit(f"startup integration FAIL/BLOCKED: {error}") from error
