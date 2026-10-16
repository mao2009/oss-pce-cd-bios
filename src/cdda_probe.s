; Original DEVELOPMENT-ONLY CD-DA hardware observation diagnostic (Issue #15).
; NOT a production BIOS, NOT CD_PLAY ($06) / CD_PAUSE ($08) and NOT a System Card ABI.
; It issues raw SCSI commands on the CD bus ($1800-$1804) so the emulator's CD-DA state can be
; observed from outside: READ(6) on an audio LBA, then $D8 play, $DD Q-subcode, $DA pause, $DD,
; $D9 stop, $DD. The $D8/$D9/$DA/$DD field layouts were read from the pinned Geargrafx source
; (scsi_controller.cpp) and are emulator facts only; no code was transcribed.
; Polling only (IRQs masked, I flag set), bounded waits, same handshake as src/cd_read_probe.s.
.setcpu "huc6280"
.segment "BOOT"
.export cdda_entry, cdda_cmd_done, cdda_wait_done, cdda_halt_ok, cdda_halt_fail
.export cdda_unexpected_interrupt, cdda_table

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
ptr    = $04             ; data-in destination
remain = $06             ; data-in bytes still allowed (16-bit)
wmask  = $08
want   = $09
last   = $0A
entry  = $0C             ; offset of the current table entry
wait_n = $0D             ; delay units left
RES    = $2300           ; RES+0 code ($FF running, 0 ok), RES+1 failing command index
SLOT   = $2310           ; per command: status, message, bytes lo, bytes hi
BUF    = $2800           ; data-in buffer (READ(6) sector), sentinel-filled
BUF_PAGES = 9            ; 2048 bytes + one guard page
ENTRY_BYTES = 16         ; table: cdb length, dest lo, dest hi, cap lo, cap hi, delay units, 10 CDB bytes

cdda_entry:
    sei
    cld
    csh
    lda #$FF
    tam #$01
    lda #$F8
    tam #$02
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
    tam #$80
    ldx #$FF
    txs
    lda #$00                 ; clear $2300-$23FF
    tax
@clear:
    sta RES,x
    inx
    bne @clear
    lda #$FF
    sta RES
    lda #$00
    sta CD_ACK
    lda #<BUF
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
    lda #$02                 ; SCSI bus reset pulse, then wait for BUS FREE
    sta CD_RESET
    jsr delay
    lda #$00
    sta CD_RESET
    jsr delay
    lda #PH_FREE
    jsr wait_for
    bcc @start
    lda #1
    jmp fail
@start:
    lda #0
    sta entry
main_loop:
    ldx entry
    lda cdda_table,x
    beq main_done            ; cdb length 0 terminates the table
    jsr run_command
    bcc @ok
    jmp fail                 ; A = error code
@ok:
    txa                      ; X = command index for the observer
    ldx entry
    pha
    lda cdda_table+5,x
    sta wait_n
    plx
cdda_cmd_done:
    nop
    lda wait_n
    beq main_next
main_units:
    jsr delay_unit
    dec wait_n
    bne main_units
cdda_wait_done:
    nop
main_next:
    lda entry
    clc
    adc #ENTRY_BYTES
    sta entry
    bra main_loop
main_done:
    lda #0
    sta RES
    ldx #$C0
cdda_halt_ok:
    bra cdda_halt_ok

; A = error code, RES+1 = failing command index. Never returns.
fail:
    sta RES
    tax
cdda_halt_fail:
    bra cdda_halt_fail

; Runs table entry `entry`. C clear = GOOD status and COMMAND COMPLETE; C set = A holds the error code.
; Returns X = command index (entry / 16).
run_command:
    ldx entry
    lda cdda_table+1,x
    sta ptr
    lda cdda_table+2,x
    sta ptr+1
    lda cdda_table+3,x
    sta remain
    lda cdda_table+4,x
    sta remain+1
    txa
    lsr a
    lsr a
    lsr a
    lsr a
    sta RES+1
    lda #$81
    sta CD_DATA
    sta CD_STATUS            ; selection
    lda #PH_COMMAND
    jsr wait_for
    bcc @send
    lda #2
    rts
@send:
    ldy #0
@cmd:
    lda #PH_COMMAND
    jsr wait_for
    bcc @put
    lda #3
    rts
@put:
    phy
    tya
    clc
    adc entry
    tax
    lda cdda_table+6,x
    sta CD_DATA
    ply
    jsr ack_byte
    bcc @next
    lda #10
    rts
@next:
    iny
    tya
    ldx entry
    cmp cdda_table,x
    bne @cmd
@phase:                      ; DATA IN bytes until STATUS (a $D8 play waits here through BUSY)
    jsr poll_phase
    bcc @got
    lda #4
    rts
@got:
    cmp #PH_STATUS
    beq @status
    lda remain
    ora remain+1
    bne @take
    lda #5                   ; more data than this command allows
    rts
@take:
    lda CD_DATA
    sta (ptr)
    inc ptr
    bne @stored
    inc ptr+1
@stored:
    lda remain
    bne @low
    dec remain+1
@low:
    dec remain
    jsr ack_byte
    bcc @phase
    lda #10
    rts
@status:
    ldx RES+1                ; slot = SLOT + 4 * index
    txa
    asl a
    asl a
    tax
    lda CD_DATA
    sta SLOT,x
    phx
    jsr ack_byte
    plx
    bcs @tail
    lda #PH_MSG_IN
    jsr wait_for
    bcs @tail
    lda CD_DATA
    sta SLOT+1,x
    phx
    jsr ack_byte
    plx
    bcs @tail
    lda #PH_FREE
    jsr wait_for
    bcs @tail
    phx                      ; bytes transferred = dest pointer - table dest
    ldx entry
    sec
    lda ptr
    sbc cdda_table+1,x
    ply
    sta SLOT+2,y
    lda ptr+1
    sbc cdda_table+2,x
    sta SLOT+3,y
    tya
    tax
    lda SLOT,x
    ora SLOT+1,x
    bne @bad
    ldx RES+1
    clc
    rts
@bad:
    lda #6
    sec
    rts
@tail:
    lda #7
    sec
    rts

; Wait until (status & PH_MASK) is DATA IN or STATUS. C clear: A = that phase.
poll_phase:
    lda #0
    sta tmo0
    sta tmo1
    lda #TMO_HI
    sta tmo2
@poll:
    lda CD_STATUS
    sta last
    and #PH_MASK
    cmp #PH_STATUS
    beq @hit
    cmp #PH_DATA_IN
    beq @hit
    jsr tick
    bcc @poll
    rts
@hit:
    clc
    rts

; A = wanted phase under PH_MASK. C clear = reached.
wait_for:
    sta want
    lda #PH_MASK
    sta wmask
wait_masked:
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
    beq @hit
    jsr tick
    bcc @poll
    rts
@hit:
    clc
    rts

; 24-bit timeout counter. C set = expired.
tick:
    lda tmo0
    bne @d0
    lda tmo1
    bne @d1
    lda tmo2
    beq @out
    dec tmo2
@d1:
    dec tmo1
@d0:
    dec tmo0
    clc
    rts
@out:
    sec
    rts

ack_byte:
    lda #$80
    sta CD_ACK
    lda #SCSI_REQ
    sta wmask
    lda #0
    sta want
    jsr wait_masked
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

; 65536 DEX/BNE iterations (~55 ms of CPU time at 7.16 MHz); lets the drive run between commands.
delay_unit:
    ldy #0
@outer:
    ldx #0
@inner:
    dex
    bne @inner
    dey
    bne @outer
    rts

cdda_unexpected_interrupt:
    ldx #$EE
@halt:
    bra @halt

; Command script. LBA 16 is the first sector of the fixture's audio track 02 (16 data + 64 audio sectors).
; $D8/$D9 byte 9 = $00 selects LBA addressing (bytes 3-5) in the pinned emulator.
cdda_table:
    .byte 6                                       ; 0: READ(6) LBA 16 x1 -> BUF (audio sector read as data)
    .word BUF, 2048
    .byte 0
    .byte $08, $00, $00, $10, $01, $00, 0, 0, 0, 0
    .byte 10                                      ; 1: $D8 start position, byte1=$01 (play), LBA 16
    .word 0, 0
    .byte 12                                      ;    then let it play ~0.66 s
    .byte $D8, $01, $00, $00, $00, $10, $00, $00, $00, $00
    .byte 10                                      ; 2: $DD Q-subcode -> $2380
    .word $2380, 10
    .byte 0
    .byte $DD, $0A, $00, $00, $00, $00, $00, $00, $00, $00
    .byte 10                                      ; 3: $DA pause
    .word 0, 0
    .byte 4                                       ;    then wait ~0.22 s paused
    .byte $DA, $00, $00, $00, $00, $00, $00, $00, $00, $00
    .byte 10                                      ; 4: $DD -> $2390
    .word $2390, 10
    .byte 0
    .byte $DD, $0A, $00, $00, $00, $00, $00, $00, $00, $00
    .byte 10                                      ; 5: $D9 stop position, byte1=$00 (stop now)
    .word 0, 0
    .byte 2
    .byte $D9, $00, $00, $00, $00, $00, $00, $00, $00, $00
    .byte 10                                      ; 6: $DD -> $23A0
    .word $23A0, 10
    .byte 0
    .byte $DD, $0A, $00, $00, $00, $00, $00, $00, $00, $00
    .byte 0

.segment "VECTORS"
    .word cdda_unexpected_interrupt
    .word cdda_unexpected_interrupt
    .word cdda_unexpected_interrupt
    .word cdda_unexpected_interrupt
    .word cdda_entry
