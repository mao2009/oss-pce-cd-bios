; Original DEVELOPMENT-ONLY ADPCM DMA-from-CD guest (Issue #400 / #299).
; Project-owned HuC6280 code. NOT BIOS, NOT AD_TRANS ($11), NOT a System Card ABI, release_eligible=false.
; One READ(6) (SCSI via $1800-$1804, as src/cd_read_probe.s), then the DATA-IN bytes are moved into
; ADPCM RAM by the ADPCM DMA path: $1808/$1809 address, $180D latch, $180B DMA enable. The guest never
; reads $1801 in the data phase; the DMA unit pulls each byte itself (src/cd_read_probe.s is the
; reference for the SCSI steps only).
; cd_dma_enable = 0 is the negative case: the DMA is never armed, so the data phase cannot drain.
.setcpu "huc6280"
.segment "BOOT"
.export cd_entry, cd_stack_ready, cd_buffer_ready, cd_command_sent, cd_data_done
.export cd_halt_ok, cd_halt_fail, cd_unexpected_interrupt, cd_cdb, cd_dma_enable

CD_STATUS = $1800        ; read: SCSI bus flags (mask $F8)
CD_DATA   = $1801
CD_ACK    = $1802        ; bit 7 = ACK (manual handshake for status/message only)
CD_RESET  = $1804        ; bit 1 = SCSI bus reset
ADPCM_ADDR_LO = $1808    ; write: ADPCM ADDRESS low
ADPCM_ADDR_HI = $1809    ; write: ADPCM ADDRESS high
ADPCM_DMA = $180B        ; bit0 = DMA active (cleared by hardware when the data phase ends)
ADPCM_STAT = $180C       ; bit2 = write slot busy
ADPCM_CTRL = $180D       ; bit1 0->1 latches write address = ADDRESS (bit0 set: exact)

SCSI_REQ  = $40
PH_MASK   = $F8
PH_FREE   = $00
PH_COMMAND = $D0
PH_DATA_IN = $C8
PH_STATUS = $D8
PH_MSG_IN = $F8
TMO_HI    = 8            ; wait budget = TMO_HI * 65536 polls
ADPCM_DEST = $0100       ; first ADPCM RAM byte written by the DMA

; zero page (HuC6280 ZP = CPU $2000-$20FF)
tmo0 = $00
tmo1 = $01
tmo2 = $02
wmask = $08
want  = $09

; result block (work RAM $2300; read by the host probe)
RES = $2300              ; 0 = OK, $FF = in progress, else error code
; RES+1 stage, RES+4 SCSI status, RES+5 message, RES+7 DMA flag used, RES+8 ADPCM_DMA at drain, RES+9 ADPCM_STAT at drain

cd_entry:
    sei
    cld
    csh
    lda #$FF
    tam #$01                 ; MPR0: hardware IO
    lda #$F8
    tam #$02                 ; MPR1: work RAM (CPU stack included)
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
cd_stack_ready:
    lda #$00
    ldx #$20
@clear:
    sta RES-1,x
    dex
    bne @clear
    lda #$FF
    sta RES
    lda #$00
    sta CD_ACK
    sta ADPCM_DMA            ; DMA off before the transfer
    lda cd_dma_enable
    sta RES+7
cd_buffer_ready:
    lda #1
    sta RES+1
    lda cd_cdb+4             ; this guest transfers exactly one sector
    cmp #1
    bne @bad_request
    lda #2
    sta RES+1
    lda #$02                 ; pulse SCSI bus reset
    sta CD_RESET
    jsr delay
    lda #$00
    sta CD_RESET
    jsr delay
    lda #PH_MASK
    sta wmask
    lda #PH_FREE
    sta want
    jsr wait_phase
    bcc @selecting
    lda #1                   ; bus not free after reset
    jmp fail
@bad_request:
    lda #9
    jmp fail
@selecting:
    lda #3
    sta RES+1
    lda #$81                 ; initiator and target ID bits
    sta CD_DATA
    sta CD_STATUS            ; select
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
    lda #3                   ; REQ for a command byte never arrived
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
    lda #PH_MASK
    sta wmask
    lda #PH_DATA_IN
    sta want
    jsr wait_phase
    bcc @program
    lda #4                   ; data phase never entered
    jmp fail
@program:
    lda #6
    sta RES+1
    lda #$00                 ; clear the write-latch bit so the next set latches
    sta ADPCM_CTRL
    lda #<ADPCM_DEST
    sta ADPCM_ADDR_LO
    lda #>ADPCM_DEST
    sta ADPCM_ADDR_HI
    lda #$03                 ; bit1 0->1: write address = ADDRESS
    sta ADPCM_CTRL
    lda RES+7
    beq @no_arm              ; negative case: DMA deliberately not armed
    lda #$01                 ; DMA enable, while the data phase is active (IsDataReady)
    sta ADPCM_DMA
@no_arm:
cd_dma_armed:
    lda #7
    sta RES+1
    lda #PH_MASK
    sta wmask
    lda #PH_STATUS           ; data phase ends when the DMA has drained the sector
    sta want
    jsr wait_phase
    bcc @drain
    lda #11                  ; DMA not armed or data phase did not drain
    jmp fail
@drain:
    jsr wait_adpcm_drained
    bcc @drained
    lda #12                  ; ADPCM DMA or write slot never went idle
    jmp fail
@drained:
cd_data_done:
    lda #8
    sta RES+1
    lda ADPCM_DMA
    sta RES+8
    lda ADPCM_STAT
    sta RES+9
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
    sta RES                  ; success: DMA done, GOOD status, bus free
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
cd_halt_fail:
    bra cd_halt_fail

; Bounded poll of $1800: (status & wmask) == want. C clear = reached, C set = timeout.
wait_phase:
    lda #0
    sta tmo0
    sta tmo1
    lda #TMO_HI
    sta tmo2
@poll:
    lda CD_STATUS
    and wmask
    cmp want
    beq @reached
    jsr tick
    bcc @poll
    sec
    rts
@reached:
    clc
    rts

; Bounded poll: C clear when $180B bit0 and $180C bit2 are both clear (DMA and write slot idle).
wait_adpcm_drained:
    lda #0
    sta tmo0
    sta tmo1
    lda #TMO_HI
    sta tmo2
@poll:
    lda ADPCM_DMA
    and #$01
    bne @more
    lda ADPCM_STAT
    and #$04
    beq @drained
@more:
    jsr tick
    bcc @poll
    sec
    rts
@drained:
    clc
    rts

; One poll step of the shared countdown. C set when the budget is exhausted.
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

; Manual REQ/ACK handshake (status and message phases only). C set = REQ never dropped.
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

; READ(6): opcode, LBA[20:16], LBA[15:8], LBA[7:0], count, control. LBA 5, 1 sector.
cd_cdb:
    .byte $08, $00, $00, $05, $01, $00
; 1 = arm the ADPCM DMA (positive case); 0 = never arm (negative case). Patched by the driver.
cd_dma_enable:
    .byte $01

.segment "VECTORS"
    .word cd_unexpected_interrupt ; IRQ2 / BRK
    .word cd_unexpected_interrupt ; IRQ1
    .word cd_unexpected_interrupt ; Timer
    .word cd_unexpected_interrupt ; NMI
    .word cd_entry                ; Reset
