; EXPERIMENTAL DEV-ONLY CD_BASE prototype for slot $02 (Issue Agent-A G5 P0).
; NOT a compatible System Card BIOS. NOT the genuine CD_BASE ABI. NOT release eligible.
; NOT DEV_CALLABLE as a formal BIOS service. Experimental processing only, linked
; exclusively into the experimental BIOS image (tools/build_experimental_bios_boot.py).
; Normal DEV ROM never links this file (it lives outside src/overrides/).
;
; EXPERIMENTAL contract CD_BASE_PROTO_V1 (synthetic media only, pinned core only):
;   Entry: JSR $E006 (slot $02) tail-jumps here (JMP), RTS returns to the caller.
;   Preconditions: MPR1=$F8 (work RAM $2000-$3FFF readable), stack in work RAM,
;     IRQs masked by the caller (same discipline as the experimental loader),
;     state area $2400-$2407 zeroed before the first call by the test harness,
;     inputs in work RAM $20F8-$20FF set by the caller before JSR.
;   Inputs (work RAM, read-only, preserved):
;     $20F8 track selector, must equal the selection track (experimental_selection+7,
;       1..99 for synthetic media). Mirrors HuCC caller S1 _al usage (ledger S1).
;       Genuine meaning UNKNOWN; this equality check is EXPERIMENTAL.
;     $20FB flags, must be $80. Mirrors S1 _bh=$80. Genuine meaning UNKNOWN.
;     $20F9/$20FA/$20FF reserved, must be $00. Genuine meaning UNKNOWN; the
;       strict zero check is EXPERIMENTAL to prove input sensitivity.
;     $20FC/$20FD/$20FE base LO/MID/HI (24-bit little-endian absolute LBA
;       candidate). Layout is EXPERIMENTAL; genuine block/base layout UNKNOWN.
;       Ledger hypothesis for CD_READ is _cl:_ch:_dl high:mid:low at the same
;       addresses, but CD_BASE mapping is UNKNOWN, so this file defines its own
;       order explicitly and never claims it matches the genuine ABI.
;   INDEX01: 21-bit absolute LBA from the host-derived selection record
;     experimental_selection+8 (HI, low 5 bits), +9 (MID), +10 (LO).
;     Synthetic-only host contract, not a guest TOC. Genuine track/index
;     handling UNKNOWN.
;   Computation (REAL processing, not a bare RTS):
;     - 24-bit range: base HI >= $20 (base > $1FFFFF, beyond READ(6) 21-bit)
;       is refused (mirrors host fits_read6_lba, experimental only).
;     - 24-bit compare base vs INDEX01 (HI/MID/LO):
;       base > INDEX01 or ==  -> DATA (success).
;       base < INDEX01        -> PREGAP vs BEFORE distinction using virtual
;         PREGAP LEAD_IN=150 (host model virtual_pregap_start): if INDEX01>=150,
;         pregap_start=INDEX01-150 (24-bit subtract); base>=pregap_start is
;         PREGAP, base<pregap_start is BEFORE. If INDEX01<150 there is no
;         virtual pregap and any base<INDEX01 is BEFORE.
;     - On DATA: offset=base-INDEX01 (24-bit subtract), stored with base.
;   State (owned work RAM $2400-$2407, MPR1=$F8):
;     $2400/$2401/$2402 stored base HI/MID/LO (big-endian to match selection
;       order; updated only on success, preserved on error).
;     $2403 generation counter (INC on each success, wraps $FF->$00).
;     $2404 last status (A code of the last call; $FF means never called,
;       set by the harness before the first call).
;     $2405/$2406/$2407 last offset HI/MID/LO (base-INDEX01 on success,
;       cleared to 0 on error).
;     $2408/$2409/$240A scratch pregap_start LO/MID/HI (transient, written
;       only on the base<INDEX01 and INDEX01>=150 path; otherwise untouched).
;   Returns:
;     A=0 success (DATA), C=0, Z=1; A=error, C=1, Z=0 (N=bit7 of A).
;     X/Y/MPRs preserved; I/D/V/T preserved (PLP restore, only C/Z/N change
;     via LDA/CLC/SEC); SP balanced (4 pushes/4 pulls on every path).
;     Error codes (EXPERIMENTAL, not genuine): $10 track, $11 flags,
;       $12 reserved, $21 over 21-bit, $30 pregap, $31 before_track.
;   Side effects: only the state above; inputs, RES $2300, BUF $2800, EXEC
;     $3000, MARK $3F00 and all MPRs untouched (no TAM, no I/O, no IRQ).
;
; Genuine ABI status: UNKNOWN/BLOCKED for (1)-(6) per docs/cd-base-abi-ledger.md.
; This file proves REAL guest arithmetic/state handling on the pinned core for
; synthetic media; it never claims VERIFIED, DEV_CALLABLE, or compatibility.
; Reference JSR 0/81, ABI VERIFIED 0/81, DEV_CALLABLE 0, release_eligible=false.
.setcpu "huc6280"
.segment "BOOT"
.export cd_base_proto_entry
.import experimental_selection

