; MA_MUL16U — slot $41, candidate entry $E0C3. DEV ROM only (Issue #448).
;
; Project-own routine, NOT the reference System Card MA_MUL16U ABI (remains UNVERIFIED).
; The operands and the 32-bit product travel on the stack, so no zero page or work RAM is used:
;   caller: PHA multiplicand lo, PHA multiplicand hi, PHA multiplier lo, PHA multiplier hi, JSR
;   in   : the 4 pushed bytes (unsigned 16x16; every pair is valid)
;   out  : the same 4 bytes hold the product, bits 0-7 in the first pushed byte, 8-15 in the second,
;          16-23 in the third, 24-31 in the fourth. The caller pops them (4 x PLA). A and X are clobbered.
;   precondition: SP at entry (after the JSR push) in $04..$F9, so the indexed $21xx,X accesses stay
;          in the stack page (the real-core test checks the boundary caller SP=$0A and SP=$FF)
;   precondition: caller T (P bit 5) must be 0 at entry. T=1 is OUT OF CONTRACT: this routine defines no
;          behaviour for it. The HuC6280 T flag changes ADC/AND/EOR/ORA (memory-to-memory mode), and the
;          routine's ADC is one of them. The pinned core's JSR consumes a SET before it (the routine is entered
;          with T=0); tests/integration/geargrafx_dev_mul16u.py checks that JSR behaviour. No test runs the routine
;          with T=1.
;   kept: Y. P is restored by PLP from the value pushed at entry (C,Z,I,D,V,N as the caller left them;
;          the test checks bit 5 is 0 on return, bit 4 is ignored); D is cleared inside for binary ADC.
;   uses: stack page $2100-$21FF only (4 bytes pushed inside plus the 2 return bytes). No I/O, no MPR, I is never changed.
; ABI remains UNVERIFIED and this ROM stays DEV ONLY (release_eligible: false).
;
; Stack layout (X = SP after the 4 inside pushes; offsets are $2100 + X + n):
;   X+1 product bits 24-31 (scratch, starts 0)   X+2 product bits 16-23 (scratch, starts 0)
;   X+3 saved Y                                  X+4 saved P
;   X+7 multiplier hi (product bits 8-15 later)  X+8 multiplier lo (product bits 0-7 later)
;   X+9 multiplicand hi                          X+10 multiplicand lo
; Algorithm: textbook shift-add on the 32-bit register {high, multiplier}: 16 rounds; each round adds the
; multiplicand into the high word when the multiplier bit 0 is set, then rotates the whole 32-bit
; register right through C (the carry out of the add enters bit 15 of the high word).
.setcpu "huc6280"
.segment "OVERRIDES"
.export api_slot_41
api_slot_41:
    php
    cld                  ; ADC must be binary
    phy                  ; Y preserved
    lda #0
    pha                  ; high word, low byte
    pha                  ; high word, high byte
    tsx                  ; X = S
    ldy #16              ; round counter (Y was saved above and is restored below)
@round:
    lda $2108,x          ; C = multiplier bit 0 (the bit about to be shifted out of the 32-bit register)
    lsr a
    bcc @skip
    clc
    lda $2102,x          ; high lo
    adc $210A,x          ; + multiplicand lo
    sta $2102,x
    lda $2101,x          ; high hi
    adc $2109,x          ; + multiplicand hi + carry
    sta $2101,x
@skip:
    ror $2101,x          ; C enters high hi bit 15, bit 0 out
    ror $2102,x          ; high hi bit 0 enters high lo bit 7
    ror $2107,x          ; high lo bit 0 enters multiplier hi bit 7
    ror $2108,x          ; multiplier hi bit 0 enters multiplier lo bit 7; lo bit 0 is the next C
    dey                  ; N/Z only, C is kept
    bne @round
    lda $2108,x          ; product bits 0-7   -> first pushed byte
    sta $210A,x
    lda $2107,x          ; product bits 8-15  -> second pushed byte
    sta $2109,x
    lda $2102,x          ; product bits 16-23 -> third pushed byte
    sta $2108,x
    lda $2101,x          ; product bits 24-31 -> fourth pushed byte
    sta $2107,x
    pla                  ; discard high word (both bytes)
    pla
    ply                  ; Y = caller's Y
    plp                  ; caller's P
    rts
