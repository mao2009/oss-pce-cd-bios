; Project-own guest driver for ad_reset_idle (Issue #295). Test fixture only: not a BIOS, not an ABI.
; Runs on the real CPU (guest code) inside the Geargrafx probe. Results go to a work-RAM block.
;   case 0 (pos): ADDRESS preset, CONTROL write $80 from idle.
;   case 1 (neg): playback started with CONTROL $20, then ad_reset_idle with $A0 (play bit kept).
; Each case record (32 bytes, base = RES + 32*case):
;   +0 A out  +1 C out (0/1)  +2 X after  +3 Y after  +4 S before  +5 S after  +6 status before call
;   +7 playing observed before call (case 1: 1 = yes)  +8..+15 MPR0-7 before  +16..+23 MPR0-7 after
; Done marker RES+$40 = $A5 after both cases; then the guest halts with BRA *.
.setcpu "huc6280"
.segment "CODE"
.import ad_reset_idle
.export ad_guest_entry
RES        = $2300           ; result block, CPU $2300 (MPR1 = work RAM)
AD_STATUS  = $180C
AD_CONTROL = $180D
AD_ADDR_LO = $1808
AD_ADDR_HI = $1809
PLAY_WAIT_OUTER = $40

.macro SNAP dst
    tma #$01
    sta dst+0
    tma #$02
    sta dst+1
    tma #$04
    sta dst+2
    tma #$08
    sta dst+3
    tma #$10
    sta dst+4
    tma #$20
    sta dst+5
    tma #$40
    sta dst+6
    tma #$80
    sta dst+7
.endmacro

; A = routine result, X/Y after the call, S and MPR snapshot compared by the judge.
.macro RECORD base
    sta base+0
    php
    pla
    and #$01
    sta base+1
    stx base+2
    sty base+3
    tsx
    stx base+5
    SNAP base+16
.endmacro

ad_guest_entry:
    sei
    cld
    ldx #$FF
    txs
    lda #$FF
    tam #$01                 ; MPR0 = hardware I/O (ADPCM at $1808-$180E)
    lda #$F8
    tam #$02                 ; MPR1 = work RAM ($2000-$3FFF), holds the stack and RES
    lda #$00
    sta RES+$40              ; done marker cleared

; --- case 0: reset from idle ---
    lda #$34
    sta AD_ADDR_LO
    lda #$12
    sta AD_ADDR_HI           ; preset ADDRESS (internal state; not bus-readable)
    lda #0
    sta RES+7
    tsx
    stx RES+4                ; S before
    lda AD_STATUS
    sta RES+6                ; status before call
    SNAP RES+8
    lda #$80
    ldx #$A5
    ldy #$3C
    jsr ad_reset_idle
    RECORD RES

; --- case 1: reset written while playing (play bit kept in the written value) ---
    lda #0
    sta RES+32+7
    lda #$20
    sta AD_CONTROL           ; start playback
    ldy #PLAY_WAIT_OUTER
@wait_out:
    ldx #0
@wait:
    lda AD_STATUS
    and #$08
    bne @playing
    dex
    bne @wait
    dey
    bne @wait_out
    bra @armed
@playing:
    lda #1
    sta RES+32+7
@armed:
    tsx
    stx RES+32+4
    lda AD_STATUS
    sta RES+32+6
    SNAP RES+32+8
    lda #$A0
    ldx #$A5
    ldy #$3C
    jsr ad_reset_idle
    RECORD RES+32

    lda #$A5
    sta RES+$40
@halt:
    bra @halt
