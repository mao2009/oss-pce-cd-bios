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
; The window covers logical $0000-$1FFF. Evidence (src/huc6280.h of the pinned Geargrafx, non-GG_TESTING build):
; ZERO_PAGE_ADDR = $2000 and STACK_ADDR = $2100, both in MPR1, so zero page and stack are outside the window.
; The routine uses NO zero-page addressing: the three VDC accesses are written with a: (absolute) so ca65 does not
; choose the zero-page form, which would address $2000-$2003 instead of $0000-$0003. The routine is linked into
; OVERRIDES at $E300 (src/bank0.cfg), in MPR7, so instruction fetch is not affected by MPR0.
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
    stz a:$0000               ; ST0: select VDC register 0 (MAWR); a: forces absolute (not zero-page) form
    pla
    tay                       ; Y = caller MPR0
    pla                       ; A = caller A (low byte)
    sta a:$0002               ; ST1: MAWR low (absolute form; zero-page form would hit $20xx)
    plx                       ; X = caller X (high byte)
    stx a:$0003               ; ST2: MAWR high (absolute form)
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
