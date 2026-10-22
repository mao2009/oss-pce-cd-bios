; Synthetic caller-stack contract fixture (Issue #473). Test-only HuCARD, NOT a BIOS, no reference material.
; One build per case (ca65 -D):
;   CASE_RAM  contract honoured: MPR1 = WRAM ($F8) when the caller JSRs api_guard.
;   CASE_IO   contract violated:  the caller sets MPR1 = $FF (hardware page) before the JSR.
;   CASE_IRQ  block transfer: TII (8 bytes) from ROM to WRAM $0220; the host asserts IRQ1 mid-transfer.
; Every label exists in every build, so the probe label file has the same names for all cases.
; WRAM is mapped at $0000-$3FFF (MPR0 and MPR1 = $F8), so the guard flag is at $0210 and the stack is $0100-$01FF.
.setcpu "huc6280"
.export reset, pre_jsr, post_return, api_guard, guard_check, guard_rts, tii_start, tii_after
.export irq1_entry, src_data

.segment "CODE"
reset:
    sei
    cld
    lda #$F8
    tam #$01             ; MPR0 = WRAM
    tam #$02             ; MPR1 = WRAM (the stack page $2100-$21FF lives in MPR1)
    ldx #$FF
    txs                  ; caller S = $FF
.ifdef CASE_IRQ
    cli                  ; I=0 so IRQ1 can be taken, but only when the transfer has ended
    jmp tii_start
.endif
.ifdef CASE_IO
    lda #$FF
    tam #$02             ; contract violation: MPR1 = $FF (hardware page) before the JSR
.endif
    lda #$11
    ldx #$22
    ldy #$33
    sec
    sed                  ; D=1 and C=1, so the P that must come back is not trivial
pre_jsr:
    jsr api_guard
post_return:
    bra post_return

tii_start:               ; TII src, dst, len: executed only in CASE_IRQ
    .byte $73
    .word src_data
    .word $0220
    .word 8
tii_after:
    bra tii_after

; Test guard: PHP, PHA, TMA #$02 (A = MPR1), compare with the RAM value $F8, record the verdict at $0210,
; then restore A and P and return. The caller contract is that MPR0 is WRAM, so the store to $0210 is in RAM.
api_guard:
    php
    pha
    tma #$02
guard_check:
    cmp #$F8
    bne @deny
    lda #$01
    bra @store
@deny:
    lda #$00
@store:
    sta $0210
    pla
    plp
guard_rts:
    rts

irq2_entry:
timer_entry:
nmi_entry:
irq1_entry:
    rti

src_data:
    .byte $A1, $A2, $A3, $A4, $A5, $A6, $A7, $A8

.segment "VECTORS"      ; $FFF6 IRQ2/BRK, $FFF8 IRQ1, $FFFA TIMER, $FFFC NMI (not used by the pinned core), $FFFE RESET
    .word irq2_entry
    .word irq1_entry
    .word timer_entry
    .word nmi_entry
    .word reset
