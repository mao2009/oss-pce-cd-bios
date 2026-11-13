; EX_SCRSIZ — slot $23 (35), DEV ROM only.
;
; DEV_EXPERIMENTAL project contract, NOT the reference System Card EX_SCRSIZ ABI.
; Genuine ABI (inputs/outputs, register/memory side effects, timing) is UNKNOWN/UNVERIFIED.
; release_eligible: false.
;
; DEV contract (real VDC I/O processing):
;   - Programs VDC MWR (register $09) low byte from A via MPR0 I/O window (real VDC write).
;     X is preserved but not written to MWR high byte (HuC6270 MWR writable mask 0x00FF).
;   - Performs real VDC register I/O: AR=$09, data low=A, data high=$00.
;   - VDC AR left at $09. A,X,Y preserved. MPR0 preserved. IRQs masked during window.
;   - No work-RAM writes. Side effect: real VDC MWR state change (low byte).
;   - Precondition (DEV): caller responsible for valid MWR value; no VBlank wait performed.
;
; Stack: max 5 bytes below JSR target (P,Y,X,A,saved MPR0) transient; balanced.
.setcpu "huc6280"
.segment "OVERRIDES"
.export api_slot_23
api_slot_23:
    php
    sei                 ; mask IRQs while MPR0 = I/O
    phy
    phx
    pha                 ; save A (low)
    tma #$01
    pha                 ; save caller MPR0
    lda #$FF
    tam #$01            ; MPR0 -> I/O
    lda #$09
    sta a:$0000         ; select VDC register 9 (MWR)
    pla
    tay                 ; Y = saved MPR0
    pla                 ; A = caller A (low)
    sta a:$0002         ; MWR low
    stz a:$0003         ; MWR high (unwritable; explicit $00)
    pha                 ; keep A
    tya
    tam #$01            ; restore MPR0
    pla                 ; restore A
    plx                 ; restore X
    ply                 ; restore Y
    plp
    rts
