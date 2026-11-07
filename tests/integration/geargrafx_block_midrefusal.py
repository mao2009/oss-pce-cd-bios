#!/usr/bin/env python3
"""Issue #367 (parent #173): per-byte mid-transfer refusal (method B) and transfer-source read
side effects, measured on the pinned real core with a verification-only patch.

Method B is the per-byte guard of patches/geargrafx-b49ae82-block-write-guard.patch
(gg_verify_block_write_guard): it is called before each byte's Read/Write, and returning false
skips that one byte's Read and Write. The core still advances source/destination/length and still
runs TransferEnd, so a refusal does NOT stop the instruction and does NOT make the transfer atomic.
This driver pins that behaviour for multi-byte transfers crossing a page boundary, for all five
block opcodes, and measures the transfer-source read side effect (VDC status register read).

Cases (for each of tii, tin, tia, tdd, tai):
* ``<op>-cross-armed``: length 3, destination crosses $3FFF/$4000 (MPR2 = $FF). Exactly the
  destination bytes in a $FF page are refused; allowed WRAM bytes before AND after a refused byte
  still land, TransferEnd restores A/X/Y, S stays balanced, VDC AR/R0/R7/SR are unchanged.
* ``<op>-cross-unguarded`` (RED control): same guest, no hook. The $FF destination bytes land on
  the VDC (AR is set by writes to $4000; the expected values below encode the pinned 5-bit AR
  masking. No case in this suite writes $4001, so nothing here measures that address.)
* ``<op>-srcread-armed``: length 1, source $4104 (VDC status register, read clears it),
  destination $3E90 (WRAM, allowed). The destination receives the status value and the status
  register is cleared: a per-byte guard that only sees the destination cannot prevent this read
  side effect.
* ``<op>-srcread-unguarded``: same, no hook; identical landing (the allowing hook is transparent).
* ``<op>-srcreadff-armed``: source $4104, destination $0000 ($FF page, VDC AR, refused). The
  status register is NOT cleared and AR is unchanged: the refusal skips the source read as well.
* ``<op>-srcreadff-unguarded``: same, no hook; AR receives the status value and the status
  register is cleared.

Whole-instruction preflight (method A/C) is measured separately in
geargrafx_block_transfer_matrix.py and the #473 stack-s6 suite and is not re-measured here; the
abort guard is left null in this probe. The refusal policy (refuse a destination whose page is
mapped to $FF) is written inside this probe; it is a test policy, not a production gate. Nothing
here changes BLOCKED_ABI or release_eligible, and the gate stays BLOCKED.

    python3 tests/integration/geargrafx_block_midrefusal.py   (make block-mid-refusal)
"""
import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geargrafx_block_write_guard import build_probes
from geargrafx_reference_guard import synthetic_media

PINNED_CORE_REVISION = "b49ae82a012eade566d72e9d61c9d95a5caa4da3"
OPS = {"tii": (0, 0x73), "tin": (1, 0xD3), "tia": (2, 0xE3), "tdd": (3, 0xC3), "tai": (4, 0xF3)}
PATTERN = (0x13, 0x27, 0x3B)  # WRAM $1E80, $1E81, $1E82
CC = 0xCC  # destination-area preset
SR_PRESET = 0x05
FRAMES = 3

