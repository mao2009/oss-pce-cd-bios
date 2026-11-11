; Issue #756: synthetic payload for the slot-$02 RETURNING experiment.
; NEVER linked into any BIOS. Proves the experiment instrument itself:
; a JSR to the patched slot-$02 fallback returns (RTS), execution resumes,
; and the NEXT BIOS call is observed in order.
;
; Modes via defines (SEQ2/SEQ3/SEQ4 addresses supplied by the harness as -D):
;   SEQ   : JSR SEQ2 (slot $02, returns on experiment ROM); set A=$CC to mark
;           resumption; JSR SEQ3 (slot $03, unimplemented -> stub halt X=$03).
;           On the BASE (unpatched) ROM the same bytes halt at the
;           api_unimplemented stub with X=$02 after exactly one APICALL.
;   SEQ23 : JSR SEQ2 (slot $02); A=$CC; JSR SEQ3 (slot $33, returns only on
;           the two-slot experiment ROM); A=$DD; JSR SEQ4 (slot $03,
;           unimplemented -> stub halt X=$03); write MARK and halt.
;           Distinguishes three ROMs: base stops at the first API (X=$02),
;           slot-02-only stops at the second (X=$33), both-patched proceeds
;           past the second return to the third call (X=$03).
;   SPIN2 : JSR SEQ2, then spin at a non-halt label (budget negative: a
;           return without a later halt must never be read as a halt).
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
.ifdef SEQ23
.ifndef SEQ2
.error "SEQ23 requires -DSEQ2 (slot-$02 table entry)"
.endif
.ifndef SEQ3
.error "SEQ23 requires -DSEQ3 (slot-$33 table entry)"
.endif
.ifndef SEQ4
.error "SEQ23 requires -DSEQ4 (slot-$03 table entry)"
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
    lda #$DD
    ldx #$11
    ldy #$22
    jsr SEQ4
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
spin:
    bra spin
payload_halt:
    bra payload_halt
.else
.error "SEQ, SEQ23 or SPIN2 is required"
.endif
.endif
.endif
