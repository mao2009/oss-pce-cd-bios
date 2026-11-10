"""Shared real-core harness for the six DEV VDC CR display APIs (slots $2E-$33).

Each API preserves A/X/Y/P (C=0), balances the stack and rewrites only the BB/SB
bits of VDC CR (R05) selected by (set_mask, clear_mask), leaving every other CR
bit untouched. This module builds the guest program and checks one run; the six
thin wrappers (geargrafx_dev_{bgon,bgoff,spron,sproff,dspon,dspoff}.py) supply the
slot, masks and evidence file name.

Guest program (reset-replacement code):
  1. SEI; CLD; MPR1 = work RAM ($F8); SP = case value; fill the $2300 sentinel.
  2. Save the original MPR0, open the MPR0=$FF window and program VDC CR to the
     case pattern (low/high), then restore MPR0.
  3. Preset P (PLP, I=1 in every case), X, Y, A; JSR the slot entry; PHP and
     record A/P/X/Y/SP-after at $2200..$2205 (SEI right after PHP keeps the tail
     deterministic even if a pattern ever enabled a VDC interrupt source).
  4. Re-open the window, read back CR low/high to $2206/$2207 region and restore
     MPR0. The original MPR0 is kept at $2208 for the MPR0-restored check.
Test patterns never set CR interrupt-enable bits (0-3), so no IRQ can fire.

Requires the pinned cc65 and Geargrafx core (TOOLS_DIR). Missing tools exit 3.
"""

STORE = 0x2200            # A/P/X/Y/SP-after at STORE..STORE+4, CR low/high at +5/+6
STORE_CR_LO = STORE + 5
STORE_CR_HI = STORE + 6
STORE_MPR0 = STORE + 7    # original MPR0 saved here (8 bytes total, inside the 64-byte window)
SENTINEL = 0x2300         # 16-byte RAM sentinel, must be untouched
SENTINEL_LEN = 16
RUN_STEPS = 4096          # probe maximum (CR setup/readback roughly doubles the guest)
WRAM_BASE = 0x2000        # full work-RAM dump (Issue #242) index 0 = CPU $2000
P_MASK = 0xCF             # PHP sets B (bit 4) and bit 5; PLP ignores them


def sentinel_byte(i):
    return i ^ 0xA5


def window_open():
    """MPR0=$FF window with IRQs masked. Clobbers A. Caller must have saved MPR0."""
    return bytes([0x78,                   # SEI
                  0xA9, 0xFF, 0x53, 0x01])  # LDA #$FF; TAM #$01


def window_close(store_addr):
    """Restore the MPR0 saved at store_addr. Clobbers A."""
    return bytes([0xAD, store_addr & 0xFF, store_addr >> 8,  # LDA store_addr
                  0x53, 0x01])                              # TAM #$01


