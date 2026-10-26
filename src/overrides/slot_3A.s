; EX_SETWRT — slot $3A, candidate entry $E0AE. DEV ROM only.
;
; Project-own contract (Issue #68), NOT the reference System Card EX_SETWRT ABI. Reference contract is UNCONFIRMED;
; this ROM stays DEV ONLY (release_eligible: false).
; in : A = VRAM word address bits 7-0, X = bits 15-8. Valid range $0000..$7FFF (32K-word VRAM).
; ok : X < $80 -> VDC MAWR (register 0) = X:A, C=0. A, X, Y, MPR1 preserved. I/D/T flags preserved.
; ng : X >= $80 -> C=1, no I/O access, nothing modified (A, X, Y unchanged).
; VDC side effects (ok path only): ST0 selects register 0 (MAWR), ST1 = low byte, ST2 = high byte.
; VRAM and the VDC data port are NOT written. Zero page, the stack (page 1, MPR0) and MPR0 are NOT remapped.
; Window: MPR1 is set to $FF so the VDC ports appear at $2000-$2003, and $2000-$3FFF (normally WRAM) is hidden
; for a few instructions with IRQs masked (SEI). This routine itself lives in OVERRIDES (MPR7), so it runs
; from mapped code during the window. Ceiling: assumes no NMI can occur (PCE has none in use); add a
; guard if one is ever enabled.
; Stack: 5 bytes below the JMP target (P, Y, X, A, saved MPR1). No VBlank wait (timing not constrained).
; N/Z after return are unspecified.
.setcpu "huc6280"
.segment "OVERRIDES"
.export api_slot_3A
api_slot_3A:
    cpx #$80
    bcs setwrt_reject         ; X >= $80: out of VRAM, C=1 with nothing touched
    php
    sei                       ; no IRQ while MPR1 points at the I/O page
    phy
    phx
    pha                       ; caller A (low byte)
    tma #$02                  ; A = caller MPR1
    pha
    lda #$FF
    tam #$02                  ; MPR1 = $FF: VDC ports at $2000-$2003
    stz $2000                 ; ST0: select VDC register 0 (MAWR)
    pla
    tay                       ; Y = caller MPR1 (stack is not remapped, so this pull is safe)
    pla                       ; A = caller A (low byte)
    sta $2002                 ; ST1: MAWR low
    plx                       ; X = caller X (high byte)
    stx $2003                 ; ST2: MAWR high
    pha                       ; keep A (low) while restoring MPR1
    tya
    tam #$02                  ; restore MPR1
    pla                       ; A = caller A
    ply                       ; Y = caller Y
    plp                       ; restores I/D/T and the caller carry
    clc                       ; PLP also restored C, so set the result after it
    rts
setwrt_reject:
    sec
    rts