# Per-op cross expectations: (guard_dests, refusals, allowed byte_src, allowed byte_dst,
# final wram_3ffe, final wram_3fff, final AR when unguarded).
# The AR register masks written values to 5 bits (pinned huc6270.cpp WriteRegister case 0:
# m_address_register = value & 0x1F), so 0x3B lands as 27 and 0x27 lands as 7. For TIA the only
# $4000 byte is the middle one (source $1E81 = 0x27), and for TDD it is the first one (0x3B).
CROSS = {
    "tii": ([0x3FFE, 0x3FFF, 0x4000], 1, [0x1E80, 0x1E81], [0x3FFE, 0x3FFF], PATTERN[0], PATTERN[1], PATTERN[2] & 0x1F),
    "tin": ([0x4000, 0x4000, 0x4000], 3, [], [], CC, CC, PATTERN[2] & 0x1F),
    "tdd": ([0x4000, 0x3FFF, 0x3FFE], 1, [0x1E81, 0x1E80], [0x3FFF, 0x3FFE], PATTERN[0], PATTERN[1], PATTERN[2] & 0x1F),
    "tia": ([0x3FFF, 0x4000, 0x3FFF], 1, [0x1E80, 0x1E82], [0x3FFF, 0x3FFF], CC, PATTERN[2], PATTERN[1] & 0x1F),
    "tai": ([0x3FFE, 0x3FFF, 0x4000], 1, [0x1E80, 0x1E81], [0x3FFE, 0x3FFF], PATTERN[0], PATTERN[1], PATTERN[0]),
}
# Unguarded observer sees every byte (hook null => allow, then observe, then write).
CROSS_ALL = {
    "tii": ([0x1E80, 0x1E81, 0x1E82], [0x3FFE, 0x3FFF, 0x4000]),
    "tin": ([0x1E80, 0x1E81, 0x1E82], [0x4000, 0x4000, 0x4000]),
    "tdd": ([0x1E82, 0x1E81, 0x1E80], [0x4000, 0x3FFF, 0x3FFE]),
    "tia": ([0x1E80, 0x1E81, 0x1E82], [0x3FFF, 0x4000, 0x3FFF]),
    "tai": ([0x1E80, 0x1E81, 0x1E80], [0x3FFE, 0x3FFF, 0x4000]),
}


def cases():
    for op in OPS:
        for variant in ("cross", "srcread", "srcreadff"):
            for role in ("armed", "unguarded"):
                yield f"{op}-{variant}-{role}"


def _common(row, case):
    if row.get("exit", 0) != 0:
        raise ValueError(f"{case}: probe refused to run (exit {row.get('exit')}): {row.get('stderr', '')[-300:]}")
    if row.get("mode") != case:
        raise ValueError(f"{case}: measured wrong mode {row.get('mode')!r}")
    if row.get("status") != "ok":
        raise ValueError(f"{case}: guest did not reach its end (status={row.get('status')})")
    if row["stop_pc"] != row["expected_end_pc"]:
        raise ValueError(f"{case}: PC {row['stop_pc']:#x} != expected {row['expected_end_pc']:#x}")
    if row["before"]["mpr"] != row["after"]["mpr"]:
        raise ValueError(f"{case}: MPR changed {row['before']['mpr']} -> {row['after']['mpr']}")
    if row["sp"] != [4, 4]:
        raise ValueError(f"{case}: S not balanced {row['sp']}")
    if row["p"] != [4, 4]:
        raise ValueError(f"{case}: P changed {row['p']}")
    if [row["a_after"], row["x_after"], row["y_after"]] != [0x12, 0x34, 0x07]:
        raise ValueError(f"{case}: A/X/Y not restored from WRAM stack "
                         f"({row['a_after']:#x}/{row['x_after']:#x}/{row['y_after']:#x})")
    if row["stack_wram"] != [0x34, 0x12, 0x07]:
        raise ValueError(f"{case}: stack frame {row['stack_wram']} != [X,A,Y] pushed bytes")
    if row.get("vdc_read_buffer") != [0xBEEF, 0xBEEF]:
        raise ValueError(f"{case}: VDC read buffer {row.get('vdc_read_buffer')} != preset (no case reads $2102)")
    op_name = case.split("-")[0]
    if row["op"] != op_name or row["block_op"] != OPS[op_name][0] or row["opcode"] != OPS[op_name][1]:
        raise ValueError(f"{case}: op/opcode mismatch in row")
    return op_name, case.split("-")[1]


