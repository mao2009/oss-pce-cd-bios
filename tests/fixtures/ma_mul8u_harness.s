; Issue #73 test harness (original, test-only HuCARD image, NOT a BIOS).
; Calls ma_mul8u for a fixed edge table, then for all 65,536 (a, b) pairs and
; compares each result with an independent repeated-addition reference.
; Result block at CPU $2200 (work RAM +$200):
;   +0 "DONE"/"FAIL"; +4..+6 pairs passed; +7..+9 A/X/Y (A = masked P on a
;   flag failure) and +$A/+$B a/b at failure; +$10../+$20.. edge low/high.
.setcpu "huc6280"
.import ma_mul8u

RESULT = $2200
EDGES = 12

.zeropage
cur_a: .res 1
cur_b: .res 1
ref:   .res 2
pflag: .res 1

.segment "STARTUP"
reset:
    sei
    cld
    csh
    lda #$FF
    tam #$01                     ; MPR0 = I/O
    lda #$F8
    tam #$02                     ; MPR1 = work RAM (zero page $2000, stack $2100)
    ldx #$FF
    txs
    ldy #0
@edge:
    lda edge_a,y
    ldx edge_b,y
    jsr ma_mul8u                 ; Y must survive for this loop to work
    sta RESULT+$10,y
    txa
    sta RESULT+$20,y
    iny
    cpy #EDGES
    bne @edge
    stz RESULT+4
    stz RESULT+5
    stz RESULT+6
    stz cur_a
@outer:
    stz ref
    stz ref+1
    stz cur_b
@inner:
    lda cur_b
    and #$01
    beq @even
    lda #$CB                     ; odd b: caller enters with N=V=D=Z=C=1
@even:
    ora #$04                     ; keep I=1
    sta pflag
    pha
    lda cur_a
    ldx cur_b
    ldy #$5A
    plp
    jsr ma_mul8u
    php
    cld
    cpy #$5A
    bne fail
    cmp ref
    bne fail
    cpx ref+1
    bne fail
    pla
    and #$CF                     ; returned N/V/D/I/Z/C must equal the caller's
    cmp pflag
    bne fail
    tsx
    cpx #$FF                     ; stack balanced
    bne fail
    inc RESULT+4
    bne @count
    inc RESULT+5
    bne @count
    inc RESULT+6
@count:
    clc
    lda ref
    adc cur_a                    ; reference: a*(b+1) = a*b + a
    sta ref
    bcc @next
    inc ref+1
@next:
    inc cur_b
    bne @inner
    inc cur_a
    bne @outer
    tii done_text, RESULT, 4
halt:
    bra halt

fail:
    sta RESULT+7
    stx RESULT+8
    sty RESULT+9
    lda cur_a
    sta RESULT+10
    lda cur_b
    sta RESULT+11
    tii fail_text, RESULT, 4
    bra halt

done_text: .byte "DONE"
fail_text: .byte "FAIL"
edge_a: .byte 0, 0, 255, 1, 1, 255, 255, 2, 128, 16, 15, 170
edge_b: .byte 0, 255, 0, 1, 255, 1, 255, 128, 2, 16, 17, 85

.segment "VECTORS"
    .word halt, halt, halt, halt, reset
