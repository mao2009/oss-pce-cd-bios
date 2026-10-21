; Original DEVELOPMENT-ONLY reusable routine (Issue #300, parent #26). Project-owned HuC6280 code.
; NOT BIOS, NOT AD_TRANS ($11 / $E033), NOT a System Card ABI, release_eligible=false.
; Internal contract (self-defined, not a System Card ABI): docs/research/ad-trans-routine-contract.md.
; adt_dma_sector: one READ(6) of one sector, DATA-IN bytes moved into ADPCM RAM by the ADPCM DMA ($180B).
; Every wait is bounded; every failure after the bus is touched disarms the DMA and resets the bus.
; The boot/driver that calls this routine lives in src/adt_dma_routine_boot.s.
.setcpu "huc6280"
.export adt_dma_sector

CD_STATUS     = $1800    ; read: SCSI bus flags (mask $F8)
CD_DATA       = $1801
CD_ACK        = $1802    ; bit 7 = ACK (manual handshake, used for command, status and message bytes)
CD_RESET      = $1804    ; bit 1 = SCSI bus reset
ADPCM_ADDR_LO = $1808
ADPCM_ADDR_HI = $1809
ADPCM_DMA     = $180B    ; bit0 = DMA active
ADPCM_STAT    = $180C    ; bit2 = write slot busy
ADPCM_CTRL    = $180D    ; bit1 0->1 latches write address = ADDRESS

SCSI_REQ   = $40
PH_MASK    = $F8
PH_FREE    = $00
PH_COMMAND = $D0
PH_DATA_IN = $C8
PH_STATUS  = $D8
PH_MSG_IN  = $F8
TMO_HI     = 8            ; wait budget = TMO_HI * 65536 polls

; parameter block (caller writes, routine only reads)
PB          = $2200
PB_LBA_HI   = PB+0        ; LBA bits 20..16, must be <= $1F
PB_LBA_MID  = PB+1        ; LBA bits 15..8
PB_LBA_LO   = PB+2        ; LBA bits 7..0
PB_DST_LO   = PB+3        ; ADPCM RAM destination, low byte
PB_DST_HI   = PB+4        ; ADPCM RAM destination, high byte; dest + 2048 must be <= $10000
PB_ARM      = PB+5        ; 1 = arm the ADPCM DMA, 0 = never arm (test input); other values refused
PB_SLOT     = PB+6        ; result slot offset from RES_BASE: 0, 8, 16 or 24
CDB         = $2210       ; 6-byte READ(6) CDB built by the routine
RES_BASE    = $2300       ; 32-byte result area, 8 bytes per slot

; zero page (HuC6280 ZP = CPU $2000-$20FF); clobbered by the routine
tmo0  = $00
tmo1  = $01
tmo2  = $02
wmask = $03
want  = $04
stg   = $05               ; stage, see docs
bus_ok  = $06             ; 1 bus free and DMA idle at return, 0 not, $FF untouched
code  = $07               ; result code, also returned in A
sst   = $08               ; SCSI status byte
smsg  = $09               ; SCSI message byte
rp    = $0A               ; 16-bit pointer to the result slot ($0A lo, $0B hi)

.segment "BOOT"

; Input: parameter block at PB. Output: A = result code (0 = OK), C = 1 when A != 0.
; Preconditions: MPR0 = $FF (I/O), MPR1 = $F8 (work RAM), D flag clear, stack has 8 bytes free.
; Preserved: MPR0-MPR7, P.I, P.D. Clobbered: A, X, Y, zero page $00-$0B, $2210-$2215, RES_BASE slot.
adt_dma_sector:
    lda #0
    sta stg
    sta sst
    sta smsg
    lda #$FF
    sta bus_ok
    lda PB_SLOT              ; a slot other than 0/8/16/24 is refused (code 9) and written to slot 0
    and #$E7
    beq @slot_ok
    lda #0
    bra @slot_set
@slot_ok:
    lda PB_SLOT
@slot_set:
    clc
    adc #<RES_BASE           ; rp = RES_BASE + slot offset
    sta rp
    lda #>RES_BASE
    adc #0
    sta rp+1
    ldy #7                   ; clear the slot before anything can fail
    lda #0
@clr:
    sta (rp),y
    dey
    bpl @clr

    lda PB_SLOT              ; validate: slot, 21-bit LBA, arm in {0,1}, destination fits in 64 KiB
    and #$E7
    beq @slot_valid
    jmp bad_request
@slot_valid:
    lda PB_LBA_HI
    cmp #$20
    bcc @lba_ok
    jmp bad_request
@lba_ok:
    lda PB_ARM
    cmp #2
    bcc @arm_ok
    jmp bad_request
@arm_ok:
    lda PB_DST_HI
    cmp #$F8
    bcc @dst_ok
    bne @dst_bad
    lda PB_DST_LO
    beq @dst_ok
@dst_bad:
    jmp bad_request
@dst_ok:
    lda #$08                 ; READ(6) CDB, count fixed at 1 sector
    sta CDB
    lda PB_LBA_HI
    sta CDB+1
    lda PB_LBA_MID
    sta CDB+2
    lda PB_LBA_LO
    sta CDB+3
    lda #1
    sta CDB+4
    stz CDB+5

    lda #0                   ; from here on the bus may be touched: failures go through recover
    sta ADPCM_DMA            ; DMA off before the transfer
    sta CD_ACK
    lda #$02                 ; SCSI bus reset pulse
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
@selecting:
    lda #3
    sta stg
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
    sta stg
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
    lda CDB,y
    sta CD_DATA
    jsr ack_byte
    bcc @next
    lda #10
    jmp fail
@next:
    iny
    cpy #6
    bne @cmd

    lda #5
    sta stg
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
    sta stg
    lda #$00                 ; clear the write-latch bit so the next set latches
    sta ADPCM_CTRL
    lda PB_DST_LO
    sta ADPCM_ADDR_LO
    lda PB_DST_HI
    sta ADPCM_ADDR_HI
    lda #$03                 ; bit1 0->1: write address = ADDRESS
    sta ADPCM_CTRL
    lda PB_ARM
    beq @no_arm              ; arm = 0: DMA never enabled, the data phase cannot drain
    lda #$01                 ; DMA enable, while the data phase is active
    sta ADPCM_DMA
@no_arm:
    lda #7
    sta stg
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
    lda #8
    sta stg
    lda #PH_MASK
    sta wmask
    lda #PH_STATUS
    sta want
    jsr wait_phase
    bcc @status
    lda #7
    jmp fail
@status:
    lda CD_DATA
    sta sst                  ; SCSI status byte (0 = GOOD)
    jsr ack_byte
    bcc @msg
    lda #7
    jmp fail
@msg:
    lda #PH_MASK
    sta wmask
    lda #PH_MSG_IN
    sta want
    jsr wait_phase
    bcc @msg_byte
    lda #7
    jmp fail
@msg_byte:
    lda CD_DATA
    sta smsg                 ; message byte (0 = COMMAND COMPLETE)
    jsr ack_byte
    bcc @free
    lda #7
    jmp fail
@free:
    lda #PH_MASK
    sta wmask
    lda #PH_FREE
    sta want
    jsr wait_phase
    bcc @free_ok
    lda #8                   ; bus not free after the message phase
    jmp fail
@free_ok:
    lda sst
    ora smsg
    beq @good
    lda #6                   ; non-GOOD status or non-COMPLETE message
    jmp fail
@good:
    lda #0
    sta code                 ; success must not inherit an earlier call's code
    lda #9
    sta stg
    lda #1
    sta bus_ok
    lda #0
    jmp finish

; A = first error code. Recover the bus and DMA, then report.
fail:
    sta code
    jsr recover
    jmp finish

bad_request:                 ; refused before the bus is touched
    lda #9
    sta code
    jmp finish

; Disarm the DMA, reset the bus, and confirm bus free and DMA idle (all bounded).
recover:
    lda #0
    sta ADPCM_DMA
    lda #$02
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
    bcs @not_ok
    jsr wait_adpcm_drained
    bcs @not_ok
    lda #1
    sta bus_ok
    rts
@not_ok:
    lda #0
    sta bus_ok
    rts

; Write the result slot from the zero-page state and return A = code, C = (code != 0).
finish:
    ldy #0
    lda code
    sta (rp),y               ; [0] result code
    iny
    lda stg
    sta (rp),y               ; [1] stage at return
    iny
    lda sst
    sta (rp),y               ; [2] SCSI status
    iny
    lda smsg
    sta (rp),y               ; [3] SCSI message
    iny
    lda ADPCM_DMA
    sta (rp),y               ; [4] ADPCM_DMA at return
    iny
    lda ADPCM_STAT
    sta (rp),y               ; [5] ADPCM_STAT at return
    iny
    lda bus_ok
    sta (rp),y               ; [6] bus free and DMA idle at return
    iny
    lda PB_ARM
    sta (rp),y               ; [7] arm echo
    lda code
    bne @err
    clc
    rts
@err:
    sec
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

; Manual REQ/ACK handshake for one command, status or message byte. C set = REQ never dropped.
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
