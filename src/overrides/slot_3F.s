; MA_MUL8U — slot $3F, candidate entry $E0BD. DEV ROM only (Issue #436).
;
; Project-own routine, NOT the reference System Card MA_MUL8U ABI (remains UNVERIFIED).
; Same shift-add algorithm as src/math/ma_mul8u.s, but the state lives on the stack instead of
; zero page, so the DEV ROM needs no ZP/BSS segment:
;   in : A = multiplicand, X = multiplier (unsigned 8-bit; every pair is valid)
;   out: A = product bits 0-7, X = product bits 8-15
;   kept: Y and P (D is cleared inside for binary ADC; PLP restores the caller's flags)
;   uses: stack only (7 bytes including the JSR return); no RAM, no I/O, I is never changed
; ABI remains UNVERIFIED and this ROM stays DEV ONLY (release_eligible: false).
.setcpu "huc6280"
.segment "OVERRIDES"
.export api_slot_3F
api_slot_3F:
    php
    cld                  ; ADC must be binary
    phy                  ; Y preserved
    pha                  ; m  (multiplicand)
    txa
    pha                  ; lo (multiplier, shifted out as the product fills)
    lda #0
    pha                  ; hi (product high byte)
    tsx                  ; X = S: hi $0101,x  lo $0102,x  m $0103,x
    lsr $0102,x          ; C = multiplier bit 0; each ROR lo below leaves the next bit in C
    .repeat 8
    bcc :+
    clc
    lda $0101,x
    adc $0103,x          ; hi += m; C = carry out
    sta $0101,x
:   ror $0101,x          ; hi: carry enters bit 7
    ror $0102,x          ; lo: hi bit 0 enters bit 7, lo bit 0 becomes the next C
    .endrepeat
    plx                  ; X = product high
    pla                  ; A = product low
    ply                  ; discard m
    ply                  ; Y = caller's Y
    plp                  ; caller's P
    rts
