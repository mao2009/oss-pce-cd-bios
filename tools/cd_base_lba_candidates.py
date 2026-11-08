#!/usr/bin/env python3
"""CD_BASE (Issues #11/#186) LBA coordinate candidate models and their discrimination.

HOST ARITHMETIC ONLY. This module does not run, emulate or describe the System Card CD_BASE /
CD_READ callee, executes no JSR and establishes no BIOS ABI. It is the measurement-preparation
half of Issue #186: the candidate block-number coordinate systems (docs/cd-base-abi-ledger.md
§4 for the first four; this module adds the INDEX 00 / PREGAP-inclusive and caller-fixup
families the issue requires) and a discriminator that proves which probe tuples separate them
on synthetic discs. Nothing here is evidence about the genuine BIOS: synthetic results and
genuine-callee observations are separate evidence classes (docs/research/186-cd-base-lba-coordinate.md §0).

A *model* is callable(block, base, disc) -> disc LBA, raising ValueError when the coordinate
is out of range (models reject, never wrap). ``disc`` provides the data track's ``index01``
(INDEX 01 LBA), ``index00`` (INDEX 00 / PREGAP-start LBA) and ``read(lba)`` -> payload int or
any non-int (unreadable). A self-naming fixture returns the sector's own LBA as payload, so a
readback names the sector it came from.

Equivalence limit (recorded, not hidden): on one disc, any two models with the same effective
offset are inseparable by READ LBA alone -- ABS_LBA == CALLER_FIXUP(0), TRACK_START ==
CALLER_FIXUP(index01), INDEX00_START == CALLER_FIXUP(index00), MSF_FRAMES == CALLER_FIXUP(-150),
BASE_PLUS_BLOCK(base=k) == CALLER_FIXUP(k). Whether a constant is added by the caller or the
callee is not separable by READ LBA on any disc; it needs the input-sensitivity stage (which
CD_BASE input bytes the callee reads, tools/cd_base_probe.py OBSERVATION_PLAN (1)) and
caller-source facts. The discriminator reports classes, never a unique verdict, and never
emits VERIFIED.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cd_base_model import LEAD_IN_FRAMES, block_to_lba, check_u24  # noqa: E402

# The five registry entries. BASE_PLUS_BLOCK's offset is the variable base input; the other four
# ignore it. The first four are the ledger §4 candidates (formulas restated from
# docs/cd-base-abi-ledger.md §4, not new claims); INDEX00_START_PLUS_BLOCK is the INDEX 00 /
# PREGAP-inclusive relative coordinate required by Issue #186.
ABS_LBA = "ABS_LBA"
BASE_PLUS_BLOCK = "BASE_PLUS_BLOCK"
TRACK_START_PLUS_BLOCK = "TRACK_START_PLUS_BLOCK"
INDEX00_START_PLUS_BLOCK = "INDEX00_START_PLUS_BLOCK"
MSF_FRAMES_FROM_00_00_00 = "MSF_FRAMES_FROM_00_00_00"
CANDIDATE_NAMES = (ABS_LBA, BASE_PLUS_BLOCK, TRACK_START_PLUS_BLOCK, INDEX00_START_PLUS_BLOCK,
                   MSF_FRAMES_FROM_00_00_00)


def abs_lba(block, base, disc):
    return block_to_lba(block, 0)


def base_plus_block(block, base, disc):
    return block_to_lba(block, base)


def track_start_plus_block(block, base, disc):
    return block_to_lba(block, disc.index01)


def index00_start_plus_block(block, base, disc):
    return block_to_lba(block, disc.index00)


def msf_frames_from_00_00_00(block, base, disc):
    if block < LEAD_IN_FRAMES:
        raise ValueError(f"block {block} is before MSF 00:02:00 under the frames-from-00:00:00 candidate")
    return check_u24(block - LEAD_IN_FRAMES)


def _require_int(value, name):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer, got {value!r}")
    return value


MODELS = {
    ABS_LBA: abs_lba,
    BASE_PLUS_BLOCK: base_plus_block,
    TRACK_START_PLUS_BLOCK: track_start_plus_block,
    INDEX00_START_PLUS_BLOCK: index00_start_plus_block,
    MSF_FRAMES_FROM_00_00_00: msf_frames_from_00_00_00,
}


def caller_fixup(delta):
    """Caller-side correction family: the value handed to CD_READ is block + delta, delta derived
    by the caller (TOC, ISO directory) before any CD_BASE call. One instance per hypothesised
    delta; instances with delta equal to a disc constant join that model's equivalence class."""
    def model(block, base, disc):
        return check_u24(block + delta)  # range-check the sum: delta may be negative (lead-in)
    return model


