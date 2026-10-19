; Original HuCARD probe for src/ex_binbcd.s (Issue #70). No System Card code, no BIOS JSR.
; Calls ex_binbcd_packed for every A = 0..255 and stores, per input index Y:
;   $2300+Y = returned A, $2400+Y = returned P, $2500+Y = returned X (entry X = $A5)
; then writes "BCD!" to $2200 as the completion marker.
.setcpu "huc6280"
.import ex_binbcd_packed
.export probe_reset
.segment "RESET"
probe_reset:
    sei
    cld
    csh
    lda #$ff
    tam #$01                 ; MPR0: I/O
    lda #$f8
    tam #$02                 ; MPR1: working RAM ($2000-$3fff), stack page $2100
    ldx #$ff
    txs
    ldy #0
@loop:
    ldx #$a5
    tya
    jsr ex_binbcd_packed
    sta $2300,y
    php
    pla
    sta $2400,y
    txa
    sta $2500,y
    iny
    bne @loop
    tii marker, $2200, 4
probe_halt:
    bra probe_halt
marker:
    .byte "BCD!"
.segment "VECTORS"
    .word probe_halt, probe_halt, probe_halt, probe_halt, probe_reset
