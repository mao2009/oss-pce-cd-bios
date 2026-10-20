; Original HuCARD probe for src/ex_rnd.s (Issue #72). No System Card code, no BIOS JSR.
; Phase 1: state zeroed explicitly, 256 outputs -> $2300+i (zero state is replaced by 1).
; Phase 2: ex_rnd_seed($34, $12), 256 outputs -> $2400+i.
; Phase 3: ex_rnd_seed($00, $00), 256 outputs -> $2500+i (seed 0 behaves as state 1).
; Every call enters with X = $A5; a changed X increments the counter at $2204.
; "RND!" is written to $2200 last as the completion marker.
.setcpu "huc6280"
.import ex_rnd_seed, ex_rnd_next, ex_rnd_state
.export probe_reset

.macro rnd_phase base
    ldy #0
@loop:
    ldx #$a5
    jsr ex_rnd_next
    sta base,y
    cpx #$a5
    beq @ok
    inc $2204
@ok:
    iny
    bne @loop
.endmacro

.segment "RESET"
probe_reset:
    sei
    cld
    csh
    lda #$ff
    tam #$01                 ; MPR0: I/O
    lda #$f8
    tam #$02                 ; MPR1: working RAM ($2000-$3fff), stack page $2100
    ldx #$ff
    txs
    stz $2204
    stz ex_rnd_state
    stz ex_rnd_state+1
    rnd_phase $2300
    lda #$34
    ldx #$12
    jsr ex_rnd_seed
    rnd_phase $2400
    lda #0
    tax
    jsr ex_rnd_seed
    rnd_phase $2500
    tii marker, $2200, 4
probe_halt:
    bra probe_halt
marker:
    .byte "RND!"
.segment "VECTORS"
    .word probe_halt, probe_halt, probe_halt, probe_halt, probe_reset
