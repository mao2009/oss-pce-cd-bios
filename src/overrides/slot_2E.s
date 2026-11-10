; EX_BGON — slot $2E, candidate entry $E08A. DEV ROM only (Issue #56).
;
; Project-own routine, NOT the reference System Card EX_BGON ABI (remains UNVERIFIED).
; Sets the HuC6270 background-blanking bit (CR R05, bit 7: 1 = display background)
; and preserves every other CR bit. Public hardware facts used (no BIOS code):
; NEC HuC6270 manual (R05 bit 7 = BB) and the pinned Geargrafx hardware notes.
; Only the CR low byte is read and rewritten; the high byte is never touched.
;
;   in : nothing read (A/X/Y are not inputs).
;   ok : VDC CR low = old CR low | $80, CR high unchanged, C=0.
;        A, X, Y, MPRs preserved; N/Z/V/D/I/T = caller flags (C forced to 0).
;   side effects: ST0 selects VDC register 5; CR low rewritten. The new BB value
;        is effective from the next horizontal display period (hardware).
;        No zero-page, work-RAM, VRAM, joypad, CD, ADPCM or BURAM access.
;   window: MPR0 = $FF while touching the VDC ports at $0000/$0002, IRQs masked
;        (SEI); MPR0 restored before RTS. Same discipline as EX_SETWRT (slot $3A).
;        MPR1 (stack page $2100-$21FF) is never remapped. NMI assumed absent.
;   stack: max 5 bytes simultaneous below the JSR return (flags, Y, X, A, MPR0);
;        caller SP (before JSR) must be >= $07. SP=$06 or lower is out of contract.
;   ABI remains UNVERIFIED and this ROM stays DEV ONLY (release_eligible: false).
; Real-core evidence: tests/integration/geargrafx_dev_bgon.py (pinned Geargrafx, DEV ROM).
; The host model tests/host/test_api_cr_dev.py checks the contract only, not these bytes.
.setcpu "huc6280"
.segment "OVERRIDES"
.export api_slot_2E
api_slot_2E:
    php
    sei                       ; no IRQ while MPR0 points at the I/O page
    phy
    phx
    pha                       ; caller A (scratch below; restored on exit)
    tma #$01                  ; A = caller MPR0
    pha
    lda #$FF
    tam #$01                  ; MPR0 = $FF: VDC ports at $0000/$0002 (stack unaffected)
    lda #$05
    sta a:$0000               ; ST0: select VDC register 5 (CR); a: forces absolute form
    lda a:$0002               ; CR low (absolute form; zero-page form would hit $20xx)
    ora #$80                  ; set BB (background display)
    sta a:$0002               ; CR low = modified (ST1)
    pla
    tam #$01                  ; restore caller MPR0
    pla                       ; A = caller A
    plx                       ; X = caller X
    ply                       ; Y = caller Y
    plp                       ; restores caller flags incl. C
    clc                       ; contract result: C=0 (no other flag is changed here)
    nop                       ; layout pad: keeps the OVERRIDES segment end odd so the
                              ; MA_MUL16S real-core trampoline still lands at its PC
                              ; window (see tests/integration/geargrafx_dev_mul16s.py)
    rts
