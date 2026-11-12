; EXPERIMENTAL DEV-ONLY CD_READ link V1 (Agent-B G5 P0).
; NOT a compatible System Card BIOS. NOT the genuine CD_READ ABI. NOT release eligible.
; NOT DEV_CALLABLE as a formal BIOS service. Experimental ROM-resident helper only,
; linked into a custom experimental image's BOOT bank ($F000 area, alongside the
; experimental boot + probe objects), never into the normal DEV ROM and never via a
; BIOS slot/vector. Genuine CD_BASE/CD_READ ABI UNKNOWN/BLOCKED.
;
; EXPERIMENTAL contract CD_READ_LINK_V1 (synthetic media only, pinned core only):
;   Entry: JSR cd_read_link_entry (ROM-resident BOOT helper, direct JSR by address;
;     NOT a BIOS slot entry). RTS returns.
;   Preconditions: MPR1=$F8 (work RAM $2000-$3FFF), stack in work RAM, IRQs masked
;     by the caller (same discipline as the experimental loader/payload), MPRs already
;     set to EXPECTED_MPR by the bootloader (this routine performs no TAM).
;   Inputs (work RAM, read-only, preserved):
;     $2400/$2401/$2402 stored base HI/MID/LO (big-endian, from CD_BASE proto V1 state;
;       updated only by the proto on success, never by this link routine).
;     $2403 generation, $2404 last (proto state; last must be $00/DATA else INVALID_STATE).
;       $2405-$2407 proto offset (preserved, never read by this routine).
;     $20F2/$20F3/$20F4 block LO/MID/HI (24-bit little-endian block operand).
;     $20F5/$20F6/$20F7 window start LO/MID/HI (24-bit little-endian INDEX 01 of the
;       selected data track; synthetic-only, host-derived, 21-bit).
;     WINDOW_LEN=16 (synthetic-only data-track length; exclusive end = start+16).
;   Computation (REAL processing, not a bare RTS):
;     - 24-bit add LBA = base + block WITH carry (LO->MID->HI). Carry out of bit 23
;       is OVER_CAP (never wraps).
;     - Refuse BEFORE any $1800 access when: last != $00 (INVALID_STATE $60),
;       window HI >= $20 (INVALID_WINDOW $61), carry or LBA HI >= $20 (OVER_CAP $62),
;       LBA < window start (BEFORE $63), LBA >= window start+16 (AFTER $64).
;       No SCSI command, no bus reset, no $1800 write on refusal paths.
;     - On valid request: build READ(6) CDB (opcode $08, LBA 21 bits, count 1) in
;       LINK_CDB $27E8, transfer exactly 2048 bytes to RAM $2800 via the same polled
;       CD-ROM2 protocol as src/cd_read_probe.s (copied, not linked: that file halts
;       and cannot return; cd_load_exec_probe.s performs PCEX header/EXEC handling
;       unsuitable for a raw transfer). Distinguish success ($00), READ fail ($70:
;       premature STATUS / bad SCSI status), timeout ($71: bus/select/command/data/
;       ACK/timeout, bus not freed).
;   Returns:
;     A=code, C=0/Z=1 on success, C=1/Z=0 on error (N=bit7 of A).
;     X/Y/MPRs preserved (no TAM, no TXS, no I/O mapping change); I/D/V/T preserved
;     (PLP restore, only C/Z/N change via LDA/CLC/SEC); SP balanced (4 pushes/4 pulls
;     on every path; inner JSRs balanced).
;   Side effects:
;     LINK_RES $2320-$2329 updated on every call (code/stage/bytes/SCSI/msg/last/LBA).
;     LINK_CDB $27E8-$27ED written on valid path only. ZP temps $00-$0B + $0C-$0E used.
;     On success: $2800-$2FFF = exactly 2048 sector bytes. On refusal: $2800 untouched.
;     On READ fail/timeout: $2800 may be partial (bytes field reports transfer count);
;       never writes outside $2800-$2FFF. Untouched: $2300 bootloader RES, $2400-$2407
;       proto state (base/gen/last/off preserved even on link error, mirroring the
;       proto V1 fix), $20F2-$20F7 inputs, $3000+ payload/code, all MPRs.
;   Reuse note: cd_set_read_lba CDB idiom (A=LO/X=MID/Y=HI) reused as a pattern;
;     cd_entry_selected flow NOT reused (it validates a PCEX header and jumps to EXEC).
;
; Genuine ABI status: UNKNOWN/BLOCKED for all CD_READ callee items per docs/cd-read-contract.md
; and docs/cd-base-abi-ledger.md. This file never claims VERIFIED, DEV_CALLABLE, or compatibility.
; Reference JSR 0/81, ABI VERIFIED 0/81, DEV_CALLABLE 0, release_eligible=false.
.setcpu "huc6280"
.segment "BOOT"
.export cd_read_link_entry

