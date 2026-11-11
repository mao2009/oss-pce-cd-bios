; Issue #756: synthetic payload for the slot-$02 RETURNING experiment.
; NEVER linked into any BIOS. Proves the experiment instrument itself:
; a JSR to the patched slot-$02 fallback returns (RTS), execution resumes,
; and the NEXT BIOS call is observed in order.
;
; Modes via defines (SEQ2/SEQ3 addresses supplied by the harness as -D):
;   SEQ   : JSR SEQ2 (slot $02, returns on experiment ROM); set A=$CC to mark
;           resumption; JSR SEQ3 (slot $03, unimplemented -> stub halt X=$03);
;           write MARK and halt at payload_halt.
;           On the BASE (unpatched) ROM the same bytes halt at the
;           api_unimplemented stub with X=$02 after exactly one APICALL.
;   SPIN2 : JSR SEQ2, then spin at payload_halt (budget negative: a return
;           without a later halt must never be read as a halt).
.setcpu "huc6280"
.segment "CODE"

PRES = $2F80
MARK = $AB

.ifdef SEQ
.ifndef SEQ2
.error "SEQ requires -DSEQ2 (slot-$02 table entry)"
.endif
.ifndef SEQ3
.error "SEQ requires -DSEQ3 (slot-$03 table entry)"
.endif
payload_entry:
    lda #$AA
    ldx #$11
    ldy #$22
    jsr SEQ2
    lda #$CC
    ldx #$11
    ldy #$22
    jsr SEQ3
    lda #MARK
    sta PRES
payload_halt:
    bra payload_halt
.else
.ifdef SPIN2
.ifndef SEQ2
.error "SPIN2 requires -DSEQ2 (slot-$02 table entry)"
.endif
payload_entry:
    lda #$AA
    ldx #$11
    ldy #$22
    jsr SEQ2
payload_halt:
    bra payload_halt
.else
.error "SEQ or SPIN2 is required"
.endif
.endif
