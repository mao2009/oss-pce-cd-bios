; Condition harness for src/ex_bcdbin.s (Issue #416). OSS-only; NOT the reference BIOS EX_BCDBIN.
; 8 cases (input C, X, Y, SP) x 16 inputs. Records A/X/Y/C/SP-after-return at $2300/$2380/$2400/$2480/$2500
; (index = case*16+k). Tables mirror CASES / INPUTS in geargrafx_bcdbin_conditions.py. Marker "PCE!" at $2200 last.
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
    stz $2000                    ; record index
    stz $2001                    ; case index
@case:
    ldx $2001
    lda sp_tab,x
    tax
    txs                          ; SP for this case
    ldx $2001
    lda c_tab,x
    sta $2005
    lda x_tab,x
    sta $2003
    lda y_tab,x
    sta $2004
    stz $2002                    ; input index
@input:
    lda $2005
    lsr                          ; C = input carry
    ldx $2002
    lda in_tab,x
    ldx $2003
    ldy $2004
    jsr ex_bcdbin
    sta $2010
    stx $2011
    sty $2012
    tsx                          ; SP right after return
    stx $2014
    php
    pla
    and #$01                     ; C only: N/Z/V are undefined in the spec
    sta $2013
    ldx $2000
    lda $2010
    sta $2300,x
    lda $2011
    sta $2380,x
    lda $2012
    sta $2400,x
    lda $2013
    sta $2480,x
    lda $2014
    sta $2500,x
    inc $2000
    inc $2002
    lda $2002
    cmp #16
    bne @input
    inc $2001
    lda $2001
    cmp #8
    beq @cases_done
    jmp @case
@cases_done:
    tii marker, $2200, 4
    tma #$02
    sta $2204
    lda #$5a
    sta $2205
checkpoint:
    bra checkpoint
c_tab:
    .byte 0, 1, 0, 1, 0, 1, 0, 1
x_tab:
    .byte $5a, $5a, $00, $ff, $12, $ab, $5a, $00
y_tab:
    .byte $a5, $a5, $00, $ff, $34, $cd, $a5, $ff
sp_tab:
    .byte $ff, $ff, $ff, $ff, $80, $80, $80, $c0
in_tab:
    .byte $00, $01, $09, $10, $19, $20, $42, $59, $90, $99, $0a, $0f, $9a, $a0, $c5, $ff
marker:
    .byte "PCE!"
.segment "VECTORS"
    .word checkpoint, checkpoint, checkpoint, checkpoint, reset
