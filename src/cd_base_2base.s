; EXPERIMENTAL DEV-ONLY CD_BASE 2-base variant for slot $02 (G5 P0).
; NOT a compatible System Card BIOS. NOT the genuine CD_BASE ABI. NOT release eligible.
; NOT DEV_CALLABLE as a formal BIOS service. Experimental processing only, linked
; exclusively into the G5 experimental image when selected (separate variant from
; src/cd_base_proto.s, which remains unchanged for synthetic media).
;
; EXPERIMENTAL contract CD_BASE_2BASE_V1 (synthetic + opt-in commercial observation only):
;   Entry: JSR $E006 (slot $02) tail-jumps here (JMP), RTS returns to the caller.
;   Preconditions: MPR1=$F8 (work RAM $2000-$3FFF readable), stack in work RAM,
;     IRQs masked by the caller, experimental_selection (track + 21-bit INDEX01 LBA
;     of the first data track, from the loader XSEL record) available.
;   Inputs (work RAM, read-only OBSERVABLE NET-ZERO: saved on entry, restored on
;   every exit; no caller/game byte is observably modified):
;     $20FB (_bh) top two bits: $00 LBA, $40 MSF, $80 TRACK, $C0 CURRENT (reject).
;     TRACK: $20F8 (_al) BCD track (01-99). $20F9/$20FA/$20FF must be $00.
;       Only the selection track (first data track) resolves to INDEX01; other
;       BCD tracks are refused (fail-closed, no TOC lookup).
;     LBA: $20F8/$20F9/$20FA big-endian 24-bit LBA (HI/MID/LO). Must be < $200000
;       (21-bit READ(6) range). $20FF must be $00.
;     MSF: $20F8 min BCD, $20F9 sec BCD, $20FA frame BCD. Ranges: sec<60, frame<75.
;       LBA = ((min*60)+sec)*75+frame-150. Negative (before lead-in) refused.
;       $20FF must be $00.
;     $20FC (_cl) update mode: 0 BOTH, 1 FIRST (recbase0), 2 SECOND (recbase1),
;       3 NONE (success without update). Values >3 refused.
;     $20FD (_ch) must be $00 (reserved high; Zeograd _ch skip-bits are NOT used;
;       commercial + HuCC S1 + Stum + llvm support _cl, so _cl wins with grounds).
;     $20FE/$20FF (_dl/_dh) must be $00 (unused).
;   Owned state (MPR1=$F8 work RAM):
;     $2400/$2401/$2402 recbase0 HI/MID/LO (big-endian; $03 link reads this).
;     $2408/$2409/$240A recbase1 HI/MID/LO (big-endian; failover reserve).
;     $2403 generation counter (INC on each success that updates >=1 base).
;     $2404 ST_LAST (0 success; errors below).
;     $2405/$2406/$2407 preserved (untouched, never scratch).
;     $240B magic (0xA5 when seeded) + $240C valid flags (bit0 recbase0,
;       bit1 recbase1): first-use bootstrap seeds both bases to INDEX01 when
;       invalid, so ST_LAST==0 always implies a usable recbase0 (A-6). Seed does
;       not touch ST_GEN/ST_LAST.
;   Harness aliasing (G5/Geargrafx, MPR0=$FF/MPR1=$F8): zero page $00F0-$00FF
;     is cell-identical with WRAM $20F0-$20FF (verified F0-FD; last write
;     wins). The routine is written so this is harmless: every $20Fx input
;     byte is read for the last time BEFORE any same-index $00Fx scratch
;     write ($20F8-$20FD reads end before min*64/min*4; $20F0-$20F7 and
;     $20F2-$20F4 are never read except by the entry saves), and both
;     restores carry the same saved values with INPUTS last, so the aliased
;     cells always end at pre-call input values. SAVE_ZP/RESTORE_ZP is
;     defense-in-depth for real hardware (independent ZP); in-harness it is
;     unobservable, which is why no ZP-canary RED exists here (see tests).
;   Computation:
;     - On entry with invalid seed, seed both bases to INDEX01 first.
;     - Decode _bh top bits; CURRENT ($C0) refused.
;     - Resolve LBA per mode (TRACK: selection track -> INDEX01; LBA: direct;
;       MSF: 16-bit-safe convert -150). Range HI < $20 required.
;     - Update per snapshot _cl; NONE updates neither.
;     - ST_LAST=0 on success (any mode incl. NONE); error code otherwise.
;   Returns:
;     A=0 success, C=0, Z=1; A=error, C=1, Z=0 (N=bit7 of A, always 0 here).
;     X/Y/MPRs preserved; I/D/V/T preserved (PLP); SP balanced.
;     Error codes (EXPERIMENTAL, not genuine): $18 bad mode, $19 bad address,
;     $1A range over 21-bit, $15 bad track (non-BCD or not selection track).
;   Side effects: only the state above; inputs restored, RES $2300, BUF $2800,
;     EXEC $3000, MARK $3F00 and all MPRs untouched (no TAM, no I/O, no IRQ).
;     $20F2-$20F4 (old link block) preserved.
;
; Genuine ABI status: UNKNOWN/BLOCKED. This file never claims VERIFIED, DEV_CALLABLE,
; or compatibility. Reference JSR 0/81, ABI VERIFIED 0/81, DEV_CALLABLE 0,
; release_eligible=false. Resolves _cl vs _ch in favor of _cl with grounds
; (HuCC S1 sets _cl=2, Stum _CL=1/2 examples, llvm BASE_SET 0-3, commercial _cl 1->2
; with _ch constant 0); Zeograd _ch skip-bits recorded as UNKNOWN/mislabeled.
.setcpu "huc6280"
.segment "BOOT"
.export cd_base_2base_entry
.import experimental_selection