def evaluate(model, disc, block, base):
    """Predicted READ outcome for one probe: the payload int, or None when no readable READ
    (model rejects or the disc has no user data at the LBA). block/base must be ints (bools,
    floats and strings are rejected, as in tools/cd_base_model.py and tools/cd_base_probe.py)."""
    _require_int(block, "block")
    _require_int(base, "base")
    try:
        lba = model(block, base, disc)
    except ValueError:
        return None
    if isinstance(lba, bool) or not isinstance(lba, int):
        raise ValueError(f"model returned a non-int LBA: {lba!r}")
    payload = disc.read(lba)
    if isinstance(payload, bool) or not isinstance(payload, int):
        return None
    return payload


def signature(model, disc, probes):
    return tuple(evaluate(model, disc, block, base) for block, base in probes)


def _pairs(names):
    return [(a, b) for i, a in enumerate(names) for b in names[i + 1:]]


def separation(models, discs, probes):
    """Pairwise separation across labelled discs. ``discs`` is a list of (label, disc).

    Returns {"separated": {(a, b): {"disc", "probe", "outcomes"}}, "inseparable": [(a, b)]}.
    A pair is separated when one probe on one disc yields different READ outcomes (including
    readable vs unreadable). Inseparable pairs are equivalence classes on the given discs --
    the discriminator's limit, reported instead of hidden."""
    labels_discs = list(discs)
    separated, inseparable = {}, []
    for a, b in _pairs(sorted(models)):
        found = None
        for label, disc in labels_discs:
            sig_a, sig_b = signature(models[a], disc, probes), signature(models[b], disc, probes)
            for probe, out_a, out_b in zip(probes, sig_a, sig_b):
                if out_a != out_b:
                    found = {"disc": label, "probe": probe, "outcomes": (out_a, out_b)}
                    break
            if found:
                break
        if found:
            separated[(a, b)] = found
        else:
            inseparable.append((a, b))
    return {"separated": separated, "inseparable": inseparable}


def minimal_probe_set(models, discs, universe):
    """Greedy smallest subset of ``universe`` separating every separable pair on some disc.

    Returns (chosen, residual): ``chosen`` is the probe list, ``residual`` the pairs no probe
    in the universe separates (the equivalence classes). Deterministic: ties break by the
    universe order."""
    remaining = {(a, b) for a, b in _pairs(sorted(models))}
    chosen = []
    universe = list(universe)
    while remaining:
        best, best_gain = None, set()
        for probe in universe:
            gain = set()
            for label, disc in discs:
                sigs = {name: signature(fn, disc, [probe]) for name, fn in models.items()}
                for pair in remaining:
                    if sigs[pair[0]][0] != sigs[pair[1]][0]:
                        gain.add(pair)
            if len(gain) > len(best_gain):
                best, best_gain = probe, gain
        if best is None:
            break
        chosen.append(best)
        remaining -= best_gain
    return chosen, sorted(remaining)


def consistent(models, disc, observations):
    """Model names whose predictions match every (block, base, payload|None) observation.

    block/base must be ints and payload an int or None (bool/float/str observations are rejected
    with ValueError, as in tools/cd_base_probe.py's judge). Returns a sorted list -- an
    equivalence class, never a single name, when the observations cannot separate the class
    members. The caller must not read one element as a verdict."""
    for block, base, payload in observations:
        _require_int(block, "observation block")
        _require_int(base, "observation base")
        if payload is not None:
            _require_int(payload, "observation payload")
    out = []
    for name, fn in sorted(models.items()):
        if all(evaluate(fn, disc, block, base) == payload for block, base, payload in observations):
            out.append(name)
    return out


def detection_witness(models, discs, universe, true_name, wrong_name):
    """First (disc, probe, true_outcome, wrong_outcome) where a wrong-base implementation's
    predicted READ differs from the true model's; None when equivalent on every disc. This is
    the negative-case detector: every wrong base the issue requires must have a witness."""
    for label, disc in discs:
        for probe in universe:
            out_true = evaluate(models[true_name], disc, *probe)
            out_wrong = evaluate(models[wrong_name], disc, *probe)
            if out_true != out_wrong:
                return {"disc": label, "probe": probe, "true": out_true, "wrong": out_wrong}
    return None
