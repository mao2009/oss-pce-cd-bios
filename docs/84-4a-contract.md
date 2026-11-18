# $4A KEY_BIOS / EX_MEMOPEN contract research (Issue #84, G5 P0 task)

Status: **contract UNKNOWN for behavior; experimental implementation scope only.
NOT a compatible System Card BIOS. NOT genuine ABI. NOT DEV_CALLABLE.
`release_eligible=false`. G5_OBSERVED=false.** Reference JSR 0/81, genuine ABI
VERIFIED 0/81. No System Card image was executed, disassembled, or copied.
No game-disc bytes, titles, paths, or hashes appear here.

## A. Source ledger (provenance)

| # | Source (pinned where applicable) | Kind | What it establishes | Disposition |
| --- | --- | --- | --- | --- |
| S1 | HuCC `include/hucc/pcengine.inc` @ `54f5c736` (sha256 `b6a25d33…`, verified 2026-11-18) | Caller-side header, BSL-1.0, PENDING | `ex_memopen = $00:E0DE, FUNC` inside the CDROM conditional block (line 548) | REFERENCE_ONLY. Name + table mapping only; no behavior. No code taken. |
| S2 | Stum "PC Engine CD-ROM² BIOS Method offsets" (2025-07-28, secondary article) | Public offset table + notes | Row `key_bios / ex_memopen`, JSR `$E0DE`, per-generation bodies `1.0=$FCCA, 2.0/2.1=$E175, 3.0=$FE92, TG2.0=$E18E, TG3.0=$FEAB`. General BIOS discipline: MPR#7=`$00` at startup, MPR#0=`$FF`, MPR#1=`$F8` assumed/required; JSR-via-table discipline. Official 1.0-manual names; 2.0 added 4 methods (none is `$4A`). | REFERENCE_ONLY. Mapping + MPR discipline only; bodies differ per generation (no single behavior). |
| S3 | Zeograd `pce_bios.html` (public BIOS function list) | Public function list | `KEY_BIOS, Function #$4A`. No IN/OUT contract (neighbors `$33`/`$3A`-`$3D` etc. document registers; `$4A` has none). | REFERENCE_ONLY. Name + slot only. |
| S4 | Hugo `bios.c` @ `64ed226d` (GPL-2.0, PENDING, REFERENCE_ONLY candidate per `docs/reuse-inventory.md`) | Emulator HLE source | `KEY_BIOS 0x4A` used **only** as a selector-name label; `handle_bios()` implements no `$4A` behavior case. | REFERENCE_ONLY. Name only; explicitly not entry proof. Nothing copied. |
| S5 | HuCC `hucc-systemcard.asm` @ `54f5c736` (sha256 `4258b7c3…`, verified) | Caller-side wrappers, BSL-1.0, PENDING | BRAM/file helpers (`bm_*`) manipulate IFU `$1803`/`$1807` and bank `$F7` **directly**; no `JSR $E0DE` anywhere in the file. | REFERENCE_ONLY. Negative evidence: no caller-side `$4A` contract exists here. |
| S6 | Repo specs `spec/api_slots.json`, `spec/api_contracts.json` (slot 74), Issue #84 | Project ledger | Slot `$4A`, candidate `$E0DE`, name `KEY_BIOS / EX_MEMOPEN`; every behavioral field UNKNOWN. | Project record, unchanged by this doc. |
| S7 | Issue #802 sanitized FIRST-mode observations (Owner-permitted, read-only, structural only) | Commercial call-state | CUE[0]: `$02→$33→$4A` stub halt; CUE[1]: `$1E→$4A` stub halt. Both cold/warm identical, MPR1=`$F8`, SP balanced, no IRQ. Entry regs: CUE[0] `$4A` A=`$00` X=`$00` Y=`$00` P=`$07`; CUE[1] `$4A` A=`$B0` X=`$03` Y=`$00` P=`$07`. | OBSERVED (entry interface only). No memory/sector/content data. |
| S8 | Local-only `$4A` return-semantics matrix (throwaway `/tmp` diagnostics, SIMULATED class, never merged) | Local experiment: 12 runs (6 return-shape variants x 2 CUEs, cold/warm each). CUE[0]: pure-RTS/A/X/Y/A=$00-forced variants all continue `$02->$33->$4A->$23->$25` halt; CLC (C=0) uniquely diverts to `$05` (CD_EXEC) halt. CUE[1]: all 6 variants continue `$1E->$4A->$02->$02->$21->$1C` halt. Conclusion: CUE[0] provably branches on the return Carry; neither game checks A/X/Y; CUE[1] checks none of C/A/X/Y. | OBSERVED constraints; never genuine ABI; never published raw. |