; --- owned / caller-owned addresses ---
ST_HI   = $2400
ST_MID  = $2401
ST_LO   = $2402
ST_GEN  = $2403
ST_LAST = $2404

BLK_LO  = $20F2
BLK_MID = $20F3
BLK_HI  = $20F4
WIN_LO  = $20F5
WIN_MID = $20F6
WIN_HI  = $20F7

LINK_RES = $2320
LR_CODE  = $2320
LR_STAGE = $2321
LR_BYTES = $2322          ; LO
LR_BYTES_HI = $2323
LR_SCSI  = $2324
LR_MSG   = $2325
LR_LAST  = $2326
LR_LHI   = $2327
LR_LMID  = $2328
LR_LLO   = $2329

BUF     = $2800
SECTOR_BYTES = 2048

LINK_CDB = $27E8

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
wend_lo = $0C
wend_mid = $0D
wend_hi  = $0E
lhi   = $0F
lmid  = $10
llo   = $11

; --- experimental error codes (not genuine BIOS codes) ---
ERR_STATE  = $60
ERR_WINDOW = $61
ERR_CAP    = $62
ERR_BEFORE = $63
ERR_AFTER  = $64
ERR_FAIL   = $70
ERR_TMO    = $71

cd_read_link_entry:
    php
    pha
    phx
    phy
    cld
    ; LINK_RES in-progress marker; stage 1 = entered/computed (refusal stage).
    lda #$FF
    sta LR_CODE
    lda #$01
    sta LR_STAGE
    lda #$00
    sta LR_BYTES
    sta LR_BYTES_HI
    sta LR_SCSI
    sta LR_MSG
    sta LR_LAST
    ; MEDIUM-1: initialise the one-shot early-abort phase ($0B alt) before the
    ; first wait so caller-residue RAM cannot cause a false timeout. Re-inited
    ; only here (per call), never unconditionally inside link_wait_phase. The
    ; DATA_IN path still overrides alt with PH_STATUS before its wait.
    lda #$FF
    sta alt
    ; --- invalid-state: proto base never established (last != $00/DATA) ---
    lda ST_LAST
    beq @have_base
    jmp @ref_state
@have_base:
    ; --- invalid-window: window start must fit READ(6) 21 bits ---
    lda WIN_HI
    cmp #$20
    bcc @win_ok
    jmp @ref_window
@win_ok:
    ; --- 24-bit add LBA = base + block WITH carry (LO->MID->HI) ---
    clc
    lda ST_LO
    adc BLK_LO
    sta llo
    lda ST_MID
    adc BLK_MID
    sta lmid
    lda ST_HI
    adc BLK_HI
    sta lhi
    bcc @no_wrap
    jmp @ref_cap               ; carry out of bit 23: definitely over 21 bits
@no_wrap:
    lda lhi
    sta LR_LHI
    lda lmid
    sta LR_LMID
    lda llo
    sta LR_LLO
    ; --- 21-bit cap: HI >= $20 refused ---
    lda lhi
    cmp #$20
    bcc @cap_ok
    jmp @ref_cap