def check_cross_armed(row, case):
    op, _ = _common(row, case)
    dests, refusals, bsrc, bdst, w3ffe, w3fff, _ar = CROSS[op]
    if row["armed"] is not True or row["length"] != 3:
        raise ValueError(f"{case}: not an armed length-3 run")
    if row["guard_calls"] != 3 or row["guard_refusals"] != refusals:
        raise ValueError(f"{case}: guard calls/refusals {row['guard_calls']}/{row['guard_refusals']}, want 3/{refusals}")
    if row["guard_dests"] != dests:
        raise ValueError(f"{case}: guard dest sequence {row['guard_dests']}, want {dests}")
    if row["byte_src"] != bsrc or row["byte_dst"] != bdst:
        raise ValueError(f"{case}: allowed byte sequence {row['byte_src']}/{row['byte_dst']}, want {bsrc}/{bdst}")
    # Continuation proof: for tia the byte after the refused byte landed (byte_dst second entry is
    # the post-refusal byte); for tdd bytes 2-3 landed after byte 1 was refused. The dest finals
    # below pin both the pre- and post-refusal landings.
    if [row["wram_3ffe"], row["wram_3fff"]] != [w3ffe, w3fff]:
        raise ValueError(f"{case}: WRAM dest finals {[row['wram_3ffe'], row['wram_3fff']]}, want {[w3ffe, w3fff]}")
    if row["vdc_ar"][0] != row["vdc_ar"][1] or row["vdc_r0"][0] != row["vdc_r0"][1] \
            or row["vdc_r7"][0] != row["vdc_r7"][1] or row["vdc_sr"] != [SR_PRESET, SR_PRESET]:
        raise ValueError(f"{case}: refused bytes left a VDC side effect "
                         f"(AR {row['vdc_ar']}, R0 {row['vdc_r0']}, R7 {row['vdc_r7']}, SR {row['vdc_sr']})")
    return "per_byte_refusal_continues_and_restores"


def check_cross_unguarded(row, case):
    op, _ = _common(row, case)
    dests, _ref, _bs, _bd, w3ffe, w3fff, ar = CROSS[op]
    all_src, all_dst = CROSS_ALL[op]
    if row["armed"] is not False or row["guard_calls"] != 0 or row["guard_refusals"] != 0:
        raise ValueError(f"{case}: unguarded run consulted the guard")
    if row["byte_src"] != all_src or row["byte_dst"] != all_dst:
        raise ValueError(f"{case}: unguarded byte sequence {row['byte_src']}/{row['byte_dst']}, want {all_src}/{all_dst}")
    if row["vdc_ar"] != [0, ar]:
        raise ValueError(f"{case}: VDC AR {row['vdc_ar']}, want [0, {ar:#x}] (forbidden bytes must land)")
    if [row["wram_3ffe"], row["wram_3fff"]] != [w3ffe, w3fff]:
        raise ValueError(f"{case}: WRAM dest finals differ from armed {[w3ffe, w3fff]}")
    return "forbidden_bytes_land_without_hook"


def check_srcread_armed(row, case, unguarded, want_ar):
    """want_ar is the expected final VDC AR."""
    _common(row, case)
    if row["length"] != 1 or row["src"] != 0x4104:
        raise ValueError(f"{case}: unexpected operands src={row['src']:#x} length={row['length']}")
    if row["vdc_sr"] != [SR_PRESET, 0]:
        raise ValueError(f"{case}: VDC status {row['vdc_sr']}, want [{SR_PRESET}, 0] (source read must clear it)")
    if unguarded:
        if row["armed"] is not False or row["guard_calls"] != 0:
            raise ValueError(f"{case}: unguarded run consulted the guard")
        if row["byte_src"] != [0x4104]:
            raise ValueError(f"{case}: unguarded byte_src {row['byte_src']}")
    if row["vdc_ar"] != [0, want_ar]:
        raise ValueError(f"{case}: VDC AR {row['vdc_ar']}, want [0, {want_ar:#x}]")


def check_srcread_unguarded(row, case):
    # No hook, allowed destination: status value lands and the status register is cleared.
    check_srcread_armed(row, case, True, 0)
    if row["byte_dst"] != [0x3E90]:
        raise ValueError(f"{case}: byte_dst {row['byte_dst']}")
    if row["wram_1e90"] != SR_PRESET:
        raise ValueError(f"{case}: destination {row['wram_1e90']:#x} != status value {SR_PRESET:#x}")
    return "source_read_side_effect_lands_without_hook"


def check_srcread_ok(row, case):
    # Destination is allowed, so the guard allows and the source read's side effect lands.
    check_srcread_armed(row, case, False, 0)
    if row["armed"] is not True or row["guard_calls"] != 1 or row["guard_refusals"] != 0:
        raise ValueError(f"{case}: guard calls/refusals {row['guard_calls']}/{row['guard_refusals']}, want 1/0")
    if row["byte_src"] != [0x4104] or row["byte_dst"] != [0x3E90]:
        raise ValueError(f"{case}: byte sequence {row['byte_src']}/{row['byte_dst']}")
    if row["wram_1e90"] != SR_PRESET:
        raise ValueError(f"{case}: destination {row['wram_1e90']:#x} != status value {SR_PRESET:#x}")
    return "source_read_side_effect_lands_on_allowed_transfer"


