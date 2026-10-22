; MA_MUL16S — slot $4F, candidate entry $E0ED. DEV ROM only (Issue #494; design docs/ma-mul16s.md, Issue #479).
;
; Project-own routine, NOT the reference System Card MA_MUL16S ABI (remains UNVERIFIED).
; Same stack convention as MA_MUL16U (slot_41.s), but the 16-bit operands are signed (two's complement):
;   caller: PHA multiplicand lo, PHA multiplicand hi, PHA multiplier lo, PHA multiplier hi, JSR
;   in   : the 4 pushed bytes (signed 16x16, -32768..32767; every pair is valid, no overflow exists)
;   out  : the same 4 bytes hold the 32-bit signed product (two's complement), bits 0-7 in the first pushed byte,
;          8-15 in the second, 16-23 in the third, 24-31 in the fourth. The caller pops them (4 x PLA).
;          A and X are clobbered. No error return.
;   precondition: SP at entry (after the JSR push) in $08..$F9. The routine pushes 6 bytes (P, Y, multiplier copy x2,
;          high word x2; design budget 8), and the highest indexed access is $210C+X with X = entry SP - 6.
;   precondition: caller T (P bit 5) must be 0 at entry; T=1 is OUT OF CONTRACT (T selects the memory-to-memory mode
;          of ADC/AND/EOR/ORA and the shift-add uses ADC; reasoning as in slot_41.s). No CLT is added and no T=1
;          support is claimed. On the pinned core a SET before the JSR is consumed by the JSR.
;   kept: Y. P is restored by PLP from the value PHP pushed at entry (C,Z,I,D,V,N as the caller left them; the pushed
;          T is 0 and B is not stored, so the test compares every bit except B and expects T=0). D is cleared inside.
;   uses: stack page $2100-$21FF only. No zero page, work RAM, I/O or MPR access; I is never changed.
; ABI remains UNVERIFIED and this ROM stays DEV ONLY (release_eligible: false).
;
; Stack layout (X = SP after the 6 inside pushes; offsets are $2100 + X + n):
;   X+1 high word bits 24-31 (scratch)   X+2 high word bits 16-23 (scratch)
;   X+3 multiplier lo copy               X+4 multiplier hi copy
;   X+5 saved Y                          X+6 saved P            X+7,X+8 JSR return
;   X+9 multiplier hi (product bits 8-15 later)   X+10 multiplier lo (product bits 0-7 later)
;   X+11 multiplicand hi                 X+12 multiplicand lo
; Algorithm: unsigned 16x16 shift-add exactly as slot_41.s, then the mod 2^16 identity on the high word:
;   multiplicand < 0 -> high -= multiplier;  multiplier < 0 -> high -= multiplicand  (both: both).
.setcpu "huc6280"
.segment "OVERRIDES"
.export api_slot_4F
api_slot_4F:
    php
    cld                  ; ADC/SBC must be binary
    phy                  ; Y preserved
    tsx
    lda $2105,x          ; multiplier hi
    pha                  ; copy for the sign correction (the shift destroys the original)
    lda $2106,x          ; multiplier lo
    pha
    lda #0
    pha                  ; high word, low byte
    pha                  ; high word, high byte
    tsx                  ; X = S
    ldy #16              ; round counter (Y was saved above and is restored below)
@round:
    lda $210A,x          ; C = multiplier bit 0
    lsr a
    bcc @skip
    clc
    lda $2102,x          ; high lo
    adc $210C,x          ; + multiplicand lo
    sta $2102,x
    lda $2101,x          ; high hi
    adc $210B,x          ; + multiplicand hi + carry
    sta $2101,x
@skip:
    ror $2101,x          ; C enters high hi bit 15
    ror $2102,x
    ror $2109,x
    ror $210A,x          ; multiplier lo bit 0 is the next C
    dey                  ; N/Z only, C is kept
    bne @round
    lda $210B,x          ; multiplicand hi: negative -> high -= multiplier
    bpl @multiplicand_done
    sec
    lda $2102,x
    sbc $2103,x          ; - multiplier lo
    sta $2102,x
    lda $2101,x
    sbc $2104,x          ; - multiplier hi - borrow
    sta $2101,x
@multiplicand_done:
    lda $2104,x          ; multiplier hi: negative -> high -= multiplicand
    bpl @multiplier_done
    sec
    lda $2102,x
    sbc $210C,x          ; - multiplicand lo
    sta $2102,x
    lda $2101,x
    sbc $210B,x          ; - multiplicand hi - borrow
    sta $2101,x
@multiplier_done:
    lda $210A,x          ; product bits 0-7   -> first pushed byte
    sta $210C,x
    lda $2109,x          ; product bits 8-15  -> second pushed byte
    sta $210B,x
    lda $2102,x          ; product bits 16-23 -> third pushed byte
    sta $210A,x
    lda $2101,x          ; product bits 24-31 -> fourth pushed byte
    sta $2109,x
    pla                  ; discard high word and multiplier copy (4 bytes)
    pla
    pla
    pla
    ply                  ; Y = caller's Y
    plp                  ; caller's P
    rts
