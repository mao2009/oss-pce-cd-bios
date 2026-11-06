; Issue #692: synthetic payload for the unimplemented-API stub test (DEV-only, no commercial content).
; Mode API_ENTRY: call one DEV jump-table entry (3 bytes per slot at $E000 + slot*3), then record a marker.
;   An unimplemented slot never returns, so the marker must NOT be written.
; Mode SPIN: a non-halting loop that is not a halt label (used to check that the budget is not misread as a stub).
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
.ifndef API_ENTRY
    .error "API_ENTRY (or SPIN) is required"
.endif
payload_entry:
    jsr API_ENTRY
    lda #MARK
    sta PRES                 ; reached only if the call returned
payload_halt:
    bra payload_halt
.endif
