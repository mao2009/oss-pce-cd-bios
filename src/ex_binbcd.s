; Issue #70: original internal EX_BINBCD routine (PROJECT-OWN spec, NOT the System Card ABI).
; DEV ROM only: included by src/overrides/slot_3C.s. The reference $3C/$E0B4 contract remains UNCONFIRMED.
; Contract (this project only, see docs/ex-binbcd-internal.md):
;   in : A = unsigned binary 0..99
;   out: A = PACKED BCD, one byte: tens digit in bits 7-4, ones digit in bits 3-0; C=0
;   out of range (A = 100..255): A unchanged, C=1
;   X/Y preserved, D never set, no memory or IO writes; the only memory touched is the stack
;   (PHX/PLX), stack use 3 bytes (JSR + PHX).
;   N/Z/V on return are unspecified.
; Unpacked BCD (one digit per byte) is NOT provided by this routine.
.setcpu "huc6280"
.export ex_binbcd_packed, ex_binbcd_tens_step
ex_binbcd_packed:
    cmp #100
    bcs binbcd_done           ; out of range: C=1, A/X/Y untouched
    phx
    ldx #0                   ; X accumulates tens digit << 4
binbcd_tens:
    cmp #10
    bcc binbcd_ones           ; A = ones digit 0..9
    sbc #10                  ; C=1 from CMP, so exact subtract
    sax
    clc
ex_binbcd_tens_step:
    adc #$10                 ; one more ten in the high nibble (max $90, no carry)
    sax
    bra binbcd_tens
binbcd_ones:
    sax                      ; A = tens << 4, X = ones
binbcd_add_ones:
    cpx #0
    beq binbcd_packed
    inc a
    dex
    bra binbcd_add_ones
binbcd_packed:
    plx
    clc
binbcd_done:
    rts
