; EX_RND — slot $3E, candidate entry $E0BA. DEV ROM only (Issue #72).
;
; Project-own routine, NOT the reference System Card EX_RND ABI (remains UNVERIFIED).
; One entry, two operations selected by Y:
;   Y = 1 : seed.   in X:A = seed (X high, A low). No output.
;   Y <> 1: next.   out A = random byte. (Y=0 is the documented next selector.)
; State: 16-bit Galois LFSR, 2 bytes of work RAM at $2000 (low) and $2001 (high).
;   Step: shift right; if the bit shifted out was 1, XOR the high byte with $B4 (taps $B400).
;   next runs 8 steps per call and returns the low byte of the state.
;   A zero state (seed 0, or zeroed RAM) is replaced by 1 before stepping, so seed 0 == seed 1.
;   Power-on RAM is not reset here: call the seed operation before the first next.
;   Same seed -> same output sequence every time (period 65535 over non-zero states).
; Preserved: X and Y in both operations. Output flags: N/Z reflect A after next; C is
;   modified by the shifts (unspecified); D and I are not changed.
; Stack: next pushes X (1 byte) plus the JSR return; seed uses no stack.
; Uses: work RAM $2000-$2001 plus the stack page (next pushes X). No I/O.
;   MPR1 must map work RAM ($F8); the routine does not check this.
; Real-core evidence: tests/integration/geargrafx_dev_ex_rnd.py (pinned Geargrafx, DEV ROM).
; The host model tests/host/test_ex_rnd_dev.py checks the contract only, not these bytes.
; ABI remains UNVERIFIED and this ROM stays DEV ONLY (release_eligible: false).
.setcpu "huc6280"
.segment "OVERRIDES"
.export api_slot_3E
api_slot_3E:
    cpy #1
    beq @seed
    phx                      ; X preserved
    lda $2000
    ora $2001                ; Z = (state == 0)
    bne @live
    lda #1
    sta $2000                ; zero state -> 1
@live:
    ldx #8
@step:
    lsr $2001                ; 16-bit shift right; C = old bit 0 of the low byte
    ror $2000
    bcc @nofb
    lda $2001
    eor #$B4                 ; taps $B400 (high byte)
    sta $2001
@nofb:
    dex
    bne @step
    plx                      ; PLX sets N/Z from X, so the output load comes after it
    nop                      ; layout parity: makes the OVERRIDES end odd so the even-rounded trampoline == body end (MUL16S window)
    lda $2000                ; A = output byte; N/Z from A
    rts
@seed:
    sta $2000
    stx $2001
    rts
