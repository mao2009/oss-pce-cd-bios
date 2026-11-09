; Issue #670 Phase 2: DEV-only multi-sector IPL loader on synthetic media.
; NOT a compatible System Card BIOS, NOT a BIOS ABI, NOT CD_READ, NOT release eligible.
;
; Flow: reset -> READ(6) of the information sector (data-relative sector 1) -> full field validation
; (every rejection happens BEFORE the first RAM write to the load area) -> one READ(6) per body sector
; -> copy each sector to IPLSTA with MPR2/MPR3 switched at 8 KiB boundaries -> read the destination back
; and compare both sums with the header -> set MPR2..6 from the header -> jump to IPLJMP.
;
; Partial copies: a READ failure after the first copy leaves a PARTIAL image in RAM. The loader halts
; with the copied-sector count in the result block and never jumps. A body whose checksum differs from
; the header is also copied and then refused without a jump. Both are reported, never executed.
;
; Memory contract (work RAM $2000-$3FFF via MPR1 = $F8):
;   $2100-$21FF  stack (below the staging buffer)
;   $2200-$29FF  BUF: one 2048-byte staging sector (READ(6) target, then copy source)
;   $2F00-$2F7F  RB: result block (dumped by the probe; never used as a load area)
;   $2F80-$2F8F  payload result area (written by the payload, not by this loader)
;   $3000-$7FFF  load area (IPLSTA..IPLSTA+N*2048-1, validated to be within $3000-$7FFF)
; The PCEX markers at $3F00-$3F12 (src/cd_load_exec_probe.s) are NOT used here. A load area that covers
; them is therefore allowed; the Phase 2 test loads a payload over exactly that region.
;
; IPL_SEL is the 16-bit LBA of the mounted data track's INDEX 01 (host-derived, see the test driver).
.setcpu "huc6280"

.ifndef IPL_SEL
    .error "IPL_SEL (16-bit LBA of the mounted data track INDEX 01) is required"
.endif

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
SECTOR     = 2048
SIG_LEN    = 23

; zero page
tmo0   = $00
tmo1   = $01
tmo2   = $02
ptr    = $04
remain = $06
wmask  = $08
want   = $09
last   = $0A
alt    = $0B
src    = $0C
dst    = $0E
cnt    = $10
curreg = $12
m2     = $13
m3     = $14
m4     = $15
m5     = $16
m6     = $17
sumv   = $18             ; 2 bytes: source sum
rdv    = $1A             ; 2 bytes: readback sum
hdrv   = $1C             ; 2 bytes: DEV body checksum from the header
nsec   = $1E
stav   = $1F             ; 2 bytes: IPLSTA
bodyl  = $21             ; 2 bytes: low 16 bits of the 24-bit body base LBA (IPL_SEL + IPLBLK)
bodyh  = $27             ; 1 byte: high 8 bits of the 24-bit body base LBA
lbalo  = $23             ; 3 bytes (lbalo/lbamid/lbahi): working 24-bit LBA for one READ(6)
lbamid = $24
lbahi  = $03
idx    = $25
scr    = $26
byt    = $28

BUF     = $2200
RB      = $2F00
R_CODE  = RB + 0
R_STAGE = RB + 1
R_COPIED = RB + 2
R_NSEC  = RB + 3
R_DST   = RB + 4         ; 2 bytes
R_SRC   = RB + 6         ; 2 bytes: source sum
R_RD    = RB + 8         ; 2 bytes: readback sum
R_HDR   = RB + 10        ; 2 bytes: header checksum
R_ENTRY = RB + 12        ; 2 bytes: IPLJMP
R_LAST  = RB + 14
R_SCSI  = RB + 15
R_CDB   = RB + 16        ; 6 bytes READ(6) CDB built in RAM
R_MSG   = RB + 22

.segment "CODE"
reset:
    sei
    cld
    lda #$FF
    tam #$01                 ; MPR0: hardware I/O ($1800 CD-ROM2)
    lda #$F8
    tam #$02                 ; MPR1: work RAM $2000-$3FFF
    ldx #$FF
    txs
    lda #$00
    sta CD_ACK
    lda #$FF
    sta alt                  ; no phase is "one-shot" until a wait sets it
    sta R_CODE
    lda #1
    sta R_STAGE
    lda #0
    sta R_COPIED
    sta R_NSEC
    sta R_LAST
    sta R_SCSI
    sta R_MSG
    lda #2
    sta R_STAGE
    lda #<(IPL_SEL+1)        ; information sector = data-relative sector 1
    sta lbalo
    lda #>(IPL_SEL+1)
    sta lbamid
    lda #0                     ; IPL_SEL is 16-bit, so the high byte is 0
    sta lbahi
    jsr read_sector
    jsr validate_info
    jsr probe_last
    jsr copy_body
    jsr verify_body
    jsr launch

