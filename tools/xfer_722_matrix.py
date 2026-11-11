#!/usr/bin/env python3
"""Issue #722: five-opcode transfer regression matrix (host-only, synthetic).

Reuses ``tools/peek_table.py`` from Issue #717 (PR #766) for every peek,
fetch and per-byte address-sequence computation.  Nothing here reimplements
the peek contract: ``is_peekable``, ``peek_byte``, ``fetch_t``,
``transfer_sequences`` and ``overlaps_instruction`` are imported, not copied.
Reads no emulator source, no BIOS and no ROM byte.

For one (op, src, dst, length, MPR, backing) the module answers the P07
regression question: which verdict does the pre-start check reach?

* ``REFUSED`` with ``reason == "len0_never_executed"`` for ``length == 0``
  (65536 bytes, computed exactly by ``peek.transfer_sequences`` but never
  executed on-core).
* ``FAIL_CLOSED`` when the 7-byte fetch is unpeekable, when any transfer
  source byte is unpeekable (unpredictable), or when any transfer byte
  resolves to an unmapped bank.
* ``REFUSED`` when a destination byte lands on the hardware page (``$FF``)
  or overwrites the 7-byte instruction itself (each byte step re-runs the
  opcode, so self-overwrite must be refused before the first step).
* ``UNKNOWN`` for rows the #367 evidence does not reconcile: a byte in a
  mapped bank outside ``$F8-$FB`` (other-bank), a TIA/TAI alternation that
  crosses a page boundary (pair phase unreconciled), a real mapper bank
  below ``$80``, or any device-DMA (W17) claim.
* ``WOULD_ALLOW_STATIC`` only when every source byte is peekable and every
  destination byte lands in mapped WRAM (``$F8-$FB``).

Verdicts are host-model judgments only (``REFUSED`` / ``WOULD_ALLOW_STATIC``
/ ``FAIL_CLOSED`` / ``UNKNOWN``).  They never claim real-core or hardware
behaviour, and ``gate`` is always ``BLOCKED``: a static range check does not
show the transfer cannot be disturbed mid-instruction, and no production
guard is wired (parent #473, Owner decision pending).
"""

try:
    from tools import peek_table as peek
except ImportError:  # run as ``python3 tools/xfer_722_matrix.py``
    import peek_table as peek

OPS = ("TII", "TIN", "TDD", "TIA", "TAI")
OP_INDEX = {"TII": 0, "TIN": 1, "TIA": 2, "TDD": 3, "TAI": 4}
LENGTHS = (1, 2, 3, 4, 8)
LENGTH0 = 0
GATE = "BLOCKED"

WRAM_BANKS = frozenset((0xF8, 0xF9, 0xFA, 0xFB))

MPR_WRAM = [0x00, 0xF8, 0xF8, 0xF8, 0x00, 0x00, 0x00, 0x00]
MPR_HW = [0x00, 0xF8, 0xFF, 0xF8, 0x00, 0x00, 0x00, 0x00]
MPR_ALIAS = [0x00, 0xF8, 0xF9, 0xF8, 0x00, 0x00, 0x00, 0x00]

UNKNOWN_DEVICE_DMA_W17 = "device-DMA (W17): no real-core device-write measurement"
UNKNOWN_OTHER_BANK = "other-bank: mapped bank outside $F8-$FB unreconciled with #367/#648 WRAM-only preflight"
UNKNOWN_ALTERNATION_XPAGE = "TIA/TAI alternation across a page boundary unreconciled"
UNKNOWN_MAPPER_BANK = "mapper bank below $80: synthetic setup installs no mapper (conservative fail-closed)"


def wram_backing(cells=None, unmapped=()):
    """8 KiB synthetic backing stores for WRAM banks plus a ROM zero page.

    Banks ``$F8``/``$F9`` are WRAM (writable), bank ``$00`` is ROM (peekable
    but never writable: an ``UNKNOWN`` other-bank row).  Every other bank is
    absent unless explicitly provided, so it fails closed.
    """
    wram = bytearray(0x2000)
    wram_alias = bytearray(0x2000)
    rom = bytearray([0xEA] * 0x2000)
    for addr, value in (cells or {}).items():
        bank = MPR_WRAM[(addr >> 13) & 7]
        if bank == 0xF8:
            wram[addr & 0x1FFF] = value
        elif bank == 0x00:
            rom[addr & 0x1FFF] = value
    backing = {0xF8: bytes(wram), 0xF9: bytes(wram_alias), 0x00: bytes(rom)}
    for bank in unmapped:
        backing.pop(bank, None)
    return backing


