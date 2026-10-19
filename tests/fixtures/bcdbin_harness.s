; Original HuCARD harness for src/ex_bcdbin.s (Issue #71). Not a BIOS.
; Calls ex_bcdbin for every input $00-$FF with X=$5A, Y=$A5 and records
; A/P/X/Y at work RAM $2206/$2306/$2406/$2506 + input, final SP at $2606.
; The smoke marker "PCE!",$F8,$5A at $2200 is written only after all 256 calls.
.setcpu "huc6280"
.import ex_bcdbin
.segment "RESET"
reset:
    sei
    cld
    csh
    lda #$ff
    tam #$01                     ; MPR0: I/O
    lda #$f8
    tam #$02                     ; MPR1: working RAM ($2000-$3fff)
    ldx #$ff
    txs
    stz $2000                    ; input counter
@next:
    ldx #$5a
    ldy #$a5
    lda $2000
    jsr ex_bcdbin
    php
    stx $2001
    ldx $2000
    sta $2206,x
    pla
    sta $2306,x
    lda $2001
    sta $2406,x
    tya
    sta $2506,x
    inc $2000
    bne @next
    tsx
    stx $2606
    tii marker, $2200, 4
    tma #$02
    sta $2204
    lda #$5a
    sta $2205
checkpoint:
    bra checkpoint
marker:
    .byte "PCE!"
.segment "VECTORS"
    .word checkpoint, checkpoint, checkpoint, checkpoint, reset
