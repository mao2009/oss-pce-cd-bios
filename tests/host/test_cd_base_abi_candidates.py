"""Synthetic tests for CANDIDATE CD_BASE call models (Issue #11).

These tests exercise four host-side candidate models of how a block number could be
interpreted, on a synthetic disc whose INDEX 01 is not LBA 0. They do NOT run, emulate
or describe the System Card CD_BASE / CD_READ callee, and they establish no BIOS ABI.
Their only purpose is to show which probe outcomes would separate the candidates once
an executed callee exists. See docs/cd-base-abi-ledger.md.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

REJECT = "REJECT"
MAX_U24 = 0xFFFFFF
LEAD_IN = 150  # absolute MSF 00:02:00 is LBA 0; frames before INDEX 01 are not stored


class SyntheticCD:
    """One data track. INDEX 01 at index01_lba; user sectors index01_lba..+length-1.

    Each readable sector's payload is its own disc LBA, so a readback names the sector it
    came from. Frames before INDEX 01 (a virtual PREGAP) are not stored and read as REJECT.
    """

    def __init__(self, index01_lba, length=16):
        self.index01 = index01_lba
        self.length = length

    def read(self, lba):
        if self.index01 <= lba < self.index01 + self.length:
            return lba
        return REJECT


def _u24(value):
    if not 0 <= value <= MAX_U24:
        raise ValueError(f"out of 24-bit range: {value}")
    return value


# CANDIDATE models: (block, base, disc) -> disc LBA. base is the value a CD_BASE call would set.
CANDIDATE_MODELS = {
    "ABS_LBA": lambda block, base, cd: _u24(block),
    "BASE_PLUS_BLOCK": lambda block, base, cd: _u24(base + block),
    "TRACK_START_PLUS_BLOCK": lambda block, base, cd: _u24(cd.index01 + block),
    "MSF_FRAMES_FROM_00_00_00": lambda block, base, cd: _u24(block - LEAD_IN),
}

# (block, base) probes. Each probe is one hypothetical CD_READ after one CD_BASE setting.
PROBES = [(5, 0), (5, LEAD_IN), (5 + LEAD_IN, 0), (5, 16)]


def probe(model, block, base, cd):
    try:
        lba = CANDIDATE_MODELS[model](block, base, cd)
    except ValueError:
        return REJECT
    return cd.read(lba)


def signature(model, cd):
    return tuple(probe(model, block, base, cd) for block, base in PROBES)


class CandidateModelSyntheticTest(unittest.TestCase):
    def test_index01_nonzero_disc_separates_all_candidates(self):
        cd = SyntheticCD(index01_lba=LEAD_IN)
        sigs = {name: signature(name, cd) for name in CANDIDATE_MODELS}
        self.assertEqual(len(set(sigs.values())), len(sigs), sigs)
        # Documented outcomes, so a change in the models is caught:
        self.assertEqual(sigs["ABS_LBA"], (REJECT, REJECT, 155, REJECT))
        self.assertEqual(sigs["BASE_PLUS_BLOCK"], (REJECT, 155, 155, REJECT))
        self.assertEqual(sigs["TRACK_START_PLUS_BLOCK"], (155, 155, REJECT, 155))
        self.assertEqual(sigs["MSF_FRAMES_FROM_00_00_00"], (REJECT, REJECT, REJECT, REJECT))

    def test_index01_zero_control_cannot_separate_absolute_from_track_start(self):
        # Control: with INDEX 01 at LBA 0 the absolute and track-start candidates coincide,
        # which is why the discriminating fixture must have INDEX 01 != 0.
        cd = SyntheticCD(index01_lba=0)
        self.assertEqual(signature("ABS_LBA", cd), signature("TRACK_START_PLUS_BLOCK", cd))

    def test_pregap_frames_are_not_stored(self):
        cd = SyntheticCD(index01_lba=LEAD_IN)
        self.assertEqual(cd.read(LEAD_IN - 1), REJECT)
        self.assertEqual(cd.read(LEAD_IN), LEAD_IN)
        self.assertEqual(cd.read(LEAD_IN + 15), LEAD_IN + 15)
        self.assertEqual(cd.read(LEAD_IN + 16), REJECT)

    def test_candidate_arithmetic_rejects_instead_of_wrapping(self):
        cd = SyntheticCD(index01_lba=LEAD_IN)
        # base + block past 24 bits must be rejected, not wrapped to a low LBA.
        self.assertEqual(probe("BASE_PLUS_BLOCK", MAX_U24, 1, cd), REJECT)
        # under the MSF candidate, block 149 maps to LBA -1 (before the disc start) and is rejected.
        self.assertEqual(probe("MSF_FRAMES_FROM_00_00_00", LEAD_IN - 1, 0, cd), REJECT)


if __name__ == "__main__":
    unittest.main()
