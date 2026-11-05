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
- Any whitespace (space or TAB) after a directive or mnemonic ends the statement word.
- A constant assigned inside a conditional block is not resolved: it can differ between builds.
- Unsupported constructs (`.macro`, `.incbin`, `.define`, unknown statements) are a FAIL, because CI-1.02 is a
  declared contract rule for bios files. Values the reader cannot decide are NOT_STATIC.

## CI-1: MPR / stack contract (#659)

| Rule | Check |
|---|---|
| CI-1.01 | Every source file is classified; every fixture has a reason. |
| CI-1.02 | Each bios file declares its role (`reset_init`, `api` or `internal`); no unsupported constructs. |
| CI-1.03 | Every TAM is declared in source order, with its mask and value. Undeclared or stale declarations are FAIL. |
| CI-1.04 | The TAM mask is an immediate. The value comes from the `LDA #imm` just before the TAM. A run-time value is accepted only as `restore`, which needs an earlier TMA of the same mask in the file. If the value cannot be decided from the text — a directive or data line (`.byte`, `.else`, `.endif`, ...) between the LDA and the TAM, different conditional states of the two, or a constant defined inside a conditional block (a build option such as `-D`) — the site is NOT_STATIC, never STATIC_OK. An MPR1 change outside `reset_init` stays a FAIL whatever the value. |
| CI-1.05 | A TAM that changes MPR1 (the stack page) is allowed only in `reset_init`, and is reported BLOCKED because the production allowlist is UNDECIDED. Anywhere else it is a FAIL (static contract S-1). |
| CI-1.06 | TXS is allowed only in `reset_init`, and is reported BLOCKED because the stack-pointer ABI is UNKNOWN. Anywhere else it is a FAIL. |
| CI-1.07 | The production allowlist is UNDECIDED, with all values null. |
| CI-1.08 | A store (STA / STX / STY / STZ) or read-modify-write (INC / DEC / ASL / LSR / ROL / ROR / TSB / TRB / RMBn / SMBn) to a label in a ROM segment is a FAIL, including `label+offset` forms. The absence of self-modifying code in general is reported NOT_STATIC. |

The contract lists the rule groups it is checked with (`rule_groups`). A declared group whose module is missing is a
FAIL, never a silent skip.

## CI-2: block transfer contract (#660)

Each bios file lists its TII / TIN / TDD / TIA / TAI sites in source order (`transfers`), with:

- the operands;
- the mapping (`mpr`: page → bank) of every page the transfer touches;
- a source and a destination region, taken from the `regions` table (kind `ram`, `rom` or `mmio`);
- the purpose.

Address sequences follow the pinned core, as measured in #648:

| Instruction | Sequence |
|---|---|
| TDD | decrements both addresses |
| TIN | keeps the destination fixed |
| TIA | alternates the destination between dst and dst+1 |
| TAI | alternates the source |

Length 0 means 65536 bytes.

| Rule | Check |
|---|---|
| CI-2.01 | Every transfer is declared, in order. |
| CI-2.02 | The operands are static constants. Run-time or linker-placed operands are outside the supported scope and are a FAIL. |
| CI-2.03 | The declared operands equal the source operands. |
| CI-2.04 | Every touched page has a declared mapping (fail closed). |
| CI-2.05 | Every source and destination byte is inside its declared region. A ROM destination is a FAIL. |
| CI-2.06 | A legitimate I/O transfer, such as TIA to the VDC data port, is declared with an `mmio` source or destination region. It is **not** refused for being I/O. It is reported BLOCKED, never STATIC_OK, until the production allowlist (UNDECIDED) covers it. This applies to reading device ports as well as writing them. |
| CI-2.08 | The declared mapping of every touched page is cross-checked against the TAMs of the same routine before the transfer. A TAM that gives a different bank is a FAIL. A page the routine does not map itself (its mapping comes from the caller or environment), a TAM value that cannot be decided, or a TAM that is not on every path to the transfer (a label, local ones included, a branch, jump, call or return, or a directive line between them, or a different conditional state) makes the transfer NOT_STATIC: the declaration is then only an assumption, and the transfer is never STATIC_OK. |
| CI-2.00 | A region's kind must match its bank, using memory-map facts: `mmio` = $FF, `ram` = $68-$87 / $F7-$FB, `rom` = below $68. The `wram` region covers the whole bank, including the zero page and the stack page. |
| CI-2.07 | NOT_STATIC: a mid-transfer mapping change, IRQ entry during a transfer, device DMA, and operands rewritten at run time (#648 / #653 are emulator evidence; real hardware is #655). |

Today the bios scope has no block transfers. The tool prints the site count.

## Not decidable statically (NOT_STATIC / UNKNOWN)

- Self-modifying code.
- Mappings computed at run time.
- The behaviour of callers outside the BIOS.
- IRQ timing (#653).
- Device DMA (#367).
- Real hardware (#655).
