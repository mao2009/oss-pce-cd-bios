; MA_MUL8S — slot $40, candidate entry $E0C0. DEV ROM only (Issue #461).
;
; Project-own routine, NOT the reference BIOS MA_MUL8S ABI (remains UNVERIFIED). Same algorithm as
; src/math/ma_mul8s.s (contract in docs/ma-mul8s.md); the DEV override only changes the segment
; (OVERRIDES instead of MATHLIB), because the DEV ROM has no MATHLIB segment. Stack only: no zero page, no BSS.
;   in : A = multiplicand, X = multiplier (two's complement, -128..127)
;   out: X:A = signed 16-bit product (X high, A low); no overflow case
;   kept: Y and P (PHP/PLP); D is cleared inside for binary ADC; I is never changed
;   precondition: S at routine entry >= $05 (5 bytes pushed by the routine, no wrap of the stack page);
;        MPR1 maps the stack page (RAM, as for any JSR). The real-core test checks the boundary at S=$05.
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
