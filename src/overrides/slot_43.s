; MA_DIV16U — slot $43, candidate entry $E0C9. DEV ROM only (Issue #478).
;
; Project-own routine, NOT the reference BIOS MA_DIV16U ABI (remains UNVERIFIED). Same restoring-division layout as
; src/overrides/slot_42.s (MA_DIV16S), but unsigned: the divisor is a full 16-bit value, so the remainder can reach 17
; bits after a shift. The 17th bit (carry out of the remainder high byte) forces the subtraction, as in
; src/math/ma_div16u.s. The routine uses the stack only: no zero page, no BSS, no I/O, no MPR access.
;   in : X:A = dividend (unsigned 16-bit; X high, A low)
;        caller pushes the divisor before JSR: divisor low first, then divisor high (high on top)
;   out: X:A = quotient (unsigned); the two divisor stack bytes become the remainder
;        (the top byte, pulled first, is the remainder high; the byte below it is the remainder low)
;        divisor 0 (project contract): quotient $FFFF, remainder = dividend, C=1, V=0
;        N/Z reflect the quotient; C=0 for a non-zero divisor; V is always 0; I, D and T are taken from the caller's
;        saved P (the $2C mask at @finish, PLP at exit); B is not a stored flag (PHP pushes B=1, PLP drops it)
;   kept: Y
;   precondition: caller T=0 at entry. The routine copies the caller's T bit into the restored P, and its SBC would run
;        in memory-to-memory mode with T=1, so T=1 is OUT OF CONTRACT (as for slot $42). On the pinned core
;        (Geargrafx b49ae82) the T flag is consumed by the instruction that follows SET, and PHP always pushes T=0,
;        so a caller's SET before the JSR never reaches the routine's own instructions with T=1.
;   precondition: S at routine entry >= $07 (7 bytes pushed by the routine), MPR1 maps the stack page (RAM, as for
;        any JSR). The real-core test checks the boundary at entry S=$07.
; ABI remains UNVERIFIED and this ROM stays DEV ONLY (release_eligible: false).
.setcpu "huc6280"
.segment "OVERRIDES"
.export api_slot_43
api_slot_43:
    php
    cld                  ; binary SBC regardless of the caller's D
    pha                  ; dividend low  (becomes quotient low)
    phx                  ; dividend high (becomes quotient high)
    lda #$00
    pha                  ; flags: $01 C (divide by zero)
    lda #16
    pha                  ; restoring-division loop counter
    lda #$00
    pha                  ; remainder high
    pha                  ; remainder low
    tsx                  ; $2101 R lo, $2102 R hi, $2103 count, $2104 flags, $2105 N hi,
                         ; $2106 N lo, $2107 P, $2108/$2109 return, $210A divisor hi, $210B divisor lo
    lda $210A,x
    ora $210B,x
    bne @divide
    lda $2105,x          ; divide by zero: remainder := dividend, quotient := $FFFF, C := 1
    sta $210A,x
    lda $2106,x
    sta $210B,x
    lda #$FF
    sta $2105,x
    sta $2106,x
    lda $2104,x
    ora #$01
    sta $2104,x
    bra @finish
@divide:
@loop:                   ; restoring division: (R:N) <<= 1; if R >= D then R -= D, N |= 1
    asl $2106,x
    rol $2105,x          ; C = msb of the dividend, shifted out
    rol $2101,x
    rol $2102,x          ; C = remainder bit 16 (the shifted remainder no longer fits 16 bits)
    bcs @sub             ; then the remainder is larger than the divisor
    lda $2102,x
    cmp $210A,x
    bcc @no_sub
    bne @sub
    lda $2101,x
    cmp $210B,x
    bcc @no_sub
@sub:
    sec
    lda $2101,x
    sbc $210B,x
    sta $2101,x
    lda $2102,x
    sbc $210A,x
    sta $2102,x
    lda $2106,x
    ora #$01
    sta $2106,x
@no_sub:
    dec $2103,x
    bne @loop
    lda $2101,x          ; remainder := result of the division
    sta $210B,x
    lda $2102,x
    sta $210A,x
@finish:
    lda $2107,x          ; P := caller I/D/T kept, then C from flags, then N/Z from quotient
    and #$2C
    sta $2107,x
    lda $2104,x
    and #$01
    ora $2107,x
    sta $2107,x
    lda $2105,x
    and #$80
    ora $2107,x
    sta $2107,x
    lda $2105,x
    ora $2106,x
    bne @nz
    lda $2107,x
    ora #$02
    sta $2107,x
@nz:
    pla                  ; discard remainder low, remainder high, count, flags
    pla
    pla
    pla
    pla                  ; quotient high
    tax
    pla                  ; quotient low
    plp
    rts
