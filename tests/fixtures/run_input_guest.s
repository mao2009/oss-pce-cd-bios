; Synthetic HuCARD RUN-input fixture.  Test-only, not a BIOS and not reference code.
; MPR0 maps the controller I/O page, and this guest reads the RUN bit through $1000.
; Its only persistent output is the $2200-$220F fixture-owned WRAM result block.
.setcpu "huc6280"

.segment "CODE"
reset:
    sei
    cld
    lda #$F8
    tam #$02                 ; MPR1: fixture result block at logical $2200
    lda #$FF
    tam #$01                 ; MPR0: controller I/O at logical $1000
    ; Keep initialization explicit: this also makes each byte of the externally
    ; reported block fixture-initialized, rather than relying on power-on RAM.
    stz $2200
    stz $2201
    stz $2202
    stz $2203
    stz $2204
    stz $2205
    stz $2206
    stz $2207
    stz $2208
    stz $2209
    stz $220A
    stz $220B
    stz $220C
    stz $220D
    stz $220E
    stz $220F

; Each pass reads the button nibble (SEL=0), where RUN is active-low bit 3.
; Result layout: total polls u32, current/previous state, released/pressed polls u32,
; released/pressed transitions u8.  State is 1 when RUN is pressed.
@poll:
    lda #$00
    sta $1000
    lda $1000
    and #$08
    beq @pressed
@released:
    lda #$00
    sta $2204
    inc $2206
    bne @release_edge
    inc $2207
    bne @release_edge
    inc $2208
    bne @release_edge
    inc $2209
@release_edge:
    lda $2205
    beq @finish
    inc $220F
    bra @finish
@pressed:
    lda #$01
    sta $2204
    inc $220A
    bne @press_edge
    inc $220B
    bne @press_edge
    inc $220C
    bne @press_edge
    inc $220D
@press_edge:
    lda $2205
    bne @finish
    inc $220E
@finish:
    lda $2204
    sta $2205
    inc $2200
    bne @poll
    inc $2201
    bne @poll
    inc $2202
    bne @poll
    inc $2203
    bra @poll

irq2_entry:
irq1_entry:
timer_entry:
nmi_entry:
    rti

.segment "VECTORS"
    .word irq2_entry
    .word irq1_entry
    .word timer_entry
    .word nmi_entry
    .word reset