@cap_ok:
    ; --- window check: LBA < start => BEFORE ---
    ; (far refusal targets: use short-skip + JMP trampolines for 6502 range)
    lda lhi
    cmp WIN_HI
    bcs @hi_ge_win
    jmp @ref_before
@hi_ge_win:
    bne @ge_win
    lda lmid
    cmp WIN_MID
    bcs @mid_ge_win
    jmp @ref_before
@mid_ge_win:
    bne @ge_win
    lda llo
    cmp WIN_LO
    bcs @lo_ge_win
    jmp @ref_before
@lo_ge_win:
@ge_win:
    ; wend = win + 16 (24-bit; win < $200000 so no wrap out of 24 bits)
    clc
    lda WIN_LO
    adc #16
    sta wend_lo
    lda WIN_MID
    adc #$00
    sta wend_mid
    lda WIN_HI
    adc #$00
    sta wend_hi
    ; LBA >= wend => AFTER
    lda lhi
    cmp wend_hi
    bcc @in_window
    bne @ref_after
    lda lmid
    cmp wend_mid
    bcc @in_window
    bne @ref_after
    lda llo
    cmp wend_lo
    bcc @in_window
    jmp @ref_after
@in_window:
    ; --- valid request: fall through to the real READ(6) ---
    jmp @do_read

@ref_state:
    lda #ERR_STATE
    sta LR_CODE
    ; LBA bytes unknown without a valid base; store zeros (no base read claimed).
    lda #$00
    sta LR_LHI
    sta LR_LMID
    sta LR_LLO
    ply
    plx
    pla
    plp
    lda #ERR_STATE
    sec
    rts
@ref_window:
    ; store computed LBA if computable (base+block), else zeros; base is valid here.
    clc
    lda ST_LO
    adc BLK_LO
    sta LR_LLO
    lda ST_MID
    adc BLK_MID
    sta LR_LMID
    lda ST_HI
    adc BLK_HI
    sta LR_LHI
    lda #ERR_WINDOW
    sta LR_CODE
    ply
    plx
    pla
    plp
    lda #ERR_WINDOW
    sec
    rts
@ref_cap:
    lda #ERR_CAP
    sta LR_CODE
    ply
    plx
    pla
    plp
    lda #ERR_CAP
    sec
    rts
@ref_before:
    lda #ERR_BEFORE
    sta LR_CODE
    ply
    plx
    pla
    plp
    lda #ERR_BEFORE
    sec
    rts
@ref_after:
    lda #ERR_AFTER
    sta LR_CODE
    ply
    plx
    pla
    plp
    lda #ERR_AFTER
    sec
    rts

@do_read:
    ; Build READ(6) CDB at LINK_CDB: 08, HI&$1F, MID, LO, 01, 00.
    lda #$08
    sta LINK_CDB
    lda lhi
    and #$1F
    sta LINK_CDB+1
    lda lmid
    sta LINK_CDB+2
    lda llo
    sta LINK_CDB+3
    lda #$01
    sta LINK_CDB+4
    lda #$00
    sta LINK_CDB+5
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
    sta LR_STAGE
    ; Pulse SCSI bus reset.
    lda #$02
    sta CD_RESET
    jsr link_delay
    lda #$00
    sta CD_RESET
    jsr link_delay
    lda #PH_MASK
    sta wmask
    lda #PH_FREE
    sta want
    jsr link_wait_phase
    bcc @selecting
    lda #ERR_TMO
    sta LR_CODE
    jsr link_store_counts
    ply
    plx
    pla
    plp
    lda #ERR_TMO
    sec
    rts