def check_srcreadff_armed(row, case):
    # Destination is refused, so the source read is skipped too: status stays preset, AR unchanged.
    _common(row, case)
    if row["armed"] is not True or row["guard_calls"] != 1 or row["guard_refusals"] != 1:
        raise ValueError(f"{case}: guard calls/refusals {row['guard_calls']}/{row['guard_refusals']}, want 1/1")
    if row["byte_src"] or row["byte_dst"]:
        raise ValueError(f"{case}: refused byte was observed as landed")
    if row["vdc_sr"] != [SR_PRESET, SR_PRESET]:
        raise ValueError(f"{case}: VDC status {row['vdc_sr']} != preset (refusal must skip the source read)")
    if row["vdc_ar"] != [0, 0]:
        raise ValueError(f"{case}: VDC AR {row['vdc_ar']} != [0, 0]")
    return "refusal_skips_source_read"


def check_srcreadff_unguarded(row, case):
    # No hook: the status value lands in AR and the status register is cleared.
    check_srcread_armed(row, case, True, SR_PRESET)
    if row["byte_dst"] != [0x0000]:
        raise ValueError(f"{case}: byte_dst {row['byte_dst']}")
    return "forbidden_source_read_and_write_land_without_hook"


def mutation_test(row, case):
    """Each falsified field must be rejected by the classifier via ValueError (the purpose
    assertion), never accepted. Applied via the real check functions, not via exceptions/timeouts."""
    variant = case.split("-")[1]
    if variant == "cross":
        check = check_cross_armed
        mutations = [
            ("refusal_not_counted", {"guard_refusals": 0}),
            ("guard_not_called", {"guard_calls": 0}),
            ("dest_sequence_wrong", {"guard_dests": [0]}),
            # TIN allows no byte, so truncating the allowed list would be a no-op: use a
            # different falsification for it (still purpose-asserted below).
            ("post_refusal_byte_missing", {"byte_src": row["byte_src"][:1], "byte_dst": row["byte_dst"][:1]}),
            ("dest_final_wrong", {"wram_3fff": row["wram_3fff"] ^ 0xFF}),
            ("vdc_side_effect_claimed", {"vdc_ar": [0, 1]}),
            ("registers_not_restored", {"a_after": 0}),
            ("sp_unbalanced", {"sp": [4, 1]}),
            ("mpr_changed", {"after": {"mpr": [0] * 8}}),
        ]
        if not row["byte_src"]:
            mutations = [m for m in mutations if m[0] != "post_refusal_byte_missing"] + \
                        [("allowed_byte_claimed", {"byte_src": [0x1E80], "byte_dst": [0x4000]})]
    elif variant == "srcread":
        check, mutations = check_srcread_ok, [
            ("status_not_cleared", {"vdc_sr": [SR_PRESET, SR_PRESET]}),
            ("dest_value_wrong", {"wram_1e90": 0}),
            ("guard_refused_although_allowed", {"guard_refusals": 1}),
            ("byte_not_observed", {"byte_src": []}),
        ]
    else:
        check, mutations = check_srcreadff_armed, [
            ("status_cleared_despite_refusal", {"vdc_sr": [SR_PRESET, 0]}),
            ("ar_side_effect_claimed", {"vdc_ar": [0, SR_PRESET]}),
            ("refusal_not_counted", {"guard_refusals": 0}),
            ("byte_claimed_landed", {"byte_src": [0x4104]}),
        ]
    for name, changes in mutations:
        broken = copy.deepcopy(row)
        broken.update(changes)
        try:
            check(broken, case)
        except ValueError:
            continue
        raise ValueError(f"mutation test FAILED: {case} {name} accepted by the classifier")


