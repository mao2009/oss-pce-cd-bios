; EXPERIMENTAL DEV-ONLY CD_READ commercial first-mode helper V1 (Agent-B G5 P1).
; NOT a compatible System Card BIOS. NOT the genuine CD_READ ABI. NOT release eligible.
; NOT DEV_CALLABLE as a formal BIOS service. Experimental helper only, assembled
; into a custom experimental image's BOOT bank or into a synthetic test payload
; (segment-agnostic body; no BOOT-only dependency), never into the normal DEV ROM
; and never via a BIOS slot/vector. Genuine CD_BASE/CD_READ ABI UNKNOWN/BLOCKED.
;
; EXPERIMENTAL contract CD_READ_COMMERCIAL_V1 (synthetic media + opt-in commercial
; observation only, pinned core only; FIRST commercial mode only):
;   Entry: JSR cd_read_commercial_entry (direct JSR by address; NOT a BIOS slot
;     entry). RTS returns.
;   Preconditions: MPR1=$F8 (work RAM $2000-$3FFF), stack in work RAM, IRQs masked
;     by the caller (same discipline as the experimental loader/payload), MPRs already
;     set to EXPECTED_MPR by the bootloader (this routine performs no TAM).
;   Inputs (work RAM, read-only, preserved):
;     $2400/$2401/$2402 stored base HI/MID/LO (big-endian, from the 2-base CD_BASE
;       state; updated only by CD_BASE on success, never by this routine).
;     $2403 generation, $2404 last (base state; last must be $00/DATA else INVALID_STATE).
;       $2405-$2407 preserved, never read by this routine.
;       $240B ST_MAGIC must be $A5 and $240C ST_VALID bit0 set (recbase0 valid)
;       else INVALID_STATE; other VALID bits follow the #805 contract and are
;       not over-constrained here.
;     Commercial block (big-endian _cl:_ch:_dl per docs/cd-read-contract.md R2,
;       HYPOTHESIS from HuCC callers): $20FC block HI, $20FD block MID,
;       $20FE block LO. NOTE: $20F2/$20F3/$20F4 (the synthetic link V1 operand)
;       are IGNORED by this routine; writes there never change the computed LBA.
;       The $20FC-vs-$20F2 distinction is proven by execution (host + real-core
;       tests vary $20F2 while holding $20FC-$20FE fixed and assert LBA unchanged).
;     $20FF _dh destination mode: only $00 (LOCAL bytes, FIRST commercial mode)
;       is implemented; any other value is refused fail-closed ($65) with no
;       $1800 access. Grounds for _dh=0 first: HuCC writes `_dh=0` (stz) with an
;       `_ax` byte length at hucc-systemcard.asm L381-384 is a caller code fact [C]
;       per docs/188-cd-read-transfer-mode-contract.md A1/A5; the "bytes" unit is
;       comment-derived HYPOTHESIS there, so this routine treats _ax strictly as a
;       byte count and accepts exactly 2048 (one synthetic sector) for this mode.
;     $20F8/$20F9 size LO/HI (_al/_ah byte count; must be $00/$08 = 2048 here).
;     $20FA/$20FB destination LO/HI (_bl/_bh logical address; must be $00/$28 =
;       $2800 here: the single-sector fixed destination whose end $3000 exactly
;       meets, but never overlaps, payload/code at $3000+; see dest safety below).
;     $20F5/$20F6/$20F7 are caller RAM and are NEVER trusted: this routine
;       does not read them (mutation-proven; any values leave the INDEX01
;       bound unchanged). The trusted lower bound is the host-determined
;       XSEL selection record (experimental_selection) below.
;     Trusted INDEX 01 (host-determined, NOT caller RAM): experimental_selection
;       11-byte XSEL record (magic "XSEL", version 1, format 1, KIND, TRACK,
;       INDEX01 HI/MID/LO big-endian). Magic/version/format are validated;
;       KIND/TRACK are not interpreted here (positional guard only). Invalid
;       XSEL, INDEX01 HI >= $20, or LBA below XSEL INDEX01 is refused before
;       any $1800 access ($61/$61/$63). XSEL travels with this routine in both
;       links (ROM BOOT via the glue object; test payload via its own record),
;       so the synthetic fixture exercises the identical boundary path.
;       No WINDOW_LEN: unlike CD_READ_LINK_V1 there is NO 16-sector exclusive end.
;       Grounds for lifting the 16-window (not lifted without grounds): the pinned
;       2-base commit observes commercial CUE[1] reaching $03 $64 AFTER past the
;       synthetic window on an in-disc LBA (a070d24a message, L2 note); the window
;       is a synthetic-fixture bound, not a drive bound. Past-end on real media is
;       reported by the drive itself (STATUS path -> $70/$71), never precomputed.
;   Computation (REAL processing, not a bare RTS):
;     - 24-bit add LBA = recbase0 + commercial block WITH carry
;       (LO $20FE -> MID $20FD -> HI $20FC). Carry out of bit 23 is OVER_CAP
;       (refused; never used as a READ address).
;     - CRES LBA spec: on the valid path only, CR_LHI/CR_LMID/CR_LLO hold
;       the low 24 bits of base+block (total & $FFFFFF). Refusals write no
;       CRES bytes (read-only fail-closed); the caller reads code in A.
;     - Refuse BEFORE any $1800 access when: last != $00 (INVALID_STATE $60),
;       window HI >= $20 (INVALID_WINDOW $61), carry or LBA HI >= $20
;       (OVER_CAP $62), LBA < window start (BEFORE $63: pregap/audio positional
;       guard; LBA below INDEX 01 is never issued. Track KIND (data vs audio) is
;       UNKNOWN -- no TOC is read -- so the guard is positional only),
;       _dh != $00 (BAD_MODE $65), size != 2048 (BAD_SIZE $66),
;       dest != $2800 (BAD_DEST $67). No SCSI command, no bus reset, no $1800
;       write on any refusal path.
;     - $64 AFTER is RESERVED and never returned by this routine: it exists only
;       so result-taxonomy comparisons with CD_READ_LINK_V1 stay mechanical
;       (link V1 reports $64 past its synthetic window; this routine accepts
;       such LBAs and lets the drive judge them). A past-disc-end READ returns
;       via the drive STATUS path ($70/$71), exactly as the transport contract
;       T3/T4 describes for READ(6).
;     - On valid request: build READ(6) CDB (opcode $08, LBA 21 bits, count 1) in
;       CRES_CDB $27E8, transfer exactly 2048 bytes to RAM $2800 via the same polled
;       CD-ROM2 protocol as src/cd_read_link.s (copied, not linked: that file's
;       entry decodes a different operand set and enforces the synthetic window).
;       Distinguish success ($00), READ fail ($70 via two disjoint paths:
;       @premature = early STATUS in the DATA_IN wait -> stage 5 + partial bytes,
;       STATUS byte never read (SCSI stays 0); @tail_bad = bad SCSI/message status
;       AFTER the 2048-byte transfer -> stage 8 + 2048 bytes), timeout ($71:
;       bus/select/command/data-ACK/status/message/bus-free wait expiry;
;       stage/bytes = the wait site: stages 2/3/4 carry 0 bytes, stage 5 partial,
;       stages 6/7/8 full 2048).
;   Dest safety (first mode, fixed $2800; logical vs MPR-physical):
;     - The transfer target is the logical address $2800 (MPR1=$F8 work RAM) with
;       size exactly 2048: end-exclusive $3000. The range [$2800,$3000) overlaps
;       none of: inputs $20F8-$20FF, CRES $2330-$2339, base state $2400-$240A,
;       CDB $27E8-$27ED, payload/code $3000+, ZP temps. Any dest != $2800 or any
;       size != 2048 is refused ($66/$67) before any $1800 access, so partial-
;       transfer bounds can never exceed [$2800,$3000): the READ loop counts down
;       exactly 2048 from the fixed pointer and bytes-field accounting mirrors it.
;     - BIOS/MPR/MMIO collision: no TAM/TMA/TXS is executed (MPRs preserved and
;       asserted by tests); $1800-$1804 are touched only on the valid path with
;       the exact polled sequence; no IRQ is enabled; SP is balanced (4 pushes/
;       4 pulls on every path; inner JSRs balanced).
;     - Unsupported modes/sizes/dests fail closed ($65/$66/$67); there is no
;       fallback, no truncation, no silent wrap. Success ($00) is reported only
;       after an actual 2048-byte DATA_IN transfer with GOOD status/message and
;       bus-free observed; $60-$71 values are EXPERIMENTAL codes, never genuine
;       BIOS codes (see genuine-ABI status below).
;   Returns:
;     A=code, C=0/Z=1 on success, C=1/Z=0 on error (N=bit7 of A).
;     X/Y/MPRs preserved (no TAM, no TXS, no I/O mapping change); I/D/V/T preserved
;     (PLP restore, only C/Z/N change via LDA/CLC/SEC); SP balanced.
;   Side effects:
;     CRES $2330-$2339 written only on the valid path (in-progress marker +
;       result; deliberately separate from link V1 LINK_RES $2320-$2329 so
;       control (V1) vs experimental runs are comparable without overwrite).
;     Refusals write nothing except A (+ observable net-zero temp restore):
;       CRES keeps caller-visible prior bytes (canary-tested), never stale
;       LBA claims. CRES_CDB $27E8-$27ED written on valid path only.
;     ZP temps $00-$02,$04-$0B,$0F-$11 (= aliased $2000-$2002,$2004-$200B,
;       $200F-$2011) are saved on entry and restored on every exit
;       (observable net-zero, canary-tested); inputs $20F8-$20FF are only
;       ever read, never written. On success: $2800-$2FFF = exactly 2048
;       sector bytes. On refusal: $2800 untouched (canary intact).
;       $2800 untouched. On READ fail/timeout: $2800 may be partial (bytes field
;       reports transfer count); never writes outside $2800-$2FFF. Untouched: $2300
;       bootloader RES, LINK_RES $2320-$2329, $2400-$2407 base state, input bytes,
;       $3000+ payload/code, all MPRs.
;   Reuse note: READ(6) polled protocol + wait/ACK helpers copied from
;     src/cd_read_link.s (same pinned-core discipline); entry/operand/window/mode
;     checks are new for the commercial block + first-mode contract.
;
; Genuine ABI status: UNKNOWN/BLOCKED for all CD_READ callee items per docs/cd-read-contract.md
; and docs/cd-base-abi-ledger.md. $60-$67/$70-$71 are EXPERIMENTAL codes, never genuine
; BIOS codes. This file never claims VERIFIED, DEV_CALLABLE, or compatibility.
; Reference JSR 0/81, ABI VERIFIED 0/81, DEV_CALLABLE 0, release_eligible=false.
; G5_OBSERVED=false. Commercial observation: BLOCKED here (no ./rom/ media in this
; worktree; Phase B not run -- reported as SKIP/BLOCKED, never PASS).
.setcpu "huc6280"
.segment "BOOT"
.export cd_read_commercial_entry
.import experimental_selection

