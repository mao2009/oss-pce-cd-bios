; EX_BCDBIN — slot $3D, candidate entry $E0B7. DEV ROM only.
;
; Project-own routine (docs/ex-bcdbin.md), NOT the reference BIOS EX_BCDBIN ABI. The body is src/ex_bcdbin.s,
; included here so that it is assembled into OVERRIDES (the build rejects code outside that segment).
; Contract: A = packed BCD $00-$99 in; valid -> C=0, A = binary; invalid -> C=1, A unchanged; X/Y preserved.
; ABI remains UNVERIFIED and this ROM stays DEV ONLY (release_eligible: false).
.setcpu "huc6280"
.segment "OVERRIDES"
.export api_slot_3D
api_slot_3D:
    jmp ex_bcdbin
.include "../ex_bcdbin.s"
