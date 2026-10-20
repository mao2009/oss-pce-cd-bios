; ma_div16u - unsigned 16/16 -> 16-bit divide with 16-bit remainder (Issue #77). Original code.
;
; PROJECT-INTERNAL interface defined by this repository. It is NOT the
; reference System Card MA_DIV16U ($43 / $E0C9) ABI, which remains UNVERIFIED,
; and this routine is not connected to the API jump table.
;
;   in:   ma_div16u_num = dividend (low, high); ma_div16u_den = divisor (low, high)
;   out:  ma_div16u_num = quotient (low, high); ma_div16u_rem = remainder (low, high)
;         ma_div16u_den is unchanged
;   zero divisor (project-internal choice, not a reference contract): no trap and no
;         flag. The restoring loop then always "subtracts 0", so the result is
;         quotient = $FFFF and remainder = dividend.
;   kept: Y and P (decimal mode is forced off inside, then restored)
;   uses: A, X; 6 zero-page bytes (placed by the linker, exported). No stack use
;         besides the JSR return address; no I/O, I flag never changed.
;         Not reentrant (shared ZP scratch); do not call from an interrupt
;         that may preempt another call.
;
; Rationale for the zero-page block: a 16-bit dividend and divisor do not fit in
; A/X/Y, and in-place quotient/remainder storage avoids stack traffic and register
; shuffling. Same placement style as ma_mul8u.
.setcpu "huc6280"
.export ma_div16u
.exportzp ma_div16u_num, ma_div16u_den, ma_div16u_rem

.zeropage
ma_div16u_num: .res 2            ; dividend in, quotient out
ma_div16u_den: .res 2            ; divisor (unchanged)
ma_div16u_rem: .res 2            ; remainder out (zeroed on entry)

.code
ma_div16u:
    php
    cld                          ; SBC must be binary
    stz ma_div16u_rem
    stz ma_div16u_rem+1
    ldx #16
@bit:
    asl ma_div16u_num            ; shift dividend MSB into remainder
    rol ma_div16u_num+1          ; C = old dividend bit 15
    rol ma_div16u_rem
    rol ma_div16u_rem+1          ; C = remainder bit 16 (remainder overflowed 16 bits)
    bcs @sub                     ; overflow means remainder > divisor
    lda ma_div16u_rem
    cmp ma_div16u_den
    lda ma_div16u_rem+1
    sbc ma_div16u_den+1          ; C = 1 when remainder >= divisor
    bcc @next
@sub:
    sec
    lda ma_div16u_rem
    sbc ma_div16u_den
    sta ma_div16u_rem
    lda ma_div16u_rem+1
    sbc ma_div16u_den+1
    sta ma_div16u_rem+1
    lda ma_div16u_num            ; quotient bit 0 is still 0 after the ASL
    ora #1
    sta ma_div16u_num
@next:
    dex
    bne @bit
    plp
    rts
