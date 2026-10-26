; MA_SIN — slot $45, candidate entry $E0CF. DEV ROM only (Issue #79).
;
; Project-own routine, NOT the reference System Card MA_SIN ABI (remains UNVERIFIED).
; DEV-only contract:
;   angle unit : binary angle, 256 units per full turn (1 unit = 360/256 degrees).
;                Every 8-bit value 0..255 is valid; the angle is taken modulo 256.
;   in         : A = angle. X and Y are not inputs.
;   out        : A = sin(angle) as signed 8-bit fixed point, scale 127:
;                value = trunc(127 * sin(2*pi*angle/256)), range -127..+127.
;                Angles 0..127 give 0..+127 (positive half-turn), 128..255 give
;                0..-127 (negative half-turn). -128 is never produced.
;   kept       : X, Y and P (caller's values restored: X by PLX, P by PLP).
;   clobbered  : A (the result).
;   uses       : zero page $F7-$F8 only. No I/O, no MPR access, I is never changed.
;   stack      : JSR pushes 2 bytes, then php and phx push 2 more: the lowest
;                write is caller_SP - 3. Precondition: caller SP (before JSR) >= $04,
;                so no stack write wraps below $0100. Balanced on return.
;   ABI remains UNVERIFIED and this ROM stays DEV ONLY (release_eligible: false).
;
; Method: quarter-wave table T[q] = trunc(127 * sin(pi/2 * q / 64)), q = 0..64.
;   quadrant = angle >> 6 (bits 7-6), r = angle & 63
;   quadrant 0: +T[r]      quadrant 1: +T[64 - r]
;   quadrant 2: -T[r]      quadrant 3: -T[64 - r]
;   (T[64 - r] = cos(pi/2 * r / 64), so the odd quadrants are the mirror of the even ones.)
;
; Zero-page layout:
;   $F7  angle    (input, kept for the sign test)
;   $F8  r        (angle & 63)
;
.setcpu "huc6280"
.segment "OVERRIDES"
.export api_slot_45
api_slot_45:
    php                 ; save caller's P
    cld                 ; binary mode (table lookup and negation are plain binary)
    phx                 ; save caller's X
    sta $F7             ; angle
    and #$3F            ; A = r = angle & 63
    sta $F8             ; r
    lda $F7
    and #$40            ; odd quadrant (1 or 3) when bit 6 is set
    beq use_r
    lda #64
    sec
    sbc $F8             ; A = 64 - r  (1..64)
    bra have_index
use_r:
    lda $F8             ; A = r  (0..63)
have_index:
    tax
    lda sin_quarter,x   ; A = T[index], 0..127
    bit $F7             ; N = angle bit 7 (negative half-turn)
    bpl done
    eor #$FF            ; two's complement negation: A = -T[index]
    inc a
done:
    plx                 ; restore caller's X
    plp                 ; restore caller's P
    rts

; T[q] = trunc(127 * sin(pi/2 * q / 64)), q = 0..64 (65 bytes, generated and checked by
; tests/host/test_ma_sin_dev.py against an independent math.sin oracle).
sin_quarter:
    .byte   0,  3,  6,  9, 12, 15, 18, 21, 24, 27, 30, 33, 36
    .byte  39, 42, 45, 48, 51, 54, 57, 59, 62, 65, 67, 70, 73
    .byte  75, 78, 80, 82, 85, 87, 89, 91, 94, 96, 98,100,102
    .byte 103,105,107,108,110,112,113,114,116,117,118,119,120
    .byte 121,122,123,123,124,125,125,126,126,126,126,126,127
