; MA_MUL8S — slot $40, candidate entry $E0C0. DEV ROM only (Issue #461).
;
; Project-own routine, NOT the reference BIOS MA_MUL8S ABI (remains UNVERIFIED). Same algorithm as
; src/math/ma_mul8s.s (contract in docs/ma-mul8s.md); the DEV override only changes the segment
; (OVERRIDES instead of MATHLIB), because the DEV ROM has no MATHLIB segment. Stack only: no zero page, no BSS.
;   in : A = multiplicand, X = multiplier (two's complement, -128..127)
;   out: X:A = signed 16-bit product (X high, A low); no overflow case
;   kept: Y. P is restored by PLP from the value PHP pushed at entry (C,Z,I,D,V,N as the caller left them). The
;        pushed value always has T=0 (PHP runs after the core cleared P.T) and B=1 (PHP pushes B set); PLP stores the
;        value without B, so the restored P has T=0 and B is not a stored flag. The test compares every bit except B
;        and checks that T is 0 on return; D is cleared inside for binary ADC; I is never changed.
;   precondition: S at routine entry >= $05 (5 bytes pushed by the routine, no wrap of the stack page);
;        MPR1 maps the stack page (RAM, as for any JSR). The real-core test checks the boundary at S=$05.
;   precondition: caller T (P bit 5) must be 0 at entry. T=1 is OUT OF CONTRACT: this routine defines no behaviour
;        for it. The HuC6280 T flag selects the memory-to-memory mode of ADC/AND/EOR/ORA: with T=1 the operand is
;        combined with the zero-page byte addressed by X and the result is written back there, so the routine's ADC
;        (adc $2103,x) would not compute into A at all and would corrupt the zero page. On the pinned core
;        (Geargrafx b49ae82) the T flag is consumed by the instruction that follows SET: RunInstruction latches P.T
;        into the memory-to-memory flag and clears P.T at the start of every instruction, and PHP always pushes T=0.
;        A caller's SET before the JSR is therefore swallowed by the JSR, the routine is entered with P.T=0, and its
;        own first instruction is PHP, which no T setting changes. So the routine's ADC always runs in register mode
;        on that core. The premise is kept explicit so that the multiply does not depend on the core's T semantics.
;        No test runs the routine with T=1.
; ABI remains UNVERIFIED and this ROM stays DEV ONLY (release_eligible: false).
.setcpu "huc6280"
.segment "OVERRIDES"
.export api_slot_40
api_slot_40:
    php
    cld                  ; binary ADC/SBC regardless of the caller's D
    phy
    pha
    phx                  ; multiplier (kept for the sign correction)
    phx                  ; multiplier work byte, becomes the product low byte
    tsx                  ; $2101,x work  $2102,x b  $2103,x a  $2104,x Y
    lda #0
    ldy #8
    lsr $2101,x
@loop:                   ; unsigned shift-add: A = high, work = low
    bcc @skip
    clc
    adc $2103,x
@skip:
    ror a
    ror $2101,x
    dey
    bne @loop
    ldy $2103,x          ; signed = unsigned - (a<0 ? b<<8) - (b<0 ? a<<8)
    bpl @a_pos
    sec
    sbc $2102,x
@a_pos:
    ldy $2102,x
    bpl @b_pos
    sec
    sbc $2103,x
@b_pos:
    sta $2102,x          ; high byte replaces b for the pull sequence
    pla                  ; A = low
    plx                  ; X = high
    ply                  ; drop a
    ply                  ; caller Y
    plp                  ; caller P
    rts
