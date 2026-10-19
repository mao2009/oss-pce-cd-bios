; ma_mul8u - unsigned 8x8 -> 16-bit multiply (Issue #73). Original code.
;
; PROJECT-INTERNAL interface defined by this repository. It is NOT the
; reference System Card MA_MUL8U ($3F / $E0BD) ABI, which remains UNVERIFIED,
; and this routine is not connected to the API jump table.
;
;   in:   A = multiplicand, X = multiplier (both unsigned)
;   out:  A = product bits 0-7, X = product bits 8-15
;   kept: Y and P (decimal mode is forced off inside, then restored)
;   uses: 2 zero-page bytes (ma_mul8u_zp, placed by the linker) and 1 stack
;         byte besides the JSR return address; no I/O, I flag never changed.
.setcpu "huc6280"
.export ma_mul8u
.exportzp ma_mul8u_zp

.zeropage
ma_mul8u_zp: .res 2              ; +0 multiplicand, +1 multiplier -> product low

.code
ma_mul8u:
    php
    cld                          ; ADC must be binary
    sta ma_mul8u_zp
    stx ma_mul8u_zp+1
    lda #0                       ; A = running product high byte
    ldx #8
    lsr ma_mul8u_zp+1            ; C = next multiplier bit
@bit:
    bcc @skip
    clc
    adc ma_mul8u_zp
@skip:
    ror a                        ; carry of the add enters bit 7
    ror ma_mul8u_zp+1            ; product low fills from the top
    dex
    bne @bit
    tax
    lda ma_mul8u_zp+1
    plp
    rts