; --- owned / caller-owned addresses ---
ST_HI   = $2400
ST_MID  = $2401
ST_LO   = $2402
ST_GEN  = $2403
ST_LAST = $2404
ST_MAGIC = $240B
ST_VALID = $240C
VALID_RB0 = $01
MAGIC_A5  = $A5

; Commercial block: big-endian _cl:_ch:_dl (R2 HYPOTHESIS). $20F2-$20F4 ignored.
CBLK_HI = $20FC
CBLK_MID = $20FD
CBLK_LO = $20FE
; Destination mode _dh: only $00 implemented.
CMODE   = $20FF
; Size _al/_ah (byte count, must be $00/$08).
CSZ_LO  = $20F8
CSZ_HI  = $20F9
; Destination _bl/_bh (logical address, must be $00/$28).
CDST_LO = $20FA
CDST_HI = $20FB
; NOTE: $20F5/$20F6/$20F7 are untrusted caller RAM and are never read.
; The trusted INDEX01 bound is experimental_selection+8/9/10 (XSEL).

CRES = $2330
CR_CODE  = $2330
CR_STAGE = $2331
CR_BYTES = $2332          ; LO
CR_BYTES_HI = $2333
CR_SCSI  = $2334
CR_MSG   = $2335
CR_LAST  = $2336
CR_LHI   = $2337
CR_LMID  = $2338
CR_LLO   = $2339

