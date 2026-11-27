; EXPERIMENTAL DEV-ONLY EX_SCRMOD preservation-only scaffold for slot $25 (G5 P0, Issue #47).
; NOT a compatible System Card BIOS. NOT the genuine EX_SCRMOD ABI.
; NOT release eligible. NOT DEV_CALLABLE as a formal BIOS service. Experimental
; helper only, linked exclusively into G5 experimental images when selected
; (separate variant; the normal DEV ROM keeps its nonreturning $25 stub, and
; the 2-base builder default keeps the table on the stub).
;
; EXPERIMENTAL contract EX_SCRMOD_B1_V1 (synthetic media only, pinned core only;
; see docs/47-ex-scrmod-contract.md sections 4.2, 6.2, 6.3 and docs/47-ex-scrmod-b1.md):
;   Entry: JSR $E06F (slot $25) tail-jumps here (JMP), RTS returns to the
;     caller+3 (JSR pushes caller+2, RTS adds 1). The $E06F table entry stays
;     JMP (never JSR/RTS-patched).
;   Preconditions (caller obligations, not callee guarantees): MPR1=$F8
;     (work RAM $2000-$3FFF readable; general BIOS discipline, same as every
;     DEV shadow design), stack in work RAM, IRQs masked by the caller (same
;     assumption as the sibling experimental bodies). Entry P/A/X/Y are
;     arbitrary (no $25 entry observation exists; execution halts AT $25 in
;     the standard image, so no genuine entry regs were ever measured).
;     MPR1!=$F8 is OUT OF CONTRACT (BLOCKED): the JSR itself already wrote
;     the return address through the MPR1 mapping before entry, so a
;     non-RAM mapping cannot be refused with zero side effects. Safety for
;     MPR1!=$F8 is NOT proven; callers must ensure MPR1=$F8. The harness
;     validates MPR1 BEFORE executing JSR (contract T13); this body defines
;     NO in-body gate (B1 has no gate by design, section 4.2).
;   Grounded behavior (contract docs/47-ex-scrmod-contract.md): genuine $25
;     semantics are UNKNOWN (no public source defines inputs, outputs, flags,
;     VDC target, shadow, timing, or side effects), so this body performs NO
;     invented service and returns NO invented success value. What is
;     grounded: entry-state preservation only (A/X/Y/P exact including
;     Carry, MPRs unchanged, SP balanced), with the VDC/MMIO/RAM discipline
;     below. Preservation is load-bearing: dropping any save/restore is
;     caught by the synthetic mutants.
;   Side-effect discipline (B1, preservation-only):
;     NO VDC/MMIO writes of any kind (no ST0/ST1/ST2 at $0000-$0003, no
;     VCE/PSG/timer/CD access; no TAM/TMA-write, no TXS, no SEI requirement:
;     there is no I/O to protect). AR unchanged (no target register exists,
;     so AR_out == AR_in is asserted). MPR0-MPR7 unchanged (no remapping of
;     any MPR). No work-RAM writes outside the transient caller-stack bytes
;     $21(SP-k)..$21(SP). No zero-page/VRAM/joypad/CD/BURAM access.
;   Success: restores Y/X/A then P via plain PLP and returns via RTS. Entry
;     Carry is preserved exactly (C_in == C_out on both seeds per T07; no
;     CLC/SEC fabricated). Preservation is latch-exact: on the pinned core
;     the P latch has no B bit (HuC6280 SetP masks the popped byte with $EF,
;     so PHP's forced B in the stacked image never enters the latch and a
;     plain PHP/PLP round trip restores N/V/T/D/I/Z/C exactly; no
;     stacked-byte patch is needed or used).
;   No refusal path exists (B1 defines no ok/reject values, no invented
;     success or refusal code; T04/T08/T09 are NOT_APPLICABLE: no I/O
;     window, no shadow, no mask).
;   Observability: none beyond the return itself (no record store: no
;     collision-free record area is established for $25; the probe observes
;     entry state externally). Only stack pushes occur (JSR + saves); no
;     other memory effects. Result class SIMULATED, never genuine, never
;     DEV_CALLABLE.
;   Stack: JSR return (2 bytes) plus max 4 transient bytes below it
;     (P,A,X,Y); SP balanced before RTS. Caller SP below $06 is out of
;     contract (same 4-push discipline as ex_memopen.s), unchecked,
;     fail-closed by the caller's own setup. Stack must be in work RAM,
;     which is part of the MPR1=$F8 caller precondition; without it even
;     the JSR is unsafe.
;   NMI/IRQ assumed absent across the short body window (~35 cycles:
;     3+3+3+3+4+4+4+5+6 for PHP/PHA/PHX/PHY/PLY/PLX/PLA/PLP/RTS; same
;     assumption as the sibling experimental bodies).
;
; Provenance: ORIGINAL project-own code, MIT (repository LICENSE). No
; reference BIOS bytes, no third-party copy, no disassembly derivation.
; Contract sources are caller-side/public records only (see Issue #47 and
; docs/47-ex-scrmod-contract.md); behavior beyond preservation is BLOCKED.
.setcpu "huc6280"
.segment "BOOT"
.export ex_scrmod_entry

ex_scrmod_entry:
    php                       ; entry P (B forced only in the stacked image;
                              ; the latch has no B bit: SetP masks with $EF)
    pha                       ; entry A (no invented code: B1 owns no code space)
    phx                       ; entry X
    phy                       ; entry Y
    ply                       ; Y = entry Y
    plx                       ; X = entry X
    pla                       ; A = entry A
    plp                       ; P = entry P, latch-exact (Carry preserved:
                              ; no CLC/SEC fabricated, no ok/reject values)
    rts
