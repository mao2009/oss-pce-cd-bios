; Original DEVELOPMENT-ONLY System Card startup checkpoint for Issue #4.
; Not a production BIOS initialization, CD boot, or API service ABI.
; Mapping is a tested Geargrafx diagnostic configuration, not a historical System Card contract.
.setcpu "huc6280"
.segment "BOOT"
.export startup_entry, startup_mpr_ready, startup_stack_ready
.export startup_marker_write, startup_checkpoint, startup_unexpected_interrupt
startup_entry:
    sei
    cld
    csh
    lda #$FF
    tam #$01                 ; MPR0: hardware IO for this probe
    lda #$F8
    tam #$02                 ; MPR1: work RAM, including CPU stack
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
    tam #$80                 ; keep reset vectors in ROM bank 0
startup_mpr_ready:
    ldx #$FF
    txs
startup_stack_ready:
    lda #$42                 ; B
startup_marker_write:
    sta $2200
    lda #$4F                 ; O
    sta $2201
    sta $2202
    lda #$54                 ; T
    sta $2203
    lda #$21                 ; !
    sta $2204
    ldx #$B0                 ; startup-specific diagnostic ID, NOT a return value
startup_checkpoint:
    bra startup_checkpoint  ; bounded real CPU observation, no fabricated success
startup_unexpected_interrupt:
    ldx #$EE                 ; unexpected interrupt is NOT startup success
@halt:
    bra @halt

.segment "VECTORS"
    .word startup_unexpected_interrupt ; IRQ2 / BRK
    .word startup_unexpected_interrupt ; IRQ1
    .word startup_unexpected_interrupt ; Timer
    .word startup_unexpected_interrupt ; NMI
    .word startup_entry                ; Reset
