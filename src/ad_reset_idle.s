; ad_reset_idle - ADPCM reset device operation (Issue #295). Original code, internal routine only.
;
; THIS PROJECT'S OWN SPEC, NOT the System Card AD_RESET ABI (still BLOCKED_ABI).
;   in : A = CONTROL value to write; bit 7 is forced on (reset). $80 is the verified mode
;        (play bit 5 clear). MPR0 must map hardware I/O ($FF) so $180C/$180D are reachable.
;   out: idle after reset -> C=0, A=$00
;        still busy/playing after a bounded poll (256 reads of $180C) -> C=1,
;        A = status & $8D ($08 play, $04/$80 busy, $01 END IRQ). Failure is never reported as success.
;   preserved: X, Y, S, MPR0-7. Uses 2 bytes of stack (PHX/PHY). N/Z undefined.
; Device layer only (fixed Geargrafx b49ae82a). Not wired into the jump table or slot $10.
.setcpu "huc6280"
.segment "CODE"
.export ad_reset_idle
AD_STATUS  = $180C           ; read: ADPCM status (mask $8D = play | busy | END IRQ)
AD_CONTROL = $180D           ; write: CONTROL (bit 7 = reset, bit 5 = play)
ad_reset_idle:
    phx
    phy
    ora #$80
    sta AD_CONTROL
    ldx #0                   ; 256 polls, bounded
@poll:
    lda AD_STATUS
    and #$8D
    beq @idle
    dex
    bne @poll
    sec                      ; A = bits still set after the bounded wait
    bra @out
@idle:
    clc                      ; A = $00
@out:
    ply
    plx
    rts
