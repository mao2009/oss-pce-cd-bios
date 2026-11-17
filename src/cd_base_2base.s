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
;   Inputs (work RAM, read-only, preserved):
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
;   State (owned work RAM $2400-$2404 + $2408-$240A, MPR1=$F8):
;     $2400/$2401/$2402 recbase0 HI/MID/LO (big-endian; $03 link reads this).
;     $2408/$2409/$240A recbase1 HI/MID/LO (big-endian; failover reserve).
;     $2403 generation counter (INC on each success that updates >=1 base).
;     $2404 ST_LAST (0 success; errors below). $2405-$2407 preserved (untouched).
;   Computation:
;     - Decode _bh top bits; CURRENT ($C0) refused.
;     - Resolve LBA per mode (TRACK: selection track -> INDEX01; LBA: direct;
;       MSF: convert -150). Range HI < $20 required.
;     - Update recbase0 and/or recbase1 per _cl; NONE updates neither.
;     - ST_LAST=0 on success (any mode incl. NONE); error code otherwise.
;   Returns:
;     A=0 success, C=0, Z=1; A=error, C=1, Z=0 (N=bit7 of A, always 0 here).
;     X/Y/MPRs preserved; I/D/V/T preserved (PLP); SP balanced (4 pushes/4 pulls).
;     Error codes (EXPERIMENTAL, not genuine): $18 bad mode, $19 bad address,
;       $1A range over 21-bit, $15 bad track (non-BCD or not selection track).
;   Side effects: only the state above; inputs, RES $2300, BUF $2800, EXEC $3000,
;     MARK $3F00 and all MPRs untouched (no TAM, no I/O, no IRQ).
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

; Work-RAM inputs (caller-owned, read-only).
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

; Experimental error codes (not genuine BIOS codes).
ERR_MODE = $18
ERR_ADDR = $19
ERR_RANGE = $1A
ERR_TRACK = $15

cd_base_2base_entry:
    php
    pha
    phx
    phy
    cld
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
    ; --- _cl mode 0-3 ---
    lda IN_CL
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
    ; experimental_selection+7 = track (binary? loader stores binary track).
    ; Compare BCD input converted to binary vs selection track binary.
    ; Convert BCD _al to binary in X.
    lda IN_AL
    pha
    and #$F0
    lsr a
    lsr a
    lsr a
    lsr a
    sta $00F0
    asl $00F0
    asl $00F0
    asl $00F0
    sta $00F0
    ; $00F0 = hi*8? redo: hi*10 = hi*8+hi*2
    pla
    pha
    and #$F0
    lsr a
    lsr a
    lsr a
    lsr a
    sta $00F1
    lda $00F1
    asl a
    asl a
    asl a
    clc
    adc $00F1
    adc $00F1
    sta $00F1
    pla
    and #$0F
    clc
    adc $00F1
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
    ; $20F8/$20F9/$20FA big-endian HI/MID/LO; $20FF already 0
    lda IN_AL
    sta $00F2
    lda IN_AH
    sta $00F3
    lda IN_BL
    sta $00F4
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
    ; Convert BCD to binary: min, sec, frame
    ; min
    lda IN_AL
    pha
    and #$F0
    lsr a
    lsr a
    lsr a
    lsr a
    sta $00F5
    lda $00F5
    asl a
    asl a
    asl a
    clc
    adc $00F5
    adc $00F5
    sta $00F5
    pla
    and #$0F
    clc
    adc $00F5
    sta $00F5
    ; sec
    lda IN_AH
    pha
    and #$F0
    lsr a
    lsr a
    lsr a
    lsr a
    sta $00F6
    lda $00F6
    asl a
    asl a
    asl a
    clc
    adc $00F6
    adc $00F6
    sta $00F6
    pla
    and #$0F
    clc
    adc $00F6
    sta $00F6
    ; frame
    lda IN_BL
    pha
    and #$F0
    lsr a
    lsr a
    lsr a
    lsr a
    sta $00F7
    lda $00F7
    asl a
    asl a
    asl a
    clc
    adc $00F7
    adc $00F7
    sta $00F7
    pla
    and #$0F
    clc
    adc $00F7
    sta $00F7
    ; range: sec < 60, frame < 75
    lda $00F6
    cmp #60
    bcc @msf_sec_ok
    jmp @err_addr
@msf_sec_ok:
    lda $00F7
    cmp #75
    bcc @msf_fr_ok
    jmp @err_addr
@msf_fr_ok:
    ; total = ((min*60)+sec)*75+frame; 8-bit min (0-99) fits: min*60 <= 5940, +59 = 5999, *75 = 449925 (>24-bit? 449925 < 16777216 ok).
    ; Compute with 24-bit scratch $00F8-$00FA? Use $00F8/$00F9/$00FA (careful: overlaps IN_AL? IN_AL=$20F8 work RAM, scratch $00F8 is zero page, different (MPR0 zero page $2000? No, $00F8 is zero page, distinct from $20F8). Safe.
    ; min*60: min*64-min*4
    lda $00F5
    sta $00F8
    lda #$00
    sta $00F9
    sta $00FA
    ; *64
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
    ; -min*4: compute min*4 in $00FB-$00FD
    lda $00F5
    asl a
    asl a
    sta $00FB
    lda #$00
    sta $00FC
    sta $00FD
    ; subtract
    sec
    lda $00F8
    sbc $00FB
    sta $00F8
    lda $00F9
    sbc $00FC
    sta $00F9
    lda $00FA
    sbc $00FD
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
    ; *75 = *64 + *8 + *2 + *1
    ; save base in $00FB-$00FD
    lda $00F8
    sta $00FB
    lda $00F9
    sta $00FC
    lda $00FA
    sta $00FD
    ; *64
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
    ; + *8: base<<3
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
    ; +frame
    clc
    lda $00F8
    adc $00F7
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
    ; LBA HI/MID/LO = $00FA/$00F9/$00F8? Our $00F8=LO? Let's track: $00F8 low (frames), $00F9 mid, $00FA high. Yes.
    lda $00FA
    sta $00F2
    lda $00F9
    sta $00F3
    lda $00F8
    sta $00F4
    jmp @have_lba
@have_lba:
    ; $00F2=HI, $00F3=MID, $00F4=LO. Range HI < $20 already checked for LBA/MSF; TRACK uses INDEX01 (<$20 via loader validation).
    ; Update per _cl ($20FC): 0 BOTH, 1 FIRST, 2 SECOND, 3 NONE.
    lda IN_CL
    cmp #$00
    beq @upd_both
    cmp #$01
    beq @upd_first
    cmp #$02
    beq @upd_second
    ; $03 NONE: success without update (gen not incremented? keep: no gen inc for NONE).
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
    ply
    plx
    pla
    plp
    lda #$00
    clc
    rts
@err_mode:
    ply
    plx
    pla
    plp
    lda #ERR_MODE
    sta ST_LAST
    sec
    rts
@err_addr:
    ply
    plx
    pla
    plp
    lda #ERR_ADDR
    sta ST_LAST
    sec
    rts
@err_range:
    ply
    plx
    pla
    plp
    lda #ERR_RANGE
    sta ST_LAST
    sec
    rts
@err_track:
    ply
    plx
    pla
    plp
    lda #ERR_TRACK
    sta ST_LAST
    sec
    rts
