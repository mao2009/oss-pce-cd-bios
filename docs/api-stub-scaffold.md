# Issue #100 — fail-closed API-entry scaffold (DEV ONLY)

This is a diagnostic ROM, **not compatible System Card firmware**. All 81 API
services remain unimplemented. No proprietary BIOS, disassembly or game is used.

## Inventory and replacement

`spec/api_slots.json` declares 81 ordered candidates `$00..$50`. The generator
emits three-byte `JMP abs` entries at candidate CPU address `$E000 + 3*slot`:
first `$E000`, last `$E0F0`, table end `$E0F3` (exclusive). Names match
[the inventory](api-inventory.md), including unresolved aliases at `$0D/$4A/$4B/$4C/$50`.
Names are documentation, not confirmed service contracts. `$48` is one PSG_BIOS
slot; selectors `00/01/02/03/04/0B/0C/10/13` are nine candidate subcommands,
not additional jump-table entries. Their selector ABI remains unverified (#3).

Each fallback executes `LDX #slot; JMP api_unimplemented`, then `BRA self`.
Reset executes `SEI; LDX #$FF; BRA self`. There is no RTS, BRK, success result,
BIOS work RAM write or CD boot. Interrupt vectors point to the same diagnostic
reset, a development choice that does not implement any interrupt ABI.

A future implementation adds `src/overrides/slot_XX.s`, exports `api_slot_XX`
in segment `OVERRIDES`, and replaces only that generated fallback. The host test
assembles one **nonreturning diagnostic override**, not a successful API.
Filename presence does not establish implementation, license approval or ABI
validation. `make dev-release-gate` refuses missing slots and remains blocked even
when all slots are overridden until ROM/ABI/boot and adoption approvals exist.
`make release` retains the existing blanket refusal. No production ROM is emitted.

### What override verification does and does not prove

`build()` links every override with ld65 debug info and runs `verify_overrides`.
This is **structural** verification of the binary, nothing more:

- the table `JMP` for the slot equals the linked `api_slot_XX` address, and that
  address is an instruction (not data/padding) of `slot_XX.s` in `OVERRIDES`;
- every branch/`BRA`/`BSR`/`BBRn`/`BBSn`/`JMP abs`/`JSR abs` target inside
  `$E000-$FFFF` lands on an instruction of an override or on a table/stub entry,
  never padding, data, vectors, boot code or the middle of an instruction;
- no instruction falls through into data or padding (code must end in
  `JMP`/`RTS`/`RTI`/`BRA`);
- instruction boundaries from ld65 debug info are **cross-checked against the ROM
  bytes** with cc65's own `da65` HuC6280 opcode table (built by `make setup`), so a
  patched opcode whose length no longer matches, or an undefined opcode, is rejected.

Limitations (all fail closed or are stated): `JMP (abs)`/`JMP (abs,X)` targets and
calls below `$E000` are data-dependent and accepted unchecked; only `.byte`/`.word`
data is recognised as data, so `.incbin`/`.res` inside `OVERRIDES` are rejected.
Not checked: register/flag/RAM behaviour, return conventions, indirect jumps,
calls below `$E000`, or compatibility with any System Card ABI. A passing
override is therefore **not** an implemented API; `manifest.json` records
`override_checks: structural-only` and `abi: UNVERIFIED`, and the release gate
stays blocked.

### Release-gate exit codes

`tools/build_rom.py --release-gate` exits **3** with a `BLOCKED:` message when it
refuses as designed, and **2** for build/tool errors (a Python exception exits 1).
`make` reports any failing recipe as 2, so CI uses `make dev-release-gate-blocked`
(`tools/expect_exit.py 3 '^BLOCKED:' -- ...`), which passes only for exactly that
refusal; unexpected success, missing tools or tracebacks fail the job.

### Manifest provenance

`manifest.json` describes the source root actually built. `source_sha` and
`working_tree_dirty` are recorded only when that root is itself a git top level
(otherwise `null`, `source_root_is_git_repo: false`), and `source_sha256` lists the
hashes of `boot.s`, `bank0.cfg`, `api_slots.json`, each override source and every
`.include`d file ca65 reports (`--create-dep`). The cc65 toolchain is pinned by
`cc65_revision` but not hashed; this is input tracking, not a full reproducible-build proof.
`spec/api_slots.json` is always read from the tool's own checkout.

## Reproducible commands and artifacts

```sh
make setup
make build                 # unchanged 8 KiB HuCARD smoke fixture
make dev-rom               # build/debug/bios-dev/ diagnostic image + map/labels/manifest
make test                  # all host tests (host-only; no Geargrafx)
make test-real-core        # existing HuCARD real-core tests (required CI gate)
make dev-test              # API host tests + diagnostic real-core tests
make dev-rom MODE=release
cmp build/debug/bios-dev/dev-only-not-compatible-syscard3.pce build/release/bios-dev/dev-only-not-compatible-syscard3.pce
make dev-release-gate-blocked  # passes only on exit 3 + BLOCKED (plain dev-release-gate: make shows 2)
```

Both programs use the existing pinned ca65/ld65 and Geargrafx source cache.
No apt-installed assembler or copied emulator source is required. Debug/release
are build modes for diagnostics; neither is a releasable BIOS. The diagnostic
image is headerless 262144 bytes: linked bank0 plus 31 FF-padded 8192-byte banks.
Size, table opcodes/targets/IDs, vectors, reset instructions and padding are
checked explicitly. This validator checks the **development layout**, never
System Card compatibility. Smoke fixture validation remains separate.

## Measured Geargrafx behavior

`tests/integration/geargrafx_api.py` links an original C++ probe against objects
from the separate debugger-enabled official pinned Geargrafx libretro build. It uses public
[`GeargrafxCore`](https://github.com/drhelius/Geargrafx/blob/b49ae82a012eade566d72e9d61c9d95a5caa4da3/src/geargrafx_core.h),
[`HuC6280::GetState`](https://github.com/drhelius/Geargrafx/blob/b49ae82a012eade566d72e9d61c9d95a5caa4da3/src/huc6280.h),
[`Memory::GetMpr` / `GetPhysicalAddress`](https://github.com/drhelius/Geargrafx/blob/b49ae82a012eade566d72e9d61c9d95a5caa4da3/src/memory_inline.h) APIs.
The core's [reset initialization](https://github.com/drhelius/Geargrafx/blob/b49ae82a012eade566d72e9d61c9d95a5caa4da3/src/memory.cpp)
is a source of emulator behavior hypotheses, not a hardware specification.
There are no patches to Geargrafx, no Python CPU simulation and no SDL GUI dependency.
The new debugger-enabled build proves single-instruction capability; earlier
frame-bounded checkpoints and their corrected interpretation are documented
in [expanded evidence](geargrafx-contract-evidence.md).
An independently generated 32-sector zero-filled CUE/BIN activates the System Card
path after `LoadBiosFromBuffer(..., true)` loads the diagnostic ROM.

Observed against Geargrafx `b49ae82a012eade566d72e9d61c9d95a5caa4da3`:

- Reset vector loads PC `$F000`; real stepping reaches `$F003`, X `$FF`, MPR7 `$00`.
- Additional bounded stepping and warm reset retain the expected nonreturning checkpoint.
- Three synthetic variants replace the checked reset instruction sequence with
  `SEI; LDX #$FF; JMP candidate_entry`. Real execution traverses slots `$00/$48/$50`
  and reaches the shared halt with distinct X `$00/$48/$50`, including warm resets.
  This is a JMP dispatch experiment, **not a JSR/RTS or caller ABI test**.
- With MPR7 `$00`, CPU `$E000..$FFFF` maps physical offsets `$0000..$1FFF`.
  A debugger-controlled MPR7 `$01` exposes physical `$2000` FF padding.
- Three wrong-entry variants fail the same PC/X assertion. A fourth guest TAM
  selecting bank1 fails the reset checkpoint. All execute on the actual core.

Evidence is `build/<mode>/bios-dev/geargrafx-api-evidence.json`. PC/X/MPR0..7 and
physical PC are measured, not inferred from assembly. MPR0..6 start randomized in
this core and are deliberately not asserted as hardware reset defaults.

The probe executable links GPL-3.0-or-later Geargrafx (and its upstream dependencies).
It stays local in ignored build output; CI uploads only original ROM, maps,
labels and JSON evidence, never the linked executable or emulator source.
Firmware does not contain or depend on Geargrafx implementation code. Any future
redistribution of that executable requires its own full upstream license/source
obligation review; MIT documentation does not relicense the linked program.
All OSS firmware reuse candidates remain PENDING under [the reuse policy](provenance.md).

## Confirmed experiment vs unresolved ABI

| Property | Current evidence / limit |
| --- | --- |
| 81 contiguous entries, JMP bytes, unique IDs | Built binary + host tests; source candidates only |
| System Card loader acceptance and bank0 reset | Real pinned Geargrafx with synthetic disc; emulator behavior only |
| 32-bank file, physical bank0, FF bank1 | Diagnostic linker contract + core memory observation |
| Other MPR reset defaults, real hardware bank behavior | UNVERIFIED; core randomization is not hardware proof |
| IRQ/NMI semantics, stack, RAM/zero page allocation | UNVERIFIED; diagnostic does not initialize/use a caller stack |
| Register preservation/destruction, arguments, returns, PSG selectors | UNVERIFIED; X is a development diagnostic marker only |
| Guest synthetic-CD READ(6) | Measured in separate low-level diagnostic; not a BIOS callee |
| BIOS `CD_READ`, disc boot/handoff, retail compatibility | NOT_IMPLEMENTED / NOT_TESTED |
| Production System Card validity | BLOCKED pending #1/#3/#4/#6/#8 |

CI has an independent BIOS DEV workflow and artifact, preserving the HuCARD
workflow/result. Nightly keeps its original smoke-only baseline schema and success
criteria; BIOS DEV is intentionally not added to Nightly yet.

Next: use already implemented startup and guest CD diagnostics as **scoped
evidence**, not working BIOS services. Resolve #1/#3 ROM/caller ABI before
implementing production #4 startup and callable CD services. The RAM payload
experiment (#122) is merged as a diagnostic; it is not a CD boot (2026-10-16).
#100 retains release gates; #6/#103 retain trace/MCP work.
