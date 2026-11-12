; EX_DSPOFF — project-own DEV display-control routine, slot $33, candidate entry $E099 (Issue #61).
;
; Project-own routine, NOT the reference System Card EX_DSPOFF ABI (remains UNVERIFIED).
; NOT linked into the normal DEV ROM: the OVERRIDES segment ends at $E7FB with
; 5 bytes of slack below the $E800 probe overlay (see docs/61-dspoff-contract.md),
; so no src/overrides/slot_33.s exists and the normal $E099 entry keeps its
; nonreturning stub. The experimental System-Card-sized DEV image DOES link this
; file into its BOOT region (src/experimental_bios_boot.s includes it) and redirects
; the experimental $E099 entry to the linked body
; (tools/build_experimental_bios_boot.py; docs/ex-dspoff-real-cpu-g5.md). That
; experimental path is DEV ONLY, NOT a compatible System Card BIOS, and confers
; no DEV_CALLABLE, ABI VERIFIED, or release eligibility.
;
; Reuses the DEV VDC CR display-family discipline of src/overrides/slot_2E.s
; (EX_BGON, Issue #56) byte-for-byte except for the mask: the family owns CR low
; in a work-RAM shadow because the pinned Geargrafx core returns stale data for
; CR port reads (MAJOR-1). Public hardware facts used (no BIOS code):
; NEC HuC6270 manual (R05 bit 7 = BB background enable; bit 6 = SB sprite
; enable) and the pinned Geargrafx hardware notes (huc6270.h
; k_register_mask[5] = 0x1FFF, RenderLine latched_cr & 0x80/0x40).
;
; Reference-survey note (HYPOTHESIS, not a contract): Zeograd's tentative table
; says EX_DSPOFF "clears bit 6+7 in $20F3. It does not update the VDC reg #5!"
; (sibling entries: BGON sets bit 7, BGOFF clears bit 7, SPRON sets bit 6,
; SPROFF clears bit 6, DSPON sets bits 6+7 — all "does not update VDC reg #5").
; The $20F3 address disagrees with that page's own register table ($20F3 =
; current VDC register selected; $20F7 = value of VDC reg #5), so the shadow
; address is unresolved. This DEV fragment intentionally DIFFERS from Zeograd:
; like slot_2E.s it writes the new CR low to the VDC immediately instead of
; deferring it. The reference deferred-vs-immediate behavior, the true shadow
; address, and every register/RAM/flag side effect remain UNKNOWN. Do not use
; this file as evidence for the reference ABI.
;
;   shadow: $2502 = CR-low image (this family's single writer for CR low),
;           $2503 = $A5 iff initialized, $2504 reserved (CR-high image for the
;           future $2F-$33 family, which shares this discipline).
;           $2503 == $A5 is ONLY an initialization marker: it records that the
;           shadow was seeded, and does NOT prove the shadow still matches the
;           real VDC CR. Any direct CR write that bypasses this family silently
;           desyncs the shadow; the next call rewrites CR from the stale image.
;           MPR1 must map work RAM ($F8) when calling (same precondition as
;           EX_RND's $2500/$2501 state); the routine does not remap MPR1.
;   sync responsibility: the CALLER owns synchronization. While using this
;           family, never write VDC CR directly; if any code path writes CR
;           outside this family the caller must re-seed $2502/$2503 before the
;           next call. Out-of-sync state is out of contract: behavior is
;           defined only as "CR low := stale shadow & $3F" (no error is
;           reported), so a desynced caller gets a wrong-but-deterministic CR.
;   competing VDC users audited: EX_SETWRT (slot $3A) touches only MAWR (R00,
;           leaves AR=0) and never CR; no other in-tree routine writes VDC CR.
;           The only writers of $2502/$2503 are this family and the caller's own
;           seeding code. Because no mechanism can observe a bypassing CR write,
;           this API is generally callable ONLY under the no-direct-CR-write
;           discipline above; without it, correct operation cannot be claimed.
;           DEV ONLY in any case (release_eligible: false).
;   in : nothing read (A/X/Y are not inputs; X/Y are never written either).
;   ok (shadow initialized): VDC CR low = shadow & $3F (clear BB+SB, keep every
;        other CR bit); shadow updated; CR high untouched; C=0 (same result
;        convention as EX_SETWRT). A, X, Y, MPRs, N/Z/V/D/I/T preserved
;        (N/Z/V/D/I/T restored via PLP; C forced to 0 after restore).
;   reject (shadow not initialized): no VDC write and no RAM write outside the
;        call's own transient stack slots (see below), C=1. A, X, Y, MPRs and
;        other flags preserved. Seed $2502/$2503 from work RAM before the
;        first call (the harness does this).
;   side effects: ST0 selects VDC register 5 (AR left at 5, documented per the
;        per-API convention: SETWRT leaves AR=0/MAWR). CR low rewritten on ok.
;        The new BB/SB value is effective from the next horizontal display
;        period. Work RAM $2502/$2503 is the owned shadow (see above);
;        otherwise no zero-page, VRAM, joypad, CD, ADPCM or BURAM access.
;   window: MPR0 = $FF while touching the VDC ports at $0000/$0002, IRQs masked
;        (SEI); MPR0 restored before RTS. Same discipline as EX_SETWRT.
;        MPR1 (stack page $2100-$21FF) is never remapped. NMI assumed absent.
;        The validity check runs before the window so the MPR0 window stays one
;        straight-line run (static-analysis contract).
;   stack: max 3 bytes simultaneous below the JSR return (flags, A, MPR0);
;        caller SP (before JSR) must be >= $05. SP=$04 or lower is out of
;        contract. PHP/PHA (and the caller's own JSR) DO write stack RAM: the
;        transient pushes touch exactly $21(caller_SP)..$21(caller_SP-3) on the
;        reject path and additionally $21(caller_SP-4) on the ok path, all
;        inside the caller's own stack area and balanced before RTS. No other
;        RAM is written on either path.
;   ABI remains UNVERIFIED and any future ROM wiring stays DEV ONLY
;        (release_eligible: false). Provenance: ORIGINAL project-own code, MIT
;        (repository LICENSE), no reference BIOS bytes, no third-party copy.
.setcpu "huc6280"
.export ex_dspoff
ex_dspoff:
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
    and #$3F                  ; clear BB+SB (background + sprite display off)
    sta $2502                 ; publish the new image first
    sta a:$0002               ; CR low = image (ST1; high byte never touched)
    pla                       ; A = caller MPR0
    tam #$01                  ; restore MPR0
    pla                       ; A = caller A
    plp                       ; restores caller flags incl. C
    clc                       ; ok: C=0 (same result convention as EX_SETWRT)
    rts
