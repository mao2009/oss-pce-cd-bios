; Issue #77 test harness (original, test-only HuCARD image, NOT a BIOS).
; Calls ma_div16u for a fixed edge table (divide-by-zero, max value, exact and
; non-exact cases), then for 65,536 LFSR-generated (n, d) pairs. Each result is checked
; in-guest with q*d + r == n (32-bit), r < d for d != 0, and for d == 0: q == $FFFF, r == n.
; Also checks: divisor unchanged, Y preserved, P returned as entered (masked), stack balanced.
; Result block at CPU $2200 (work RAM +$200):
;   +0 "DONE"/"FAIL"; +4..+6 pairs passed (3 bytes LE); +8..+11 n/d at failure
;   edge quotient/remainder tables: +$40/+$60 (quotient lo/hi), +$80/+$A0 (remainder lo/hi), index = case.
.setcpu "huc6280"
.import ma_div16u
.importzp ma_div16u_num, ma_div16u_den, ma_div16u_rem

RESULT = $2200
EDGES = 20

; long conditional branches (the check code is longer than a rel8 branch reaches)
.macro jne target
    beq :+
    jmp target
:
.endmacro
.macro jcs target
    bcc :+
    jmp target
:
.endmacro

.zeropage
n_lo:  .res 1
n_hi:  .res 1
d_lo:  .res 1                    ; divisor as the caller set it
d_hi:  .res 1
retp:  .res 1                    ; P returned by the callee
pflag: .res 1                    ; P entering the callee (masked compare)
fb:    .res 1
idx:   .res 1
lfsr:  .res 2
ctr:   .res 2
p:     .res 4                    ; 32-bit product accumulator
m:     .res 4                    ; 32-bit multiplicand
qq:    .res 2                    ; multiplier copy

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
    stz RESULT+4
    stz RESULT+5
    stz RESULT+6
    ldy #0
@edge:
    sty idx
    lda edge_nlo,y
    sta n_lo
    sta ma_div16u_num
    lda edge_nhi,y
    sta n_hi
    sta ma_div16u_num+1
    lda edge_dlo,y
    sta d_lo
    sta ma_div16u_den
    lda edge_dhi,y
    sta d_hi
    sta ma_div16u_den+1
    lda #$04                     ; caller enters with I=1 only
    pha
    plp
    jsr ma_div16u                ; Y must survive for this loop to work
    cpy idx
    jne fail
    jsr check
    ldy idx
    lda ma_div16u_num
    sta RESULT+$40,y
    lda ma_div16u_num+1
    sta RESULT+$60,y
    lda ma_div16u_rem
    sta RESULT+$80,y
    lda ma_div16u_rem+1
    sta RESULT+$A0,y
    iny
    cpy #EDGES
    bne @edge

    lda #$E1                     ; LFSR seed (non-zero)
    sta lfsr
    lda #$AC
    sta lfsr+1
    stz ctr
    stz ctr+1
@rand:
    jsr step
    lda lfsr
    sta n_lo
    lda lfsr+1
    sta n_hi
    jsr step
    lda lfsr
    sta d_lo
    lda lfsr+1
    sta d_hi
    lda ctr                      ; shift divisor right by (ctr & 15): small divisors too
    and #15
    tax
    beq @noshift
@sh:
    lsr d_hi
    ror d_lo
    dex
    bne @sh
@noshift:
    lda ctr                      ; odd cases enter with N=V=D=Z=C=1
    and #1
    beq @even
    lda #$CB
    bra @setp
@even:
    lda #$04                     ; I=1
@setp:
    sta pflag
    lda n_lo
    sta ma_div16u_num
    lda n_hi
    sta ma_div16u_num+1
    lda d_lo
    sta ma_div16u_den
    lda d_hi
    sta ma_div16u_den+1
    lda pflag
    pha
    ldy #$5A
    plp
    jsr ma_div16u
    php
    pla
    sta retp
    cpy #$5A
    jne fail
    cld
    jsr check
    lda retp
    and #$CF                     ; returned N/V/D/I/Z/C must equal the caller's
    cmp pflag
    jne fail
    tsx
    cpx #$FF                     ; stack balanced
    jne fail
    inc RESULT+4
    bne @count
    inc RESULT+5
    bne @count
    inc RESULT+6
