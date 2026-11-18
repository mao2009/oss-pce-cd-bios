; EXPERIMENTAL DEV-ONLY KEY_BIOS/EX_MEMOPEN preservation body for slot $4A (G5 P0).
; NOT a compatible System Card BIOS. NOT the genuine KEY_BIOS/EX_MEMOPEN ABI.
; NOT release eligible. NOT DEV_CALLABLE as a formal BIOS service. Experimental
; helper only, linked exclusively into G5 experimental images when selected
; (separate variant; the normal DEV ROM keeps its nonreturning $4A stub, and
; the 2-base builder default keeps the table on the stub).
;
; EXPERIMENTAL contract EX_MEMOPEN_PRESERVE_V1 (synthetic media + opt-in
; commercial observation only, pinned core only):
;   Entry: JSR $E0DE (slot $4A) tail-jumps here (JMP), RTS returns to the
;     caller+2. The $E0DE table entry stays JMP (never JSR/RTS-patched).
;   Preconditions: MPR1=$F8 (work RAM $2000-$3FFF readable; OBSERVED on both
;     commercial CUEs, DOCUMENTED BIOS discipline), stack in work RAM,
;     IRQs masked by the caller (same discipline as the experimental
;     loader/payload). Entry P/A/X/Y are arbitrary (OBSERVED: CUE[0]
;     A=X=Y=$00 P=$07; CUE[1] A=$B0 X=$03 Y=$00 P=$07).
;   Grounded behavior (Issue #84 contract research docs/84-4a-contract.md):
;     genuine $4A semantics are UNKNOWN (no public source defines inputs,
;     outputs, flags, or side effects), so this body performs NO invented
;     service and returns NO invented success value. What is grounded:
;     (a) commercial games observably require entry-state-preserving return
;         with entry Carry (local-only return-semantics matrix: forcing C=0
;         diverts CUE[0] from $23 to $05 while A/X/Y forcing changes nothing;
;         CUE[1] is insensitive to all single-element changes);
;     (b) MPR1=$F8 entry discipline (observed + documented).
;   Processing (REAL, not a bare RTS): full register/flag/MPR preservation
;     plus the MPR1=$F8 entry gate. Preservation is load-bearing: dropping
;     any save/restore is caught by the synthetic mutants.
;   Success (MPR1=$F8): restores Y/X/A then P and returns via RTS. Entry
;     Carry is preserved (CUE[0] provably needs entry C=1; no CLC/SEC
;     fabricated). Preservation is latch-exact: on the pinned core the P
;     latch has no B bit (HuC6280 SetP masks the popped byte with $EF, so
;     PHP's forced B in the stacked image never enters the latch and a
;     plain PHP/PLP round trip restores N/V/T/D/I/Z/C exactly; no
;     stacked-byte patch is needed or used).
;   Refusal (MPR1!=$F8, unobserved class): restores Y/X/A, restores P via
;     plain PLP, then SEC (project-experimental refusal signal C=1; polarity
;     vs genuine UNKNOWN and documented as such). No memory written on
;     either path, no TAM/TMA-write/TXS, no MMIO, no bus access.
;   Observability: none beyond the return itself (no CRES write: no
;     collision-free record area is established for $4A; the probe observes
;     entry state externally). Zero side effects under every operand
;     hypothesis, including unknown memory operands.
;   Stack: max 4 transient bytes below the JSR return (P,A,X,Y); SP balanced
;     before RTS. Caller SP below $06 is out of contract (same discipline as
;     ex_dspoff.s), unchecked, fail-closed by the caller's own setup.
;   NMI/IRQ assumed absent across the ~20-cycle window (same assumption as
;     the sibling experimental bodies).
;
; Provenance: ORIGINAL project-own code, MIT (repository LICENSE). No
; reference BIOS bytes, no third-party copy, no disassembly derivation.
; Contract sources are caller-side/public records only (see Issue #84 and
; docs/84-4a-contract.md); behavior beyond preservation is BLOCKED.
.setcpu "huc6280"
.segment "BOOT"
.export ex_memopen_entry

ex_memopen_entry:
    php                       ; entry P (B forced only in the stacked image;
                              ; the latch has no B bit: SetP masks with $EF)
    pha                       ; entry A
    phx                       ; entry X
    phy                       ; entry Y
    tma #$02                  ; A = MPR1 (mask bit1; #$01 reads MPR0.
                              ; Disambiguated by the synthetic gate test: MPR0
                              ; stays $FF in every test class, so only a true
                              ; MPR1 read accepts)
    cmp #$F8
    bne @refuse
    ; --- success: MPR1 is work RAM; restore entry state, RTS ---
    ply                       ; Y = entry Y
    plx                       ; X = entry X
    pla                       ; A = entry A (no invented code: $4A owns no code space)
    plp                       ; P = entry P, latch-exact (Carry preserved:
                              ; observed requirement)
    rts
@refuse:
    ; --- refusal: MPR1 is not work RAM; nothing may be trusted below ---
    ply                       ; Y = entry Y
    plx                       ; X = entry X
    pla                       ; A = entry A
    plp                       ; P = entry P
    sec                       ; experimental refusal signal C=1 (NOT genuine polarity)
    rts
