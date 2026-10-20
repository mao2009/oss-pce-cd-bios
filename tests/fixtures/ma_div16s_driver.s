; Original MA_DIV16S test driver (Issue #76). HuCARD fixture, NOT a BIOS.
; Host protocol: fill the input tables at $2200 (dividend low), $2300 (dividend high),
; $2400 (divisor low), $2500 (divisor high) for entries 0..255, then write a new request id to
; $2000. The driver runs ma_div16s for every entry and copies the id to $2001 (done).
; Outputs per entry: $2600 quotient low, $2700 quotient high, $2800 remainder low,
; $2900 remainder high, $2A00 P after the call.
.setcpu "huc6280"
.import ma_div16s
req_id = $2000
done_id = $2001

.segment "RESET"
reset:
    sei
    cld
    csh
    lda #$FF
    tam #$01
    lda #$F8
    tam #$02
    ldx #$FF
    txs
    stz req_id
    stz done_id
idle:
    lda req_id
    cmp done_id
    beq idle
    ldy #$00
row:
    lda $2400,y          ; divisor low pushed first
    pha
    lda $2500,y          ; divisor high on top
    pha
    lda $2200,y          ; dividend low -> A
    ldx $2300,y          ; dividend high -> X
    jsr ma_div16s
    sta $2600,y
    stx $2700,y
    php
    pla
    sta $2A00,y
    pla                  ; remainder high (top)
    sta $2900,y
    pla                  ; remainder low
    sta $2800,y
    iny                  ; Y is preserved by ma_div16s
    bne row
    lda req_id
    sta done_id
    bra idle
halt:
    bra halt

.segment "VECTORS"
.word halt, halt, halt, halt, reset
