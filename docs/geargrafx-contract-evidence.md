# System Card diagnostic evidence — #1 / #3 / #6 / #101

All programs in this report are independently authored experiments, not BIOS
services. No commercial BIOS/disassembly, game data or adopted OSS firmware is
used. The original HuCARD fixture and base BIOS DEV bytes remain unchanged.
Detailed source facts/unknowns: [ROM contract](system-card-rom.md),
[API ledger](abi-contracts.md), [OSS audit](oss-audit-systemcard.md).

## Debugger capability correction

The pinned upstream libretro Makefile defines `GG_DISABLE_DISASSEMBLER` even
with its `DEBUG=1` option. Previous #101 `RunToVBlank(debug.step_debugger=true)`
calls ran frames; their initial and bounded halt checkpoints were real, but
were not individual instruction steps. Earlier step wording is corrected here.

`make setup` now also builds the **same checksum-verified source archive** in
an isolated external `geargrafx-<SHA>-debugger` tree. Compiler variables are
overridden to leave disassembly/debugger enabled. No upstream source is patched,
no emulator implementation is copied into this repository, and the normal
libretro core used by HuCARD smoke remains separate. Source inventory checks,
SHA-256 validation, setup lock and incremental rebuilding remain active.
Manual debugger-only setup: `python3 tools/setup.py geargrafx --debugger`.

The original frontend uses matching build macros and verifies that executing
one known `SEI` advances PC by exactly one byte. A disabled debugger, missing
core or unavailable System Card/CD path fails the experiment. The frontend is
compiled at O0 for fast setup; the official core objects are O2. This does not
model CPU instructions in Python or bypass official core execution.

## Reproduce and inspect

```sh
make setup
make build
make test
make check
make build MODE=release
make dev-test
make dev-rom MODE=release
make dev-release-gate    # expected nonzero: API services/ABI approval missing
```

`make test` includes 116 host tests, retaining all 89 previous tests.
`make dev-test` retains 30 API host checks and executes the original reset,
three slot probes and four negatives, plus the experiments below. Additional
host tests assemble probes in debug/release and compare their actual bytes.

Evidence: `build/debug/bios-dev/geargrafx-api-evidence.json`.
Experimental ROMs, labels, map and manifest:
`build/debug/bios-dev/contract-probes/`. CI saves only these original ROMs and
sanitized JSON/map/labels, never the GPL-linked executable or emulator objects.
Any future executable redistribution needs its own upstream source/license
obligation review; test frontend linkage does not relicense Geargrafx as MIT.

## Measurement scope

Official Geargrafx revision: `b49ae82a012eade566d72e9d61c9d95a5caa4da3`.
Archive SHA-256:
`a80a3eae26a8efcd994350e84bbe73c975e406ced41f8f2404c88b00d4217f68`.
Every executed ROM and loader input has its own SHA-256 in the evidence. The
loader is `LoadBiosFromBuffer(syscard=true)` followed by `LoadMedia` of an
original 32-sector, zero-filled MODE1/2352 CUE/BIN. This is **System Card/CD
hardware context, not HuCARD loading**. No CD read/boot protocol is exercised.

Observed fields are PC, A/X/Y, P, SP, IRR/IDR, MPR0..7, physical PC and physical
working-RAM snapshots (offsets `$0200..$023F`, stack `$01F8..$01FF`). These RAM
bytes correspond to CPU `$2200..$223F` and `$21F8..$21FF` only after the guest
maps MPR1 to `$F8`. Initial MPR0..6 and general registers are recorded as seen;
no test turns the core's random reset values into hardware defaults.

The original reset diagnostic reaches `$F003`, X `$FF`; candidate entries
`$00/$48/$50` reach slot-specific X markers in a nonreturning loop. Their old
cold/warm assertions and four wrong-target/bank negatives are retained. API
exits are `NOT_OBSERVABLE`: no implemented BIOS callee exists. The public CPU
interface has IRQ1/IRQ2 injection but no NMI injection, so NMI execution is
`NOT_OBSERVABLE`, not PASS. The NMI vector address is a separate source fact.

