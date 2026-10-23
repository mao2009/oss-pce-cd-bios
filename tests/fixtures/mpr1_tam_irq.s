; Synthetic MPR1 + IRQ1 fixture (Issue #498). Test-only HuCARD, NOT a BIOS, no reference material.
; One build per case (ca65 -D). The probe asserts IRQ1 before the tam_site step (see the driver).
;   CASE_TAM_IRQ     N-IRQ-TAM: I=0, A=$FF, TAM #$02 at tam_site. IRQ1 is pending when the TAM starts.
;   CASE_TAM_NOIRQ   control: I=0, A=$FF, TAM #$02, no IRQ1 asserted.
;   CASE_TAM_MASKED  control: I=1 (SEI kept), A=$FF, TAM #$02, IRQ1 asserted but masked.
;   CASE_TAM_F8      control: I=0, A=$F8 (MPR1 stays WRAM), TAM #$02, IRQ1 asserted (normal path).
.setcpu "huc6280"
.export reset, tam_site, after_tam, irq1_entry

.segment "CODE"
reset:
    sei
    cld
    clc                  ; C and V differed between runs (P = $81 / $C0 in the same case): fixed for a reproducible P record
    clv
    lda #$F8
    tam #$01             ; MPR0 = WRAM
    tam #$02             ; MPR1 = WRAM (the stack page $2100-$21FF lives in MPR1)
    ldx #$FF
    txs                  ; S = $FF
.ifndef CASE_TAM_MASKED
    cli                  ; I = 0: a pending IRQ1 is latched at the start of the next instruction
.endif
.ifdef CASE_TAM_F8
    lda #$F8
.else
    lda #$FF
.endif
tam_site:
    tam #$02             ; MPR1 = A (the write under test)
after_tam:
    bra after_tam

irq2_entry:
timer_entry:
nmi_entry:
irq1_entry:
    rti

.segment "VECTORS"      ; $FFF6 IRQ2/BRK, $FFF8 IRQ1, $FFFA TIMER, $FFFC NMI (not used by the pinned core), $FFFE RESET
    .word irq2_entry
    .word irq1_entry
    .word timer_entry
    .word nmi_entry
    .word reset
