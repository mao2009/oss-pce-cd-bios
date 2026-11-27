; EXPERIMENTAL DEV-ONLY EX_RCROFF limited-functional body for slot $2B (G5 P0, Issue #53).
; NOT a compatible System Card BIOS. NOT the genuine EX_RCROFF ABI.
; NOT release eligible. NOT DEV_CALLABLE as a formal BIOS service. Experimental
; helper only, linked exclusively into G5 experimental images when selected
; (separate variant; the normal DEV ROM keeps its nonreturning $2B stub, and
; the 2-base builder default keeps the table on the stub).
;
; EXPERIMENTAL contract EX_RCROFF_D2F_V1 (synthetic media only, pinned core only;
; see docs/53-ex-rcroff-contract.md sections 4-5):
;   Entry: JSR $E081 (slot $2B) tail-jumps here (JMP), RTS returns to the
;     caller+3 (JSR pushes caller+2, RTS adds 1). The $E081 table entry stays
;     JMP (never JSR/RTS-patched).
;   Preconditions (caller obligations, not callee guarantees): MPR1=$F8
;     (work RAM $2000-$3FFF readable; general BIOS discipline, same as every
;     DEV shadow design), stack in work RAM, IRQs masked by the caller (same
;     assumption as the sibling experimental bodies). Entry A/X/Y are
;     arbitrary and UNUSED by this D2 body (Zeograd IN (N/A) is a public
;     description, HuC no-setup is a caller-side observation; the genuine
;     input requirements stay UNKNOWN and MUST NOT be inferred from either.
;     D2-makes-no-use-of-A/X/Y is a PROJECT_SPEC_ONLY scope choice, never
;     a genuine ABI claim). Entry P is arbitrary EXCEPT T=1
;     (T=1 entry is OUT OF THE VERIFIED DOMAIN: the pinned core consumes the
;     transfer flag at every instruction boundary, so entry T=1 cannot
;     round-trip on ANY implementation; platform fact, not body behavior).
;     MPR1!=$F8 is OUT OF CONTRACT (BLOCKED): the JSR itself already wrote
;     the return address through the MPR1 mapping before entry, so a
;     non-RAM mapping cannot be refused with zero side effects. The harness
;     validates MPR1 BEFORE executing JSR; this body defines NO in-body gate.
;   Grounded behavior (public functional description + independent hardware):
;     Zeograd (S1) documents IN (N/A) / OUT (N/A) + "clears bit 2 in VDC reg #5
;     and updates $20F3 and $20F7". Pinned Geargrafx (S5a) grounds CR = R05,
;     bit 2 = SCANLINE/RCR IE $04, R[5] mask $1FFF, and the stale-RMW hazard
;     that forces a work-RAM shadow instead of port read-modify-write. THIS
;     BODY IMPLEMENTS ONLY THE BIT OPERATION: CR low := shadow & $FB (clear
;     RCR IE, keep every other CR bit including VBLANK IE bit 3), via the
;     shared $2502 shadow, CR high untouched. AR left at 5 on the ok path;
;     the reject path executes no ST0 and leaves AR at its entry value.
;     The $2C (bit 3 set) operation is never cleared by this body; the $2A
;     (bit 2 set) is a separate API, not co-implemented.
;   Explicitly NOT implemented (guessing forbidden):
;     $20F3/$20F7 mirrors (address/value/order unresolved, family precedent
;     touches neither; synthetic tests assert them UNCHANGED), CPU I action
;     (exit I = entry I; no SEI/CLI net effect), IDR/IRR mask (untouched),
;     SR reads (none; read-to-clear avoidance), VCE/VRAM/SATB/VRAM/zp/BURAM/
;     CD/ADPCM/joypad (untouched), $2A/$2C/$2D (separate APIs, not
;     co-implemented), RCR register R06 (untouched; CR bit2 clear is not an
;     R06 write), handlers/timing.
;   Success convention (PROJECT_SPEC_ONLY, SIMULATED label, never genuine):
;     C=0 ok (VDC written, shadow published), C=1 reject (shadow unseeded:
;     no VDC write, no shadow/sentinel write). Zeograd OUT (N/A) defines no
;     genuine Carry contract; this convention belongs to the CR shadow family
;     ($2E/$33/$2C discipline) and MUST NOT be cited as $2B semantics.
;   Side-effect discipline:
;     Single straight-line MPR0=$FF window with SEI (validate shadow BEFORE
;     the window; never remap MPR1); restore MPR0 before RTS. ST0 select R05
;     (AR left at 5, family convention). No work-RAM writes outside the
;     transient caller-stack slots and the owned shadow $2502 image publish
;     (ok path only; the marker $2503 is never written by the body).
;     No zero-page/VRAM/joypad/CD/BURAM access.
;   ok: VDC CR low = shadow & $FB (clear RCR IE bit 2, keep every other CR
;        bit including bit 3); shadow updated; CR high untouched; C=0. A, X,
;        Y, MPRs, N/Z/V/D/I/T preserved (N/Z/V/D/I/T restored via PLP; C
;        forced to 0 after restore).
;   reject (shadow not initialized): no VDC write and no RAM write outside the
;        call's own transient stack slots; C=1. A, X, Y, MPRs and other flags
;        preserved. Seed $2502/$2503 from work RAM before the first call.
;   The new CR value is effective from the next horizontal display period
;   (pinned-core hardware fact, noted only).
;   Sync responsibility (shared single-writer family with $2E/$33/$2C): the
;   CALLER owns synchronization. While using this family, never write VDC CR
;   directly; if any code path writes CR outside this family, the caller must
;   re-seed $2502/$2503 before the next call. Out-of-sync state is out of
;   contract. This body never clears bit 3 set by $2C except when the stale
;   shadow itself lacks bit 3 (desync case, out of contract).
;   Competing VDC users audited: EX_SETWRT (slot $3A) touches only MAWR (R00,
;   leaves AR=0) and never CR; the only writers of $2502/$2503 are this family
;   and the caller's own seeding code.
;   stack: max 3 bytes simultaneous below the JSR return (flags, A, MPR0);
;        caller SP (before JSR) must be >= $05. SP=$04 or lower is out of
;        contract. Transient pushes touch exactly $21(caller_SP)..$21(SP-3)
;        on the reject path and additionally $21(SP-4) on the ok path, all
;        inside the caller's own stack area and balanced before RTS.
;   ABI remains UNVERIFIED and any ROM wiring stays DEV ONLY
;   (release_eligible: false). Provenance: ORIGINAL project-own code, MIT
;   (repository LICENSE), no reference BIOS bytes, no third-party copy.
.setcpu "huc6280"
.segment "BOOT"
.export ex_rcroff_entry
ex_rcroff_entry:
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
    and #$FB                  ; clear RCR/scanline IE (bit 2); every other CR bit kept
    sta $2502                 ; publish the new image first
    sta a:$0002               ; CR low = image (ST1; high byte never touched)
    pla                       ; A = caller MPR0
    tam #$01                  ; restore MPR0
    pla                       ; A = caller A
    plp                       ; restores caller flags incl. C
    clc                       ; ok: C=0 (family result convention, PROJECT_SPEC_ONLY)
    rts
