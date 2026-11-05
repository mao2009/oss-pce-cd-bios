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

- It reads every branch of `.if` / `.ifdef` blocks. That is a superset for "what may happen" rules (a TAM, an
  access, a call in any build). For rules that need something in every build (an SEI or the MPR0 set-up of a
  window, the MPR0 set-up before an unconditional call), a conditional SEI / set-up / restore is a FAIL.
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
| CI-2.06 | A legitimate I/O transfer, such as TIA to the VDC data port, is declared with an `mmio` destination region. It is **not** refused for being I/O. It is reported BLOCKED until the production allowlist (UNDECIDED) covers it. |
| CI-2.07 | NOT_STATIC: a mid-transfer mapping change, IRQ entry during a transfer, device DMA, and operands rewritten at run time (#648 / #653 are emulator evidence; real hardware is #655). |

Today the bios scope has no block transfers. The tool prints the site count.

## CI-3: IRQ / MMIO contract (#661)

| Rule | Check |
|---|---|
| CI-3.01 | ST0 / ST1 / ST2 sites are declared. They write the VDC independently of the MPRs (#365 / #429). Declared sites are BLOCKED (production allowlist UNDECIDED). |
| CI-3.02 | Page-0 accesses ($0000-$1FFF, through MPR0): see below. |
| CI-3.03 | `precondition` routines and their callers: see below. |
| CI-3.04 | Indirect (pointer) accesses are declared with a count and a reason. They are NOT_STATIC, because the effective address is a run-time value. |
| CI-3.05 | The VECTORS segment and IRQ handlers: see below. |
| CI-3.06 | NOT_STATIC: device-originated DMA (#367), IRQ timing and IRQs newly asserted inside an instruction (#653), and real hardware (#655). |

### CI-3.02: page-0 accesses

Every page-0 memory operand must use an address declared for the file. These are a FAIL:

- an undeclared access, or a declared address that is never used;
- an indexed page-0 access, because its reach is not a single declared address;
- a memory operand that cannot be resolved and is not a label of the file, such as an import or an expression
  (fail closed).

Addressing follows ca65:

- An operand without `a:` and a value of $FF or less is zero page, which is WRAM at $2000, not MMIO.
- The exception is `,y` on opcodes without a zero page,Y form (everything except LDX and STX). ca65 assembles those
  as absolute,Y.
- For TST, the memory operand is the second one.

A TAM that maps $FF through an MPR other than MPR0 is a FAIL. Absolute accesses to pages 2-6, and to
linker-placed RAM (BSS), are NOT_STATIC.

**`window`:** the file maps MPR0 to the hardware page itself. Each window is `LDA #$FF / TAM #$01` …
`TAM #$01` (the CI-1 `restore`). The window must:

- be one straight-line run, with no label, branch, jump, call, return, CLI, PLP or RTI inside it;
- follow an SEI earlier in the routine, with no CLI or PLP after that SEI;
- contain every page-0 access of the file.

A window file needs at least one window and one access.

### CI-3.03: `precondition` routines

The routine documents that its caller maps MPR0 = $FF.

- The tool itself finds every JSR / JMP / BSR in a bios file to any label the routine's file exports.
- The calling file must be listed in `callers`. A listed caller that never calls is also a FAIL.
- The closest `TAM #$01` before the call must be `LDA #$FF / TAM #$01`, not a restore, in the same routine and on
  one straight-line run to the call: no branch, return or label in between.
- A non-local label in that run is accepted only if it is a declared pass-through label (`mmio_pass_labels`, with a
  reason) and no branch, jump or call in the bios scope targets it. Such a label is reported NOT_STATIC, because an
  entry from outside the bios scope would skip the setup.
- A JSR / JMP / BSR whose operand is not a bare label (indirect, or an expression) is a FAIL, because callers and
  targets cannot be found statically.
- Callers outside the bios scope are NOT_STATIC.

### CI-3.05: VECTORS and handlers

- VECTORS holds exactly five `.word` entries, and nothing else: IRQ2/BRK, IRQ1, Timer and NMI must point to declared
  handlers, and Reset to the declared reset entry.
- A handler body runs to the next non-local label and must end in RTI or an unconditional loop, with no
  fall-through.
- A handler is a FAIL if it contains TAM / TMA / ST0-2, a block transfer, a call or jump to another routine, or an
  indirect, unresolved or page-0 operand.

### Known limits (stated, not hidden)

- **Window restore value:** a window's closing TAM must take its value at run time (the CI-1 `restore`). The value
  it restores is not checked.
- **Handler flags:** a handler may contain CLI, PLP or SEI; the static check does not judge IRQ re-enabling inside
  handlers.
- **IRQ masking in precondition routines:** the precondition model checks the MPR0 set-up only, not that IRQs are
  masked in the caller or the callee. Handlers themselves may not touch page 0 (CI-3.05).
- **Window entry labels:** an SEI placed before a fall-through entry label inside a window file is rejected. This is
  fail-closed, and no current source uses the pattern (accepted LOW).
- **Vectors in other builds:** a reset entry whose vectors live in fixtures or build options is reported NOT_STATIC,
  and those handlers are not checked.

### Limits of the reader

- A constant assigned more than once is reported as unsupported, which is a FAIL.
- Command-line `-D` overrides used by the build scripts are outside the check.

## Not decidable statically (NOT_STATIC / UNKNOWN)

- Self-modifying code.
- Mappings computed at run time.
- The behaviour of callers outside the BIOS.
- IRQ timing (#653).
- Device DMA (#367).
- Real hardware (#655).
