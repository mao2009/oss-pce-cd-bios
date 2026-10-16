#!/usr/bin/env python3
"""CD_BASE (Issue #11) measurement harness: fixture disc, observation plan and judge. DIAGNOSTIC ONLY.

This module does not run, emulate or describe the System Card CD_BASE / CD_READ callee and
establishes no BIOS ABI. It defines (a) an original synthetic disc whose INDEX 01 is NOT LBA 0,
(b) what must be observed on an executed callee to answer the six questions of
docs/cd-base-abi-ledger.md, and (c) a judge that turns such observations into per-question
statuses. The judge never outputs VERIFIED: an executed observation becomes OBSERVED (the
recorded value, verbatim, or the candidate models it is consistent with). Anything not
recorded stays UNKNOWN, and a missing or nonreturning callee makes every question BLOCKED.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cue_analysis import analyze  # noqa: E402
from make_cd_image import raw_sector  # noqa: E402

LEAD_IN = 150
INDEX01_LBA = 150  # fixture: virtual CUE PREGAP 00:02:00, so INDEX 01 / BIN frame 0 is disc LBA 150
FIXTURE_SECTORS = 16
BIN_NAME, CUE_NAME = "cd-base-index01-150.bin", "cd-base-index01-150.cue"
QUESTIONS = ("(1)", "(2)", "(3)", "(4)", "(5)", "(6)")
# CD_BASE input bytes written by the only licensed caller (ledger S1/S2); which ones the callee reads is (1).
INPUT_BYTES = ("_al", "_ah", "_bl", "_bh", "_cl", "_ch", "_dl", "_dh")

# What an executed run must record (field of the record -> question it answers).
OBSERVATION_PLAN = {
    "(1)": "cd_base_reads: [{inputs:{_al..}, block, read_lba}] - same block, inputs differing in one byte",
    "(2)": "plain_reads: [{block, read_lba}] - CD_READ after reset, no CD_BASE, >=2 distinct blocks; "
           "read_lba is the READ LBA the emulator's SCSI layer saw (null when no READ was issued)",
    "(3)": "retention: {after_cd_reset: offset|null, after_second_read: offset|null}",
    "(4)": "cd_base_reads compared with plain_reads (offset = read_lba - block)",
    "(5)": "side_effects: {mpr_before, mpr_after, ram_before_hex, ram_after_hex, ram_base} around one CD_BASE call",
    "(6)": "returns: [{case, a, x, y, p}] for no-disc / out-of-range / before-INDEX-01 / normal",
}


def build_fixture(directory: Path) -> dict:
    """Write the original INDEX01=150 CUE/BIN and return cue_analysis' report; fail if INDEX 01 is not 150."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / BIN_NAME).write_bytes(b"".join(raw_sector(INDEX01_LBA + i) for i in range(FIXTURE_SECTORS)))
    cue = directory / CUE_NAME
    cue.write_text(f'FILE "{BIN_NAME}" BINARY\n  TRACK 01 MODE1/2352\n    PREGAP 00:02:00\n    INDEX 01 00:00:00\n')
    report = analyze(cue)
    track = report["tracks"][0]
    if track["start_lba"] != INDEX01_LBA or track["sector"]["msf_mismatch_frames"] or report.get("findings"):
        raise ValueError(f"fixture INDEX 01 is not LBA {INDEX01_LBA}: {track}")
    return report


def _offsets(rows):
    return {row["read_lba"] - row["block"] for row in rows if row.get("read_lba") is not None}


def _blocks_read(rows):
    """Distinct block numbers among rows where a READ was actually issued (read_lba not null)."""
    return len({row["block"] for row in rows if row.get("read_lba") is not None})


def _candidates(offset, index01):
    """Candidate models (ledger §4) consistent with one constant offset read_lba - block."""
    names = []
    if offset == 0:
        names.append("ABS_LBA")
    if offset == index01:
        names.append("TRACK_START_PLUS_BLOCK")
    if offset == -LEAD_IN:
        names.append("MSF_FRAMES_FROM_00_00_00")
    names.append(f"BASE_PLUS_BLOCK(base={offset})")
    return names


def judge(record) -> dict:
    out = {q: {"status": "UNKNOWN", "reason": "not recorded"} for q in QUESTIONS}
    if not record or record.get("callee") != "returned":
        reason = (record or {}).get("callee_reason", "no executable CD_BASE callee")
        return {q: {"status": "BLOCKED", "reason": reason} for q in QUESTIONS}
    index01 = record.get("fixture_index01_lba")
    plain = record.get("plain_reads", [])
    plain_offsets = _offsets(plain)
    if _blocks_read(plain) >= 2 and len(plain_offsets) == 1:
        (offset,) = plain_offsets
        if not index01:
            out["(2)"] = {"status": "UNKNOWN", "reason": "INDEX 01 is LBA 0: absolute and track-relative coincide"}
        else:
            out["(2)"] = {"status": "OBSERVED", "offset": offset, "consistent_with": _candidates(offset, index01)}
    elif len(plain_offsets) > 1:
        out["(2)"] = {"status": "UNKNOWN", "reason": f"no constant offset: {sorted(plain_offsets)}"}
    based = record.get("cd_base_reads", [])
    # (4) needs a decided (2) baseline and the same premise on the CD_BASE side: >=2 blocks actually read.
    if out["(2)"]["status"] == "OBSERVED" and _blocks_read(based) >= 2:
        changed = _offsets(based) != plain_offsets
        out["(4)"] = {"status": "OBSERVED", "cd_base_changes_offset": changed, "offsets": sorted(_offsets(based))}
    read_bytes = set()
    for a in based:
        for b in based:
            diff = [k for k in INPUT_BYTES if a["inputs"].get(k) != b["inputs"].get(k)]
            if len(diff) == 1 and a["block"] == b["block"] and a.get("read_lba") != b.get("read_lba"):
                read_bytes.add(diff[0])
    if read_bytes:
        out["(1)"] = {"status": "OBSERVED", "bytes_that_change_the_read": sorted(read_bytes),
                      "untested_bytes": "UNKNOWN"}
    if record.get("retention"):
        out["(3)"] = {"status": "OBSERVED", **record["retention"]}
    fx = record.get("side_effects")
    if fx:
        before, after = bytes.fromhex(fx["ram_before_hex"]), bytes.fromhex(fx["ram_after_hex"])
        out["(5)"] = {"status": "OBSERVED",
                      "mpr_changed": [i for i in range(8) if fx["mpr_before"][i] != fx["mpr_after"][i]],
                      "ram_changed": [fx["ram_base"] + i for i, (x, y) in enumerate(zip(before, after)) if x != y]}
    if record.get("returns"):
        out["(6)"] = {"status": "OBSERVED", "returns": record["returns"]}
    return out


def record_from_api_rows(rows, handler: int) -> dict:
    """Build a judge record from geargrafx_api_probe rows of a jump to $E006 (slot $02)."""
    stuck = len(rows) >= 2 and all(row["pc"] == handler and row["x"] == 2 for row in rows[1:])
    if not stuck:
        raise ValueError("slot $02 rows do not show the fail-closed fallback")
    return {"callee": "nonreturning", "fixture_index01_lba": INDEX01_LBA,
            "callee_reason": f"$E006 is the fail-closed fallback: PC stays at ${handler:04X} with X=$02 "
                             "through cold, +32 steps and warm; no RTS, so nothing after the call is observable"}


if __name__ == "__main__":
    print(json.dumps({"plan": OBSERVATION_PLAN, "no_callee": judge(None)}, indent=2, ensure_ascii=False))
