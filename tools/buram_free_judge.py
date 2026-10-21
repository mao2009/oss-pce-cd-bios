"""Issue #306: HOST MODEL of a BM_FREE free-space judge on a SYNTHETIC backup-RAM image. Pure stdlib.

This is NOT a BIOS routine and NOT the System Card backup-RAM format. The layout below is invented for this model
so the judge's branches (free count, unformatted, invalid header, capacity boundary) can be exercised on the host.
The BM_FREE call ABI is UNKNOWN (spec/api_contracts.json, slot 25), so the BM_FREE API stays BLOCKED_ABI and
release_eligible=false. The module does no file I/O at all: it only reads the bytes it is handed, so it cannot
touch a real user save.

Synthetic layout (SIZE bytes, BLOCK-byte blocks, block 0 is the header block):
  [0:4]   magic b"SBR1"
  [4]     version 1
  [5:15]  reserved (zero in a formatted image), covered by the checksum
  [15]    checksum = (sum(bytes[0:15]) + sum(table)) & 0xFF
  [16:31] allocation table, one byte per data block 1..15: 0x00 free, 0x01 used (table is checksummed)
  blocks 1..15 data, never inspected by the judge. Capacity 15 * 128 = 1920 bytes.

  python3 tools/buram_free_judge.py   # runs the boundary and mutation self-check on synthetic images
"""
SIZE = 2048                      # synthetic image size (2 KiB is only the docs/system-card-rom.md init note)
BLOCK = 128
NDATA = SIZE // BLOCK - 1        # data blocks after the header block
MAGIC = b"SBR1"
VERSION = 1
TABLE = 16
FREE, USED = 0x00, 0x01
ERASED = b"\xff" * SIZE
BLANK = bytes(SIZE)

OK, UNFORMATTED, INVALID_HEADER, BAD_SIZE = "OK", "UNFORMATTED", "INVALID_HEADER", "BAD_SIZE"


def _checksum(data):
    return (sum(data[0:15]) + sum(data[TABLE:TABLE + NDATA])) & 0xFF


def make_image(used=()):
    """Synthetic formatted image; ``used`` holds 0-based data-block indices (0..NDATA-1) marked USED."""
    used = set(used)
    if not used <= set(range(NDATA)):
        raise ValueError(f"block index out of range 0..{NDATA - 1}: {sorted(used)}")
    data = bytearray(SIZE)
    data[0:4] = MAGIC
    data[4] = VERSION
    for i in range(NDATA):
        data[TABLE + i] = USED if i in used else FREE
    data[15] = _checksum(data)
    return bytes(data)


def judge_free(image):
    """Return (verdict, free_blocks, reason). free_blocks is None unless verdict is OK.

    Read-only: the input is snapshotted to bytes and never written. Only a formatted image with a valid header and
    checksum and a clean allocation table yields OK; everything else is rejected, never counted as free space.
    """
    if not isinstance(image, (bytes, bytearray)):
        raise TypeError("image must be bytes or bytearray")
    data = bytes(image)
    if len(data) != SIZE:
        return BAD_SIZE, None, f"{len(data)} bytes, expected {SIZE}"
    if data[0:4] != MAGIC:
        if data in (ERASED, BLANK):
            return UNFORMATTED, None, "no magic; erased or blank image"
        return INVALID_HEADER, None, "magic mismatch"
    if data[4] != VERSION:
        return INVALID_HEADER, None, f"version {data[4]}, expected {VERSION}"
    if any(data[5:15]):
        return INVALID_HEADER, None, "reserved bytes [5:15] not zero"
    if data[15] != _checksum(data):
        return INVALID_HEADER, None, "checksum mismatch"
    table = data[TABLE:TABLE + NDATA]
    if any(b not in (FREE, USED) for b in table):
        return INVALID_HEADER, None, "allocation table holds a value other than 0x00/0x01"
    free = table.count(FREE)
    return OK, free, f"formatted: {free} free of {NDATA} blocks ({free * BLOCK} of {NDATA * BLOCK} bytes)"


def mutation_self_check(base):
    """Flip every bit of every checksummed byte of a formatted image. None may come back OK."""
    checked = 0
    for index in range(TABLE + NDATA):   # header 0..15 and table 16..30; byte 31 is outside the model
        for bit in range(8):
            broken = bytearray(base)
            broken[index] ^= 1 << bit
            verdict, _, reason = judge_free(bytes(broken))
            if verdict == OK:
                raise ValueError(f"bit flip at byte {index} bit {bit} accepted as OK ({reason})")
            checked += 1
    return checked


def main():
    fresh = make_image()
    verdict, free, reason = judge_free(fresh)
    if verdict != OK or free != NDATA:
        raise ValueError(f"fresh image not OK with {NDATA} free: {verdict} {reason}")
    mutations = mutation_self_check(fresh)
    print(f"HOST MODEL (not a BIOS routine): fresh OK, {free} free; {mutations} bit flips rejected")
    print(f"ERASED {judge_free(ERASED)[0]}, BLANK {judge_free(BLANK)[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
