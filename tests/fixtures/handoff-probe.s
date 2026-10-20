; Issue #242: synthetic ROM-to-RAM handoff probe (original code, test-only, NOT a BIOS service).
; Reset code copies a 16-byte payload from bank0 ($F100) into work RAM ($2200, via MPR1 = $F8)
; and JMPs there. The payload writes a marker at $2220-$2221 and returns to handoff_done in ROM.
; Only facts used: reset vectors $FFF6..$FFFF, MPR7=0, work RAM bank $F8 at CPU $2000-$3FFF.
; The host builder (tests/integration/geargrafx_handoff.py) patches bytes at these labels for negatives.
.setcpu "huc6280"
.export handoff_reset, handoff_bank, handoff_jump, handoff_done, handoff_trap
.export handoff_entry, handoff_payload_end
.import __RAMCODE_RUN__, __RAMCODE_SIZE__, __RAMCODE_LOAD__

.segment "BOOT"
handoff_reset:
    sei
    cld
    lda #$FF
    tam #$01                  ; MPR0 = hardware I/O bank
    lda #$F8
handoff_bank:
    tam #$02                  ; MPR1 = work RAM bank (CPU $2000-$3FFF); negative mutates the immediate
    ldx #$FF
    txs                       ; SP = $FF (stack page $2100)
    ldx #$00
handoff_copy:
    lda __RAMCODE_LOAD__,x
    sta __RAMCODE_RUN__,x
    inx
    cpx #<__RAMCODE_SIZE__
    bne handoff_copy
handoff_jump:
    jmp __RAMCODE_RUN__       ; negative mutates the low operand byte
handoff_done:
    bra handoff_done          ; control returned from the RAM payload
handoff_trap:
    bra handoff_trap          ; all IRQ/BRK vectors

.segment "RAMCODE"
handoff_entry:
    lda #$A5
    sta $2220
    lda #$5A
    sta $2221
    jmp handoff_done
handoff_payload_end:

.segment "VECTORS"
    .word handoff_trap        ; IRQ2 / BRK
    .word handoff_trap        ; IRQ1
    .word handoff_trap        ; Timer
    .word handoff_trap        ; NMI
    .word handoff_reset       ; Reset
