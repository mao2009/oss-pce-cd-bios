; Original DEVELOPMENT-ONLY CD load-and-execute experiment (Issues #14 and #5).
; NOT a production BIOS, NOT CD_READ ($03) and NOT an approved System Card ABI.
; Stage 0 (this ROM) reads ONE 2048-byte sector with READ(6) using the same polled CD-ROM2 protocol as
; src/cd_read_probe.s (copied, not linked: that file halts on success and cannot hand control onward),
; validates a small header, copies the code to RAM at EXEC and jumps there. The payload returns through
; a mailbox vector. Failures halt in cd_halt_fail with the result block set and never reach EXEC.
.setcpu "huc6280"
.segment "BOOT"
.export cd_entry, cd_stack_ready, cd_buffer_ready, cd_command_sent, cd_data_done
.export cd_halt_ok, cd_halt_fail, cd_unexpected_interrupt, cd_cdb, cd_select, cd_payload_return

; --- CD-ROM2 I/O (MPR0 = $FF maps hardware at $0000-$1FFF) ---
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
TMO_HI     = 8           ; bounded wait: TMO_HI * 65536 polls

; --- guest memory (work RAM $2000-$3FFF via MPR1) ---
tmo0 = $00
tmo1 = $01
tmo2 = $02
ptr    = $04             ; 16-bit destination pointer
remain = $06             ; 16-bit bytes still to read
wmask  = $08
want   = $09
last   = $0A
alt    = $0B
RES    = $2300           ; result block: [0] code, [1] stage, [2:3] bytes, [4] SCSI status, [5] msg, [6] last bus
MARK   = $3F00           ; payload marker (src/cd_load_exec_payload.s); inside the probe's $2800-$3FFF RAM dump
RETVEC = $3F10           ; payload returns with jmp (RETVEC)
EXECVEC = $3F12          ; stage 0 enters the payload with jmp (EXECVEC)
BUF    = $2800           ; staging: one 2048-byte sector
EXEC   = $3000           ; execution area in work RAM
SECTOR_BYTES = 2048
HDR_BYTES = 8            ; "PCEX", entry offset, code length, code checksum, reserved

cd_entry:
    sei
    cld
    csh
    lda #$FF
    tam #$01                 ; MPR0: hardware IO
    lda #$F8
    tam #$02                 ; MPR1: work RAM $2000-$3FFF
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
    tam #$80                 ; MPR7: bank 0 (this ROM, vectors)
    ldx #$FF
    txs                      ; stack $21FF, below nothing we use
cd_stack_ready:
    lda #$00
    ldx #$20
@clear:
    sta RES-1,x
    dex
    bne @clear
    lda #$FF
    sta RES                  ; $FF = in progress; only the final success path writes 0
    sta alt
    lda #$00
    sta CD_ACK
    lda #<BUF                ; sentinel-fill the staging sector so stale RAM cannot pass
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
cd_buffer_ready:
    lda #1
    sta RES+1
    lda cd_cdb+4             ; this experiment reads exactly one sector
    cmp #1
    bne @bad_request
    lda #0
    sta remain
    lda #>SECTOR_BYTES
    sta remain+1
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
    lda #3
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
    lda #PH_STATUS           ; STATUS before all bytes = short read
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
    sta RES+4
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
    sta RES+5
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
    jsr store_counts
    jmp load_check
tail_timeout:
    lda #7
    jmp fail
tail_not_free:
    lda #8
    jmp fail
tail_bad_status:
    lda #6
    jmp fail

; Header and code checks on the transferred sector. Any failure halts before EXEC is touched.
load_check:
    lda #11
    sta RES+1
    ldx #0
@magic:
    lda BUF,x
    cmp magic,x
    bne @bad_magic
    inx
    cpx #4
    bne @magic
    lda BUF+5                ; length must be 1..255
    beq @bad_length
    lda BUF+4                ; entry offset must lie inside the code
    cmp BUF+5
    bcs @bad_entry
    lda #0                   ; checksum over the code bytes in the sector
    ldy #0
@sum:
    clc
    adc BUF+HDR_BYTES,y
    iny
    cpy BUF+5
    bne @sum
    cmp BUF+6
    bne @bad_checksum
    lda #12
    sta RES+1
    ldy #0
@copy:
    lda BUF+HDR_BYTES,y
    sta EXEC,y
    iny
    cpy BUF+5
    bne @copy
    lda #13
    sta RES+1
    lda BUF+4                ; EXECVEC = EXEC + entry offset
    clc
    adc #<EXEC
    sta EXECVEC
    lda #>EXEC
    adc #0
    sta EXECVEC+1
    lda #<cd_payload_return
    sta RETVEC
    lda #>cd_payload_return
    sta RETVEC+1
    lda #14
    sta RES+1
    jmp (EXECVEC)
@bad_magic:
    lda #20                  ; sector does not start with "PCEX"
    jmp fail
@bad_length:
    lda #22
    jmp fail
@bad_entry:
    lda #21                  ; entry offset outside the code
    jmp fail
@bad_checksum:
    lda #23                  ; code bytes differ from the header checksum
    jmp fail

; Reached only through the payload's jmp (RETVEC). Checks the payload's observations.
cd_payload_return:
    lda #15
    sta RES+1
    lda #$A5
    cmp MARK
    bne payload_marker_bad
    lda #$5A
    cmp MARK+1
    bne payload_marker_bad
    lda #$F8                 ; MPR1 as seen by the payload (TMA #$02)
    cmp MARK+4
    bne payload_marker_bad
    lda #$FF                 ; stack pointer as seen by the payload
    cmp MARK+5
    bne payload_marker_bad
    lda #$3C
    cmp MARK+6
    bne payload_marker_bad
    lda #0
    sta RES
    lda #16
    sta RES+1
    jsr store_counts
cd_halt_ok:
    bra cd_halt_ok
payload_marker_bad:
    lda #30                  ; payload returned but its marker differs
    jmp fail

; A = error code. Never returns.
fail:
    sta RES
    jsr store_counts
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
    sta RES+3
    rts

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
    ldx #$EE                 ; an interrupt is NOT success
@halt:
    bra @halt

magic:
    .byte "PCEX"

; READ(6): opcode, LBA[20:16], LBA[15:8], LBA[7:0], count, control. Default: LBA 5, 1 sector.
cd_cdb:
    .byte $08, $00, $00, $05, $01, $00

.segment "VECTORS"
    .word cd_unexpected_interrupt ; IRQ2 / BRK
    .word cd_unexpected_interrupt ; IRQ1
    .word cd_unexpected_interrupt ; Timer
    .word cd_unexpected_interrupt ; NMI
.ifdef DEV_BOOT_INTERNAL
.import dev_boot_entry
    .word dev_boot_entry          ; Issue #501 DEV-only internal path (not compatible); default build keeps cd_entry
.else
    .word cd_entry                ; Reset
.endif
