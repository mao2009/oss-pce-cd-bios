; Independently authored HuCARD fixture. No System Card API or CD boot code.
.setcpu "huc6280"
.export reset, checkpoint, marker
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
    tii marker, $2200, 4          ; Exercise a HuC6280 block transfer.
    tma #$02
    sta $2204
    lda #$5a
    sta $2205
.segment "CHECKPOINT"
checkpoint:
    bra checkpoint
.segment "MARKER"
marker:
    .byte "PCE!"
.segment "VECTORS"
    .word checkpoint, checkpoint, checkpoint, checkpoint, reset
