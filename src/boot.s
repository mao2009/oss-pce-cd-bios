; DEV-ONLY reset marker, not a boot implementation.
.setcpu "huc6280"
.segment "BOOT"
reset_not_implemented:
    sei
    ldx #$FF             ; X=$FF distinguishes reset from an API call
@loop:
    bra @loop            ; Never report successful boot

.segment "VECTORS"
.word reset_not_implemented  ; IRQ2/BRK
.word reset_not_implemented  ; IRQ1/VDC
.word reset_not_implemented  ; Timer
.word reset_not_implemented  ; NMI
.word reset_not_implemented  ; Reset
