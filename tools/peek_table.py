#!/usr/bin/env python3
"""Side-effect-free peek model for Issue #717 (child of #473, plan item P02).

Host-only, synthetic inputs. Reads no emulator source text, no BIOS and no ROM
byte. It restates the *observable contract* of the pinned Geargrafx
``Memory::TryPeek`` (revision ``b49ae82``) as recorded in
``docs/717-peek-failclosed.md`` section 2, so host tests can judge the
fetch-order and fail-closed rules without running the core:

* ``bank == 0xFF`` (hardware page) is never peekable: the call fails without
  touching the device (no VDC status clear, no VRAM queue, no ``io_buffer``
  update, no cycle injection).
* Banks below ``0x80`` need a mapper-backed mapping, otherwise they fail
  closed. The synthetic fixtures in this repository never install such a
  mapper, so the host model treats every ``bank < 0x80`` as unpeekable
  (conservative: a real mapper case would need its own measurement).
* Any other bank needs a present backing store, otherwise it fails closed.
* A successful peek is a direct backing-store read: no memory hooks, no
  device-register read, no state change.

``fetch_d`` (TAM ``$53`` + 1 operand byte) and ``fetch_t`` (block opcode +
6 operand bytes) both fetch the opcode first at ``pc`` and then the operand
bytes in increasing address order. If any byte is unpeekable the whole fetch
fails closed with no partial use: the caller must refuse the instruction
without stepping the core.

Transfer address sequences follow the pinned-core matrix model
(``tests/integration/geargrafx_block_transfer_matrix.py::addr_lists``):
TII increments both, TDD decrements both, TIN fixes the destination, TIA
alternates the destination, TAI alternates the source. ``length == 0`` means
65536 bytes and is never executed on-core; the host model can compute the
sequence but callers must refuse it before any step.

Verdicts here are host-model judgments only (``REFUSED`` / ``WOULD_ALLOW`` /
``FAIL_CLOSED``). They never claim real-core or hardware behaviour. Real-core
evidence is recorded separately in ``docs/717-peek-failclosed.md``.
"""

BLOCK_OPCODES = {0x73: "TII", 0xC3: "TDD", 0xD3: "TIN", 0xE3: "TIA", 0xF3: "TAI"}
TAM_OPCODE = 0x53
HARDWARE_BANK = 0xFF
MAPPER_BANK_LIMIT = 0x80
LENGTH0_BYTES = 0x10000


def is_peekable(bank, has_mapping=True):
    """Whether ``TryPeek`` can return a byte for ``bank`` in the synthetic setup.

    ``bank`` is the MPR-resolved 8-bit bank. ``has_mapping`` is whether the
    core has a backing store for that bank (``m_memory_map[bank]`` present).
    Mapper-backed banks below ``0x80`` are treated as unpeekable here because
    the synthetic fixtures install no mapper (conservative fail-closed).
    """
    if type(bank) is not int or not 0 <= bank <= 0xFF:
        raise ValueError(f"bank must be a byte: {bank!r}")
    if bank == HARDWARE_BANK:
        return False
    if bank < MAPPER_BANK_LIMIT:
        return False
    return bool(has_mapping)


def peek_byte(address, mpr, backing):
    """Peek one byte through the MPR mapping, or return ``(False, None)``.

    ``mpr`` is the 8-entry MPR list, ``backing`` maps bank -> 8 KiB bytes-like
    (or ``None`` for an unmapped bank). The address is a 16-bit CPU address.
    A ``False`` first element means fail-closed: no device was touched and the
    caller must not use the value.
    """
    if not isinstance(mpr, (list, tuple)) or len(mpr) != 8:
        raise ValueError(f"mpr must be an 8-entry list: {mpr!r}")
    if not isinstance(address, int) or not 0 <= address <= 0xFFFF:
        raise ValueError(f"address must be 0..0xFFFF: {address!r}")
    bank = mpr[(address >> 13) & 7]
    if not is_peekable(bank, has_mapping=backing.get(bank) is not None):
        return False, None
    offset = address & 0x1FFF
    store = backing[bank]
    if offset >= len(store):
        return False, None
    value = store[offset]
    if not isinstance(value, int) or not 0 <= value <= 0xFF:
        raise ValueError(f"backing store holds a non-byte at bank ${bank:02X}+${offset:04X}")
    return True, value