; Work-RAM inputs (caller-owned, read-only; saved/restored across the call).
IN_AL = $20F8
IN_AH = $20F9
IN_BL = $20FA
IN_BH = $20FB
IN_CL = $20FC
IN_CH = $20FD
IN_DL = $20FE
IN_DH = $20FF

; Owned state (MPR1=$F8 work RAM).
RB0_HI = $2400
RB0_MID = $2401
RB0_LO = $2402
ST_GEN = $2403
ST_LAST = $2404
RB1_HI = $2408
RB1_MID = $2409
RB1_LO = $240A
ST_MAGIC = $240B
ST_VALID = $240C
SEED_MAGIC = $A5
VALID_BOTH = $03

; Experimental error codes (not genuine BIOS codes).
ERR_MODE = $18
ERR_ADDR = $19
ERR_RANGE = $1A
ERR_TRACK = $15

; Save/restore the caller input window $20F0-$20FF (16 bytes). The order is
; fixed ($20F0 first) so every exit path restores identically. Zero-page
; scratch $00F0-$00FD is saved/restored separately (SAVE_ZP/RESTORE_ZP).
.macro SAVE_INPUTS
    lda $20F0
    pha
    lda $20F1
    pha
    lda $20F2
    pha
    lda $20F3
    pha
    lda $20F4
    pha
    lda $20F5
    pha
    lda $20F6
    pha
    lda $20F7
    pha
    lda $20F8
    pha
    lda $20F9
    pha
    lda $20FA
    pha
    lda $20FB
    pha
    lda $20FC
    pha
    lda $20FD
    pha
    lda $20FE
    pha
    lda $20FF
    pha
.endmacro

.macro RESTORE_INPUTS
    pla
    sta $20FF
    pla
    sta $20FE
    pla
    sta $20FD
    pla
    sta $20FC
    pla
    sta $20FB
    pla
    sta $20FA
    pla
    sta $20F9
    pla
    sta $20F8
    pla
    sta $20F7
    pla
    sta $20F6
    pla
    sta $20F5
    pla
    sta $20F4
    pla
    sta $20F3
    pla
    sta $20F2
    pla
    sta $20F1
    pla
    sta $20F0
.endmacro

; Save/restore the zero-page scratch window $00F0-$00FD (14 bytes) used by
; BCD conversion and 16/24-bit math below. Inline X-indexed loops keep the
; 8 KiB bank0 budget (~10 bytes per site); a jsr-subroutine form is WRONG
; here (a save-sub would have to rts past its own pushes). A/X/flags are
; scratch here (caller regs pushed before SAVE_ZP runs; exits set A/C
; explicitly after the restores; Y snapshot kept in Y throughout).
.macro SAVE_ZP
    ldx #$00
@svS: lda $00F0,x
    pha
    inx
    cpx #$0E
    bne @svS
.endmacro


cd_base_2base_entry:
    php
    pha
    phx
    phy
    ; Snapshot _cl now: later scratch must never change what @have_lba sees.
    lda IN_CL
    tay
    SAVE_INPUTS
    SAVE_ZP
    ; --- first-use seed: both bases = INDEX01 while VALID incomplete ---
    lda ST_MAGIC
    cmp #SEED_MAGIC
    bne @seed
    lda ST_VALID
    cmp #VALID_BOTH
    beq @seeded
