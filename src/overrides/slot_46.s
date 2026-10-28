; MA_COS — slot $46. DEV ROM only (Issue #80). Stacked on MA_SIN (slot $45, Issue #79): the entry
; shares the MA_SIN table and routine, so this file carries no table of its own.
;
; Project-own routine, NOT the reference System Card MA_COS ABI (remains UNVERIFIED).
; Identity: cos(a) = sin(a + 64) for the 8-bit binary angle (256 steps = one turn, 64 steps = 90 degrees).
;
;   in : A = angle index (unsigned 8-bit, every value 0..255 valid). X is not read.
;   out: X:A = cos(2*pi*A/256) as signed Q15, exactly the value api_slot_45 returns for (A+64) & $FF.
;   kept: Y and P (P is restored here before the jump; MA_SIN saves and restores Y and P itself).
;   clobbered: A and X (the result).
;   uses: nothing beyond what api_slot_45 uses (zero page $F7, $F9, $FA; stack: php only, then the jump).
;   precondition: caller SP (before JSR) >= $06, same as api_slot_45. This wrapper pushes one byte
;                 transiently (php) and pops it before the jump, so the stack seen by api_slot_45 is
;                 identical to a direct call.
;   ABI remains UNVERIFIED and this ROM stays DEV ONLY (release_eligible: false).
;
; Depends on api_slot_45 (src/overrides/slot_45.s). Building this slot without slot $45 fails at link.
;
.setcpu "huc6280"
.segment "OVERRIDES"
.import api_slot_45
.export api_slot_46
api_slot_46:
    php                 ; save caller's P: the add below changes C, Z, N and V
    cld                 ; binary add (the sum is an angle, not BCD)
    clc
    adc #64             ; A = (angle + 64) & $FF, a quarter turn ahead
    plp                 ; P = caller's P again; PLP does not touch A
    jmp api_slot_45     ; MA_SIN returns X:A to our caller and restores Y and P
