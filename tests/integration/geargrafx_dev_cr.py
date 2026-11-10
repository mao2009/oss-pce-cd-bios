"""Shared real-core harness for the DEV VDC CR display APIs (slot $2E EX_BGON;
the $2F-$33 family shares this discipline once the ROM layout allows it).

Each API preserves A/X/Y/P (C=0 on ok, C=1 on uninitialized-shadow reject),
balances the stack and rewrites only the BB/SB bits of VDC CR (R05) selected by
(set_mask, clear_mask), leaving every other CR bit untouched — via the work-RAM
shadow at $2502/$2503 (magic $A5), because the pinned core returns stale data
for CR port reads (MAJOR-1). This module builds the guest program and checks one
run; thin wrappers supply the slot, masks and evidence file name.

Guest program (reset-replacement code):
  1. SEI; CLD; MPR1 = work RAM ($F8); SP = case value; fill the $2300 sentinel.
  2. Seed the shadow ($2502 = pattern low or garbage, $2503 = $A5 or $00) and
     save the original MPR0, open the MPR0=$FF window and program VDC CR to the
     case pattern (low/high), then restore MPR0.
  3. Preset P (PHA/PLP, I=1 in every case), X, Y and A; JSR the slot entry; PHP
     and record A/P/X/Y/SP-after at $2200..$2205 (SEI right after PHP keeps the
     tail deterministic even if a pattern ever enabled a VDC interrupt source).
  4. Re-open the window, read back CR low/high to $2206/$2207 region and restore
     MPR0. The original MPR0 is kept at $2208 for the MPR0-restored check.
The authoritative oracle is the probe's independent vdc_cr observation
(GetState()->R[5]); the guest port readback is only a secondary signal.
Test patterns never set CR interrupt-enable bits (0-3), so no IRQ can fire.

Requires the pinned cc65 and Geargrafx core (TOOLS_DIR). Missing tools exit 3.
"""

SHADOW_LO = 0x2502        # CR-low image (this DEV family is its single writer)
SHADOW_MAGIC = 0x2503     # $A5 iff the shadow is initialized
SHADOW_HI_RSVD = 0x2504   # reserved: CR-high image for the future $2F-$33 family
SHADOW_VALID = 0xA5
STORE = 0x2200            # A/P/X/Y/SP-after at STORE..STORE+4, CR low/high at +5/+6
STORE_CR_LO = STORE + 5
STORE_CR_HI = STORE + 6
STORE_MPR0 = STORE + 7    # original MPR0 saved here (8 bytes total, inside the 64-byte window)
SENTINEL = 0x2300         # 16-byte RAM sentinel, must be untouched
SENTINEL_LEN = 16
RUN_STEPS = 1024          # probe cap is 2048; the guest is ~130 instructions
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


def guest(reset, entry, cr_low, cr_high, a, x, y, p, sp, seed_shadow=True):
    """Reset-replacement code. seed_shadow=False leaves garbage ($FF) with a zero
    magic to exercise the uninitialized-shadow reject path. Returns (code, halt)."""
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
    # Seed the CR shadow (work RAM, MPR1=$F8 already maps it).
    shadow_lo = cr_low if seed_shadow else 0xFF
    shadow_magic = SHADOW_VALID if seed_shadow else 0x00
    code += bytes([0xA9, shadow_lo, 0x8D, 0x02, 0x25,   # LDA #lo; STA $2502
                   0xA9, shadow_magic, 0x8D, 0x03, 0x25])  # LDA #magic; STA $2503
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


def check(label, rows, halt, a, x, y, p, sp, cr_low, cr_high, set_mask, clear_mask,
          seed_shadow=True):
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
    # The LDA #a after PLP refreshes N/Z from A: fold that into the expectation.
    # (B/T are masked out: PHP always pushes B=1 and the pinned core pushes T=0.)
    # C is the result bit (SETWRT convention): C=0 on ok, C=1 on reject.
    want_p = (p & 0x7C) | (0x80 if a & 0x80 else 0) | (0x02 if a == 0 else 0)
    if not seed_shadow:
        want_p |= 0x01
    got = (at(STORE), at(STORE + 1) & P_MASK, at(STORE + 2), at(STORE + 3))
    want = (a, want_p & P_MASK, x, y)
    if got != want:
        raise ValueError(f"{label}: A/P/X/Y observed {[f'${v:02X}' for v in got]}, "
                         f"expected {[f'${v:02X}' for v in want]}")
    if at(STORE + 4) != sp or cold["sp"] != sp:
        raise ValueError(f"{label}: stack not balanced (SP after ${at(STORE + 4):02X}, halt ${cold['sp']:02X}, "
                         f"want ${sp:02X})")
    want_lo = (cr_low & ~clear_mask) | set_mask
    want_shadow_lo = want_lo if seed_shadow else 0xFF
    want_magic = SHADOW_VALID if seed_shadow else 0x00
    if at(SHADOW_LO) != want_shadow_lo or at(SHADOW_MAGIC) != want_magic:
        raise ValueError(f"{label}: shadow ${at(SHADOW_MAGIC):02X}{at(SHADOW_LO):02X}, "
                         f"want ${want_magic:02X}{want_shadow_lo:02X}")
    # Authoritative oracle: the VDC's actual CR state from the probe (GetState()->R[5]),
    # independent of the guest's $0002/$0003 port readback (MAJOR-2). The readback is
    # kept only as a secondary consistency signal: it must agree with the hardware.
    if "vdc_cr" not in cold or "vdc_ar" not in cold:
        raise ValueError(f"{label}: probe observation lacks vdc_cr/vdc_ar (probe too old?)")
    if cold["vdc_ar"] != 5:
        raise ValueError(f"{label}: VDC AR ${cold['vdc_ar']:02X}, want $05 (contract: AR left at CR)")
    want_vdc = (cr_high << 8) | (want_lo if seed_shadow else cr_low)
    if cold["vdc_cr"] != want_vdc:
        raise ValueError(f"{label}: VDC CR ${cold['vdc_cr']:04X}, "
                         f"want ${want_vdc:04X} (independent hardware oracle)")
    if at(STORE_CR_LO) != (want_lo if seed_shadow else cr_low) or at(STORE_CR_HI) != cr_high:
        raise ValueError(f"{label}: CR readback ${at(STORE_CR_HI):02X}{at(STORE_CR_LO):02X}, "
                         f"want ${cr_high:02X}{(want_lo if seed_shadow else cr_low):02X}")
    sentinel = bytes(wram[SENTINEL - WRAM_BASE:SENTINEL - WRAM_BASE + SENTINEL_LEN])
    want_sentinel = bytes(sentinel_byte(i) for i in range(SENTINEL_LEN))
    if sentinel != want_sentinel:
        raise ValueError(f"{label}: RAM sentinel ${SENTINEL:04X} changed")
    return {"a": a, "x": x, "y": y, "p": f"{p:02X}", "sp": f"{sp:02X}",
            "cr": f"{want_vdc:04X}"}