@seed:
    lda experimental_selection+8
    sta RB0_HI
    lda experimental_selection+9
    sta RB0_MID
    lda experimental_selection+10
    sta RB0_LO
    lda experimental_selection+8
    sta RB1_HI
    lda experimental_selection+9
    sta RB1_MID
    lda experimental_selection+10
    sta RB1_LO
    lda #SEED_MAGIC
    sta ST_MAGIC
    lda #VALID_BOTH
    sta ST_VALID
@seeded:
    ; --- _ch must be $00 (reserved high; _cl carries the mode) ---
    lda IN_CH
    beq @ok_ch
    jmp @err_mode
@ok_ch:
    ; --- _dl/_dh must be $00 (unused) ---
    lda IN_DL
    beq @ok_dl
    jmp @err_mode
@ok_dl:
    lda IN_DH
    beq @ok_dh
    jmp @err_mode
@ok_dh:
    ; --- _cl mode 0-3 (snapshot in Y; scratch below must not matter) ---
    tya
    cmp #$04
    bcc @ok_mode
    jmp @err_mode
@ok_mode:
    ; --- decode _bh top bits ---
    lda IN_BH
    and #$C0
    cmp #$80
    bne @not_track
    jmp @mode_track
@not_track:
    cmp #$00
    bne @not_lba
    jmp @mode_lba
@not_lba:
    cmp #$40
    bne @not_msf
    jmp @mode_msf
@not_msf:
    ; $C0 CURRENT: refused (no current-position support)
    jmp @err_addr
@mode_track:
    ; _ah/_bl must be $00; _dh already 0
    lda IN_AH
    beq @trk_ah0
    jmp @err_addr
@trk_ah0:
    lda IN_BL
    beq @trk_bl0
    jmp @err_addr
@trk_bl0:
    ; BCD check + range 01-99
    lda IN_AL
    pha
    and #$F0
    cmp #$A0
    pla
    bcc @trk_hi_ok
    jmp @err_track
@trk_hi_ok:
    lda IN_AL
    and #$0F
    cmp #$0A
    bcc @trk_lo_ok
    jmp @err_track
@trk_lo_ok:
    lda IN_AL
    cmp #$01
    bcs @trk_ge1
    jmp @err_track
@trk_ge1:
    cmp #$99
    bcc @trk_le99
    beq @trk_le99
    jmp @err_track
@trk_le99:
    ; Only the selection track resolves (first data track -> INDEX01).
    ; Convert BCD _al to binary in X (hi*10+lo); X restored via plx at exit.
    ; (Y holds the _cl snapshot here and must not be touched.)
    lda IN_AL
    pha
    and #$F0
    lsr a
    lsr a
    lsr a
    lsr a
    sta $00F0
    asl a
    asl a
    asl a
    clc
    adc $00F0
    adc $00F0
    sta $00F0
    pla
    and #$0F
    clc
    adc $00F0
    tax
    ; X = binary track; compare to selection track
    lda experimental_selection+7
    stx $00F1
    cmp $00F1
    beq @trk_is_sel
    jmp @err_track
@trk_is_sel:
    ; LBA = INDEX01 (experimental_selection+8/9/10 HI/MID/LO)
    lda experimental_selection+8
    sta $00F2
    lda experimental_selection+9
    sta $00F3
    lda experimental_selection+10
    sta $00F4
    jmp @have_lba
@mode_lba:
    ; $20F8/$20F9/$20FA big-endian HI/MID/LO; $20FF already 0.
    ; Copy via stack (no static temps): push HI/MID/LO, pop to LBA homes.
    lda IN_AL
    pha
    lda IN_AH
    pha
    lda IN_BL
    pha
    pla
    sta $00F4
    pla
    sta $00F3
    pla
    sta $00F2
    ; HI < $20 (21-bit)
    lda $00F2
    cmp #$20
    bcc @lba_ok
    jmp @err_range
@lba_ok:
    jmp @have_lba
@mode_msf:
    ; BCD checks: min 00-99, sec 00-59, frame 00-74; $20FF already 0
    lda IN_AL
    pha
    and #$F0
    cmp #$A0
    pla
    bcc @msf_min_hi_ok
    jmp @err_addr
@msf_min_hi_ok:
    lda IN_AL
    and #$0F
    cmp #$0A
    bcc @msf_min_lo_ok
    jmp @err_addr
@msf_min_lo_ok:
    lda IN_AH
    pha
    and #$F0
    cmp #$60
    pla
    bcc @msf_sec_hi_ok
    jmp @err_addr
@msf_sec_hi_ok:
    lda IN_AH
    and #$0F
    cmp #$0A
    bcc @msf_sec_lo_ok
    jmp @err_addr
