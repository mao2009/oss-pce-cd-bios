; EX_SETWRT — slot $3A, candidate entry $E0AE. DEV ROM only.
;
; Project-own contract (Issue #68), NOT the reference System Card EX_SETWRT ABI. Reference contract is UNCONFIRMED;
; this ROM stays DEV ONLY (release_eligible: false).
; in : A = VRAM word address bits 7-0, X = bits 15-8. Valid range $0000..$7FFF (32K-word VRAM).
; ok : X < $80 -> VDC MAWR (register 0) = X:A, C=0. A, X, Y, MPR0 preserved. I/D/T flags preserved.
; ng : X >= $80 -> C=1, no I/O access, nothing modified (A, X, Y unchanged).
; VDC side effects (ok path only): ST0 selects register 0 (MAWR), ST1 = low byte, ST2 = high byte.
; VRAM and the VDC data port are NOT written. MPR1 (the stack page $2100-$21FF) is never remapped.
; Window: MPR0 is set to $FF so the VDC ports appear at $0000-$0003 for a few instructions, with IRQs masked (SEI).
; The window covers logical $0000-$1FFF. It does not cover the stack ($2100-$21FF, MPR1) or the zero page at $2000
; (the pinned core uses ZERO_PAGE_ADDR = $2000; see docs/497-mpr1-stack-contract.md). Stack instructions inside the
; window are therefore safe. This routine runs from OVERRIDES (MPR7), so instruction fetch is unaffected.
; Ceiling: NMI is assumed not to occur (PCE has no NMI source in use). Not observed on a real core.
; Stack: 5 bytes below the JMP target (P, Y, X, A, saved MPR0). No VBlank wait (timing not constrained).
; ok path: N/Z/V restored to entry values by PLP. ng path: N/Z from CPX (not a contract).
.setcpu "huc6280"
.segment "OVERRIDES"
.export api_slot_3A
api_slot_3A:
    cpx #$80
    bcs setwrt_reject         ; X >= $80: out of VRAM, C=1 with nothing touched
    php
    sei                       ; no IRQ while MPR0 points at the I/O page
    phy
    phx
    pha                       ; caller A (low byte)
    tma #$01                  ; A = caller MPR0
    pha
    lda #$FF
    tam #$01                  ; MPR0 = $FF: VDC ports at $0000-$0003 (stack in MPR1 is unaffected)
    stz $0000                 ; ST0: select VDC register 0 (MAWR)
    pla
    tay                       ; Y = caller MPR0
    pla                       ; A = caller A (low byte)
    sta $0002                 ; ST1: MAWR low
    plx                       ; X = caller X (high byte)
    stx $0003                 ; ST2: MAWR high
    pha                       ; keep A (low) while restoring MPR0
    tya
    tam #$01                  ; restore MPR0
    pla                       ; A = caller A
    ply                       ; Y = caller Y
    plp                       ; restores I/D/T and the caller carry
    clc                       ; PLP also restored C, so set the result after it
    rts
setwrt_reject:
    sec
    rts
