; DEVELOPMENT-ONLY boot and driver for the reusable adt_dma_sector routine (Issue #300).
; Project-owned HuC6280 code. NOT BIOS, NOT AD_TRANS ($11), NOT a System Card ABI, release_eligible=false.
; Runs four calls of the same routine: positive, negative (DMA never armed), recovery positive, bad input,
; then call E (invalid slot 4, after the others): the routine must write code 9 to slot 0, overwriting A.
; Result slots: 0 = call E (after E), 8 = call B, 16 = call C, 24 = call D (see 撤去済み文書).
; Call A's 8-byte record is copied to $2320 right after A succeeds, so A stays observable after E.
.setcpu "huc6280"
.import adt_dma_sector
.export cd_entry, cd_halt_ok, cd_halt_fail, cd_unexpected_interrupt

PB          = $2200
PB_LBA_HI   = PB+0
PB_LBA_MID  = PB+1
PB_LBA_LO   = PB+2
PB_DST_LO   = PB+3
PB_DST_HI   = PB+4
PB_ARM      = PB+5
PB_SLOT     = PB+6
RES         = $2300

; Load the parameter block, call the routine. Carry from the call is left for the caller.
.macro ADT_CALL lba_hi, lba_mid, lba_lo, dst_lo, dst_hi, arm, slot
    lda #lba_hi
    sta PB_LBA_HI
    lda #lba_mid
    sta PB_LBA_MID
    lda #lba_lo
    sta PB_LBA_LO
    lda #dst_lo
    sta PB_DST_LO
    lda #dst_hi
    sta PB_DST_HI
    lda #arm
    sta PB_ARM
    lda #slot
    sta PB_SLOT
    jsr adt_dma_sector
.endmacro

.segment "BOOT"

cd_entry:
    sei
    cld
    csh
    lda #$FF
    tam #$01                 ; MPR0: hardware IO
    lda #$F8
    tam #$02                 ; MPR1: work RAM (CPU stack included)
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
    tam #$80                 ; MPR7: ROM bank 0 (this code)
    ldx #$FF
    txs
    lda #$00                 ; clear the result area
    ldx #$20
@clear:
    sta RES-1,x
    dex
    bne @clear

    ; A: positive reference. LBA 5, ADPCM RAM $0100, DMA armed. Must succeed.
    ADT_CALL 0, 0, 5, $00, $01, 1, 0
    bcc @a_ok
    jmp cd_halt_fail
@a_ok:
    ldx #7                   ; snapshot A's record (slot 0) to $2320, outside the routine's slots
@snap:
    lda RES,x
    sta RES+$20,x
    dex
    bpl @snap
    ; B: negative. LBA 5, ADPCM RAM $0900, DMA never armed. Expect code 11, bus recovered.
    ADT_CALL 0, 0, 5, $00, $09, 0, 8
    ; C: recovery. Same as A at $1100 after B. Expect success: the bus is usable again.
    ADT_CALL 0, 0, 5, $00, $11, 1, 16
    ; D: bad input. LBA $200000 is outside the 21-bit READ(6) range. Expect code 9, no bus activity.
    ADT_CALL $20, 0, 5, $00, $15, 1, 24
    ; E: invalid slot 4 (not 0/8/16/24), otherwise valid. Expect code 9 in slot 0 (A's record is gone from slot 0).
    ADT_CALL 0, 0, 5, $00, $15, 1, 4
cd_halt_ok:
    bra cd_halt_ok

cd_halt_fail:
    bra cd_halt_fail

cd_unexpected_interrupt:
    ldx #$EE                 ; an interrupt is NOT success
@halt:
    bra @halt

.segment "VECTORS"
    .word cd_unexpected_interrupt ; IRQ2 / BRK
    .word cd_unexpected_interrupt ; IRQ1
    .word cd_unexpected_interrupt ; Timer
    .word cd_unexpected_interrupt ; NMI
    .word cd_entry                ; Reset
