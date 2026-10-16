; Original DEVELOPMENT-ONLY CD-ROM2 bus-reset diagnostic for CD_RESET ($01, Issue #10).
; NOT a production BIOS, NOT the System Card CD_RESET API and NOT an approved ABI.
; Register/phase facts come from docs/cd-read-diagnostic.md (#5, measured in Geargrafx) and are
; re-measured here. Structure mirrors src/cd_read_probe.s on purpose (same stage numbers, same
; result block, same bounded polling) so that the #5 judge (tools/cdread_verify.py) applies unchanged.
; Sequence: sentinel fill -> two SCSI bus-reset pulses ($1804 bit 1), each followed by a bounded wait
; for bus free -> one READ(6) after the resets -> success halt. Polling only, IRQs masked.
.setcpu "huc6280"
.segment "BOOT"
.export cd_entry, cd_stack_ready, cd_buffer_ready, cd_reset_done, cd_command_sent, cd_data_done
.export cd_halt_ok, cd_halt_fail, cd_unexpected_interrupt, cd_cdb, cd_select

; --- CD-ROM2 I/O (MPR0 = $FF maps hardware at $0000-$1FFF) ---
CD_STATUS = $1800        ; read: SCSI bus flags (mask $F8); write: selection
CD_DATA   = $1801
CD_ACK    = $1802        ; bit 7 = ACK, bits 0-6 = IRQ enable (kept 0)
CD_RESET  = $1804        ; bit 1 = SCSI bus reset

SCSI_REQ  = $40
PH_MASK   = $F8          ; BSY REQ MSG C/D I/O
PH_FREE   = $00
PH_COMMAND = $D0
PH_DATA_IN = $C8
PH_STATUS  = $D8
PH_MSG_IN  = $F8
TMO_HI    = 8            ; wait budget = TMO_HI * 65536 polls
RESET_CYCLES = 2

; --- zero page (CPU $2000-$20FF); operands are offsets into it ---
tmo0 = $00
tmo1 = $01
tmo2 = $02
ptr    = $04
remain = $06
wmask  = $08
want   = $09
last   = $0A
alt    = $0B             ; one-shot early-abort phase ($FF = none)
cycle  = $0C             ; reset pulses completed
coff   = $0D             ; 4 * cycle: index into the per-cycle record

; --- result block (work RAM, CPU $2300) ---
; +0 code (0 OK, $FF in progress) +1 stage +2/3 bytes LE +4 SCSI status +5 message +6 last $1800
; +7 reset pulses completed
; +8+8n .. +13+8n per reset pulse n: +0 bus while RST asserted, +1 bus after release,
;                                     +2 bus-free wait (0 reached, 1 timeout),
;                                     +3 $1804 readback while asserted, +4 $1804 readback after release
RES       = $2300
BUF       = $2800        ; destination: 3 sectors max ($2800-$3FFF)
BUF_PAGES = 24

cd_entry:
    sei
    cld
    csh
    lda #$FF
    tam #$01                 ; MPR0: hardware IO
    lda #$F8
    tam #$02                 ; MPR1: work RAM, including CPU stack
    lda #$01
    tam #$04
    lda #$02
    tam #$08
    lda #$1F
    tam #$10
    lda #$03
    tam #$20
    lda #$04
    tam #$40
    lda #$00
    tam #$80                 ; keep reset vectors in ROM bank 0
    ldx #$FF
    txs
cd_stack_ready:
    lda #$00                 ; clear result block
    ldx #$20
@clear:
    sta RES-1,x
    dex
    bne @clear
    lda #$FF
    sta RES                  ; $FF = in progress; only the success path writes 0
    sta alt
    lda #$00
    sta CD_ACK               ; ACK off and every CD IRQ enable off
    lda #<BUF                ; sentinel so stale RAM can never pass
    sta ptr
    lda #>BUF
    sta ptr+1
    ldx #BUF_PAGES
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
cd_buffer_ready:
    lda #1
    sta RES+1
    lda cd_cdb+4             ; sector count: 1..3 fits the buffer
    beq @reject
    cmp #4
    bcc @sized
@reject:
    lda #9
    jmp fail
@sized:
    asl a
    asl a
    asl a                    ; count * 2048 -> high byte
    sta remain+1
    lda #0
    sta remain
    lda #0
    sta cycle
@reset_pulse:
    lda #2
    sta RES+1
    lda cycle                ; coff = 8 * cycle
    asl a
    asl a
    asl a
    sta coff
    lda #$02                 ; assert SCSI bus reset
    sta CD_RESET
    ldx coff
    lda CD_RESET
    sta RES+11,x             ; $1804 readback while asserted
    jsr delay
    ldx coff
    lda CD_STATUS
    and #PH_MASK
    sta RES+8,x              ; bus while reset is asserted
    lda #$00                 ; release
    sta CD_RESET
    ldx coff
    lda CD_RESET
    sta RES+12,x             ; $1804 readback after release
    jsr delay
    ldx coff
    lda CD_STATUS
    and #PH_MASK
    sta RES+9,x              ; bus right after release
    lda #PH_MASK
    sta wmask
    lda #PH_FREE
    sta want
    jsr wait_phase
    ldx coff
    bcc @free
    lda #1
    sta RES+10,x
    lda #1                   ; bus not free after this reset pulse
    jmp fail
@free:
    lda #0
    sta RES+10,x
    inc cycle
    lda cycle
    sta RES+7
    cmp #RESET_CYCLES
    bne @reset_pulse
cd_reset_done:
    lda #3
    sta RES+1
    lda #PH_MASK
    sta wmask
    lda #PH_FREE
    sta want
    jsr wait_phase           ; bus must still be free before selecting
    bcc selecting
    lda #1
    jmp fail
selecting:
    lda #$81                 ; ID bits on the data bus, then pulse selection
    sta CD_DATA
cd_select:
    sta CD_STATUS
    lda #PH_MASK
    sta wmask
    lda #PH_COMMAND
    sta want
    jsr wait_phase
    bcc @send
    lda #2                   ; target never entered COMMAND phase
    jmp fail
@send:
    lda #4
    sta RES+1
    ldy #0
@cmd:
    lda #PH_MASK
    sta wmask
    lda #PH_COMMAND
    sta want
    jsr wait_phase
    bcc @put
    lda #3                   ; REQ for command byte never arrived
    jmp fail
@put:
    lda cd_cdb,y
    sta CD_DATA
    jsr ack_byte
    bcc @next
    lda #10
    jmp fail
@next:
    iny
    cpy #6
    bne @cmd
cd_command_sent:
    lda #5
    sta RES+1
@data:
    lda #PH_MASK
    sta wmask
    lda #PH_DATA_IN
    sta want
    lda #PH_STATUS           ; a STATUS phase here means no (more) data will come
    sta alt
    jsr wait_phase
    bcc @take
    lda last
    and #PH_MASK
    cmp #PH_STATUS
    beq @premature
    lda #4                   ; data never arrived
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
cd_data_done:
    lda #6
    sta RES+1
    lda #PH_MASK
    sta wmask
    lda #PH_STATUS
    sta want
    jsr wait_phase
    bcs tail_timeout
    lda CD_DATA
    sta RES+4                ; SCSI status byte (0 = GOOD)
    jsr ack_byte
    bcs tail_timeout
    lda #7
    sta RES+1
    lda #PH_MASK
    sta wmask
    lda #PH_MSG_IN
    sta want
    jsr wait_phase
    bcs tail_timeout
    lda CD_DATA
    sta RES+5                ; message byte (0 = COMMAND COMPLETE)
    jsr ack_byte
    bcs tail_timeout
    lda #8
    sta RES+1
    lda #PH_MASK
    sta wmask
    lda #PH_FREE
    sta want
    jsr wait_phase
    bcs tail_not_free
    lda RES+4
    ora RES+5
    bne tail_bad_status
    lda #9
    sta RES+1
    lda #0
    sta RES                  ; success: resets done, READ(6) complete, GOOD, bus free
    jsr store_counts
    ldx #$C0
cd_halt_ok:
    bra cd_halt_ok
tail_timeout:
    lda #7
    jmp fail
tail_not_free:
    lda #8
    jmp fail
tail_bad_status:
    lda #6
    jmp fail

; A = error code. Never returns.
fail:
    sta RES
    jsr store_counts
    tax
cd_halt_fail:
    bra cd_halt_fail

store_counts:
    lda last
    sta RES+6
    sec
    lda ptr
    sbc #<BUF
    sta RES+2
    lda ptr+1
    sbc #>BUF
    sta RES+3                ; bytes transferred (little endian)
    rts

; Bounded poll of $1800: (status & wmask) == want. C clear = reached, C set = timeout.
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

; Manual REQ/ACK handshake: ACK on, wait REQ low, ACK off. C set = REQ never dropped.
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
    ldx #$EE                 ; an interrupt is NOT success
@halt:
    bra @halt

; READ(6): opcode, LBA[20:16], LBA[15:8], LBA[7:0], count, control. Default: LBA 5, 1 sector.
cd_cdb:
    .byte $08, $00, $00, $05, $01, $00

.segment "VECTORS"
    .word cd_unexpected_interrupt ; IRQ2 / BRK
    .word cd_unexpected_interrupt ; IRQ1
    .word cd_unexpected_interrupt ; Timer
    .word cd_unexpected_interrupt ; NMI
    .word cd_entry                ; Reset
