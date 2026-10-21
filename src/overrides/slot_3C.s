; EX_BINBCD — slot $3C, candidate entry $E0B4. DEV ROM only.
;
; Project-own routine (docs/ex-binbcd-internal.md), NOT the reference BIOS EX_BINBCD ABI. The body is
; src/ex_binbcd.s, included here so that it is assembled into OVERRIDES (the build rejects code outside that segment).
; Contract: A = binary 0..99 in; valid -> C=0, A = packed BCD; out of range (100..255) -> C=1, A unchanged;
; X/Y preserved. ABI remains UNCONFIRMED and this ROM stays DEV ONLY (release_eligible: false).
.setcpu "huc6280"
.segment "OVERRIDES"
.export api_slot_3C
api_slot_3C:
    jmp ex_binbcd_packed
.include "../ex_binbcd.s"
