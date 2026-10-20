; Issue #72: project-own EX_RND (PROJECT-OWN spec, NOT the System Card $3E / $E0BA ABI).
; Not wired to the ROM jump table; the reference $3E contract remains UNCONFIRMED.
; Contract (this project only, see docs/ex-rnd-internal.md):
;   state: 16-bit Galois LFSR in ex_rnd_state (2 bytes, BSS, low byte first), taps $B400
;   ex_rnd_seed: in A = seed low, X = seed high; stores state; no return value
;   ex_rnd_next: out A = low byte after 8 LFSR steps; X and Y preserved; D untouched
;     a zero state (power-on or seed 0) is replaced by 1 before stepping
;   stack: 2 bytes (PHX, JSR); no IO access; N/Z/V on return unspecified
.setcpu "huc6280"
.export ex_rnd_seed, ex_rnd_next, ex_rnd_state, ex_rnd_taps
.segment "BSS"
ex_rnd_state:
    .res 2
.segment "CODE"
ex_rnd_seed:
    sta ex_rnd_state
    stx ex_rnd_state+1
    rts
ex_rnd_next:
    phx
    lda ex_rnd_state
    ora ex_rnd_state+1
    bne rnd_live
    lda #1
    sta ex_rnd_state
rnd_live:
    ldx #8
rnd_step:
    lsr ex_rnd_state+1       ; 16-bit shift right; C = old bit 0 of the low byte
    ror ex_rnd_state
    bcc rnd_nofb
    lda ex_rnd_state+1
ex_rnd_taps:
    eor #$b4                 ; taps $B400 (high byte)
    sta ex_rnd_state+1
rnd_nofb:
    dex
    bne rnd_step
    lda ex_rnd_state
    plx
    rts