def _bank_of(addr, mpr):
    return mpr[(addr >> 13) & 7]


def verdict(op, src, dst, length, mpr, backing, pc=None):
    """Pre-start verdict for one transfer instruction (host model).

    ``pc`` is the instruction address: when given, the 7-byte fetch must
    succeed and no destination byte may overwrite ``pc .. pc+6``.  When
    ``None``, only the transfer ranges are judged (fetch NOT_RUN).
    ``length == 0`` is refused without expanding the 65536-byte walk here;
    callers use ``peek.transfer_sequences`` directly for the exact walk.
    """
    if op not in OPS:
        raise ValueError(f"unknown block op: {op!r}")
    count = length or peek.LENGTH0_BYTES
    if pc is not None:
        fetched = peek.fetch_t(pc & 0xFFFF, mpr, backing)
        if not fetched["ok"]:
            return {"op": op, "length": length, "count": count,
                    "verdict": "FAIL_CLOSED", "reason": "fetch_unpeekable",
                    "gate": GATE, "unknown": None}
    else:
        fetched = None
    if length == 0:
        return {"op": op, "length": 0, "count": count,
                "verdict": "REFUSED", "reason": "len0_never_executed",
                "gate": GATE, "unknown": None, "fetch": fetched}
    srcs, dsts = peek.transfer_sequences(op, src & 0xFFFF, dst & 0xFFFF, length)
    if pc is not None and peek.overlaps_instruction(pc & 0xFFFF, dsts):
        return {"op": op, "length": length, "count": count,
                "verdict": "REFUSED", "reason": "self_overwrite",
                "gate": GATE, "unknown": None, "fetch": fetched}
    for syn_index, (syn_s, syn_d) in enumerate(zip(srcs, dsts)):
        src_bank = _bank_of(syn_s, mpr)
        if not peek.is_peekable(src_bank,
                                has_mapping=backing.get(src_bank) is not None):
            if src_bank < peek.MAPPER_BANK_LIMIT and src_bank != peek.HARDWARE_BANK:
                unknown = UNKNOWN_MAPPER_BANK
            else:
                unknown = None
            return {"op": op, "length": length, "count": count,
                    "verdict": "FAIL_CLOSED", "reason": "src_unpeekable",
                    "byte": syn_index, "gate": GATE, "unknown": unknown,
                    "fetch": fetched}
        dst_bank = _bank_of(syn_d, mpr)
        if dst_bank == peek.HARDWARE_BANK:
            return {"op": op, "length": length, "count": count,
                    "verdict": "REFUSED", "reason": "dst_hardware_page",
                    "byte": syn_index, "gate": GATE, "unknown": None,
                    "fetch": fetched}
        if dst_bank not in WRAM_BANKS or backing.get(dst_bank) is None:
            if (peek.is_peekable(dst_bank,
                                 has_mapping=backing.get(dst_bank) is not None)
                    and dst_bank != peek.HARDWARE_BANK):
                unknown = UNKNOWN_OTHER_BANK
            elif dst_bank < peek.MAPPER_BANK_LIMIT:
                unknown = UNKNOWN_MAPPER_BANK
            else:
                unknown = None
            return {"op": op, "length": length, "count": count,
                    "verdict": "FAIL_CLOSED" if unknown is None else "UNKNOWN",
                    "reason": "dst_unwritable", "byte": syn_index,
                    "gate": GATE, "unknown": unknown, "fetch": fetched}
    if op in ("TIA", "TAI"):
        pages = {a >> 13 for a in (srcs + dsts)}
        if len(pages) > 1:
            return {"op": op, "length": length, "count": count,
                    "verdict": "UNKNOWN", "reason": "alternation_crosses_page",
                    "gate": GATE, "unknown": UNKNOWN_ALTERNATION_XPAGE,
                    "fetch": fetched}
    return {"op": op, "length": length, "count": count,
            "verdict": "WOULD_ALLOW_STATIC", "reason": "all_wram",
            "gate": GATE, "unknown": None, "fetch": fetched}


