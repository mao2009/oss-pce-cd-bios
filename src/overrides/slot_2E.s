; EX_BGON — slot $2E, candidate entry $E08A. DEV ROM only (Issue #56).
;
; Project-own routine, NOT the reference System Card EX_BGON ABI (remains UNVERIFIED).
; Sets the HuC6270 background-blanking bit (CR R05, bit 7: 1 = display background)
; and preserves every other CR bit via a work-RAM shadow. Public hardware facts
; used (no BIOS code): NEC HuC6270 manual (R05 bit 7 = BB; bit 6 = SB) and the
; pinned Geargrafx hardware notes (huc6270.h k_register_mask[5] = 0x1FFF,
; RenderLine latched_cr & 0x80/0x40).
;
; Why a shadow: the pinned Geargrafx core returns the stale m_read_buffer (only
; VRAM reads update it; see huc6270.cpp ReadRegister/ProcessVramRead) for a CR
; data-port read, and portable practice keeps RAM copies (HuDK vdc_crl/vdc_crh;
; vdcdox names VRR the only valid data-port read). A port read-modify-write
; would therefore destroy the other CR bits on the verification platform, so
; the DEV contract owns CR low in work RAM instead of reading the port.
;
;   shadow: $2502 = CR-low image (this family's single writer for CR low),
;           $2503 = $A5 iff initialized, $2504 reserved (CR-high image for the
;           future $2F-$33 family, which shares this discipline).
;           $2503 == $A5 is ONLY an initialization marker: it records that the
;           shadow was seeded, and does NOT prove the shadow still matches the
;           real VDC CR. Any direct CR write that bypasses this family (port
;           ST1/ST2 to R05, or another routine touching CR) silently desyncs the
;           shadow, and the next call will then rewrite CR from the stale image.
;           MPR1 must map work RAM ($F8) when calling (same precondition as
;           EX_RND's $2500/$2501 state); the routine does not remap MPR1.
;   sync responsibility: the CALLER owns synchronization. While using this
;           family, never write VDC CR directly; if any code path writes CR
;           outside this family (including the future $2F-$33 siblings, which
;           share the same shadow), the caller must re-seed $2502/$2503 before
;           the next call. Out-of-sync state is out of contract: behavior is
;           defined only as "CR low := stale shadow | mask" (no error is
;           reported), so a desynced caller gets a wrong-but-deterministic CR.
;   competing VDC users audited: EX_SETWRT (slot $3A) touches only MAWR (R00,
;           leaves AR=0) and never CR; no other in-tree routine writes VDC CR.
;           The only writers of $2502/$2503 are this family and the caller's own
;           seeding code. Because no mechanism can observe a bypassing CR write,
;           this API is generally callable ONLY under the no-direct-CR-write
;           discipline above; without it, correct operation cannot be claimed.
;           DEV ONLY in any case (release_eligible: false).
;   in : nothing read (A/X/Y are not inputs; X/Y are never written either).
;   ok (shadow initialized): VDC CR low = shadow | $80; shadow updated; CR high
;        untouched; C=0 (same result convention as EX_SETWRT). A, X, Y, MPRs,
;        N/Z/V/D/I/T preserved.
;   reject (shadow not initialized): no VDC write, no RAM write, C=1. A, X, Y,
;        MPRs and other flags preserved. Seed $2502/$2503 from work RAM before
;        the first call (the harness does this).
;   side effects: ST0 selects VDC register 5 (AR left at 5, documented per the
;        per-API convention: SETWRT leaves AR=0/MAWR). CR low rewritten on ok.
;        The new BB value is effective from the next horizontal display period.
;        Work RAM $2502/$2503 is the owned shadow (see above); otherwise no
;        zero-page, VRAM, joypad, CD, ADPCM or BURAM access.
;   window: MPR0 = $FF while touching the VDC ports at $0000/$0002, IRQs masked
;        (SEI); MPR0 restored before RTS. Same discipline as EX_SETWRT (slot $3A).
;        MPR1 (stack page $2100-$21FF) is never remapped. NMI assumed absent.
;        The validity check runs before the window so the MPR0 window stays one
;        straight-line run (static-analysis contract).
;   stack: max 3 bytes simultaneous below the JSR return (flags, A, MPR0);
;        caller SP (before JSR) must be >= $05. SP=$04 or lower is out of contract.
;   ABI remains UNVERIFIED and this ROM stays DEV ONLY (release_eligible: false).
; Real-core evidence: tests/integration/geargrafx_dev_bgon.py (pinned Geargrafx, DEV ROM),
;        judged against the independent vdc_cr probe observation (GetState()->R[5]).
; The host model tests/host/test_api_cr_dev.py checks the contract only, not these bytes.
.setcpu "huc6280"
.segment "OVERRIDES"
.export api_slot_2E
api_slot_2E:
    php
    pha                       ; caller A (scratch below; restored on exit)
    lda $2503                 ; shadow valid? (MPR1=$F8 work RAM; absolute form)
    cmp #$A5
    beq @have
    pla                       ; reject: A = caller A (nothing written anywhere)
    plp                       ; restores caller flags
    sec                       ; uninitialized shadow: C=1 (no other flag changed here)
    rts
@have:
    sei                       ; no IRQ while MPR0 points at the I/O page
    tma #$01                  ; A = caller MPR0
    pha                       ; save MPR0 (3 pushes max: flags, A, MPR0)
    lda #$FF
    tam #$01                  ; MPR0 = $FF: VDC ports at $0000/$0002 (stack unaffected)
    lda #$05
    sta a:$0000               ; ST0: select VDC register 5 (CR); a: forces absolute form
    lda $2502                 ; CR-low image
    ora #$80                  ; set BB (background display)
    sta $2502                 ; publish the new image first
    sta a:$0002               ; CR low = image (ST1; high byte never touched)
    pla                       ; A = caller MPR0
    tam #$01                  ; restore MPR0
    pla                       ; A = caller A
    plp                       ; restores caller flags incl. C
    clc                       ; ok: C=0 (same result convention as EX_SETWRT)
    rts
