; ma_mul16u - unsigned 16x16 -> 32-bit multiply (Issue #75). Original code.
;
; PROJECT-INTERNAL interface defined by this repository. It is NOT the
; reference System Card MA_MUL16U ($41 / $E0C3) ABI: the reference register,
; flag, memory and return contract is UNVERIFIED. This routine is not connected
; to the API jump table and does not claim DEV_CALLABLE.
;
; Storage contract (chosen here; rationale: operands and product live in six
; zero-page bytes so the shift-add loop needs no registers as data and the
; caller can inspect every byte):
;   in:   ma_mul16u_zp+0/+1 = multiplicand, little-endian (preserved)
;         ma_mul16u_zp+2/+3 = multiplier, little-endian (consumed)
;   out:  ma_mul16u_zp+2..+5 = 32-bit product, little-endian
;         (+0/+1 still hold the multiplicand)
;   kept: Y and P (decimal mode is forced off inside, then restored by PLP)
;   uses: A and X are clobbered (X ends at 0); 6 zero-page bytes (linker
;         placed); 1 stack byte besides the JSR return address; no I/O; the I
;         flag is never changed. Not reentrant (shared ZP scratch).
; Algorithm: 16 rounds of shift-add over the 32-bit register {+5,+4,+3,+2}.
; Round bit = bit 0 of the multiplier (+2), which is the bit shifted out.
; When set, the multiplicand is added to the high word {+5,+4}; the 17th bit
; (carry out) is then rotated into the top of the 32-bit register.
;
; Unconfirmed reference items (not decided by this file): reference input
; registers, output registers, carry/Z/N result flags, whether the original
; routine uses zero page or fixed RAM for its operands, timing, and error
; behaviour (none is defined here).
.setcpu "huc6280"
.export ma_mul16u
.exportzp ma_mul16u_zp

.zeropage
ma_mul16u_zp: .res 6             ; +0/+1 multiplicand, +2/+3 multiplier -> product low, +4/+5 product high

.code
ma_mul16u:
    php
    cld                          ; ADC must be binary
    ldx #16
    stz ma_mul16u_zp+4           ; high word of the product starts at zero
    stz ma_mul16u_zp+5
@bit:
    lda ma_mul16u_zp+2
    lsr a                        ; C = multiplier bit 0 (leaves the register)
    bcc @shift
    lda ma_mul16u_zp+4           ; high word += multiplicand (16-bit add)
    clc
    adc ma_mul16u_zp+0
    sta ma_mul16u_zp+4
    lda ma_mul16u_zp+5
    adc ma_mul16u_zp+1
    sta ma_mul16u_zp+5           ; C = carry out of bit 15 of the high word
@shift:
    ror ma_mul16u_zp+5           ; 32-bit right shift; C enters bit 15 of the high word
    ror ma_mul16u_zp+4
    ror ma_mul16u_zp+3
    ror ma_mul16u_zp+2
    dex
    bne @bit
    plp
    rts