@selecting:
    lda #3
    sta LR_STAGE
    lda #$81
    sta CD_DATA
    sta CD_STATUS
    lda #PH_MASK
    sta wmask
    lda #PH_COMMAND
    sta want
    jsr link_wait_phase
    bcc @send
    lda #ERR_TMO
    sta LR_CODE
    jsr link_store_counts
    ply
    plx
    pla
    plp
    lda #ERR_TMO
    sec
    rts
@send:
    lda #4
    sta LR_STAGE
    ldy #0
@cmd:
    lda #PH_MASK
    sta wmask
    lda #PH_COMMAND
    sta want
    jsr link_wait_phase
    bcc @put
    lda #ERR_TMO
    sta LR_CODE
    jsr link_store_counts
    ply
    plx
    pla
    plp
    lda #ERR_TMO
    sec
    rts
@put:
    lda LINK_CDB,y
    sta CD_DATA
    jsr link_ack_byte
    bcc @next
    lda #ERR_TMO
    sta LR_CODE
    jsr link_store_counts
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
    bne @cmd
    lda #5
    sta LR_STAGE
@data:
    lda #PH_MASK
    sta wmask
    lda #PH_DATA_IN
    sta want
    lda #PH_STATUS
    sta alt
    jsr link_wait_phase
    bcc @take
    lda last
    and #PH_MASK
    cmp #PH_STATUS
    beq @premature
    lda #ERR_TMO
    sta LR_CODE
    jsr link_store_counts
    ply
    plx
    pla
    plp
    lda #ERR_TMO
    sec
    rts
@premature:
    lda #ERR_FAIL
    sta LR_CODE
    jsr link_store_counts
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
    jsr link_ack_byte
    bcc @more
    lda #ERR_TMO
    sta LR_CODE
    jsr link_store_counts
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
    bne @data
    lda #6
    sta LR_STAGE
    lda #PH_MASK
    sta wmask
    lda #PH_STATUS
    sta want
    jsr link_wait_phase
    bcs @tail_tmo
    lda CD_DATA
    sta LR_SCSI
    jsr link_ack_byte
    bcs @tail_tmo
    lda #7
    sta LR_STAGE
    lda #PH_MASK
    sta wmask
    lda #PH_MSG_IN
    sta want
    jsr link_wait_phase
    bcs @tail_tmo
    lda CD_DATA
    sta LR_MSG
    jsr link_ack_byte
    bcs @tail_tmo
    lda #8
    sta LR_STAGE
    lda #PH_MASK
    sta wmask
    lda #PH_FREE
    sta want
    jsr link_wait_phase
    bcs @tail_not_free
    lda LR_SCSI
    ora LR_MSG
    bne @tail_bad
    jsr link_store_counts
    lda #$00
    sta LR_CODE
    ply
    plx
    pla
    plp
    lda #$00
    clc
    rts
@tail_tmo:
    lda #ERR_TMO
    sta LR_CODE
    jsr link_store_counts
    ply
    plx
    pla
    plp
    lda #ERR_TMO
    sec
    rts
@tail_not_free:
    lda #ERR_TMO
    sta LR_CODE
    jsr link_store_counts
    ply
    plx
    pla
    plp
    lda #ERR_TMO
    sec
    rts
@tail_bad:
    lda #ERR_FAIL
    sta LR_CODE
    jsr link_store_counts
    ply
    plx
    pla
    plp
    lda #ERR_FAIL
    sec
    rts

link_store_counts:
    lda last
    sta LR_LAST
    sec
    lda ptr
    sbc #<BUF
    sta LR_BYTES
    lda ptr+1
    sbc #>BUF
    sta LR_BYTES_HI
    rts

link_wait_phase:
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

link_ack_byte:
    lda #$80
    sta CD_ACK
    lda #SCSI_REQ
    sta wmask
    lda #0
    sta want
    jsr link_wait_phase
    lda #$00
    sta CD_ACK
    rts

link_delay:
    ldy #$20
@outer:
    ldx #0
@inner:
    dex
    bne @inner
    dey
    bne @outer
    rts
