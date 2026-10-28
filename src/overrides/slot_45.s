; MA_SIN — slot $45, candidate entry $E0CF. DEV ROM only (Issue #79).
;
; Project-own routine, NOT the reference System Card MA_SIN ABI (remains UNVERIFIED).
; Sine by a 65-entry quarter-wave table of Q15 values, with quadrant folding.
;
;   in : A = angle (unsigned 8-bit binary angle; 256 steps = one full turn,
;        step = 360/256 degrees). X is not read. Every value 0..255 is valid.
;   out: X:A = sin(2*pi*A/256) as signed 16-bit Q15, value = round(32767 * sin(...)),
;        range -32767..+32767. X = high byte, A = low byte. No -32768 is produced.
;        Rounding: the routine computes the sign-magnitude form (table value = round-half-up
;        of |32767 sin|, negated for negative half-turns). That equals floor(x + 0.5) for all
;        256 inputs, because no input is an exact half-integer (tests/host/test_ma_sin_dev.py
;        checks all 256). Same rule as MA_COS (slot $46, Issue #80).
;   kept: Y and P (restored by PLY and PLP)
;   clobbered: A and X (the result)
;   uses: zero page $F7, $F9, $FA only. No I/O, no MPR access, I is never changed.
;   precondition: caller SP (before JSR) >= $06 (same as slot $44 and $46): the routine
;                 pushes php and phy, so the minimum SP is caller_SP - 4.
;   ABI remains UNVERIFIED and this ROM stays DEV ONLY (release_eligible: false).
;
; Quadrant folding (a = A, r = a & 63, quadrant q = a >> 6):
;   q=0: sin =  T[r]          q=1: sin =  T[64-r]
;   q=2: sin = -T[r]          q=3: sin = -T[64-r]
;   T[i] = round(32767 * sin(pi/2 * i / 64)), i = 0..64 (T[0]=0, T[64]=32767).
;   Negative for q=2,3 (bit 7 of a). Boundaries: a=0 -> 0, a=64 -> 32767,
;   a=128 -> 0, a=192 -> -32767.
;   Identity: sin(a) = cos((a - 64) mod 256), checked by tests/host/test_ma_sin_dev.py.
;
; Zero-page layout:
;   $F7  a        input angle (kept for the sign test)
;   $F9  r        a & 63; after the table index is chosen, reused as the high byte
;                 of the negated value (scratch)
;   $FA  lo       low byte of the negated value (scratch)
;
; Stack usage: JSR pushes 2 return bytes; the routine pushes php and phy and pops them
; before rts. Maximum stack depth is caller_SP - 4.
;
.setcpu "huc6280"
.segment "OVERRIDES"
.export api_slot_45
api_slot_45:
    php                 ; save caller's P
    cld                 ; binary arithmetic (negation uses SBC)
    phy                 ; save caller's Y
    sta $F7             ; a = A
    and #$3F
    sta $F9             ; r = a & 63
    lda $F7
    and #$40            ; quadrant bit 6 (odd quadrant when set)
    beq idx_even
    lda #64             ; odd quadrant: index = 64 - r
    sec
    sbc $F9
    bra idx_done
idx_even:
    lda $F9             ; even quadrant: index = r
idx_done:
    tay                 ; Y = table index 0..64
    nop                 ; layout parity: makes the OVERRIDES end odd so the even-rounded trampoline == body end (MUL16S window, as PR #526)
    lda sin_lo,y
    ldx sin_hi,y        ; X:A = T[index] (non-negative)
    bit $F7             ; N = bit 7 of a (negative half-turn)
    bpl done
    sta $FA             ; negate X:A: 0 - (X:A) with borrow
    stx $F9
    sec
    lda #0
    sbc $FA
    sta $FA
    lda #0
    sbc $F9
    sta $F9
    lda $FA
    ldx $F9
done:
    ply                 ; Y = caller's Y
    plp                 ; P = caller's P
    rts                 ; RTS pops JSR return address; SP balanced to caller

; Q15 quarter-wave table, T[i] for i = 0..64: low bytes (sin_lo), then high bytes (sin_hi).
; Generated from floor(32767*sin(pi/2*i/64)+0.5) and checked by tests/host/test_ma_sin_dev.py.
sin_lo:
    .byte $00, $24, $48, $6A, $8C, $AB, $C8, $E2
    .byte $F9, $0B, $1A, $23, $28, $26, $1F, $11
    .byte $FB, $DF, $BA, $8C, $56, $17, $CE, $7A
    .byte $1C, $B4, $3F, $BF, $33, $9B, $F5, $42
    .byte $82, $B3, $D7, $EB, $F1, $E8, $CF, $A6
    .byte $6D, $23, $C9, $5E, $E2, $54, $B5, $04
    .byte $41, $6B, $84, $89, $7C, $5C, $29, $E3
    .byte $89, $1D, $9C, $09, $61, $A6, $D8, $F5
    .byte $FF
sin_hi:
    .byte $00, $03, $06, $09, $0C, $0F, $12, $15
    .byte $18, $1C, $1F, $22, $25, $28, $2B, $2E
    .byte $30, $33, $36, $39, $3C, $3F, $41, $44
    .byte $47, $49, $4C, $4E, $51, $53, $55, $58
    .byte $5A, $5C, $5E, $60, $62, $64, $66, $68
    .byte $6A, $6C, $6D, $6F, $70, $72, $73, $75
    .byte $76, $77, $78, $79, $7A, $7B, $7C, $7C
    .byte $7D, $7E, $7E, $7F, $7F, $7F, $7F, $7F
    .byte $7F
