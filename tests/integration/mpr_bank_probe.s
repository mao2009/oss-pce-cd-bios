; Issue #670 Phase 1: synthetic MPR / bank measurement ROM (DEV only, no commercial data, no reference BIOS).
; Writes distinct values through MPR2..MPR3 to banks $80..$87 and $88, reads them back, and stores
; results in work RAM $2800..$281F (dumped by the probe). Runs forever at `halt` so the probe reports budget.
.setcpu "huc6280"

RES = $2800          ; results (probe dumps $2800-$3FFF)
CUR = $3F00          ; scratch: current bank id

.segment "CODE"
reset:
    sei
    cld
    lda #$FF
    tam #$01                 ; MPR0 = hardware I/O
    lda #$F8
    tam #$02                 ; MPR1 = work RAM $2000-$3FFF (results live here)
    ldx #$FF
    txs

    ; ---- pass 1: write distinct values to banks $80..$87 through MPR2 ----
    ldy #0
w_loop:
    tya
    clc
    adc #$80
    sta CUR
    tam #$04                 ; MPR2 = bank (logical $4000-$5FFF)
    lda CUR
    eor #$5A
    sta $4000
    lda CUR
    eor #$A5
    sta $5FFF
    iny
    cpy #8
    bne w_loop

    ; ---- pass 2: read back each bank; record TMA readback of MPR2 ----
    ldy #0
r_loop:
    tya
    clc
    adc #$80
    sta CUR
    tam #$04
    tma #$04                 ; A = MPR2 as read back
    sta RES,y                ; RES + 0..7 : MPR2 readback per bank index
    lda $4000
    sta RES+8,y              ; $4000 value per bank index
    lda $5FFF
    sta RES+16,y             ; $5FFF value per bank index
    iny
    cpy #8
    bne r_loop

    ; ---- boundary: write $5FFF in bank $80 and $6000 in bank $81 (MPR3) ----
    lda #$80
    tam #$04
    lda #$11
    sta $5FFF
    lda #$81
    tam #$08                 ; MPR3 = bank $81 (logical $6000-$7FFF)
    lda #$22
    sta $6000
    ; read back: $5FFF through bank $80 and $6000 through bank $81
    lda #$80
    tam #$04
    lda $5FFF
    sta RES+24               ; expect $11
    lda #$81
    tam #$08
    lda $6000
    sta RES+25               ; expect $22
    tma #$08
    sta RES+26               ; MPR3 readback, expect $81

    ; ---- alias: write $4002 in bank $88, then read $4002 through bank $80 and back through $88 ----
    lda #$88
    tam #$04
    lda #$33
    sta $4002
    lda #$80
    tam #$04
    lda $4002
    sta RES+27               ; bank $80 view after bank $88 write (independence / alias check)
    lda #$88
    tam #$04
    lda $4002
    sta RES+28               ; bank $88 view (expect $33)
    tma #$04
    sta RES+29               ; MPR2 readback when bank $88 selected

    ; ---- sentinel for "halted normally" ----
    lda #$C3
    sta RES+31

cd_halt_ok:
    bra cd_halt_ok

cd_halt_fail:
    bra cd_halt_fail

cd_unexpected_interrupt:
    bra cd_unexpected_interrupt

irq_stub:
    jmp cd_unexpected_interrupt

.segment "VECT"
.word irq_stub, irq_stub, irq_stub, irq_stub, reset