def case_rows():
    """Regression rows: 5 ops x lengths, len0, boundary/bank fixtures.

    Mirrors the pinned-core matrix fixtures
    (``fixture_operands`` in ``geargrafx_block_transfer_matrix.py``) at the
    host-model level: ``src=$3E80``/``dst=$3E90`` for the length matrix,
    page-boundary crossings, the TDD reverse walk, the MPR-alias pair, the
    hardware-page and unmapped refusals, and the TAM-page probes.
    """
    rows = []
    for op in OPS:
        for n in LENGTHS:
            src = 0x3E80
            dst = 0x3E90
            if op == "TDD":
                src += n - 1
                dst += n - 1
            rows.append({"case": f"{op.lower()}-len{n}", "op": op,
                         "src": src, "dst": dst, "length": n,
                         "mpr": list(MPR_WRAM), "expect": "WOULD_ALLOW_STATIC"})
    for op in OPS:
        rows.append({"case": f"{op.lower()}-len0", "op": op,
                     "src": 0x3E80, "dst": 0x3E90, "length": 0,
                     "mpr": list(MPR_WRAM), "expect": "REFUSED"})
    boundaries = [("1fff", 0x1FFF), ("3fff", 0x3FFF),
                  ("7fff", 0x7FFF), ("ffff", 0xFFFF)]
    for op in OPS:
        for name, dst in boundaries:
            rows.append({"case": f"{op.lower()}-boundary-{name}", "op": op,
                         "src": 0x3E80, "dst": dst, "length": 2,
                         "mpr": list(MPR_WRAM), "expect": None})
    rows.append({"case": "tdd-reverse-4000", "op": "TDD",
                 "src": 0x3E80, "dst": 0x4000, "length": 2,
                 "mpr": list(MPR_WRAM), "expect": None})
    rows.append({"case": "tii-boundary-alias", "op": "TII",
                 "src": 0x3E80, "dst": 0x3FFF, "length": 2,
                 "mpr": list(MPR_ALIAS), "expect": "WOULD_ALLOW_STATIC"})
    rows.append({"case": "tii-hwpage", "op": "TII",
                 "src": 0x3E80, "dst": 0x4000, "length": 2,
                 "mpr": list(MPR_HW), "expect": "REFUSED"})
    rows.append({"case": "tii-unmapped", "op": "TII",
                 "src": 0x3E80, "dst": 0x3E90, "length": 2,
                 "mpr": list(MPR_WRAM), "expect": "FAIL_CLOSED",
                 "unmapped": (0xF8,)})
    rows.append({"case": "tii-tam-alias", "op": "TII",
                 "src": 0x3E80, "dst": 0x4010, "length": 2,
                 "mpr": list(MPR_ALIAS), "expect": "WOULD_ALLOW_STATIC"})
    rows.append({"case": "tii-tam-deny", "op": "TII",
                 "src": 0x3E80, "dst": 0x4010, "length": 2,
                 "mpr": list(MPR_HW), "expect": "REFUSED"})
    return rows


def run_matrix():
    """Evaluate every regression row; returns (rows, mismatches)."""
    rows = case_rows()
    mismatches = []
    for row in rows:
        backing = wram_backing(unmapped=row.get("unmapped", ()))
        got = verdict(row["op"], row["src"], row["dst"], row["length"],
                      row["mpr"], backing)
        row["got"] = got["verdict"]
        row["reason"] = got["reason"]
        if row["expect"] is not None and got["verdict"] != row["expect"]:
            mismatches.append(row["case"])
    return rows, mismatches


if __name__ == "__main__":
    rows, mismatches = run_matrix()
    for row in rows:
        print(f"{row['case']:22s} {row['op']:3s} len={row['length']:5d} "
              f"src=${row['src']:04X} dst=${row['dst']:04X} "
              f"-> {row['got']} ({row['reason']})")
    print(f"rows={len(rows)} mismatches={mismatches} gate={GATE}")
    if mismatches:
        raise SystemExit(1)
