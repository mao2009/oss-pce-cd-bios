; Original MA_MUL8S test driver (Issue #74). HuCARD fixture, NOT a BIOS.
; Host protocol: write a to $2000 and a new request id to $2001; the driver
; calls ma_mul8s for b=0..255 and then copies the id to $2002 (done).
; Per b: $2200 low, $2300 high, $2400 Y after, $2500 S after, $2600 P after,
; $2700 P before. Before each call Y = b^$5A, C = bit 0 and D = bit 1 of b.
.setcpu "huc6280"
.import ma_mul8s
req_a = $2000
req_id = $2001
done_id = $2002
cur_b = $2003
tmp = $2004

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
    stz cur_b
row:
    lda cur_b
    eor #$5A
    tay
    lda cur_b
    and #$02
    beq @binary
    sed                  ; D = bit 1 of b: the callee must not compute in BCD
@binary:
    lda cur_b
    lsr a                ; C = bit 0 of b
    ldx cur_b
    lda req_a
    php
    jsr ma_mul8s
    php
    cld
    sta tmp
    stx tmp + 1
    sty tmp + 2
    tsx
    stx tmp + 3
    ldx cur_b
    lda tmp
    sta $2200,x
    lda tmp + 1
    sta $2300,x
    lda tmp + 2
    sta $2400,x
    lda tmp + 3
    sta $2500,x
    pla
    sta $2600,x
    pla
    sta $2700,x
    inc cur_b
    bne row
    lda req_id
    sta done_id
    bra idle
halt:
    bra halt

.segment "VECTORS"
.word halt, halt, halt, halt, reset
