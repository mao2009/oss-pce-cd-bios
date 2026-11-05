# C-3 static checks (contract + static inspection)

Status: **design and tooling only.**

- No BIOS code is changed.
- The gates stay as they are: #173 / #367 / #459, ABI VERIFIED, DEV_CALLABLE, release_eligible.
- This is not a production safety proof. Results on the verification Geargrafx core are not the safety of a
  compatible BIOS.

C-3 was adopted as the design direction: contracts and static checks, plus the verification environment. Adopting
C-3 does not complete the safety verification.

- Tool: `tools/c3_static_check.py`, with the reader in `tools/c3_asm.py`.
- Contract: `spec/c3_static_contracts.json`.
- Run by: `make check`, which CI already runs.

## Verdicts (never PASS)

| Verdict | Meaning |
|---|---|
| FAIL | A rule is broken, or something is undeclared, unresolved or unsupported (fail closed). The tool exits 1. |
| STATIC_OK | The source text matches the declaration. Nothing about run-time behaviour is claimed. |
| BLOCKED | Depends on a decision that is not approved, such as the production allowlist. |
| NOT_STATIC | Cannot be decided from source text; run-time, device or hardware evidence is needed. |

## Production allowlist: UNDECIDED

The contract keeps `production_allowlist.status = "UNDECIDED"` with every value `null`.

- Filling in values without approval is a FAIL (CI-1.07).
- The #497 verification contract ("MPR1 = $F8 only") is **not** reused as a production allowlist. A reset-time
  MPR1 = $F8 is reported as BLOCKED, not STATIC_OK.
- The `regions` table (CI-2) describes hardware memory-map facts. It is not an allowlist.

## Scope

Every `src/**/*.s` file is classified, with a reason for each fixture:

- **bios:** reset/boot entries, API overrides, internal routines and math.
- **fixture:** development diagnostics and probes.

An unclassified file is a FAIL. The reader is not an assembler:

- It reads every branch of `.if` / `.ifdef` blocks, so the result covers a superset of any one build.
- It reports `.macro`, `.incbin` and `.define`, and unknown statements, as unsupported, which is a FAIL for bios
  files.
- It does not follow `.include`. Each included file is a bios file of its own.

## CI-1: MPR / stack contract (#659)

| Rule | Check |
|---|---|
| CI-1.01 | Every source file is classified; every fixture has a reason. |
| CI-1.02 | Each bios file declares its role (`reset_init`, `api` or `internal`); no unsupported constructs. |
| CI-1.03 | Every TAM is declared in source order, with its mask and value. Undeclared or stale declarations are FAIL. |
| CI-1.04 | The TAM mask is an immediate. The value comes from the `LDA #imm` just before the TAM. A run-time value is accepted only as `restore`, which needs an earlier TMA of the same mask in the file. |
| CI-1.05 | A TAM that changes MPR1 (the stack page) is allowed only in `reset_init`, and is reported BLOCKED because the production allowlist is UNDECIDED. Anywhere else it is a FAIL (static contract S-1). |
| CI-1.06 | TXS is allowed only in `reset_init`, and is reported BLOCKED because the stack-pointer ABI is UNKNOWN. Anywhere else it is a FAIL. |
| CI-1.07 | The production allowlist is UNDECIDED, with all values null. |
| CI-1.08 | A store to a label in a ROM segment is a FAIL. The absence of self-modifying code in general is reported NOT_STATIC. |

The contract lists the rule groups it is checked with (`rule_groups`). A declared group whose module is missing is a
FAIL, never a silent skip.

## Not decidable statically (NOT_STATIC / UNKNOWN)

- Self-modifying code.
- Mappings computed at run time.
- The behaviour of callers outside the BIOS.
- IRQ timing (#653).
- Device DMA (#367).
- Real hardware (#655).
