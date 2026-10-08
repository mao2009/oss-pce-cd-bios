; Original CPU/memory experiments. These are NOT BIOS API implementations.
.setcpu "huc6280"
.segment "DIAGNOSTIC"
.export bank_probe, bank_halt, call_probe, call_before, call_entry, call_return
.export call_rts, restore_mpr, call_halt, irq_probe, irq_wait, irq_entry, irq_exit
.export ram_probe, ram_halt, mirror_read, mirror_restore, api_call_probe, api_jsr, api_call_return, diagnostic_end

.macro initialize
    sei
    cld
    csh
    lda #$FF
    tam #$01             ; explicit IO window, not an assumed reset default
    lda #$F8
    tam #$02             ; explicit first work RAM bank
    lda #$01
    tam #$04
    lda #$02
    tam #$08
    lda #$1F
    tam #$10
    lda #$03
    tam #$20
    lda #$04
    tam #$40
    lda #$00
    tam #$80
    ldx #$FF
    txs
    lda #$00
    ldx #$3F
@clear:
    sta $2200,x
    dex
    bpl @clear
.endmacro

bank_probe:
    initialize
    lda $4000
    sta $2200
    lda $5FFF
    sta $2201
    lda $6000
    sta $2202
    lda $7FFF
    sta $2203
    lda $8000
    sta $2204
    lda $9FFF
    sta $2205
    lda $A000
    sta $2206
    lda $BFFF
    sta $2207
    lda #$5A
    sta $2000
    lda #$A5
    sta $3FFF
    lda $2000
    sta $2208
    lda $3FFF
    sta $2209
    ; Guest changes MPR2 to bank2 and restores bank1.
    lda #$02
    tam #$04
    lda $4000
    sta $220A
    lda #$01
    tam #$04
    lda $4000
    sta $220B
    lda #$20
    tam #$04
mirror_read:
    lda $4000
    sta $220C
    lda $5FFF
    sta $220D
mirror_restore:
    lda #$01
    tam #$04
bank_halt:
    bra bank_halt

call_probe:
    initialize
    lda #$11
    ldx #$22
    ldy #$33
    sec
call_before:
    jsr call_entry
call_return:
    sta $2200
    stx $2201
    sty $2202
    tsx
    stx $2203
call_halt:
    bra call_halt

; Deliberately authored test procedure, not an enabled System Card API.
call_entry:
    php
    pha
    phx
    phy
    tma #$04
    pha
    lda #$03
    tam #$04
    lda $4000
    sta $2204
    pla
restore_mpr:
    tam #$04
    ply
    plx
    pla
    plp
call_rts:
    rts

irq_probe:
    initialize
    lda #$00
    sta $1402            ; this fixture explicitly unmasks interrupt sources
    lda #$44
    ldx #$55
    ldy #$66
    cli
irq_wait:
    bra irq_wait
irq_entry:
    pha
    lda #$77
    sta $2210
    pla
irq_exit:
    rti

ram_probe:
    initialize
    lda #$80
    tam #$04
    lda #$87
    tam #$08
    lda #$68
    tam #$10
    lda #$7F
    tam #$20
    lda #$81
    sta $4000
    lda #$8F
    sta $5FFF
    lda #$91
    sta $6000
    lda #$9F
    sta $7FFF
    lda #$61
    sta $8000
    lda #$6F
    sta $9FFF
    lda #$71
    sta $A000
    lda #$7F
    sta $BFFF
    lda $4000
    sta $2200
    lda $5FFF
    sta $2201
    lda $6000
    sta $2202
    lda $7FFF
    sta $2203
    lda $8000
    sta $2204
    lda $9FFF
    sta $2205
    lda $A000
    sta $2206
    lda $BFFF
    sta $2207
ram_halt:
    bra ram_halt

api_call_probe:
    initialize
    lda #$11
    ldx #$22
    ldy #$33
api_jsr:
    jsr $E003            ; test input, patched symbolically to three candidate entries
api_call_return:
    sta $2211            ; this continuation must NEVER execute for unimplemented slots
@unexpected_return:
    bra @unexpected_return

diagnostic_end:
