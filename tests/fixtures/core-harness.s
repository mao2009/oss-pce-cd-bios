; Original HuC6280 core verification harness (test overlay only, NOT a BIOS service).
; The host patches harness_input (A, X, Y, P, 32-byte ZP seed) and the JSR operand at
; harness_call, then observes registers at harness_call/harness_after on the real core.
; Window under test: zero page $E0-$FF (CPU $20E0-$20FF). Snapshots: $2200 pre, $2220 post.
.setcpu "huc6280"
.segment "DIAGNOSTIC"
.export harness_entry, harness_call, harness_after, harness_done, harness_trap, harness_input
.export harness_selftest, harness_selftest_hang, harness_selftest_brk

harness_entry:
    sei
    cld
    csh
    lda #$FF
    tam #$01             ; IO
    lda #$F8
    tam #$02             ; work RAM (ZP/stack/$2200); MPR2-6 are left as found, MPR7=0
    ldx #$FF
    txs
    ldx #31
@seed:
    lda harness_input+4,x
    sta $E0,x            ; synthetic ZP input
    sta $2200,x          ; pre snapshot
    dex
    bpl @seed
    lda harness_input+3
    pha
    lda harness_input
    ldx harness_input+1
    ldy harness_input+2
    plp                  ; P last, so A/X/Y loads cannot disturb it
harness_call:
    jsr harness_selftest ; operand patched by the host per case
harness_after:
    php
    pha
    phx
    ldx #31
@post:
    lda $E0,x
    sta $2220,x          ; post snapshot
    dex
    bpl @post
    plx
    pla
    plp
harness_done:
    bra harness_done     ; normal-return checkpoint
harness_trap:
    bra harness_trap     ; all IRQ/BRK vectors land here

harness_input:
    .res 36, $00

; Self-test routines used to validate the harness itself.
harness_selftest:        ; A = A + X + C; ZP $E0 = X, $FF = A, $FE += 1; Y unchanged
    stx $E0
    adc $E0
    sta $FF
    inc $FE
    rts
harness_selftest_hang:   ; never returns: must be classified TIMEOUT
    bra harness_selftest_hang
harness_selftest_brk:    ; BRK: must be classified TRAPPED
    brk
    nop