@count:
    inc ctr
    jne @rand
    inc ctr+1
    jne @rand
    tii done_text, RESULT, 4
halt:
    bra halt

; check: verifies the last ma_div16u result against n (n_lo/n_hi) and d (d_lo/d_hi).
check:
    lda ma_div16u_den
    cmp d_lo
    jne fail
    lda ma_div16u_den+1
    cmp d_hi
    jne fail
    lda ma_div16u_den
    ora ma_div16u_den+1
    bne @nz
    lda ma_div16u_num            ; divisor 0: quotient $FFFF, remainder = dividend
    and ma_div16u_num+1
    cmp #$FF
    jne fail
    lda ma_div16u_rem
    cmp n_lo
    jne fail
    lda ma_div16u_rem+1
    cmp n_hi
    jne fail
    rts
@nz:
    lda ma_div16u_rem            ; remainder < divisor (C=1 after the compare means >=)
    cmp ma_div16u_den
    lda ma_div16u_rem+1
    sbc ma_div16u_den+1
    jcs fail
    stz p
    stz p+1
    stz p+2
    stz p+3
    lda ma_div16u_den
    sta m
    lda ma_div16u_den+1
    sta m+1
    stz m+2
    stz m+3
    lda ma_div16u_num
    sta qq
    lda ma_div16u_num+1
    sta qq+1
    ldx #16
@mul:
    lsr qq+1                     ; C = old quotient bit 0 after the pair of shifts
    ror qq
    bcc @shift
    clc
    lda p
    adc m
    sta p
    lda p+1
    adc m+1
    sta p+1
    lda p+2
    adc m+2
    sta p+2
    lda p+3
    adc m+3
    sta p+3
@shift:
    asl m
    rol m+1
    rol m+2
    rol m+3
    dex
    bne @mul
    clc
    lda p
    adc ma_div16u_rem
    sta p
    lda p+1
    adc ma_div16u_rem+1
    sta p+1
    lda p+2
    adc #0
    sta p+2
    lda p+3
    adc #0
    sta p+3
    lda p
    cmp n_lo
    jne fail
    lda p+1
    cmp n_hi
    jne fail
    lda p+2
    ora p+3
    jne fail
    rts

; Galois LFSR step, 16-bit, taps $B400: state >>= 1, xor $B400 when the old bit 0 was set.
step:
    lda lfsr
    and #1
    sta fb
    lsr lfsr+1
    ror lfsr
    lda fb
    beq @done
    lda lfsr+1
    eor #$B4
    sta lfsr+1
@done:
    rts

fail:
    tii fail_text, RESULT, 4
    lda n_lo
    sta RESULT+8
    lda n_hi
    sta RESULT+9
    lda d_lo
    sta RESULT+10
    lda d_hi
    sta RESULT+11
    jmp halt

done_text: .byte "DONE"
fail_text: .byte "FAIL"
; (n, d) edge pairs: divide-by-zero, max value, exact, non-exact, small and large divisors
edge_nlo: .byte $00, $01, $00, $01, $FF, $FF, $FF, $FE, $FF, $00, $FF, $00, $34, $FF, $00, $FF, $FF, $00, $01, $CD
edge_nhi: .byte $00, $00, $00, $00, $FF, $FF, $FF, $FF, $FF, $80, $7F, $10, $12, $FF, $01, $00, $FF, $80, $FF, $AB
edge_dlo: .byte $01, $01, $00, $00, $00, $01, $FF, $FF, $FE, $02, $02, $03, $00, $00, $FF, $00, $01, $00, $FF, $34
edge_dhi: .byte $00, $00, $00, $00, $00, $00, $FF, $FF, $FF, $00, $00, $00, $01, $01, $FF, $01, $80, $80, $FF, $12

.segment "VECTORS"
    .word halt, halt, halt, halt, reset
