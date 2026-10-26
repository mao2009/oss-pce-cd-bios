; MA_COS — slot $46, candidate entry $E0D2. DEV ROM only (Issue #80).
;
; Project-own routine, NOT the reference System Card MA_COS ABI (remains UNVERIFIED).
; Cosine by a 65-entry quarter-wave table of Q15 values, with quadrant folding.
;
;   in : A = angle index (unsigned 8-bit binary angle; 256 steps = one full turn,
;        step = 360/256 degrees). X is not read. Every value 0..255 is valid.
;   out: X:A = cos(2*pi*A/256) as signed 16-bit Q15, value = round(32767 * cos(...)),
;        range -32767..+32767 (X high byte, A low byte). No -32768 is produced.
;   kept: Y and P (PHY/PLY and PHP/PLP; B is not a stored flag)
;   clobbered: A and X (the result)
;   uses: zero page $F7-$FA only. No I/O, no MPR access, I is never changed.
;   precondition: caller SP (before JSR) >= $06 (same as MA_SQRT slot $44): the routine
;                 pushes php and phy, so the minimum SP is caller_SP - 4.
;   ABI remains UNVERIFIED and this ROM stays DEV ONLY (release_eligible: false).
;
; Quadrant folding (k = A, r = k & 63, quadrant q = k >> 6):
;   q=0: cos =  T[r]          q=1: cos = -T[64-r]
;   q=2: cos = -T[r]          q=3: cos =  T[64-r]
;   T[i] = round(32767 * cos(2*pi*i/256)), i = 0..64 (T[0]=32767, T[64]=0).
;   Negative for q=1,2 (bit6 XOR bit7 of k). Boundaries: k=0 -> 32767, k=64 -> 0,
;   k=128 -> -32767, k=192 -> 0.
;
; Zero-page layout:
;   $F7  k      input angle index
;   $F8  sign   bit 7 set = negate the table value
;   $F9  r      k & 63
;   $FA  lo     low byte of the negated value (scratch)
;   $FB  hi     high byte of the negated value (scratch)
;
; Stack usage: JSR pushes 2 return bytes; the routine pushes php and phy and pops them
; before rts. Maximum stack depth is caller_SP - 4.
;
.setcpu "huc6280"
.segment "OVERRIDES"
.export api_slot_46
api_slot_46:
    php                 ; save caller's P
    cld                 ; binary arithmetic (negation uses SBC)
    phy                 ; save caller's Y
    sta $F7             ; k = A
    and #$3F
    sta $F9             ; r = k & 63
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
    lda $F7
    asl a               ; bit 7 <- bit 6 of k
    eor $F7             ; bit 7 = bit6 XOR bit7 of k (negative quadrants 1 and 2)
    sta $F8             ; sign flag in bit 7 (only N is tested below)
    lda cos_lo,y
    ldx cos_hi,y        ; X:A = T[index] (positive)
    bit $F8             ; N = sign flag
    bpl done
    sta $FA             ; negate X:A: 0 - (X:A) with borrow
    stx $FB
    sec
    lda #0
    sbc $FA
    sta $FA
    lda #0
    sbc $FB
    sta $FB
    lda $FA
    ldx $FB
done:
    ply                 ; Y = caller's Y
    plp                 ; P = caller's P
    rts                 ; RTS pops JSR return address; SP balanced to caller

; Q15 quarter-wave table, T[i] for i = 0..64 (low bytes, then high bytes).
cos_lo:
    .byte $FF, $F5, $D8, $A6, $61, $09, $9C, $1D
    .byte $89, $E3, $29, $5C, $7C, $89, $84, $6B
    .byte $41, $04, $B5, $54, $E2, $5E, $C9, $23
    .byte $6D, $A6, $CF, $E8, $F1, $EB, $D7, $B3
    .byte $82, $42, $F5, $9B, $33, $BF, $3F, $B4
    .byte $1C, $7A, $CE, $17, $56, $8C, $BA, $DF
    .byte $FB, $11, $1F, $26, $28, $23, $1A, $0B
    .byte $F9, $E2, $C8, $AB, $8C, $6A, $48, $24
    .byte $00
cos_hi:
    .byte $7F, $7F, $7F, $7F, $7F, $7F, $7E, $7E
    .byte $7D, $7C, $7C, $7B, $7A, $79, $78, $77
    .byte $76, $75, $73, $72, $70, $6F, $6D, $6C
    .byte $6A, $68, $66, $64, $62, $60, $5E, $5C
    .byte $5A, $58, $55, $53, $51, $4E, $4C, $49
    .byte $47, $44, $41, $3F, $3C, $39, $36, $33
    .byte $30, $2E, $2B, $28, $25, $22, $1F, $1C
    .byte $18, $15, $12, $0F, $0C, $09, $06, $03
    .byte $00