BUF     = $2800
SECTOR_BYTES = 2048

CRES_CDB = $27E8

; --- CD-ROM2 I/O (MPR0=$FF maps hardware at $0000-$1FFF) ---
CD_STATUS = $1800
CD_DATA   = $1801
CD_ACK    = $1802
CD_RESET  = $1804

SCSI_REQ   = $40
PH_MASK    = $F8
PH_FREE    = $00
PH_COMMAND = $D0
PH_DATA_IN = $C8
PH_STATUS  = $D8
PH_MSG_IN  = $F8
TMO_HI     = 8

; --- ZP temps (same scratch area as the probes; payload-owned after boot) ---
tmo0 = $00
tmo1 = $01
tmo2 = $02
ptr    = $04
remain = $06
wmask  = $08
want   = $09
last   = $0A
alt    = $0B
lhi   = $0F
lmid  = $10
llo   = $11

; --- experimental error codes (not genuine BIOS codes) ---
ERR_STATE  = $60
ERR_WINDOW = $61
ERR_CAP    = $62
ERR_BEFORE = $63
ERR_AFTER  = $64          ; reserved: never returned (no synthetic window end)
ERR_MODE   = $65
ERR_SIZE   = $66
ERR_DEST   = $67
ERR_FAIL   = $70
ERR_TMO    = $71

