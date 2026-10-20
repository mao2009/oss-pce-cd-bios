; Issue #242: synthetic ROM-to-RAM handoff probe (original code, test-only, NOT a BIOS service).
; Reset code copies a 13-byte payload from bank0 (handoff_payload_rom) to work RAM $2200 (MPR1 = $F8)
; and JMPs there. The payload writes a marker at $2220-$2221 and JMPs back to handoff_done in ROM.
; The payload uses absolute addresses only, so its bytes are identical wherever it is placed.
; Only facts used: reset vectors $FFF6..$FFFF, MPR7=0, work RAM bank $F8 at CPU $2000-$3FFF.
; The host builder (tests/integration/geargrafx_handoff.py) patches bytes at these labels for negatives.
.setcpu "huc6280"
.export handoff_reset, handoff_bank, handoff_jump, handoff_done, handoff_trap
.export handoff_payload_rom, handoff_payload_end_rom

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
    lda handoff_payload_rom,x
    sta $2200,x               ; RAM entry $2200 (fixed by the contract)
    inx
    cpx #13                   ; payload length; the host checks it against the typed expectation
    bne handoff_copy
handoff_jump:
    jmp $2200                 ; negative mutates the low operand byte
handoff_done:
    bra handoff_done          ; control returned from the RAM payload
handoff_trap:
    bra handoff_trap          ; all IRQ/BRK vectors (never falls through into the payload data)

handoff_payload_rom:          ; executed only after being copied to $2200
    lda #$A5
    sta $2220
    lda #$5A
    sta $2221
    jmp handoff_done
handoff_payload_end_rom:

.segment "VECTORS"
    .word handoff_trap        ; IRQ2 / BRK
    .word handoff_trap        ; IRQ1
    .word handoff_trap        ; Timer
    .word handoff_trap        ; NMI
    .word handoff_reset       ; Reset
