; EXPERIMENTAL DEV-ONLY CR-family D2 bodies for slots $33/$2C/$2B (G5 P0).
; Single source for the whole family (Issue #843): the three BOOT heads are
; assembled together with their shared reject/tail in THIS unit so the
; head->tail transfers stay local branches (ca65 cannot branch to an
; imported symbol; a split link would need JMPs and would not fit the
; $F000-$FFF5 BOOT+CODE budget for the full ExpG combination). Builders link
; this unit with NEED_CR33 and/or NEED_CR2C and/or NEED_CR2B (at least one
; required); unselected heads are not assembled, leave no bytes, and export
; no entry symbol (stub defaults keep the linked fallback stubs).
;
; NOT a compatible System Card BIOS. NOT any genuine ABI (genuine $33/$2C/
; $2B slot/entry/return semantics stay UNKNOWN/BLOCKED). NOT release
; eligible. NOT DEV_CALLABLE as a formal BIOS service. Experimental helpers
; only, linked exclusively into G5 experimental images that wire at least
; the $33 entry (the $33 glue always links this unit; $2C/$2B units reuse
; the same single source, never a copy).
;
; Family contract (identical for all three heads; per-head mask noted at
; each head):
;   shadow: $2502 = CR-low image (this family's single writer for CR low),
;           $2503 = $A5 iff initialized, $2504 reserved (CR-high image for
;           the future $2F-$33 family, which shares this discipline).
;           $2503 == $A5 is ONLY an initialization marker. MPR1 must map
;           work RAM ($F8) when calling (same as every DEV shadow design);
;           the routine never remaps MPR1. See the per-API notes below for
;           the sync responsibility and the audited competing VDC users.
;   in : nothing read (A/X/Y are not inputs; X/Y are never written either).
;   ok (shadow initialized): VDC CR low = f(shadow) (per-head mask below,
;        every other CR bit kept); shadow published; CR high untouched;
;        C=0 (family result convention, PROJECT_SPEC_ONLY; Zeograd OUT
;        (N/A) defines no genuine Carry contract and MUST NOT be cited).
;        A, X, Y, MPRs, N/Z/V/D/I/T preserved (N/Z/V/D/I/T restored via
;        PLP; C forced to 0 after restore).
;   reject (shadow not initialized): no VDC write and no RAM write outside
;        the call's own transient stack slots; C=1. A, X, Y, MPRs and other
;        flags preserved. Seed $2502/$2503 from work RAM before the first
;        call (the harness does this).
;   side effects: ST0 selects VDC register 5 (AR left at 5, family
;        convention). The new CR value is effective from the next horizontal
;        display period (pinned-core hardware fact, noted only). No
;        work-RAM writes outside the transient caller-stack slots and the
;        owned shadow $2502 image publish (ok path only; the marker $2503
;        is never written by any head or the tail). No zero-page, VRAM,
;        joypad, CD, ADPCM or BURAM access. $20F3/$20F7 mirrors, CPU I
;        action, IDR/IRR masks, SR reads, handlers/timing: explicitly NOT
;        implemented (guessing forbidden; family precedent touches neither
;        mirror; synthetic tests assert them UNCHANGED).
;   window: MPR0 = $FF while touching the VDC ports at $0000/$0002, IRQs
;        masked (SEI); MPR0 restored before RTS. The validity check runs
;        before the window so the MPR0 window stays one straight-line run
;        (static-analysis contract; the closing branch stays inside the
;        window and lands on the shared tail that finishes the restore).
;        MPR1 (stack page $2100-$21FF) is never remapped. NMI assumed absent.
;   stack: max 3 bytes simultaneous below the JSR return (flags, A, MPR0);
;        caller SP (before JSR) must be >= $05. SP=$04 or lower is out of
;        contract. PHP/PHA (and the caller's own JSR) DO write stack RAM:
;        the transient pushes touch exactly $21(caller_SP)..$21(caller_SP-3)
;        on the reject path and additionally $21(caller_SP-4) on the ok
;        path, all inside the caller's own stack area and balanced before RTS.
;        No other RAM is written on either path. No JSR is executed on
;        this path (heads reach the shared reject/tail by branch), so the
;        depth accounting matches the pre-commonization bodies exactly.
;        Because no mechanism can observe a bypassing CR write, this API is
;        generally callable ONLY under the no-direct-CR-write discipline
;        above; without it, correct operation cannot be claimed.
;   Grounded behavior (public functional description + independent hardware):
;        the pinned Geargrafx core grounds CR = R05, R[5] mask $1FFF, and the
;        stale-RMW hazard that forces a work-RAM shadow instead of port
;        read-modify-write. Each head implements ONLY its bit operation via
;        the shared $2502 shadow, CR high untouched, AR left at 5 on the ok
;        path; the reject path executes no ST0 and leaves AR at entry.
;   ABI remains UNVERIFIED and any ROM wiring stays DEV ONLY
;        (release_eligible: false). Provenance: ORIGINAL project-own code,
;        MIT (repository LICENSE), no reference BIOS bytes, no third-party
;        copy. Full per-API evidence lives in docs/61-dspoff-contract.md,
;        docs/54-ex-irqon-contract.md, docs/53-ex-rcroff-contract.md and
;        docs/g5-display-functional-selection.md (family selection +
;        pinned-core grounding).
;
; Per-head notes (mask + provenance; the family contract above applies):
;   $33 EX_DSPOFF (Issue #61): AND #$3F (clear BB+SB, bits 6+7). Project-own
;        DEV display-control routine; reuses the DEV VDC CR display-family
;        discipline of src/overrides/slot_2E.s (EX_BGON, Issue #56) except for
;        the mask. Reference-survey note (HYPOTHESIS, not a contract):
;        Zeograd's tentative table says EX_DSPOFF "clears bit 6+7 in $20F3.
;        It does not update the VDC reg #5!" (sibling entries: BGON sets bit
;        7, BGOFF clears bit 7, SPRON sets bit 6, SPROFF clears bit 6, DSPON
;        sets bits 6+7 -- all "does not update VDC reg #5"). The $20F3
;        address disagrees with that page's own register table ($20F3 =
;        current VDC register selected; $20F7 = value of VDC reg #5), so the
;        shadow address is unresolved. This DEV head intentionally DIFFERS
;        from Zeograd: like slot_2E.s it writes the new CR low to the VDC
;        immediately instead of deferring it. Do not use this file as
;        evidence for the reference ABI. Competing VDC users audited:
;        EX_SETWRT (slot $3A) touches only MAWR (R00, leaves AR=0) and never
;        CR; the only writers of $2502/$2503 are this family and the
;        caller's own seeding code. Sync responsibility: the CALLER owns
;        synchronization -- while using this family, never write VDC CR
;        directly; if any code path writes CR outside this family the caller
;        must re-seed $2502/$2503 before the next call. Out-of-sync state is
;        out of contract.
;   $2C EX_IRQON D2 (Issue #54): ORA #$08 (set VBLANK IE, bit 3). Grounded in
;        Zeograd (S1) IN (N/A) / OUT (N/A) + "sets bit 3 in VDC reg #5 and
;        updates $20F3 and $20F7" (mirrors explicitly NOT implemented here)
;        and pinned Geargrafx (S5a) CR = R05 bit 3 = VBLANK IE $08. Entry
;        A/X/Y arbitrary and UNUSED (D2-makes-no-use-of-A/X/Y is a
;        PROJECT_SPEC_ONLY scope choice, never a genuine ABI claim). Entry P
;        arbitrary EXCEPT T=1 (T=1 entry is OUT OF THE VERIFIED DOMAIN: the
;        pinned core consumes the transfer flag at every instruction
;        boundary). MPR1!=$F8 is OUT OF CONTRACT (BLOCKED). The $2A (bit 2
;        set) is a separate API, not co-implemented.
;   $2B EX_RCROFF D2 (Issue #53): AND #$FB (clear RCR/scanline IE, bit 2,
;        keeping every other CR bit including VBLANK IE bit 3). Grounded in
;        Zeograd (S1) "clears bit 2 in VDC reg #5 and updates $20F3 and
;        $20F7" (mirrors explicitly NOT implemented) and pinned Geargrafx
;        (S5a) CR = R05 bit 2 = SCANLINE/RCR IE $04. Same entry discipline
;        as $2C. The $2C (bit 3 set) operation is never cleared by this head
;        except when the stale shadow itself lacks bit 3 (desync case, out
;        of contract). RCR register R06 untouched (CR bit2 clear is not an
;        R06 write). $2A/$2C/$2D are separate APIs, not co-implemented.
.setcpu "huc6280"
.segment "BOOT"

.ifndef NEED_CR33
.ifndef NEED_CR2C
.ifndef NEED_CR2B
    .error "CR family unit needs at least one of NEED_CR33/NEED_CR2C/NEED_CR2B"
.endif
.endif
.endif

.ifdef NEED_CR33
.export ex_dspoff, experimental_slot_33
ex_dspoff:
experimental_slot_33:
    php
    pha                       ; caller A (scratch below; restored on exit)
    lda $2503                 ; shadow valid? (MPR1=$F8 work RAM; absolute form)
    cmp #$A5
    bne cr_reject             ; unseeded: shared reject (no write anywhere)
    sei                       ; no IRQ while MPR0 points at the I/O page
    tma #$01                  ; A = caller MPR0
    pha                       ; save MPR0 (3 pushes max: flags, A, MPR0)
    lda #$FF
    tam #$01                  ; MPR0 = $FF: VDC ports at $0000/$0002 (stack unaffected)
    lda #$05
    sta a:$0000               ; ST0: select VDC register 5 (CR); a: forces absolute form
    lda $2502                 ; CR-low image
    and #$3F                  ; clear BB+SB (background + sprite display off)
    bra cr_tail_publish       ; shared publish-and-restore tail
.endif

.ifdef NEED_CR2C
.export ex_irqon_entry
ex_irqon_entry:
    php
    pha                       ; caller A (scratch below; restored on exit)
    lda $2503                 ; shadow valid? (MPR1=$F8 work RAM; absolute form)
    cmp #$A5
    bne cr_reject             ; unseeded: shared reject (no write anywhere)
    sei                       ; no IRQ while MPR0 points at the I/O page
    tma #$01                  ; A = caller MPR0
    pha                       ; save MPR0 (3 pushes max: flags, A, MPR0)
    lda #$FF
    tam #$01                  ; MPR0 = $FF: VDC ports at $0000/$0002 (stack unaffected)
    lda #$05
    sta a:$0000               ; ST0: select VDC register 5 (CR); a: forces absolute form
    lda $2502                 ; CR-low image
    ora #$08                  ; set VBLANK IE (bit 3); every other CR bit kept
    bra cr_tail_publish       ; shared publish-and-restore tail
.endif

.ifdef NEED_CR2B
.export ex_rcroff_entry
ex_rcroff_entry:
    php
    pha                       ; caller A (scratch below; restored on exit)
    lda $2503                 ; shadow valid? (MPR1=$F8 work RAM; absolute form)
    cmp #$A5
    bne cr_reject             ; unseeded: shared reject (no write anywhere)
    sei                       ; no IRQ while MPR0 points at the I/O page
    tma #$01                  ; A = caller MPR0
    pha                       ; save MPR0 (3 pushes max: flags, A, MPR0)
    lda #$FF
    tam #$01                  ; MPR0 = $FF: VDC ports at $0000/$0002 (stack unaffected)
    lda #$05
    sta a:$0000               ; ST0: select VDC register 5 (CR); a: forces absolute form
    lda $2502                 ; CR-low image
    and #$FB                  ; clear RCR/scanline IE (bit 2); every other CR bit kept
    bra cr_tail_publish       ; shared publish-and-restore tail
.endif

; Shared CR-family reject + publish-and-restore tail (assembled once per
; image, inside this unit so every head transfer stays a local branch).
.export cr_reject, cr_tail_publish
cr_reject:
    pla                       ; reject: A = caller A (nothing written anywhere)
    plp                       ; restores caller flags
    sec                       ; uninitialized shadow: C=1 (no other flag changed here)
    rts
cr_tail_publish:
    sta $2502                 ; publish the new CR-low image first
    sta a:$0002               ; CR low = image (ST1; high byte never touched)
    pla                       ; A = caller MPR0
    tam #$01                  ; restore MPR0
    pla                       ; A = caller A
    plp                       ; restores caller flags incl. C
    clc                       ; ok: C=0 (family result convention, PROJECT_SPEC_ONLY)
    rts
