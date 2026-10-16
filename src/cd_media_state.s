; Issue #144: firmware-side CD media-state observation point. DEVELOPMENT-ONLY; not a BIOS API, not CD_READ,
; not CD_BOOT. Called once from reset_entry (src/reset_entry.s built with -D CD_MEDIA_STATE).
; Basis: the READ(6) select/command/data/status sequence already run on the pinned Geargrafx by src/cd_read_probe.s
; (#5/#133, docs/cd-read-diagnostic.md); wait_phase/ack_byte are the same logic. No other SCSI command is sent:
; TEST UNIT READY answers GOOD with or without a disc in Geargrafx b49ae82a (source reading), and READ TOC ($DE)
; is unverified. Polling only; the I flag set by reset_entry stays set. Uses only MPR0 (I/O) and MPR1 (work RAM).
; Returns A = X = state; result block at $2300 (+0 state, +1 stage, +2/+3 bytes, +4 SCSI status, +5 message, +6 last $1800).
;   $01 MEDIA_SIGNATURE_OK  LBA 0 read, GOOD, user data starts with this project's synthetic-disc magic
;   $02 MEDIA_UNRECOGNISED  LBA 0 read, GOOD, magic absent (not a claim about real discs)
;   $03 NO_DATA             STATUS phase without DATA_IN, status GOOD, message 0 (Geargrafx no-disc answer)
;   $81..$8A                bus/timeout/status errors (never a media state); bus left as-is
.setcpu "huc6280"
.segment "BOOT"
.export cd_media_state, ms_select, ms_command_sent, ms_status, ms_done

CD_STATUS = $1800
CD_DATA   = $1801
CD_ACK    = $1802
CD_RESET  = $1804
SCSI_REQ  = $40
PH_MASK   = $F8
PH_FREE   = $00
PH_COMMAND = $D0
PH_DATA_IN = $C8
PH_STATUS  = $D8
PH_MSG_IN  = $F8
TMO_HI    = 8

tmo0 = $00
tmo1 = $01
tmo2 = $02
ptr    = $04
remain = $06
wmask  = $08
want   = $09
last   = $0A
alt    = $0B
RES    = $2300
BUF    = $2800
BUF_PAGES = 8

cd_media_state:
    lda #$00
    ldx #$20
@clear:
    sta RES-1,x
    dex
    bne @clear
    lda #$FF
    sta RES
    sta alt
    lda #$00
    sta CD_ACK               ; ACK off and every CD IRQ enable off
    lda #<BUF
    sta ptr
    lda #>BUF
    sta ptr+1
    ldx #BUF_PAGES
    lda #$5A                 ; sentinel: stale RAM can never look like a sector
    ldy #0
@fill:
    sta (ptr),y
    iny
    bne @fill
    inc ptr+1
    dex
    bne @fill
    lda #>BUF
    sta ptr+1
    lda #8                   ; 2048 bytes
    sta remain+1
    lda #0
    sta remain
    lda #1
    sta RES+1
    lda #$02
    sta CD_RESET
    jsr delay
    lda #$00
    sta CD_RESET
    jsr delay
    lda #PH_FREE
    jsr wait_full
    bcc @selecting
    lda #$81
    jmp ms_finish
@selecting:
    lda #2
    sta RES+1
    lda #$81
    sta CD_DATA
ms_select:
    sta CD_STATUS
    lda #PH_COMMAND
    jsr wait_full
    bcc @send
    lda #$82
    jmp ms_finish
@send:
    lda #3
    sta RES+1
    ldy #0
@cmd:
    lda #PH_COMMAND
    jsr wait_full
    bcc @put
    lda #$83
    jmp ms_finish
@put:
    lda ms_cdb,y
    sta CD_DATA
    jsr ack_byte
    bcc @next
    lda #$8A
    jmp ms_finish
@next:
    iny
    cpy #6
    bne @cmd
ms_command_sent:
    lda #4
    sta RES+1
@data:
    lda #PH_STATUS
    sta alt
    lda #PH_DATA_IN
    jsr wait_full
    bcc @take
    lda last
    and #PH_MASK
    cmp #PH_STATUS
    beq @status_now
    lda #$84
    jmp ms_finish
@status_now:
    lda remain+1             ; STATUS before any byte = no data; after some bytes = short read
    cmp #8
    beq ms_status
    lda #$85
    jmp ms_finish
@take:
    lda CD_DATA
    sta (ptr)
    inc ptr
    bne @counted
    inc ptr+1
@counted:
    jsr ack_byte
    bcc @more
    lda #$8A
    jmp ms_finish
@more:
    lda remain
    bne @low
    dec remain+1
@low:
    dec remain
    lda remain
    ora remain+1
    bne @data
ms_status:
    lda #5
    sta RES+1
    lda #PH_STATUS
    jsr wait_full
    bcs @tail_timeout
    lda CD_DATA
    sta RES+4
    jsr ack_byte
    bcs @tail_timeout
    lda #PH_MSG_IN
    jsr wait_full
    bcs @tail_timeout
    lda CD_DATA
    sta RES+5
    jsr ack_byte
    bcs @tail_timeout
    lda #PH_FREE
    jsr wait_full
    bcc @classify
    lda #$88
    jmp ms_finish
@tail_timeout:
    lda #$87
    jmp ms_finish
@classify:
    lda #6
    sta RES+1
    lda RES+4
    ora RES+5
    beq @good
    lda #$86
    jmp ms_finish
@good:
    lda remain+1
    cmp #8
    bne @compare
    lda #$03                 ; NO_DATA
    bra ms_finish
@compare:
    ldy #0
@cmp:
    lda BUF,y
    cmp ms_magic,y
    bne @unrecognised
    iny
    cpy #18
    bne @cmp
    lda #$01
    bra ms_finish
@unrecognised:
    lda #$02

; A = state. Stores it with the byte count and returns to reset_entry.
ms_finish:
    sta RES
    pha
    lda last
    sta RES+6
    sec
    lda ptr
    sbc #<BUF
    sta RES+2
    lda ptr+1
    sbc #>BUF
    sta RES+3
    pla
    tax
ms_done:
    rts

; A = wanted phase under PH_MASK.
wait_full:
    sta want
    lda #PH_MASK
    sta wmask
; Bounded poll of $1800: (status & wmask) == want. C clear = reached, C set = timeout or `alt` phase.
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

ack_byte:
    lda #$80
    sta CD_ACK
    lda #SCSI_REQ
    sta wmask
    lda #0
    sta want
    jsr wait_phase
    lda #$00
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

; READ(6) LBA 0, one sector.
ms_cdb:
    .byte $08, $00, $00, $00, $01, $00
; tools/make_cd_image.py MAGIC (project synthetic disc only).
ms_magic:
    .byte "OSSPCE-CD-TEST-V1", $00
