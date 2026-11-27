; EXPERIMENTAL DEV-ONLY EX_SATCLR B1-analogous preservation-only scaffold for slot $36 (G5 P0, Issue #64).
; NOT a compatible System Card BIOS. NOT the genuine EX_SATCLR ABI.
; NOT release eligible. NOT DEV_CALLABLE as a formal BIOS service. Experimental
; helper only, linked exclusively into G5 experimental images when selected
; (separate variant; the normal DEV ROM keeps its nonreturning $36 stub, and
; the 2-base builder default keeps the table on the stub).
;
; EXPERIMENTAL contract EX_SATCLR_B1_V1 (synthetic media only, pinned core only;
; see docs/64-ex-satclr-contract.md and docs/64-ex-satclr-b1.md):
;   Entry: JSR $E0A2 (slot $36) tail-jumps here (JMP), RTS returns to the
;     caller+3 (JSR pushes caller+2, RTS adds 1). The $E0A2 table entry stays
;     JMP (never JSR/RTS-patched).
;   Preconditions (caller obligations, not callee guarantees): MPR1=$F8
;     (work RAM $2000-$3FFF readable; general BIOS discipline), stack in work
;     RAM, IRQs masked by the caller (same assumption as the sibling
;     experimental bodies). Entry A/X/Y are arbitrary; P is arbitrary EXCEPT
;     T=1 (no $36 genuine entry contract exists; the single dependency-env
;     observation in Issue #802 is one-game single-callsite data and MUST NOT
;     be mistaken for an ABI). T=1 entry is OUT OF THE VERIFIED DOMAIN
;     (BLOCKED/NOT_RUN, never PASS): PHP saves P including T at push time,
;     but the pinned core consumes T at every instruction boundary, so entry
;     T=1 cannot round-trip on ANY implementation (platform fact, not body
;     behavior). Verification covers T=0 seeds only; the Carry guarantee below
;     holds for both tested classes (C=0/C=1) within that domain.
;     MPR1!=$F8 is OUT OF CONTRACT (BLOCKED): the JSR itself already wrote
;     the return address through the MPR1 mapping before entry. Safety for
;     MPR1!=$F8 is NOT proven. The harness validates MPR1 BEFORE executing
;     JSR (contract T13-analog); this body defines NO in-body gate.
;   Grounded behavior: genuine $36 semantics are UNKNOWN (no public source
;     defines inputs, outputs, flags, WRAM/VRAM/VDC/SATB target, shadow,
;     timing, or side effects), so this body performs NO invented service and
;     returns NO invented success value. What is grounded: entry-state
;     preservation only (A/X/Y/P exact including Carry, MPRs unchanged, SP
;     balanced). Preservation is load-bearing (mutants caught).
;   Side-effect discipline (preservation-only):
;     NO WRAM/VRAM/VDC/VCE/MMIO writes of any kind (no ST0/ST1/ST2, no VDC
;     data-port writes, no SATB DMA writes, no VCE/PSG/timer/CD access; no
;     TAM/TMA-write, no TXS, no SEI requirement: there is no I/O to protect).
;     AR unchanged (no target register exists). MPR0-MPR7 unchanged. No
;     work-RAM writes outside the transient caller-stack bytes $21(SP-k)..
;     $21(SP). No zero-page/VRAM/joypad/CD/BURAM access. In particular: NO
;     SAT clearing, NO zero-fill, NO sprite hiding, NO DMA waiting — the name
;     SATCLR MUST NOT be taken to authorize any store.
;   Success: restores Y/X/A then P via plain PLP and returns via RTS. Entry
;     Carry preserved exactly (no CLC/SEC fabricated) for the verified domain.
;     Latch-exact for N/V/D/I/Z/C (and T=0): SetP masks $EF; plain PHP/PLP round
;     trip restores the verified bits exactly. T=1 is excluded from the verified
;     domain (see Preconditions); no T=1 preservation is claimed.
;   No refusal path exists (no ok/reject values; T04/T08/T09 NOT_APPLICABLE).
;   Observability: none beyond the return itself. Only stack pushes occur.
;     Result class SIMULATED, never genuine, never DEV_CALLABLE.
;   Stack: JSR return (2 bytes) plus max 4 transient bytes (P,A,X,Y); SP
;     balanced before RTS. Caller SP below $06 out of contract, unchecked,
;     fail-closed by the caller setup. Stack must be in work RAM (MPR1=$F8).
;   NMI/IRQ assumed absent across the short window (~35 cycles; same as siblings).
;
; Provenance: ORIGINAL project-own code, MIT (repository LICENSE). No
; reference BIOS bytes, no third-party copy, no disassembly derivation.
.setcpu "huc6280"
.segment "BOOT"
.export ex_satclr_entry

ex_satclr_entry:
    php
    pha
    phx
    phy
    ply
    plx
    pla
    plp
    rts