; --- caller-scratch save/restore (C-3): ZP temps $00-$02,$04-$0B,$0F-$11 alias
; work-RAM $2000-$2002,$2004-$200B,$200F-$2011 on this core. Saved on entry
; (loop over the contiguous $2000-$2011 window, covering the used bytes),
; restored on every exit (observable net-zero, proven by canary tests).
; Inputs $20F8-$20FF and state $2400-$2407 are only ever read.
.macro SAVE_TEMPS
    ldx #$11
:
    lda $2000,x
    pha
    dex
    bpl :-
.endmacro

.macro RESTORE_TEMPS
    ldx #$00
:
    pla
    sta $2000,x
    inx
    cpx #$12
    bcc :-
.endmacro

cd_read_commercial_entry:
    php
    pha
    phx
    phy
    cld
    SAVE_TEMPS
    ; --- invalid-state: base never established ---
    ; ST_LAST == $00/DATA alone is NOT enough (P2): also require the #805
    ; seed contract ST_MAGIC == $A5 and ST_VALID bit0 (recbase0 valid).
    ; Other VALID bits follow the #805 contract and are not over-constrained.
    ; (read-only: input/state are only read here; first writes happen at
    ; @do_read after all validation passes. Temp save above makes ZP temps
    ; free scratch with observable net-zero at every exit.)
    lda ST_LAST
    beq @st_ok1
    jmp @ref_state
@st_ok1:
    lda ST_MAGIC
    cmp #MAGIC_A5
    beq @st_ok2
    jmp @ref_state
@st_ok2:
    lda ST_VALID
    and #VALID_RB0
    bne @have_base
    jmp @ref_state
@have_base:
    ; --- trusted INDEX 01: XSEL record validation (P1) ---
    ; experimental_selection 11 bytes: "XSEL",1,1,KIND,TRACK,HI,MID,LO.
    ; Magic/version/format are validated; KIND/TRACK not interpreted.
    ; $20F5-$20F7 are never read (untrusted caller RAM).
    lda experimental_selection+0
    cmp #'X'
    beq @x0_ok
    jmp @ref_window
@x0_ok:
    lda experimental_selection+1
    cmp #'S'
    beq @x1_ok
    jmp @ref_window
@x1_ok:
    lda experimental_selection+2
    cmp #'E'
    beq @x2_ok
    jmp @ref_window
@x2_ok:
    lda experimental_selection+3
    cmp #'L'
    beq @x3_ok
    jmp @ref_window
@x3_ok:
    lda experimental_selection+4
    cmp #$01
    beq @x4_ok
    jmp @ref_window
