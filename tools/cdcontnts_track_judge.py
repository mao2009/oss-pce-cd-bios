"""Local judge for the Issue #265 synthetic CD track-attribute fixture (AUTHOR_SYNTHETIC, not an observation).

Classifies one query (medium, track number) against the author-defined fixture model. Four verdicts are kept
distinct so that a missing answer is never read as a refusal:

- POSITIVE: the query is decided by the synthetic model and the track exists (attributes returned).
- NEGATIVE: the query is decided by the model and refused (no medium, absent or malformed track number).
- UNKNOWN: the input is outside the model (medium dict not in the synthetic shape, unmodelled track type).
- BLOCKED: the answer would need the callee's own ABI (arguments, returns, registers) or a reference
  JSR. No callee is executable, so this is BLOCKED_ABI and never POSITIVE or NEGATIVE.

No ROM, emulator or reference BIOS is read. A passing judge validates only this local model, not CD_CONTNTS.
"""
import hashlib
import json
import sys
from pathlib import Path

POSITIVE, NEGATIVE, UNKNOWN, BLOCKED = "POSITIVE", "NEGATIVE", "UNKNOWN", "BLOCKED"
TRACK_TYPES = ("data", "audio")


def fixture_sha256(fixture):
    """Hash of the canonical JSON (sort_keys, compact, UTF-8). Same rule as the fixture host test."""
    text = json.dumps(fixture, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _well_formed(medium):
    if not isinstance(medium, dict) or medium.get("synthetic") is not True:
        return False
    tracks = medium.get("tracks")
    if not isinstance(tracks, list) or not isinstance(medium.get("leadout_lba"), int):
        return False
    return all(isinstance(t, dict) and t.get("type") in TRACK_TYPES and type(t.get("no")) is int for t in tracks)


def judge(medium, track, needs_callee=False):
    """Return (verdict, detail). medium is the selected synthetic medium dict, or None when no disc is loaded."""
    if needs_callee:  # the answer depends on the callee ABI, which is UNKNOWN and has no executable callee
        return BLOCKED, "BLOCKED_ABI: callee arguments/returns undecided; reference JSR not called"
    if medium is None:
        return NEGATIVE, "no medium loaded"
    if not _well_formed(medium):
        return UNKNOWN, "medium is outside the synthetic model"
    if type(track) is not int:  # bool is refused: type(True) is bool, not int
        return NEGATIVE, "malformed: track must be a plain int"
    entry = next((t for t in medium["tracks"] if t["no"] == track), None)
    if entry is None:
        return NEGATIVE, "track number not on the medium"
    return POSITIVE, {"type": entry["type"], "start": entry["start"], "end": entry["end"]}


def main(argv):
    """Usage: cdcontnts_track_judge.py FIXTURE_JSON. Prints the fixture hash and each case verdict."""
    if len(argv) != 2:
        print("usage: cdcontnts_track_judge.py FIXTURE_JSON", file=sys.stderr)
        return 2
    fixture = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    print(f"fixture_sha256={fixture_sha256(fixture)}")
    for case in fixture["cases"]:
        medium = None if case["media"] == "none" else fixture["media"][case["media"]]
        verdict, _ = judge(medium, case["track"])
        print(f"{case['id']}: expected={case['expected']} judged={verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
