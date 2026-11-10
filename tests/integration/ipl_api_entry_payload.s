; Issue #635: synthetic API-entry payload (DEV-only, no commercial content).
; Modes via defines:
;   JSR_SLOT  : JSR to the DEV jump-table entry for the given slot
;   JMP_SLOT  : JMP to the same entry (no return)
;   DIRECT_STUB : LDX #$02 then JMP to the common stub (STUB, provided by the
;                 harness as -DSTUB from the loader BIOS labels; no fallback).
;   NOCALL    : no BIOS entry; write marker and halt
;   SPIN      : non-halting loop (budget negative; never a halt label)
; Registers are preset so the entry observer can prove caller state is seen.
.setcpu "huc6280"
.segment "CODE"

PRES = $2F80
MARK = $AB

.ifdef SPIN
payload_entry:
    bra payload_entry
payload_halt:
    bra payload_halt
.else
.ifdef NOCALL
payload_entry:
    lda #$AA
    ldx #$11
    ldy #$22
    lda #MARK
    sta PRES
payload_halt:
    bra payload_halt
.else
  .ifdef JMP_SLOT
payload_entry:
    lda #$AA
    ldx #$11
    ldy #$22
    lda #MARK
    sta PRES
    jmp JMP_SLOT
payload_halt:
    bra payload_halt
  .else
    .ifdef DIRECT_STUB
.ifndef STUB
.error "DIRECT_STUB requires -DSTUB from the loader BIOS api_unimplemented label"
.endif
payload_entry:
    lda #$AA
    ldx #$02
    ldy #$22
    lda #MARK
    sta PRES
    jmp STUB
payload_halt:
    bra payload_halt
    .else
      .ifdef JSR_SLOT
payload_entry:
    lda #$AA
    ldx #$11
    ldy #$22
    jsr JSR_SLOT
    lda #MARK
    sta PRES
payload_halt:
    bra payload_halt
      .else
    .error "JSR_SLOT, JMP_SLOT, DIRECT_STUB or NOCALL is required"
      .endif
    .endif
  .endif
.endif
.endif