@x4_ok:
    lda experimental_selection+5
    cmp #$01
    beq @x5_ok
    jmp @ref_window
@x5_ok:
    ; --- trusted INDEX 01 HI must fit READ(6) 21 bits ---
    lda experimental_selection+8
    cmp #$20
    bcc @win_ok
    jmp @ref_window
@win_ok:
    ; --- 24-bit add LBA = recbase0 + commercial block WITH carry ---
    ; (LO $20FE -> MID $20FD -> HI $20FC; $20F2-$20F4 never read).
    clc
    lda ST_LO
    adc CBLK_LO
    sta llo
    lda ST_MID
    adc CBLK_MID
    sta lmid
    lda ST_HI
    adc CBLK_HI
    sta lhi
    ; (No CRES writes here: refusals stay read-only; the valid path records
    ; the LBA after all validation passes.)
    bcc @no_wrap
    jmp @ref_cap               ; carry out of bit 23: definitely over 21 bits
@no_wrap:
    ; --- 21-bit cap: HI >= $20 refused ---
    lda lhi
    cmp #$20
    bcc @cap_ok
    jmp @ref_cap
@cap_ok:
    ; --- lower bound: LBA < trusted XSEL INDEX01 => BEFORE (pregap/audio guard) ---
    ; (far refusal targets: use short-skip + JMP trampolines for 6502 range)
    lda lhi
    cmp experimental_selection+8
    bcs @hi_ge_win
    jmp @ref_before
@hi_ge_win:
    bne @ge_win
    lda lmid
    cmp experimental_selection+9
    bcs @mid_ge_win
    jmp @ref_before
@mid_ge_win:
    bne @ge_win
    lda llo
    cmp experimental_selection+10
    bcs @lo_ge_win
    jmp @ref_before
@lo_ge_win:
@ge_win:
    ; --- NO synthetic window end: commercial LBAs past INDEX01+16 are valid
    ; inputs here (the drive judges past-disc-end via STATUS). Fall through to
    ; first-mode checks.
    ; --- mode: _dh must be $00 (FIRST commercial mode only) ---
    lda CMODE
    beq @mode_ok
    jmp @ref_mode
@mode_ok:
    ; --- size: _ax byte count must be exactly 2048 ($00/$08) ---
    lda CSZ_LO
    bne @bad_size
    lda CSZ_HI
    cmp #$08
    beq @size_ok
@bad_size:
    jmp @ref_size
@size_ok:
    ; --- dest: logical address must be exactly $2800 ---
    ; Fixed-dest bounds proof: [$2800,$3000) overlaps no input/state/RES/CDB/
    ; payload region listed in the contract; end-exclusive $3000 only touches.
    lda CDST_LO
    bne @bad_dest
    lda CDST_HI
    cmp #$28
    beq @dest_ok
@bad_dest:
    jmp @ref_dest
@dest_ok:
    ; --- valid request: fall through to the real READ(6) ---
    jmp @do_read

@ref_state:
    ; Refusal writes nothing (read-only fail-closed; only A carries $60).
    RESTORE_TEMPS
    ply
    plx
    pla
    plp
    lda #ERR_STATE
    sec
    rts
@ref_window:
    ; Refusal writes nothing (read-only fail-closed; only A carries $61).
    RESTORE_TEMPS
    ply
    plx
    pla
    plp
    lda #ERR_WINDOW
    sec
    rts
@ref_cap:
    ; Refusal writes nothing (read-only fail-closed; only A carries $62).
    RESTORE_TEMPS
    ply
    plx
    pla
    plp
    lda #ERR_CAP
    sec
    rts
@ref_before:
    ; Refusal writes nothing (read-only fail-closed; only A carries $63).
    RESTORE_TEMPS
    ply
    plx
    pla
    plp
    lda #ERR_BEFORE
    sec
    rts
@ref_mode:
    ; Refusal writes nothing (read-only fail-closed; only A carries $65).
    RESTORE_TEMPS
    ply
    plx
    pla
    plp
    lda #ERR_MODE
    sec
    rts