## Additional real-core experiments

Eight independently linked test variants run 512 instruction steps to cold/warm
checkpoints (cold also includes the initial one-SEI capability step), with
another 32 steps checking the terminal state. Exact counts are in JSON. Their code lives
in an isolated `$E800..$EFFF` overlay, retaining all 81 base table entries.
Temporary procedures are **not** installed in API slots or counted as API
implementations. Reset/vector overrides are explicit test inputs.

| Experiment | Actual checked result | Limit |
| --- | --- | --- |
| ROM banks | Guest maps banks 1/2/3/31, reads first/last bytes across 8 KiB boundaries, changes MPR2 bank1→2→1, checks physical bank `$20` mirroring bank0; markers `11 1F 22 2F A1 AF 33 3F 5A A5 22 11` | Project test file layout and pinned core decode, not historical card capacity |
| Work RAM | Writes/reads logical `$2000/$3FFF`, with MPR1 `$F8`; stack mapped explicitly | Test allocation, not official BIOS work-RAM ownership |
| CD/Super RAM | Maps `$80/$87` and `$68/$7F`, writes/reads first/last bytes; markers `81 8F 91 9F 61 6F 71 7F` | Default pinned core reports 192 KiB card RAM; configuration is not BIOS version proof |
| JSR into slots `$01/$03/$1E` | Real candidate entry PC/A/X/Y/P/SP/MPR, SP `$FF→$FD`, slot-specific halt; continuation never executes | Unimplemented stubs only, no successful BIOS return or service semantics |
| Original JSR/RTS procedure | Entry/return PC, A/X/Y/P, SP `$FF→$FD→$FF`, stacked return address, temporary MPR2 switch/restoration | Its own authored preservation contract, not EX_GETVER/CD_RESET/CD_READ ABI |
| IRQ1 and IRQ2 | Public line assertion after guest CLI; source-specific vectors, SP `$FF→$FC→$FF`, stacked P/PC, I flag, guest marker `$77`, RTI restoration | CPU interrupt mechanism plus original handler, not BIOS IRQ dispatch or CD event behavior |

Two additional real execution negatives remove the symbol-qualified RTS or
change the symbol-qualified TAM restore mask. The first remains at a
nonreturning procedure checkpoint with SP `$FD`; the second returns with the
wrong MPR2/MPR3. Each fails the same positive procedure assertion, with observed
failure state recorded. They do not replace any unimplemented API with RTS.

## Loader acceptance is not firmware validity

Nine actual core-loader cases record acceptance and `execution=NOT_RUN`:

- Raw 262144 bytes and 512-byte-prefix plus payload: accepted.
- All-FF 262144 bytes: also accepted; this is deliberately **not firmware**.
- Empty, 128 KiB, one byte short/extra, 511-byte prefix and 512 KiB: rejected.

The project DEV validator rejects malformed content and headers separately.
A loader accepts unknown content and strips a prefix without proving its
meaning. No arbitrary accepted image is reported as a valid System Card BIOS.

## What remains blocked

Manufacturer CPU facts establish eight 8 KiB MPR windows, reset MPR7=0, vector
locations and the architectural logical stack. They do not establish each
System Card API's register/flag/error/IRQ contract. Pinned caller wrappers give
source-supported expectations only. In particular EX_GETVER major-in-X,
CD_READ work-byte modes and reported MPR error paths remain unexecuted callee
hypotheses. Historical v1/v2/v3 unique ROM capacities/decode and the final BIOS
layout remain unresolved where primary sources disagree or are absent.

#1/#3 stay open; #4 production startup and real API implementations must await
those contracts. #101 stays Draft and release-blocked. No Geargrafx patch is
required for these experiments; full MCP/CD tracing remains #6/#103 work.