@msf_sec_lo_ok:
    lda IN_BL
    pha
    and #$F0
    cmp #$80
    pla
    bcc @msf_fr_hi_ok
    jmp @err_addr
@msf_fr_hi_ok:
    lda IN_BL
    and #$0F
    cmp #$0A
    bcc @msf_fr_lo_ok
    jmp @err_addr
@msf_fr_lo_ok:
    ; Convert BCD to binary in $00F5(min)/$00F6(sec); frame pushed on stack.
    ; Re-reads of IN_AL/AH/BL are safe: each $00Fx temp is written only
    ; after the input byte it derives from was last read, and the whole
    ; $00F0-$00FD window is restored before return (SAVE_ZP/RESTORE_ZP).
    ; min = hi*10+lo
    lda IN_AL
    and #$F0
    lsr a
    lsr a
    lsr a
    lsr a
    sta $00F5
    asl a
    asl a
    asl a
    clc
    adc $00F5
    adc $00F5
    sta $00F5
    lda IN_AL
    and #$0F
    clc
    adc $00F5
    tax
    ; sec = hi*10+lo
    lda IN_AH
    and #$F0
    lsr a
    lsr a
    lsr a
    lsr a
    sta $00F6
    asl a
    asl a
    asl a
    clc
    adc $00F6
    adc $00F6
    sta $00F6
    lda IN_AH
    and #$0F
    clc
    adc $00F6
    sta $00F6
    ; frame = hi*10+lo, then pushed (kept clear of multiply temps)
    lda IN_BL
    and #$F0
    lsr a
    lsr a
    lsr a
    lsr a
    sta $00F7
    asl a
    asl a
    asl a
    clc
    adc $00F7
    adc $00F7
    sta $00F7
    lda IN_BL
    and #$0F
    clc
    adc $00F7
    pha
    ; range: sec < 60
    lda $00F6
    cmp #60
    bcc @msf_sec_ok
    pla
    jmp @err_addr
@msf_sec_ok:
    ; frame < 75
    pla
    cmp #75
    pha
    bcc @msf_fr_ok
    pla
    jmp @err_addr
@msf_fr_ok:
    ; total = ((min*60)+sec)*75+frame; min in X, sec in $00F6,
    ; frame on stack. min*60 = min*64-min*4, all 24-bit explicit.
    ; min*64 into $00F8(LO)/$00F9(MID)/$00FA(HI=0).
    txa
    sta $00F8
    lda #$00
    sta $00F9
    sta $00FA
    asl $00F8
    rol $00F9
    rol $00FA
    asl $00F8
    rol $00F9
    rol $00FA
    asl $00F8
    rol $00F9
    rol $00FA
    asl $00F8
    rol $00F9
    rol $00FA
    asl $00F8
    rol $00F9
    rol $00FA
    asl $00F8
    rol $00F9
    rol $00FA
    ; min*4 16-bit in $00FB(lo)/$00FC(hi): shift the 16-bit value twice.
    txa
    sta $00FB
    lda #$00
    sta $00FC
    asl $00FB
    rol $00FC
    asl $00FB
    rol $00FC
    ; total -= min*4
    sec
    lda $00F8
    sbc $00FB
    sta $00F8
    lda $00F9
    sbc $00FC
    sta $00F9
    lda $00FA
    sbc #$00
    sta $00FA
    ; +sec
    clc
    lda $00F8
    adc $00F6
    sta $00F8
    lda $00F9
    adc #$00
    sta $00F9
    lda $00FA
    adc #$00
    sta $00FA
    ; *75 = *64 + *8 + *2 + *1. Save base in $00FB(lo)/$00FC(mid)/$00FD(hi).
    lda $00F8
    sta $00FB
    lda $00F9
    sta $00FC
    lda $00FA
    sta $00FD
    ; *64 (in place, 24-bit)
    asl $00F8
    rol $00F9
    rol $00FA
    asl $00F8
    rol $00F9
    rol $00FA
    asl $00F8
    rol $00F9
    rol $00FA
    asl $00F8
    rol $00F9
    rol $00FA
    asl $00F8
    rol $00F9
    rol $00FA
    asl $00F8
    rol $00F9
    rol $00FA
    ; + *8: base<<3 into $00F5(lo)/$00F6(mid)/$00F7(hi)
    lda $00FB
    asl a
    sta $00F5
    lda $00FC
    rol a
    sta $00F6
    lda $00FD
    rol a
    sta $00F7
    asl $00F5
    rol $00F6
    rol $00F7
    asl $00F5
    rol $00F6
    rol $00F7
    clc
    lda $00F8
    adc $00F5
    sta $00F8
    lda $00F9
    adc $00F6
    sta $00F9
    lda $00FA
    adc $00F7
    sta $00FA
    ; + *2: base<<1
    lda $00FB
    asl a
    sta $00F5
    lda $00FC
    rol a
    sta $00F6
    lda $00FD
    rol a
    sta $00F7
    clc
    lda $00F8
    adc $00F5
    sta $00F8
    lda $00F9
    adc $00F6
    sta $00F9
    lda $00FA
    adc $00F7
    sta $00FA
    ; + *1: base
    clc
    lda $00F8
    adc $00FB
    sta $00F8
    lda $00F9
    adc $00FC
    sta $00F9
    lda $00FA
    adc $00FD
    sta $00FA
    ; +frame (stacked)
    pla
    clc
    adc $00F8
    sta $00F8
    lda $00F9
    adc #$00
    sta $00F9
    lda $00FA
    adc #$00
    sta $00FA
    ; -150 (lead-in)
    sec
    lda $00F8
    sbc #150
    sta $00F8
    lda $00F9
    sbc #$00
    sta $00F9
    lda $00FA
    sbc #$00
    sta $00FA
    bcs @msf_nonneg
    jmp @err_addr
