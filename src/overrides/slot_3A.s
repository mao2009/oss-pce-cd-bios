; EX_SETWRT — slot $3A, candidate entry $E0AE. DEV ROM only.
;
; Project-own contract (Issue #68), NOT the reference System Card EX_SETWRT ABI. Reference contract is UNCONFIRMED;
; this ROM stays DEV ONLY (release_eligible: false).
; in : A = VRAM word address bits 7-0, X = bits 15-8. Valid range $0000..$7FFF (32K-word VRAM).
; ok : X < $80 -> VDC MAWR (register 0) = X:A, C=0. A, X, Y, MPR0 preserved. I/D/T flags preserved.
; ng : X >= $80 -> C=1, no I/O access, nothing modified (A, X, Y unchanged).
; VDC side effects (ok path only): ST0 selects register 0 (MAWR), ST1 = low byte, ST2 = high byte.
; VRAM and the VDC data port are NOT written. Zero page, BIOS work RAM and MPR1-7 are NOT used.
; MPR0 is set to $FF (I/O page) for a short window with IRQs masked (SEI) and restored before return.
; Stack: 5 bytes below the JMP target (P, Y, X, A, saved MPR0). No VBlank wait (timing not constrained).
; N/Z after return are unspecified.
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
    pha                       ; caller A (low byte), kept for ST1
    tma #$01                  ; A = caller MPR0
    pha
    lda #$FF
    tam #$01                  ; MPR0 = $FF: VDC ports visible at $0000-$0003
    stz $0000                 ; ST0: select VDC register 0 (MAWR)
    tsx
    lda $0102,x               ; caller A (low byte)
    sta $0002                 ; ST1: MAWR low
    lda $0103,x               ; caller X (high byte)
    sta $0003                 ; ST2: MAWR high
    pla                       ; caller MPR0
    tam #$01                  ; restore MPR0
    pla                       ; caller A
    plx                       ; caller X
    ply                       ; caller Y
    plp                       ; restores I/D/T and the caller carry
    clc                       ; PLP also restored C, so set the result after it
    rts
setwrt_reject:
    sec
    rts
