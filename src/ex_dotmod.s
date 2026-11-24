; EXPERIMENTAL DEV-ONLY EX_DOTMOD preservation-only scaffold for slot $24 (G5 P0, Issue #46).
; NOT a compatible System Card BIOS. NOT the genuine EX_DOTMOD ABI.
; NOT release eligible. NOT DEV_CALLABLE as a formal BIOS service. Experimental
; helper only, linked exclusively into G5 experimental images when selected
; (separate variant; the normal DEV ROM keeps its nonreturning $24 stub, and
; the 2-base builder default keeps the table on the stub).
;
; EXPERIMENTAL contract EX_DOTMOD_B1_V1 (synthetic media only, pinned core only;
; see docs/46-ex-dotmod-contract.md sections 4.2, 6.2, 6.3 and docs/46-ex-dotmod-b1.md):
;   Entry: JSR $E06C (slot $24) tail-jumps here (JMP), RTS returns to the
;     caller+3 (JSR pushes caller+2, RTS adds 1). The $E06C table entry stays
;     JMP (never JSR/RTS-patched).
;   Preconditions (caller obligations, not callee guarantees): MPR1=$F8
;     (work RAM $2000-$3FFF readable; general BIOS discipline, same as every
;     DEV shadow design), stack in work RAM, IRQs masked by the caller (same
;     assumption as the sibling experimental bodies). Entry A/X/Y are
;     arbitrary; entry P is arbitrary EXCEPT T=1 (T=1 entry is OUT OF
;     CONTRACT per the verified P domain below: the pinned core consumes
;     the transfer flag at every instruction boundary, so entry T=1 exits
;     T=0 on ANY implementation; no $24 genuine entry contract exists; the
;     single commercial
;     observation in Issue #802 ExpB is one-game single-callsite data and
;     MUST NOT be mistaken for an ABI).
;     MPR1!=$F8 is OUT OF CONTRACT (BLOCKED): the JSR itself already wrote
;     the return address through the MPR1 mapping before entry, so a
;     non-RAM mapping cannot be refused with zero side effects. Safety for
;     MPR1!=$F8 is NOT proven; callers must ensure MPR1=$F8. The harness
;     validates MPR1 BEFORE executing JSR (contract T13-analog); this body
;     defines NO in-body gate (B1-analogous has no gate by design,
;     section 4.2).
;   Grounded behavior (contract docs/46-ex-dotmod-contract.md): genuine $24
;     semantics are UNKNOWN (no public source defines inputs, outputs, flags,
;     VDC/VCE target, shadow, timing, or side effects), so this body performs
;     NO invented service and returns NO invented success value. What is
;     grounded: entry-state preservation only (A/X/Y exact; P exact except
;     T with T=1 out of the verified calling contract, including Carry
;     exactly; MPRs unchanged, SP balanced), with the VDC/VCE/MMIO/RAM
;     discipline below. Preservation is load-bearing: dropping any
;     save/restore is caught by the synthetic mutants.
;   Side-effect discipline (B1-analogous, preservation-only):
;     NO VDC/VCE/MMIO writes of any kind (no ST0/ST1/ST2 at $0000-$0003, no
;     VCE $0400/$0402/$0404 access, no PSG/timer/CD access; no TAM/TMA-write,
;     no TXS, no SEI requirement: there is no I/O to protect). AR unchanged
;     (no target register exists, so AR_out == AR_in is asserted).
;     MPR0-MPR7 unchanged (no remapping of any MPR). No work-RAM writes
;     outside the transient caller-stack bytes $21(SP-k)..$21(SP): the JSR
;     2-byte return push is caller-side (written before entry through the
;     MPR1 mapping); the callee writes only its 4 transient pushes
;     (P,A,X,Y). SP is restored (balanced) before RTS; stacked bytes are
;     NOT scrubbed (SP restore and stack-memory restore are distinct). No
;     zero-page/VRAM/joypad/CD/BURAM access.
;   Success: restores Y/X/A then P via plain PLP and returns via RTS. Entry
;     Carry is preserved exactly (C_in == C_out on both seeds per T07; no
;     CLC/SEC fabricated). Preservation is latch-exact for the verified P
;     domain (T=0): on the pinned core the P latch has no B bit (HuC6280
;     SetP masks the popped byte with $EF, so PHP's forced B in the stacked
;     image never enters the latch) and a plain PHP/PLP round trip restores
;     N/V/D/I/Z/C exactly with T=0. T=1 entry is OUT OF CONTRACT: PHP saves
;     the stacked image including T, but the HuC6280 consumes the transfer
;     flag at every instruction boundary (Clock: snapshot + ClearFlag), so
;     entry T=1 exits T=0 on ANY implementation regardless of the save /
;     restore. No T-bit operation is added (no SEB/CLB/TST fabrications);
;     the verified calling contract is P with T=0, Carry preserved; no
;     stacked-byte patch is needed or used.
;   No refusal path exists (B1-analogous defines no ok/reject values, no
;     invented success or refusal code; T04/T08/T09 are NOT_APPLICABLE: no
;     I/O window, no shadow, no mask).
;   Observability: none beyond the return itself (no record store: no
;     collision-free record area is established for $24; the probe observes
;     entry state externally). Only stack pushes occur (caller-side JSR 2
;     bytes + callee 4 transient saves = 6 transient stack bytes); no
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
; Contract sources are caller-side/public records only (see Issue #46 and
; docs/46-ex-dotmod-contract.md); behavior beyond preservation is BLOCKED.
.setcpu "huc6280"
.segment "BOOT"
.export ex_dotmod_entry

ex_dotmod_entry:
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