License/provenance gate: S1/S4/S5 are PENDING and stay REFERENCE_ONLY.
No file or fragment from them enters the firmware. This implementation is
original code written from the interface observations above.

## B. Item-by-item classification

| # | Item | Verdict | Grounds |
| --- | --- | --- | --- |
| 1 | 1.x/2.x KEY_BIOS vs 3.x EX_MEMOPEN functional difference | UNKNOWN (names DOCUMENTED) | S2/S3/S4 agree on the dual name; S2 shows a **different body per generation**, so no single behavior can be assumed. No source describes what either generation does. |
| 2 | `$E0DE` call contract | UNKNOWN (mapping DOCUMENTED) | S1+S2+S3 agree `$E0DE` is slot `$4A`. Zero sources give inputs/outputs. |
| 3 | A/X/Y in, return values | UNKNOWN genuine; entry OBSERVED | S7 entry snapshots above. Return semantics: no source; constrained only by S8. |
| 4 | Carry/Zero and other flags | UNKNOWN genuine; entry OBSERVED | Entry P=`$07` on both CUEs (C set, Z set). S8: forcing C=0 at `$4A` return diverts CUE[0] from `$23` to `$05`; CUE[1] is C-insensitive. P-latch note: on the pinned core the P latch has no B bit (HuC6280 `SetP` masks the popped byte with `$EF`; PHP forces B only in the stacked image), so plain PHP/PLP restores every latch bit exactly. |
| 5 | MPR0–7 preconditions + preservation | Precondition DOCUMENTED (general); per-slot UNKNOWN; entry OBSERVED | S2 general discipline (MPR0=`$FF`, MPR1=`$F8`, MPR7=`$00` at startup). S7: MPR1=`$F8` at both `$4A` entries. What genuine `$4A` requires/preserves beyond that: UNKNOWN. |
| 6 | Stack and return PC | OBSERVED (interface mechanics) | S7 + probe classifier: table JSR, SP delta 2, `stack_check=MATCH`, `return_matches_caller=true`, cold/warm identical. Mechanics only, not semantics. |
| 7 | BIOS work-RAM read/write range | UNKNOWN | No source names `$4A` operands. The commercial probe is observation-only by design (no RAM dumps), so operands are deliberately unmeasured. |
| 8 | Extended-RAM / bank-switch relation | UNKNOWN (BRAM-service hypothesis REJECTED) | "MEMOPEN" suggests memory management, but S5 (the CD devkit) bypasses the BIOS for all BRAM work — negative evidence against a BIOS BRAM service. No affirmative source. Not implemented. |
| 9 | Success / failure / unsupported behavior | UNKNOWN | No source defines any `$4A` return code or error path. |
| 10 | Init / repeat calls / cold-warm | Entry-repeat OBSERVED; semantics UNKNOWN | S7 cold==warm for the entry prefix. Post-`$4A` repeat behavior unmeasured. |

## C. Rejected hypotheses (recorded, not implemented)

- H1 "`$4A` opens/checks backup RAM": REJECTED — S5 negative evidence; name-only support.
- H2 "`$4A` maps Super System Card extended RAM": REJECTED as implementation grounds — no source; S2's per-generation bodies warn against one assumed behavior.
- H3 "fixed success value / bare RTS is the genuine service": REJECTED — no grounds; prohibited by the task. Preservation-only experimental service is scoped in §D instead.

## D. Experimental scope derived from the above

Because behavior is UNKNOWN, the DEV_EXPERIMENTAL `$4A` body is scoped to
what is grounded:

- Real table entry via JSR `$E0DE` (DOCUMENTED mapping S1/S2/S3).
- Entry discipline: require MPR1=`$F8` (OBSERVED S7 + DOCUMENTED S2); anything else is fail-closed refusal, never executed blindly.
- Zero side effects: no TAM/MPR change, no memory write, no bus access. Preservation of A/X/Y/P/MPR/SP is the only "effect", proved load-bearing by mutants.
- Return: RTS resumption only. No invented success code in A, no flag fabrication beyond entry preservation. Preservation is latch-exact: on the pinned core the P latch has no B bit (`SetP` masks with `$EF`), so plain PHP/PLP restores every latch bit and no stacked-byte surgery is used. Refusal path sets Carry (project-experimental convention, documented as NOT genuine) and still preserves everything else.
- Applicability is limited to the observed input classes (S7); anything beyond preservation is BLOCKED, and the service is never presented as genuine, callable, or release-eligible.

The S8 matrix decides nothing genuine; it only checks that the scoped
preservation service is compatible with what the two observed games require
(as opposed to a guessed behavior). Any divergence is reported, not patched over.