; ---------------------------------------------------------------------------
; Field validation of the information sector in BUF. No RAM write outside RB and zero page.
validate_info:
    lda #3
    sta R_STAGE
    ldx #0
@sig:
    lda BUF+32,x
    cmp sig,x
    beq @sigok
    lda #20                  ; signature mismatch
    jmp fail
@sigok:
    inx
    cpx #SIG_LEN
    bne @sig
.ifndef IPL_PUBLIC
    ldx #16
@res:
    lda BUF,x
    beq @resok
    lda #28                  ; reserved bytes 16..31 must be zero (DEV: unknown fields are refused)
    jmp fail
@resok:
    inx
    cpx #32
    bne @res
.endif
    ; IPLBLK: 24-bit big-endian data-relative sector. DEV/PUBLIC accept values >= 2
    ; whose body stays inside the 21-bit READ(6) range (Issue #744, Owner decision B).
    lda BUF
    sta bodyh
    lda BUF+1
    sta bodyl+1
    lda BUF+2
    sta bodyl
    ora bodyl+1
    ora bodyh
    beq @blkbad                ; IPLBLK == 0
    lda bodyh
    ora bodyl+1
    bne @blkok                 ; IPLBLK >= $100 is definitely >= 2
    lda bodyl
    cmp #2
    bcs @blkok
@blkbad:
    lda #21
    jmp fail
@blkok:
    ; body LBA base = IPL_SEL + IPLBLK (24-bit; carry out of 24 bits is over range).
    ; IPL_SEL is a 16-bit host-derived INDEX 01 LBA, so its high byte is 0 here.
    lda bodyl
    clc
    adc #<IPL_SEL
    sta bodyl
    lda bodyl+1
    adc #>IPL_SEL
    sta bodyl+1
    lda bodyh
    adc #0
    sta bodyh
    bcc @lbaok
    lda #27
    jmp fail
@lbaok:
    ; IPLBLN: number of 2048-byte sectors. DEV: 1..10 (fits $3000-$7FFF).
    ; PUBLIC: 1..31 (fits $3000-$DFFF through MPR2..6 regions; see switch_region).
    lda BUF+3
    bne @nz
    lda #22
    jmp fail
@nz:
.ifdef IPL_PUBLIC
    cmp #32
.else
    cmp #11
.endif
    bcc @nok
    lda #22
    jmp fail
@nok:
    sta nsec
    sta R_NSEC
    ; the last body LBA (body + nsec - 1) must be <= $1FFFFF: require the exclusive
    ; end (body + nsec) <= $200000 with no 24-bit wraparound
    lda bodyl
    clc
    adc nsec
    sta lbalo
    lda bodyl+1
    adc #0
    sta lbamid
    lda bodyh
    adc #0
    sta lbahi
    bcs @tailbad               ; 24-bit wraparound: end past $FFFFFF
    lda lbahi
    cmp #$20
    bcc @tailok                ; end < $200000
    bne @tailbad               ; end > $200000
    lda lbamid                 ; end == $200000 only when mid == 0 and lo == 0
    ora lbalo
    beq @tailok
@tailbad:
    lda #27
    jmp fail
@tailok:
    ; IPLSTA: little-endian load address, must be in $3000-$7FFF and fit
    lda BUF+4
    sta stav
    lda BUF+5
    sta stav+1
    cmp #$30
    bcs @hi1
    lda #23
    jmp fail
@hi1:
    cmp #$80
    bcc @hi2
    lda #23
    jmp fail
@hi2:
    lda nsec                 ; scr = N*8 (high byte of N*2048)
    asl
    asl
    asl
    sta scr
    lda stav+1
    clc
    adc scr
    bcs @ovf                  ; 16-bit overflow: load end wraps past $FFFF, refuse before copy
.ifdef IPL_PUBLIC
    cmp #$E0
.else
    cmp #$80
.endif
    beq @eq
    bcc @rok
    lda #23
    jmp fail
@ovf:
    lda #23
    jmp fail
@eq:
    lda stav
    beq @rok
    lda #23
    jmp fail
@rok:
    ; IPLJMP: little-endian; must satisfy IPLSTA <= entry < IPLSTA + N*2048
    lda BUF+6
    sec
    sbc stav
    lda BUF+7
    sbc stav+1
    bcs @entge
    lda #24
    jmp fail
@entge:
    cmp scr
    bcc @entok
    lda #24
    jmp fail
@entok:
    lda BUF+6
    sta R_ENTRY
    lda BUF+7
    sta R_ENTRY+1
    ; MPR2..6. DEV format (default): each byte must already be a RAM bank in $80-$87 (Phase 1 observation).
    ; PUBLIC format (IPL_PUBLIC, Issue #635): each byte is a bank index 0..7; the BIOS maps it to $80+index
    ; (observed on the pinned core with a pure System Card BIOS; see the store block below).
    ldx #0
@mpr:
    lda BUF+8,x
.ifdef IPL_PUBLIC
    cmp #$08
    bcc @mprok
.else
    and #$F8
    cmp #$80
    beq @mprok
.endif
    lda #25
    jmp fail
@mprok:
    inx
    cpx #5
    bne @mpr
    lda BUF+8
    sta m2
    lda BUF+9
    sta m3
    lda BUF+10
    sta m4
    lda BUF+11
    sta m5
    lda BUF+12
    sta m6
.ifdef IPL_PUBLIC
    ; PUBLIC: bank = $80 + index for index 0..7 (equal to index OR $80 in this range)
    lda m2
    ora #$80
    sta m2
    lda m3
    ora #$80
    sta m3
    lda m4
    ora #$80
    sta m4
    lda m5
    ora #$80
    sta m5
    lda m6
    ora #$80
    sta m6
.endif
    lda BUF+13               ; OPENMODE: DEV loader supports none
    beq @omok
    lda #26
    jmp fail
@omok:
.ifndef IPL_PUBLIC
    lda BUF+14               ; DEV extension: body checksum (16-bit sum) in reserved bytes 14..15
    sta hdrv
    sta R_HDR
    lda BUF+15
    sta hdrv+1
    sta R_HDR+1
.endif
    rts

; ---------------------------------------------------------------------------
; Media-end pre-check (Issue #670): READ(6) the FINAL body sector (body + nsec - 1) into BUF, the staging buffer,
; before the first load-area write. A failure halts through read_sector with R_COPIED = 0. This is a runtime
; reachability probe only: the guest cannot read the TOC or the data-track length, so it does not prove that the
; sector lies inside the data track (e.g. a following audio track), nor that the sectors in between are readable.
; Issue #744: the address is the full 24-bit body base plus nsec minus 1 (no 16-bit truncation).
probe_last:
    lda #4
    sta R_STAGE
    lda bodyl                ; body + nsec - 1: validate_info guarantees body + nsec <= $200000
    clc                      ; with nsec >= 1, so no borrow out of 24 bits
    adc nsec
    sta lbalo
    lda bodyl+1
    adc #0
    sta lbamid
    lda bodyh
    adc #0
    sta lbahi
    lda lbalo
    sec
    sbc #1
    sta lbalo
    lda lbamid
    sbc #0
    sta lbamid
    lda lbahi
    sbc #0
    sta lbahi
    jsr read_sector
    rts

; ---------------------------------------------------------------------------
; Read each body sector into BUF, copy it to the load area, summing the source bytes.
copy_body:
    lda #5
    sta R_STAGE
    lda stav
    sta dst
    sta R_DST
    lda stav+1
    sta dst+1
    sta R_DST+1
    lda #0
    sta sumv
    sta sumv+1
    sta idx
@sector:
    lda bodyl
    clc
    adc idx
    sta lbalo
    lda bodyl+1
    adc #0
    sta lbamid
    lda bodyh
    adc #0
    sta lbahi
    jsr read_sector
    lda #<BUF
    sta src
    lda #>BUF
    sta src+1
    lda #0
    sta cnt
    lda #8
    sta cnt+1
    lda #$FF
    sta curreg
@byte:
    lda dst+1
    and #$E0
    cmp curreg
    beq @same
    sta curreg
    jsr switch_region
@same:
    lda (src)
    sta byt
    clc
    adc sumv
    sta sumv
    bcc @nc
    inc sumv+1
@nc:
    lda byt
.ifdef IPL_MUTANT_READBACK
    eor #$01                 ; TEST-ONLY fault injection: the written byte differs from the summed byte
.endif
    sta (dst)
    inc src
    bne @s
    inc src+1
@s:
    inc dst
    bne @d
    inc dst+1
@d:
    lda cnt
    bne @c
    dec cnt+1
@c:
    dec cnt
    lda cnt
    ora cnt+1
    bne @byte
    inc R_COPIED
    inc idx
    lda idx
    cmp nsec
    bne @sector
    lda sumv
    sta R_SRC
    lda sumv+1
    sta R_SRC+1
    rts

; Read the destination back through the same bank mapping and compare both sums with the header.
verify_body:
    lda #6
    sta R_STAGE
.ifdef IPL_PUBLIC
    ; PUBLIC: no header sum exists. The reference is the sum of the disc bytes read (R_SRC). Only the copy is
    ; verified (readback == source). A sum-preserving change on the disc is NOT detected in this format.
    lda R_SRC
    sta hdrv
    lda R_SRC+1
    sta hdrv+1
.endif
    lda stav
    sta dst
    lda stav+1
    sta dst+1
    lda #0
    sta rdv
    sta rdv+1
    sta cnt
    lda nsec
    asl
    asl
    asl
    sta cnt+1
    lda #$FF
    sta curreg
@rbyte:
    lda dst+1
    and #$E0
    cmp curreg
    beq @rsame
    sta curreg
    jsr switch_region
@rsame:
    lda (dst)
    clc
    adc rdv
    sta rdv
    bcc @rn
    inc rdv+1
@rn:
    inc dst
    bne @rd
    inc dst+1
@rd:
    lda cnt
    bne @rc
    dec cnt+1
@rc:
    dec cnt
    lda cnt
    ora cnt+1
    bne @rbyte
    lda rdv
    sta R_RD
    lda rdv+1
    sta R_RD+1
    lda R_SRC
    cmp hdrv
    bne @bsrc
    lda R_SRC+1
    cmp hdrv+1
    bne @bsrc
    lda rdv
    cmp hdrv
    bne @brd
    lda rdv+1
    cmp hdrv+1
    bne @brd
    rts
; Code 30 is reachable only in the DEV build. In the PUBLIC build hdrv is loaded from R_SRC just above
; (verify_body), so the first comparison is always equal and this branch is not taken. R_HDR (RB+10) is never
; written in the PUBLIC build and is not judged there.
@bsrc:
    lda #30                  ; body sum from disc differs from the header: copied, NOT executed
    jmp fail
@brd:
    lda #31                  ; readback differs from the sum read from disc: copied, NOT executed
    jmp fail

; Select the bank for the 8 KiB region in curreg. DEV: $20/$40/$60 only.
; PUBLIC adds $80 (MPR4), $A0 (MPR5), $C0 (MPR6) for IPLs larger than $3000-$7FFF.
switch_region:
    lda curreg
    cmp #$20
    bne @n40
    lda #$F8
    tam #$02
    rts
@n40:
    cmp #$40
    bne @n60
    lda m2
    tam #$04
    rts
@n60:
    cmp #$60
.ifdef IPL_PUBLIC
    bne @n80
.else
    bne @bad
.endif
    lda m3
    tam #$08
    rts
.ifdef IPL_PUBLIC
@n80:
    cmp #$80
    bne @na0
    lda m4
    tam #$10
    rts
@na0:
    cmp #$A0
    bne @nc0
    lda m5
    tam #$20
    rts
@nc0:
    cmp #$C0
    bne @bad
    lda m6
    tam #$40
    rts
.endif
@bad:
    lda #29
    jmp fail

; Enter the loaded code. Only reached after every sector was copied and both sums matched.
launch:
    lda #7
    sta R_STAGE
    lda m2
    tam #$04
    lda m3
    tam #$08
    lda m4
    tam #$10
    lda m5
    tam #$20
    lda m6
    tam #$40
    lda #0
    sta R_CODE
    lda #8
    sta R_STAGE
    ldx #$FF
    txs
    ldx #0
    ldy #0
    lda #0
    jmp (R_ENTRY)

; ---------------------------------------------------------------------------
; Read one 2048-byte sector at lbamid:lbalo into BUF using the polled CD-ROM2 protocol
; (same phases as src/cd_load_exec_probe.s; copied, not linked). Failures halt; they never return.
read_sector:
    lda #<BUF
    sta ptr
    lda #>BUF
    sta ptr+1
    ldx #8
    lda #$5A
    ldy #0
@fill:
    sta (ptr),y
    iny
    bne @fill
    inc ptr+1
    dex
    bne @fill
    lda #<BUF
    sta ptr
    lda #>BUF
    sta ptr+1
    lda #0
    sta remain
    lda #>SECTOR
    sta remain+1
    lda #$08                 ; READ(6): LBA is 21 bits; byte 1 carries LBA[20:16], never LUN/high bits
    sta R_CDB
    lda lbahi
    and #$1F
    sta R_CDB+1
    lda lbamid
    sta R_CDB+2
    lda lbalo
    sta R_CDB+3
    lda #1
    sta R_CDB+4
    lda #0
    sta R_CDB+5
    lda #$02
    sta CD_RESET
    jsr delay
    lda #0
    sta CD_RESET
    jsr delay
    lda #PH_MASK
    sta wmask
    lda #PH_FREE
    sta want
    jsr wait_phase
    bcc @selecting
    lda #1
    jmp fail
@selecting:
    lda #$81
    sta CD_DATA
    sta CD_STATUS
    lda #PH_MASK
    sta wmask
    lda #PH_COMMAND
    sta want
    jsr wait_phase
    bcc @send
    lda #2
    jmp fail
@send:
    ldy #0
@cmd:
    lda #PH_MASK
    sta wmask
    lda #PH_COMMAND
    sta want
    jsr wait_phase
    bcc @put
    lda #3
    jmp fail
@put:
    lda R_CDB,y
    sta CD_DATA
    jsr ack_byte
    bcc @next
    lda #10
    jmp fail
@next:
    iny
    cpy #6
    bne @cmd
@data:
    lda #PH_MASK
    sta wmask
    lda #PH_DATA_IN
    sta want
    lda #PH_STATUS           ; STATUS before all bytes = short transfer
    sta alt
    jsr wait_phase
    bcc @take
    lda last
    and #PH_MASK
    cmp #PH_STATUS
    beq @premature
    lda #4
    jmp fail
@premature:
    lda #5
    jmp fail
@take:
    lda CD_DATA
    sta (ptr)
    inc ptr
    bne @counted
    inc ptr+1
@counted:
    jsr ack_byte
    bcc @more
    lda #10
    jmp fail
@more:
    lda remain
    bne @low
    dec remain+1
@low:
    dec remain
    lda remain
    ora remain+1
    bne @data
    lda #PH_MASK
    sta wmask
    lda #PH_STATUS
    sta want
    jsr wait_phase
    bcs @tail_timeout
    lda CD_DATA
    sta R_SCSI
    jsr ack_byte
    bcs @tail_timeout
    lda #PH_MASK
    sta wmask
    lda #PH_MSG_IN
    sta want
    jsr wait_phase
    bcs @tail_timeout
    lda CD_DATA
    sta R_MSG
    jsr ack_byte
    bcs @tail_timeout
    lda #PH_MASK
    sta wmask
    lda #PH_FREE
    sta want
    jsr wait_phase
    bcs @tail_notfree
    lda R_SCSI
    ora R_MSG
    beq @done
    lda #6
    jmp fail
@tail_timeout:
    lda #7
    jmp fail
@tail_notfree:
    lda #8
    jmp fail
@done:
    lda last
    sta R_LAST
    rts

; A = error code. Writes the result block and halts. Never returns.
fail:
    sta R_CODE
    lda last
    sta R_LAST
cd_halt_fail:
    bra cd_halt_fail

; Bounded poll of $1800: (status & wmask) == want. C clear = reached, C set = timeout.
; `alt` (one-shot) names a phase that aborts the wait early with C set.
wait_phase:
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

; Manual REQ/ACK handshake. C set = REQ never dropped.
ack_byte:
    lda #$80
    sta CD_ACK
    lda #SCSI_REQ
    sta wmask
    lda #0
    sta want
    jsr wait_phase
    lda #$00                 ; LDA/STA keep the carry from wait_phase
    sta CD_ACK
    rts

delay:
    ldy #$20
@outer:
    ldx #0
@inner:
    dex
    bne @inner
    dey
    bne @outer
    rts

cd_unexpected_interrupt:
    bra cd_unexpected_interrupt

irq_stub:
    jmp cd_unexpected_interrupt

sig:
    .byte "PC Engine CD-ROM SYSTEM"

.segment "VECT"
    .word irq_stub, irq_stub, irq_stub, irq_stub, reset