def fetch_d(pc, mpr, backing):
    """Fetch a TAM (opcode ``$53`` + 1 operand byte) without side effects.

    Returns ``{"ok": True, "opcode": 0x53, "operand": bits}`` on success, or
    ``{"ok": False, "reason": "fail_closed"}`` when either byte is unpeekable
    or the opcode is not TAM. Fetch order is opcode at ``pc`` first, then the
    operand at ``pc + 1``; a failed second byte still fails the whole fetch.
    """
    ok, op = peek_byte(pc & 0xFFFF, mpr, backing)
    if not ok:
        return {"ok": False, "reason": "fail_closed"}
    if op != TAM_OPCODE:
        return {"ok": False, "reason": "not_tam"}
    ok2, bits = peek_byte((pc + 1) & 0xFFFF, mpr, backing)
    if not ok2:
        return {"ok": False, "reason": "fail_closed"}
    return {"ok": True, "opcode": op, "operand": bits}


def fetch_t(pc, mpr, backing):
    """Fetch a block transfer (opcode + 6 operand bytes) without side effects.

    Returns ``{"ok": True, "opcode": op, "name": name, "src": s, "dst": d,
    "length": n, "raw": [...]}`` on success, or ``{"ok": False, "reason":
    ...}`` with ``fail_closed`` when any of the 7 bytes is unpeekable and
    ``not_block`` when the opcode is not a block opcode. The 7 bytes are
    fetched in order ``pc`` .. ``pc + 6``; a later unpeekable byte fails the
    whole fetch even though the earlier bytes were readable.
    """
    raw = []
    for i in range(7):
        ok, byte = peek_byte((pc + i) & 0xFFFF, mpr, backing)
        if not ok:
            return {"ok": False, "reason": "fail_closed", "raw": list(raw)}
        raw.append(byte)
    op = raw[0]
    if op not in BLOCK_OPCODES:
        return {"ok": False, "reason": "not_block", "raw": list(raw)}
    src = raw[1] | (raw[2] << 8)
    dst = raw[3] | (raw[4] << 8)
    length = raw[5] | (raw[6] << 8)
    return {"ok": True, "opcode": op, "name": BLOCK_OPCODES[op],
            "src": src, "dst": dst, "length": length, "raw": list(raw)}


def transfer_sequences(op_name, src, dst, length):
    """Exact per-byte source/destination lists for one transfer instruction.

    ``op_name`` is one of ``TII/TIN/TDD/TIA/TAI``. Follows the pinned-core
    matrix model (``addr_lists`` in
    ``tests/integration/geargrafx_block_transfer_matrix.py``): TII increments
    both, TDD decrements both, TIN fixes the destination, TIA alternates the
    destination ``dst, dst+1, ...``, TAI alternates the source ``src, src+1,
    ...`` while incrementing the destination. ``length == 0`` expands to
    65536 entries; callers must refuse that size before any step (it is never
    executed on-core).
    """
    if op_name not in ("TII", "TIN", "TDD", "TIA", "TAI"):
        raise ValueError(f"unknown block op: {op_name!r}")
    count = length or LENGTH0_BYTES
    if not 1 <= count <= LENGTH0_BYTES:
        raise ValueError(f"length out of range: {length!r}")
    # Mirror addr_lists exactly: emit the current (s, d) then advance.
    op_index = {"TII": 0, "TIN": 1, "TIA": 2, "TDD": 3, "TAI": 4}[op_name]
    srcs, dsts = [], []
    s, d = src & 0xFFFF, dst & 0xFFFF
    for i in range(count):
        srcs.append(s & 0xFFFF)
        dsts.append(d & 0xFFFF)
        if op_index in (0, 1, 2):
            s += 1
        elif op_index == 3:
            s -= 1
        else:
            s += -1 if (i & 1) else 1
        if op_index in (0, 4):
            d += 1
        elif op_index == 1:
            pass
        elif op_index == 2:
            d += -1 if (i & 1) else 1
        else:
            d -= 1
    return srcs, dsts


def overlaps_instruction(pc, dsts):
    """Whether any destination byte overwrites the 7-byte instruction at ``pc``.

    Each transfer byte step re-runs the opcode, so a self-overwrite must be
    refused before the first step. The instruction occupies ``pc`` .. ``pc+6``
    with 16-bit wrap.
    """
    owned = {(pc + i) & 0xFFFF for i in range(7)}
    return any((d & 0xFFFF) in owned for d in dsts)
