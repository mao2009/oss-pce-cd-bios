; MA_MUL8S candidate: signed 8x8 -> 16-bit multiply (Issue #74).
;
; Original OSS implementation written from this project's OWN specification
; (docs/ma-mul8s.md), not from proprietary BIOS code. The reference BIOS
; MA_MUL8S calling convention is UNVERIFIED; this register ABI is a project
; definition only. NOT connected to the API jump table.
;
; In:  A = multiplicand, X = multiplier (two's complement, -128..127)
; Out: X:A = signed 16-bit product (X high, A low). |a*b| <= 16384, so the
;      result always fits; there is no overflow case.
; Preserved: Y and P (PHP/PLP). Uses 5 stack bytes; no zero page.
; Precondition: MPR1 maps the stack page at logical $2100 (RAM bank $F8).
.setcpu "huc6280"
.segment "MATHLIB"
.export ma_mul8s
ma_mul8s:
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
