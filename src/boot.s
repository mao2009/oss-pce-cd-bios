; DEV-ONLY reset marker, not a boot implementation.
; Reset MPR7=0/vector geometry: spec/rom-contract.json. MPR0..6 are unspecified.
; Does not establish BIOS stack, RAM, interrupt or API preservation contracts.
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
