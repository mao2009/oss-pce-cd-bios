"""Issue #298: synthetic 1-sector CD->ADPCM RAM write model (host only, non-executing).

Not evidence of the AD_TRANS ($11, $E033) callee. No destination mode, count rule, error code,
register or side effect of the System Card is assumed; the model's rules are its own and are
labelled HYPOTHESIS/UNKNOWN in docs/research/ad-trans-contract.md. ADPCM RAM size is a model
parameter, not a measured value. Release-eligible: False.
"""
import hashlib

SECTOR = 2048
ADPCM_RAM = 0x10000  # model parameter (HYPOTHESIS), not measured
HEADER = b'OSSPCE-AD-SYNTH-V1\0'
RELEASE_ELIGIBLE = False
CALLEE_ABI = 'UNKNOWN'


def sector_bytes(lba):
    """Deterministic synthetic 2048-byte user sector for LBA (original, no third-party data)."""
    out = bytearray()
    counter = 0
    while len(out) < SECTOR:
        out += hashlib.sha256(HEADER + lba.to_bytes(4, 'big') + counter.to_bytes(4, 'big')).digest()
        counter += 1
    return bytes(out[:SECTOR])


def validate(req, disc_sectors):
    """Model rules, checked in fixed order. Returns a rejection reason, or None when valid."""
    if not req['media']:
        return 'REJECT_NO_MEDIA'
    if req['busy']:
        return 'REJECT_BUSY'
    # Exact int only: bool is a subclass of int, and float/str must not reach the range checks or slicing.
    if any(type(req[k]) is not int for k in ('count', 'lba', 'dest')):
        return 'REJECT_TYPE'
    if req['count'] != 1:
        return 'REJECT_COUNT'
    if not 0 <= req['lba'] < disc_sectors:
        return 'REJECT_LBA'
    if not 0 <= req['dest'] <= ADPCM_RAM - SECTOR:
        return 'REJECT_DEST'
    return None


def model_transfer(req, disc_sectors, ram):
    """Apply the model to ram (bytearray). Returns 'OK' or the rejection reason; mutates ram only on OK."""
    reason = validate(req, disc_sectors)
    if reason is None:
        ram[req['dest']:req['dest'] + SECTOR] = sector_bytes(req['lba'])
        return 'OK'
    return reason


def judge(req, disc_sectors, outcome, before, after):
    """PASS or FAIL. The validity is recomputed here, so a reported outcome cannot override it."""
    expected = validate(req, disc_sectors)
    if expected is not None:
        return 'PASS' if outcome == expected and before == after else 'FAIL'
    if outcome != 'OK' or len(after) != len(before):
        return 'FAIL'
    lo, hi = req['dest'], req['dest'] + SECTOR
    if after[lo:hi] != sector_bytes(req['lba']):
        return 'FAIL'
    return 'PASS' if after[:lo] == before[:lo] and after[hi:] == before[hi:] else 'FAIL'