@ref_size:
    ; Refusal writes nothing (read-only fail-closed; only A carries $66).
    RESTORE_TEMPS
    ply
    plx
    pla
    plp
    lda #ERR_SIZE
    sec
    rts
@ref_dest:
    ; Refusal writes nothing (read-only fail-closed; only A carries $67).
    RESTORE_TEMPS
    ply
    plx
    pla
    plp
    lda #ERR_DEST
    sec
    rts

@do_read:
    ; In-progress marker (first writes: all read-only validation passed).
    lda #$FF
    sta CR_CODE
    lda #$01
    sta CR_STAGE
    lda #$00
    sta CR_BYTES
    sta CR_BYTES_HI
    sta CR_SCSI
    sta CR_MSG
    sta CR_LAST
    ; One-shot early-abort phase ($0B alt): caller residue cannot cause a
    ; false timeout; re-inited per call, never unconditionally inside waits.
    lda #$FF
    sta alt
    ; Record the validated LBA (valid path only).
    lda lhi
    sta CR_LHI
    lda lmid
    sta CR_LMID
    lda llo
    sta CR_LLO
    ; Build READ(6) CDB at CRES_CDB: 08, HI&$1F, MID, LO, 01, 00.
    lda #$08
    sta CRES_CDB
    lda lhi
    and #$1F
    sta CRES_CDB+1
    lda lmid
    sta CRES_CDB+2
    lda llo
    sta CRES_CDB+3
    lda #$01
    sta CRES_CDB+4
    lda #$00
    sta CRES_CDB+5
    ; Destination $2800, 2048 bytes.
    lda #<BUF
    sta ptr
    lda #>BUF
    sta ptr+1
    lda #0
    sta remain
    lda #>SECTOR_BYTES
    sta remain+1
    lda #2
    sta CR_STAGE
    ; Pulse SCSI bus reset.
    lda #$02
    sta CD_RESET
    jsr ccom_delay
    lda #$00
    sta CD_RESET
    jsr ccom_delay
    lda #PH_MASK
    sta wmask
    lda #PH_FREE
    sta want
    jsr ccom_wait_phase
    bcc @selecting
    lda #ERR_TMO
    sta CR_CODE
    jsr ccom_store_counts
    RESTORE_TEMPS
    ply
    plx
    pla
    plp
    lda #ERR_TMO
    sec
    rts
@selecting:
    lda #3
    sta CR_STAGE
    lda #$81
    sta CD_DATA
    sta CD_STATUS
    lda #PH_MASK
    sta wmask
    lda #PH_COMMAND
    sta want
    jsr ccom_wait_phase
    bcc @send
    lda #ERR_TMO
    sta CR_CODE
    jsr ccom_store_counts
    RESTORE_TEMPS
    ply
    plx
    pla
    plp
    lda #ERR_TMO
    sec
    rts
@send:
    lda #4
    sta CR_STAGE
    ldy #0
@cmd:
    lda #PH_MASK
    sta wmask
    lda #PH_COMMAND
    sta want
    jsr ccom_wait_phase
    bcc @put
    lda #ERR_TMO
    sta CR_CODE
    jsr ccom_store_counts
    RESTORE_TEMPS
    ply
    plx
    pla
    plp
    lda #ERR_TMO
    sec
    rts
@put:
    lda CRES_CDB,y
    sta CD_DATA
    jsr ccom_ack_byte
    bcc @next
    lda #ERR_TMO
    sta CR_CODE
    jsr ccom_store_counts
    RESTORE_TEMPS
    ply
    plx
    pla
    plp
    lda #ERR_TMO
    sec
    rts
@next:
    iny
    cpy #6
    beq @nocmd_tramp
    jmp @cmd
@nocmd_tramp:
    lda #5
    sta CR_STAGE
@data:
    lda #PH_MASK
    sta wmask
    lda #PH_DATA_IN
    sta want
    lda #PH_STATUS
    sta alt
    jsr ccom_wait_phase
    bcs @notake_tramp
    jmp @take
@notake_tramp:
    lda last
    and #PH_MASK
    cmp #PH_STATUS
    beq @premature
    lda #ERR_TMO
    sta CR_CODE
    jsr ccom_store_counts
    RESTORE_TEMPS
    ply
    plx
    pla
    plp
    lda #ERR_TMO
    sec
    rts
