; EX_JOYREP — slot $22, candidate entry $E066. DEV ROM only (Issue #44).
;
; Project-own routine, NOT the reference System Card EX_JOYREP ABI (remains UNVERIFIED).
; The published literature describes this entry as a plain RTS with no observable
; work; this DEV implementation follows that description and performs no I/O:
;
;   in : nothing read (A/X/Y are not inputs and are left untouched).
;   out: nothing written; N/Z/C/V/D/I reflect the caller's P (untouched, see below).
;   kept: A, X, Y, P, SP, and all MPRs. No zero-page, work-RAM, VRAM, PSG, joypad,
;         VDC, CD, ADPCM, or BURAM state is touched.
;   stack: balanced (the JSR return address is the only transient use; RTS returns
;          to the caller with SP unchanged). Caller SP (before JSR) must be >= $02
;          so the JSR/RTS pair itself is well-defined; no extra stack is consumed.
;   ABI remains UNVERIFIED and this ROM stays DEV ONLY (release_eligible: false).
; Real-core evidence: tests/integration/geargrafx_dev_joyrep.py (pinned Geargrafx, DEV ROM).
; The host model tests/host/test_api_joyrep_dev.py checks the contract only, not these bytes.
.setcpu "huc6280"
.segment "OVERRIDES"
.export api_slot_22
api_slot_22:
    rts