def mutation_test_unguarded(row, case):
    """The unguarded (RED) classifiers must also reject falsified rows via ValueError.

    The RED controls carry the 'forbidden bytes land' half of the argument, so their
    classifiers get the same purpose-assertion mutation proof as the armed ones.
    """
    variant = case.split("-")[1]
    if variant == "cross":
        check, mutations = check_cross_unguarded, [
            ("ar_not_landed", {"vdc_ar": [0, 0]}),
            ("guard_consulted", {"guard_calls": 1}),
            ("byte_sequence_wrong", {"byte_src": row["byte_src"][:1]}),
            ("dest_final_wrong", {"wram_3fff": row["wram_3fff"] ^ 0xFF}),
            ("registers_not_restored", {"y_after": 0}),
        ]
    elif variant == "srcread":
        check = check_srcread_unguarded
        mutations = [
            ("status_not_cleared", {"vdc_sr": [SR_PRESET, SR_PRESET]}),
            ("dest_value_wrong", {"wram_1e90": 0}),
            ("ar_wrong", {"vdc_ar": [0, 1]}),
        ]
    else:
        check, mutations = check_srcreadff_unguarded, [
            ("ar_not_landed", {"vdc_ar": [0, 0]}),
            ("status_not_cleared", {"vdc_sr": [SR_PRESET, SR_PRESET]}),
            ("byte_dst_wrong", {"byte_dst": []}),
        ]
    for name, changes in mutations:
        broken = copy.deepcopy(row)
        broken.update(changes)
        try:
            check(broken, case)
        except ValueError:
            continue
        raise ValueError(f"mutation test FAILED: {case} {name} accepted by the unguarded classifier")


def run_case(probe, bios, cue, case):
    result = subprocess.run([str(probe), str(bios), str(cue), case, str(FRAMES)],
                            capture_output=True, text=True, timeout=120, check=False)
    if result.returncode:
        return {"mode": case, "exit": result.returncode, "stderr": result.stderr[-400:]}
    try:
        row = json.loads(result.stderr)
    except ValueError as error:
        raise ValueError(f"{case}: probe emitted no JSON observation") from error
    if type(row) is not dict:
        raise ValueError(f"{case}: observation is not an object")
    row["exit"] = 0
    row["armed"] = case.endswith("-armed")
    return row


def main():
    with tempfile.TemporaryDirectory(prefix="pce-block-midrefusal-") as tmp:
        directory = Path(tmp)
        bios, cue = synthetic_media(directory)
        probe = build_probes(directory, "geargrafx_block_midrefusal.cpp", "block-midrefusal-probe")
        evidence = []
        for case in cases():
            row = run_case(probe, bios, cue, case)
            variant = case.split("-")[1]
            if variant == "cross":
                verdict = check_cross_armed(row, case) if case.endswith("-armed") else check_cross_unguarded(row, case)
            elif variant == "srcread":
                verdict = check_srcread_ok(row, case) if case.endswith("-armed") else check_srcread_unguarded(row, case)
            else:
                verdict = check_srcreadff_armed(row, case) if case.endswith("-armed") else check_srcreadff_unguarded(row, case)
            if case.endswith("-armed"):
                mutation_test(row, case)
            else:
                mutation_test_unguarded(row, case)
            evidence.append((case, verdict, row))
            print(f"PASS {case}: {verdict}")
    for case, verdict, row in evidence:
        print("EVIDENCE " + json.dumps({"case": case, "observation_kind": "REAL_CORE_EXECUTION",
                                        "core_revision": PINNED_CORE_REVISION, "bios": "SYNTHETIC",
                                        "hook": "VERIFICATION_ONLY_PATCH", "verdict": verdict, "row": row},
                                       sort_keys=True))
    print("PASS issue-367 per-byte mid-transfer refusal (method B): refused bytes are skipped, the transfer "
          "continues and TransferEnd restores A/X/Y; it is not a whole-instruction safe stop, synthetic media")
    print("PASS issue-367 transfer-source read side effect: an allowed destination still reads the VDC "
          "status register (clears it, value lands); a refused destination skips the source read, synthetic media")
    print("BLOCKED: the per-byte guard is a verification-only test policy, not a production gate; no "
          "whole-instruction atomic stop exists on this path, and no device-originated write was probed. "
          "Nothing here changes BLOCKED_ABI or release_eligible.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(f"FAIL/BLOCKED issue-367 block mid-refusal: {error}", file=sys.stderr)
        raise SystemExit(1) from error
