; ex_bcdbin - packed BCD byte to binary (Issue #71). Original code, internal routine only.
;
; THIS PROJECT'S OWN SPEC (docs/ex-bcdbin.md), NOT the reference BIOS EX_BCDBIN ABI:
;   in : A = one packed BCD byte, 2 digits, high nibble = tens, low nibble = ones ($00-$99)
;        D flag must be 0 (ADC is used)
;   out: valid   -> C=0, A = binary 0-99
;        invalid (either nibble > 9) -> C=1, A = the input byte unchanged
;        X, Y preserved; no memory other than the stack is touched; N/Z/V undefined
; Not connected to the API jump table; slot $3D / $E0B7 remain stubs.
.setcpu "huc6280"
.segment "CODE"
.export ex_bcdbin
ex_bcdbin:
    phx
    phy
    tay                  ; Y = input, kept for the error return
    and #$0F
    cmp #10
    bcs @bad             ; ones digit > 9
    tya
    cmp #$A0
    bcs @bad             ; tens digit > 9
    lsr
    lsr
    lsr
    lsr
    tax                  ; X = tens (0-9)
    tya
    and #$0F             ; A = ones
    clc                  ; sum stays <= 99, so every ADC leaves C=0
@tens:
    dex
    bmi @done
    adc #10
    bra @tens
@done:
    ply
    plx
    rts                  ; C=0
@bad:
    tya                  ; C=1 from BCS; TYA keeps it
    ply
    plx
    rts