def guest(reset, entry, cr_low, cr_high, a, x, y, p, sp):
    """Reset-replacement code. Returns (code, halt)."""
    code = bytearray([0x78, 0xD8,                # SEI; CLD
                      0xA9, 0xF8, 0x53, 0x02,    # LDA #$F8; TAM #$02 -> MPR1 = work RAM
                      0xA2, sp, 0x9A])           # LDX #sp; TXS
    code += bytes([0xA2, 0x00,                   # LDX #0 (sentinel fill loop)
                   0x8A, 0x49, 0xA5,             # TXA; EOR #$A5
                   0x9D, SENTINEL & 0xFF, SENTINEL >> 8,  # STA SENTINEL,X
                   0xE8,                         # INX
                   0xE0, SENTINEL_LEN,           # CPX #len
                   0xD0, 0xF5])                  # BNE fill (-11)
    # Save original MPR0 to STORE_MPR0. TMA #$01 is opcode $43 (TAM #$01 is $53).
    code += bytes([0x43, 0x01,                   # TMA #$01 (A = caller MPR0)
                   0x8D, STORE_MPR0 & 0xFF, STORE_MPR0 >> 8])  # STA STORE_MPR0
    # Program the CR pattern through the window.
    code += window_open()
    code += bytes([0xA9, 0x05, 0x8D, 0x00, 0x00,  # LDA #5; STA $0000 (select CR)
                   0xA9, cr_low, 0x8D, 0x02, 0x00,  # LDA #lo; STA $0002
                   0xA9, cr_high, 0x8D, 0x03, 0x00])  # LDA #hi; STA $0003
    code += window_close(STORE_MPR0)
    # Preset regs and call the slot.
    code += bytes([0xA9, p, 0x48, 0x28,             # LDA #p; PHA; PLP (I=1/D=0 in every case)
                   0xA2, x, 0xA0, y,             # LDX #x; LDY #y
                   0xA9, a,                      # LDA #a
                   0x20, entry & 0xFF, entry >> 8,  # JSR entry
                   0x08, 0x78,                   # PHP; SEI (record flags, then lock)
                   0x8D, STORE & 0xFF, STORE >> 8,  # STA STORE (A)
                   0x68,                         # PLA
                   0x8D, (STORE + 1) & 0xFF, (STORE + 1) >> 8,  # STA STORE+1 (P)
                   0x8E, (STORE + 2) & 0xFF, (STORE + 2) >> 8,  # STX STORE+2 (X)
                   0x8C, (STORE + 3) & 0xFF, (STORE + 3) >> 8,  # STY STORE+3 (Y)
                   0xBA,                         # TSX
                   0x8E, (STORE + 4) & 0xFF, (STORE + 4) >> 8])  # STX STORE+4 (SP)
    # Read back CR through the window.
    code += window_open()
    code += bytes([0xA9, 0x05, 0x8D, 0x00, 0x00,  # LDA #5; STA $0000 (select CR)
                   0xAD, 0x02, 0x00,             # LDA $0002 (CR low)
                   0x8D, STORE_CR_LO & 0xFF, STORE_CR_LO >> 8,
                   0xAD, 0x03, 0x00,             # LDA $0003 (CR high)
                   0x8D, STORE_CR_HI & 0xFF, STORE_CR_HI >> 8])
    code += window_close(STORE_MPR0)
    halt = reset + len(code)
    code += bytes([0x80, 0xFE])                  # BRA -2
    if reset + len(code) > 0xFFF6:
        raise ValueError("BLOCKED: guest code does not fit before the vectors")
    return bytes(code), halt


def check(label, rows, halt, a, x, y, p, sp, cr_low, cr_high, set_mask, clear_mask):
    cold = next(r for r in rows if r["phase"] == "cold")
    if cold["pc"] != halt:
        raise ValueError(f"{label}: guest did not reach its halt loop (pc ${cold['pc']:04X}, want ${halt:04X})")
    if cold["mpr"][1] != 0xF8 or cold["mpr"][7] != 0:
        raise ValueError(f"{label}: MPR changed (mpr={cold['mpr']})")
    wram = bytes.fromhex(cold["work_ram_hex"])
    if len(wram) < 0x2000:
        raise ValueError(f"{label}: full work-RAM dump too short ({len(wram)} bytes)")

    def at(addr):
        return wram[addr - WRAM_BASE]

    if cold["mpr"][0] != at(STORE_MPR0):
        raise ValueError(f"{label}: MPR0 not restored (MPR0 ${cold['mpr'][0]:02X}, saved ${at(STORE_MPR0):02X})")
    got = (at(STORE), at(STORE + 1) & P_MASK, at(STORE + 2), at(STORE + 3))
    want = (a, p & P_MASK, x, y)
    if got != want:
        raise ValueError(f"{label}: A/P/X/Y observed {[f'${v:02X}' for v in got]}, "
                         f"expected {[f'${v:02X}' for v in want]}")
    if at(STORE + 4) != sp or cold["sp"] != sp:
        raise ValueError(f"{label}: stack not balanced (SP after ${at(STORE + 4):02X}, halt ${cold['sp']:02X}, "
                         f"want ${sp:02X})")
    want_lo = (cr_low & ~clear_mask) | set_mask
    if at(STORE_CR_LO) != want_lo or at(STORE_CR_HI) != cr_high:
        raise ValueError(f"{label}: CR ${at(STORE_CR_HI):02X}{at(STORE_CR_LO):02X}, "
                         f"want ${cr_high:02X}{want_lo:02X}")
    sentinel = bytes(wram[SENTINEL - WRAM_BASE:SENTINEL - WRAM_BASE + SENTINEL_LEN])
    want_sentinel = bytes(sentinel_byte(i) for i in range(SENTINEL_LEN))
    if sentinel != want_sentinel:
        raise ValueError(f"{label}: RAM sentinel ${SENTINEL:04X} changed")
    return {"a": a, "x": x, "y": y, "p": f"{p:02X}", "sp": f"{sp:02X}",
            "cr": f"{cr_high:02X}{want_lo:02X}"}