@msf_nonneg:
    ; HI < $20
    lda $00FA
    cmp #$20
    bcc @msf_ok
    jmp @err_range
@msf_ok:
    ; LBA HI/MID/LO = $00FA/$00F9/$00F8
    lda $00FA
    sta $00F2
    lda $00F9
    sta $00F3
    lda $00F8
    sta $00F4
    jmp @have_lba
@have_lba:
    ; $00F2=HI, $00F3=MID, $00F4=LO. Range HI < $20 already checked for LBA/MSF; TRACK uses INDEX01 (<$20 via loader validation).
    ; Update per snapshot _cl in Y: 0 BOTH, 1 FIRST, 2 SECOND, 3 NONE.
    tya
    cmp #$00
    beq @upd_both
    cmp #$01
    beq @upd_first
    cmp #$02
    beq @upd_second
    ; $03 NONE: success without update (gen not incremented).
    jmp @success_none
@upd_both:
    lda $00F2
    sta RB0_HI
    lda $00F3
    sta RB0_MID
    lda $00F4
    sta RB0_LO
    lda $00F2
    sta RB1_HI
    lda $00F3
    sta RB1_MID
    lda $00F4
    sta RB1_LO
    inc ST_GEN
    jmp @success
@upd_first:
    lda $00F2
    sta RB0_HI
    lda $00F3
    sta RB0_MID
    lda $00F4
    sta RB0_LO
    inc ST_GEN
    jmp @success
@upd_second:
    lda $00F2
    sta RB1_HI
    lda $00F3
    sta RB1_MID
    lda $00F4
    sta RB1_LO
    inc ST_GEN
    jmp @success
@success_none:
    lda #$00
    sta ST_LAST
    ldx #$0D
@rs0: pla
    sta $00F0,x
    dex
    bpl @rs0
    RESTORE_INPUTS
    ply
    plx
    pla
    plp
    lda #$00
    clc
    rts
@success:
    lda #$00
    sta ST_LAST
    ldx #$0D
@rs1: pla
    sta $00F0,x
    dex
    bpl @rs1
    RESTORE_INPUTS
    ply
    plx
    pla
    plp
    lda #$00
    clc
    rts
@err_mode:
    lda #ERR_MODE
    sta ST_LAST
    ldx #$0D
@rs2: pla
    sta $00F0,x
    dex
    bpl @rs2
    RESTORE_INPUTS
    ply
    plx
    pla
    plp
    lda #ERR_MODE
    sec
    rts
@err_addr:
    lda #ERR_ADDR
    sta ST_LAST
    ldx #$0D
@rs3: pla
    sta $00F0,x
    dex
    bpl @rs3
    RESTORE_INPUTS
    ply
    plx
    pla
    plp
    lda #ERR_ADDR
    sec
    rts
@err_range:
    lda #ERR_RANGE
    sta ST_LAST
    ldx #$0D
@rs4: pla
    sta $00F0,x
    dex
    bpl @rs4
    RESTORE_INPUTS
    ply
    plx
    pla
    plp
    lda #ERR_RANGE
    sec
    rts
@err_track:
    lda #ERR_TRACK
    sta ST_LAST
    ldx #$0D
@rs5: pla
    sta $00F0,x
    dex
    bpl @rs5
    RESTORE_INPUTS
    ply
    plx
    pla
    plp
    lda #ERR_TRACK
    sec
    rts
