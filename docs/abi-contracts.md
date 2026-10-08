# Candidate API contracts (Issue #3)

The complete 81-slot ledger is [api_contracts.json](../spec/api_contracts.json).
It preserves the names and ordering of [api_slots.json](../spec/api_slots.json)
and [the preliminary inventory](api-inventory.md). Nothing here certifies a
System Card implementation: no actual API implementation exists at this base,
no implemented service callee was executed, and every verification record is `NOT_RUN`.

This research uses pinned upstream caller/emulator sources only. No proprietary
firmware, disassembly, external implementation or asset was copied or imported.
Sources are reference-only with provenance/adoption `PENDING`; see the
[file audit](oss-audit-systemcard.md). Issues [#3](https://github.com/mao2009/oss-pce-cd-bios/issues/3)
and [#1](https://github.com/mao2009/oss-pce-cd-bios/issues/1) were read for scope.

## Reading the ledger

Each row records candidate name, slot and CPU-visible entry, source-specific
name observations, arguments, returns, clobbered/preserved state, stack, MPR,
RAM, flags, IRQ, errors, synchronization, sources, confidence, provenance,
implementation and verification. `UNKNOWN` means the field has no supported
claim. `HYPOTHESIS` with `SOURCE_SUPPORTED` means a caller expectation or HLE
model can be located in a pinned source; it does not establish BIOS behavior.
A field may contain a reported observation while explicitly leaving the actual
callee behavior unknown. `NOT_STARTED` applies to firmware implementation.

Source objects include repository, exact SHA, path, file SHA-256 and immutable
URL. Source status never implies legal adoption approval. The observations use
upstream symbols; candidate names remain unchanged even where aliases conflict.
The `$E000 + 3*slot` convention is a candidate CPU address, not a physical ROM
address or proven target implementation address. Parent Issue #1 owns executed
CPU/map probes; architectural JSR/RTS/IRQ tests cannot establish these API ABIs.
The [default pinned libretro Makefile](https://github.com/drhelius/Geargrafx/blob/b49ae82a012eade566d72e9d61c9d95a5caa4da3/platforms/libretro/Makefile#L377-L378) defines `GG_DISABLE_DISASSEMBLER`. Prior
`RunToVBlank(debug.step_debugger=true)` probes therefore ran frames; their
bounded halt checkpoints remain useful, but were not individual instruction
steps. The integrated separate debugger-enabled build uses the same archive SHA
without upstream patches and passes a one-SEI/PC+1 capability check.
[Expanded observations](geargrafx-contract-evidence.md) cover CPU/RAM/IRQ
experiments; neither kind of architectural checkpoint verifies a BIOS API.

The nine PSG subselectors are not additional jump-table slots and are outside
this 81-row ledger.

## Names and order

[HuCC pcengine.inc](https://github.com/pce-devel/huc/blob/54f5c73606c1e7142e8095c727cab2767e2b3cb3/include/hucc/pcengine.inc#L468-L552)
provides 77 equates at the candidate addresses through `$4C`. It has no equates
for `$4D–$50`. Hu-Go's selector definitions give additional observations for
`$4F/$50`, but do not prove executable addresses. Its `bios.c` is an HLE model,
not original firmware or independent hardware measurement.

| Slot | HuCC observation | Hu-Go observation | Research disposition |
| --- | --- | --- | --- |
| `$0A` | CD_SUBQ | CD_SUBA | Additional spelling difference; semantics UNKNOWN |
| `$0C` | CD_CONTNTS | CD_CONTENTS | Additional spelling difference; semantics UNKNOWN |
| `$0D` | CD_SUBRD | CD_SUBRQ | Preserve both inventory aliases |
| `$42/$43` | MA_DIV16S / MA_DIV16U | MA_DIV16U / MA_DIV16S | Opposite signedness order; block normative assignment |
| `$4A` | EX_MEMOPEN | KEY_BIOS | Generation/behavior unresolved |
| `$4B` | PSG_DRIVER | PSG_DRIVE | Preserve both aliases |
| `$4C` | EX_COLORCMD | EX_COLORC | Preserve both aliases |
| `$4D/$4E` | absent | absent | Existing AD_STREAM_START/POLL labels remain unconfirmed |
| `$4F` | absent | MA_MUL16S | Candidate observation only |
| `$50` | absent | MA_CBASIS | Does not resolve MA_DIV8U/MA_CBASIS conflict |

Hu-Go observations are from [bios.c selector definitions](https://github.com/mckayemu/hugo/blob/64ed226da2288738781743eeef228c069e8973a4/bios.c#L30-L133).
Existing article-derived inventory labels retain their pending lineage; this
research does not adopt or expand those articles' reverse-engineering claims.

## Caller context, not a global ABI

[pcengine.inc](https://github.com/pce-devel/huc/blob/54f5c73606c1e7142e8095c727cab2767e2b3cb3/include/hucc/pcengine.inc#L330-L343)
labels `_al/_ah/_bl/_bh/_cl/_ch/_dl/_dh` as eight work-RAM bytes at bank `$F8`,
CPU `$20F8–$20FF`; `_ax/_bx/_cx/_dx` are pairs, low byte first. They are not
physical A/X/Y registers. HuCC startup maps MPR1 to `$F8` and MPR0 to `$FF`;
these are SDK environment choices, not verified per-API prerequisites.

[core.inc](https://github.com/pce-devel/huc/blob/54f5c73606c1e7142e8095c727cab2767e2b3cb3/include/hucc/core.inc#L37-L46)
passes an entry low byte in Y to `call_bios`.
[core-kernel.asm](https://github.com/pce-devel/huc/blob/54f5c73606c1e7142e8095c727cab2767e2b3cb3/include/hucc/core-kernel.asm#L486-L519)
maps bank zero into MPR7, calls the entry with JSR, restores MPR7 itself, and
returns with RTS. Its PHA/PLA pairs protect A while switching banks. Therefore
wrapper MPR7 restoration, wrapper stack balance and wrapper output flags must
not be reported as BIOS preservation. The trampoline changes Y and flags;
its post-call PLA can change N/Z. No global preserved register, flag, RAM or
MPR set has been inferred. Callee stack usage and IRQ interactions stay unknown.

## Focused caller contracts

### EX_GETVER — slot `$1E`, candidate `$E05A`

[hucc-systemcard.asm lines 72–79](https://github.com/pce-devel/huc/blob/54f5c73606c1e7142e8095c727cab2767e2b3cb3/include/hucc/hucc-systemcard.asm#L72-L79)
calls without explicit arguments, then uses TXA/SAY to adapt the presumed X:A
result to HuCC Y:A. [core-stage1.asm](https://github.com/pce-devel/huc/blob/54f5c73606c1e7142e8095c727cab2767e2b3cb3/include/hucc/core-stage1.asm#L94-L99)
compares X with 3. This supports a caller expectation of major version in X;
it does not establish that a 3.0 callee returns X=3 or a particular A byte.
Exact minor encoding, A/X/Y/S/P preservation, work-RAM changes, MPR behavior,
IRQ requirements, errors and timing are `UNKNOWN`. Hu-Go contains an EX_GETVER
name but no dedicated execution switch case; a name is not an implementation.

### CD_RESET — slot `$01`, candidate `$E003`

[HuCC lines 96–100](https://github.com/pce-devel/huc/blob/54f5c73606c1e7142e8095c727cab2767e2b3cb3/include/hucc/hucc-systemcard.asm#L96-L100)
and the C declaration expose `void cd_reset(void)`, with no explicit API
arguments or consumed return value. Ignoring a result does not prove no result.
[Hu-Go lines 356–376](https://github.com/mckayemu/hugo/blob/64ed226da2288738781743eeef228c069e8973a4/bios.c#L356-L376)
models media initialization and writes 1 to `$222D` as a disc-present marker.
Physical-drive init failure terminates the host; it is not a BIOS error code.
Actual reset sequence, ready/busy completion, no-disc handling, timeout, IRQ,
all register/flag preservation, stack behavior and RAM/MPR writes are unknown.

### CD_READ — slot `$03`, candidate `$E009`

[HuCC lines 283–459](https://github.com/pce-devel/huc/blob/54f5c73606c1e7142e8095c727cab2767e2b3cb3/include/hucc/hucc-systemcard.asm#L283-L459)
and [get_file_lba](https://github.com/pce-devel/huc/blob/54f5c73606c1e7142e8095c727cab2767e2b3cb3/include/hucc/core-kernel.asm#L542-L552)
prepare `_cl:_ch:_dl` as high:middle:low sector position.

| `_dh` reported mode | Destination | Count from caller/HLE |
| --- | --- | --- |
| 0 | CPU-local address `_bx` | `_ax` byte count (16 bits) |
| 1 | CPU-local address `_bx` | `_al` sector count (Hu-Go only) |
| 3 | starting bank `_bl`, caller uses MPR3 | `_al` sector count |
| 2, 4, 5, 6 | starting bank `_bl` | `_al` sectors (Hu-Go model; HuCC overlay loader also uses 6) |
| `$FE` | VRAM address `_bx` | `_ax` byte count |
| `$FF` | VRAM address `_bx` | `_al` sector count |

These are reported supported modes, not a proven exhaustive mode list. Sector
origin/base interpretation, count zero, upper bounds, bank wrap, odd VRAM byte
counts, address units and all invalid-input semantics require callee evidence.
HuCC's `_cd_fastvram` nearby comment uses the `cd_loadvram` name; the actual
macro/procedure symbol and C declaration distinguish sector and byte variants.

HuCC copies presumed status A to X; TAX/BNE or CMP tests zero success/nonzero
error. Such tests establish neither BIOS Z nor carry. The local-data wrapper
saves `_bx.._dl` across calls; this is evidence of caller protection rather
than a complete callee clobber list. The bank wrapper explicitly reports an
MPR3 corruption bug on CD error and restores MPR3 itself. The overlay loader
reports the analogous MPRn error-path problem, resets its mappings, and runs
CLI before CD_READ. All these remain reported hypotheses, without assuming
success-path preservation or a universal interrupt-enable rule.

[Hu-Go lines 378–573](https://github.com/mckayemu/hugo/blob/64ed226da2288738781743eeef228c069e8973a4/bios.c#L378-L573)
models synchronous 2048-byte sector loops, A=0 success, N/Z status updates and
T clearing. For `$FF` count zero it reports A=$22. Unsupported modes fall back
to original execution. These HLE shortcuts cannot establish BIOS flags,
error taxonomy, timing, work-RAM writes or MPR preservation. Exact register,
stack, flags, IRQ and memory effects must be measured on an executed callee.

## Validation and remaining evidence

Run from the repository root:

```sh
python3 tools/contracts.py
python3 -m unittest discover -s tests/host -p test_contracts.py -v
```

The original validator checks all 81 names/slots/entries against both unchanged
inventories, required fields/statuses, pinned source metadata, source unions,
UNKNOWN consistency and explicit contradictory preserved/clobbered register
lists. Prose cannot be automatically proven consistent; that requires review.
Schema 1 deliberately rejects VERIFIED/PASS and adoption APPROVED because this
ledger contains no executed-callee evidence. Future promotion requires an
explicit schema/evidence change, not silently relabeling source observations.

Host test PASS certifies bookkeeping only. All 81 BIOS verifications remain
`NOT_RUN`; preservation is UNKNOWN for every row. Remaining work includes
source-origin review, resolving naming/signedness conflicts, implementing each
callee independently after contract review, then executing original fixtures
with ROM hash, core revision, entry/exit registers/flags/MPR/stack/RAM deltas,
IRQ state, success/error/boundary inputs and recorded outcomes. Issue #1 CPU
architectural probes remain separately owned and are not duplicated here.
