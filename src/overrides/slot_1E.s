; EX_GETVER — System Card version query, slot $1E, candidate entry $E05A.
;
; Independent OSS implementation (Issue #40). It is written from the measured
; I/O contract, NOT from any proprietary BIOS code:
;   * major version in X, minor version in Y,
;   * A preserved,
;   * plain RTS return with a balanced stack.
; The contract was measured against the private reference BIOS with the pinned
; Geargrafx core (slot $1E -> $F02D reads $FEC2/$FEC3; X=3, Y=0, A preserved).
; This is evidence for one slot only; real-hardware behaviour and ABI approval
; remain PENDING and this ROM stays DEV ONLY.
.setcpu "huc6280"
.segment "OVERRIDES"
.export api_slot_1E
api_slot_1E:
    ldx #major_version   ; reported System Card major version
    ldy #minor_version   ; reported System Card minor version
    rts

; Target edition of this firmware project (System Card 3.0).
major_version = 3
minor_version = 0