@premature:
    lda #ERR_FAIL
    sta CR_CODE
    jsr ccom_store_counts
    RESTORE_TEMPS
    ply
    plx
    pla
    plp
    lda #ERR_FAIL
    sec
    rts
@take:
    lda CD_DATA
    sta (ptr)
    inc ptr
    bne @counted
    inc ptr+1
@counted:
    jsr ccom_ack_byte
    bcc @more
    lda #ERR_TMO
    sta CR_CODE
    jsr ccom_store_counts
    RESTORE_TEMPS
    ply
    plx
    pla
    plp
    lda #ERR_TMO
    sec
    rts
@more:
    lda remain
    bne @low
    dec remain+1
@low:
    dec remain
    lda remain
    ora remain+1
    beq @nodata_tramp
    jmp @data
@nodata_tramp:
    lda #6
    sta CR_STAGE
    lda #PH_MASK
    sta wmask
    lda #PH_STATUS
    sta want
    jsr ccom_wait_phase
    bcc @tail_ok_1
    jmp @tail_tmo
@tail_ok_1:
    lda CD_DATA
    sta CR_SCSI
    jsr ccom_ack_byte
    bcs @tail_tmo
    lda #7
    sta CR_STAGE
    lda #PH_MASK
    sta wmask
    lda #PH_MSG_IN
    sta want
    jsr ccom_wait_phase
    bcs @tail_tmo
    lda CD_DATA
    sta CR_MSG
    jsr ccom_ack_byte
    bcs @tail_tmo
    lda #8
    sta CR_STAGE
    lda #PH_MASK
    sta wmask
    lda #PH_FREE
    sta want
    jsr ccom_wait_phase
    bcc @tail_ok_2
    jmp @tail_not_free
@tail_ok_2:
    lda CR_SCSI
    ora CR_MSG
    bne @tail_bad
    jsr ccom_store_counts
    lda #$00
    sta CR_CODE
    RESTORE_TEMPS
    ply
    plx
    pla
    plp
    lda #$00
    clc
    rts
@tail_tmo:
    lda #ERR_TMO
    sta CR_CODE
    jsr ccom_store_counts
    RESTORE_TEMPS
    ply
    plx
    pla
    plp
    lda #ERR_TMO
    sec
    rts
@tail_not_free:
    lda #ERR_TMO
    sta CR_CODE
    jsr ccom_store_counts
    RESTORE_TEMPS
    ply
    plx
    pla
    plp
    lda #ERR_TMO
    sec
    rts
@tail_bad:
    lda #ERR_FAIL
    sta CR_CODE
    jsr ccom_store_counts
    RESTORE_TEMPS
    ply
    plx
    pla
    plp
    lda #ERR_FAIL
    sec
    rts

ccom_store_counts:
    lda last
    sta CR_LAST
    sec
    lda ptr
    sbc #<BUF
    sta CR_BYTES
    lda ptr+1
    sbc #>BUF
    sta CR_BYTES_HI
    rts

ccom_wait_phase:
    lda #0
    sta tmo0
    sta tmo1
    lda #TMO_HI
    sta tmo2
@poll:
    lda CD_STATUS
    sta last
    and wmask
    cmp want
    beq @reached
    cmp alt
    beq @timeout
    lda tmo0
    bne @d0
    lda tmo1
    bne @d1
    lda tmo2
    beq @timeout
    dec tmo2
@d1:
    dec tmo1
@d0:
    dec tmo0
    bra @poll
@reached:
    lda #$FF
    sta alt
    clc
    rts
@timeout:
    lda #$FF
    sta alt
    sec
    rts

ccom_ack_byte:
    lda #$80
    sta CD_ACK
    lda #SCSI_REQ
    sta wmask
    lda #0
    sta want
    jsr ccom_wait_phase
    lda #$00
    sta CD_ACK
    rts

ccom_delay:
    ldy #$20
@outer:
    ldx #0
@inner:
    dex
    bne @inner
    dey
    bne @outer
    rts