; Work-RAM input bytes (caller-owned, read-only).
IN_TRACK = $20F8
IN_RSVD_AH = $20F9
IN_RSVD_BL = $20FA
IN_FLAGS = $20FB
IN_BASE_LO = $20FC
IN_BASE_MID = $20FD
IN_BASE_HI = $20FE
IN_RSVD_DH = $20FF

; Owned state (MPR1=$F8 work RAM).
ST_HI = $2400
ST_MID = $2401
ST_LO = $2402
ST_GEN = $2403
ST_LAST = $2404
ST_OFF_HI = $2405
ST_OFF_MID = $2406
ST_OFF_LO = $2407
SCR_LO = $2408
SCR_MID = $2409
SCR_HI = $240A

; Experimental error codes (not genuine BIOS codes).
ERR_TRACK = $10
ERR_FLAGS = $11
ERR_RSVD = $12
ERR_RANGE = $21
ERR_PREGAP = $30
ERR_BEFORE = $31

cd_base_proto_entry:
    php
    pha
    phx
    phy
    cld                     ; binary arithmetic for the SBCs below; D restored via PLP
    ; --- track must equal the selection track (1..99 synthetic) ---
    lda IN_TRACK
    cmp experimental_selection+7
    beq @ok_track
    jmp @err_track
@ok_track:
    ; --- flags must be $80 (S1 _bh, experimental) ---
    lda IN_FLAGS
    cmp #$80
    beq @ok_flags
    jmp @err_flags
@ok_flags:
    ; --- reserved bytes must be zero (experimental strictness) ---
    lda IN_RSVD_AH
    beq @ok_rsvd1
    jmp @err_rsvd
@ok_rsvd1:
    lda IN_RSVD_BL
    beq @ok_rsvd2
    jmp @err_rsvd
@ok_rsvd2:
    lda IN_RSVD_DH
    beq @ok_rsvd3
    jmp @err_rsvd
@ok_rsvd3:
    ; --- base must fit READ(6) 21 bits (HI < $20) ---
    lda IN_BASE_HI
    cmp #$20
    bcc @ok_range
    jmp @err_range
@ok_range:
    ; --- compare base vs INDEX01 (HI/MID/LO) ---
    lda IN_BASE_HI
    cmp experimental_selection+8
    bcc @base_less
    bne @data               ; base_HI > idx_HI
    lda IN_BASE_MID
    cmp experimental_selection+9
    bcc @base_less
    bne @data               ; base_MID > idx_MID
    lda IN_BASE_LO
    cmp experimental_selection+10
    bcc @base_less
    ; base >= INDEX01 falls through to DATA (equal included)
@data:
    ; offset = base - INDEX01 (24-bit, no borrow out expected)
    sec
    lda IN_BASE_LO
    sbc experimental_selection+10
    sta ST_OFF_LO
    lda IN_BASE_MID
    sbc experimental_selection+9
    sta ST_OFF_MID
    lda IN_BASE_HI
    sbc experimental_selection+8
    sta ST_OFF_HI
    ; store base (HI/MID/LO to match selection order)
    lda IN_BASE_HI
    sta ST_HI
    lda IN_BASE_MID
    sta ST_MID
    lda IN_BASE_LO
    sta ST_LO
    inc ST_GEN
    lda #$00
    sta ST_LAST
    ; success epilogue: restore X/Y/A/P, A=0, C=0
    ply
    plx
    pla
    plp
    lda #$00
    clc
    rts
@base_less:
    ; INDEX01 < 150 has no virtual pregap: every base<INDEX01 is BEFORE.
    lda experimental_selection+8
    bne @has_pregap_check    ; HI != 0 means INDEX01 >= $10000 >= 150
    lda experimental_selection+9
    bne @has_pregap_check    ; MID != 0 means INDEX01 >= $100 >= 150? $100=256>=150 yes
    lda experimental_selection+10
    cmp #150
    bcc @err_before          ; INDEX01 LO < 150 and HI=MID=0 => INDEX01 < 150
@has_pregap_check:
    ; pregap_start = INDEX01 - 150 (24-bit, no borrow out since INDEX01>=150)
    sec
    lda experimental_selection+10
    sbc #150
    sta SCR_LO
    lda experimental_selection+9
    sbc #$00
    sta SCR_MID
    lda experimental_selection+8
    sbc #$00
    sta SCR_HI
    ; compare base vs pregap_start: base < pregap_start => BEFORE else PREGAP
    lda IN_BASE_HI
    cmp SCR_HI
    bcc @err_before
    bne @err_pregap
    lda IN_BASE_MID
    cmp SCR_MID
    bcc @err_before
    bne @err_pregap
    lda IN_BASE_LO
    cmp SCR_LO
    bcc @err_before
    ; falls through to PREGAP (base >= pregap_start and < INDEX01)
@err_pregap:
    ply
    plx
    pla
    plp
    lda #ERR_PREGAP
    sta ST_LAST
    sec
    rts
@err_before:
    ply
    plx
    pla
    plp
    lda #ERR_BEFORE
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
@err_flags:
    ply
    plx
    pla
    plp
    lda #ERR_FLAGS
    sta ST_LAST
    sec
    rts
@err_rsvd:
    ply
    plx
    pla
    plp
    lda #ERR_RSVD
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
