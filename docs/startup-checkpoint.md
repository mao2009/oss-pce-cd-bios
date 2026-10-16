# Issue #4: Independent System Card reset/startup checkpoint (development-only)

This is an **original, isolated diagnostic startup path**, not a production
BIOS, commercial CD-game boot sequence, approved System Card ABI, or firmware
release. It runs in the real pinned Geargrafx core via the System Card loader
with an independently generated synthetic 32-sector CUE/BIN disc.

## Commands

```sh
make setup
make dev-rom
make dev-startup
python3 -m unittest discover -s tests/host -p 'test_startup.py' -v
make dev-startup-integration
make dev-release-gate-blocked   # exit 3 + BLOCKED only (see api-stub-scaffold.md)
make dev-rom MODE=release
make dev-startup MODE=release
cmp build/debug/bios-dev/dev-only-not-compatible-syscard3.pce build/release/bios-dev/dev-only-not-compatible-syscard3.pce
cmp build/debug/startup-probe/startup-probe-not-bios.pce build/release/startup-probe/startup-probe-not-bios.pce
```

The primary #101 `dev-only-not-compatible-syscard3.pce` is **not modified**.
This builds `build/<mode>/startup-probe/startup-probe-not-bios.pce` as a
separate, headerless 262144-byte diagnostic input, and captures
`startup-evidence.json`. The CPU executes source from `src/startup_probe.s`,
which is **not** incorporated into the default BIOS development image.

## Explicit guest assumptions for this one experiment

The manufacturer HuC6280 reset contract establishes MPR7=0 and vectors at
`$FFF6..$FFFF` while the other MPR reset values and SP are unspecified.
This experiment sets the necessary mapping explicitly:

| Register | Test-selected physical bank | CPU window |
| --- | --- | --- |
| MPR0 | `$FF` | IO (not read by this routine) |
| MPR1 | `$F8` | working RAM / stack `$2000..$3FFF` |
| MPR2/MPR3/MPR4 | `$01/$02/$1F` | diagnostic ROM banks |
| MPR5/MPR6/MPR7 | `$03/$04/$00` | diagnostic ROM banks; vectors remain in bank0 |

The code executes `SEI/CLD/CSH`, initializes these MPRs and `SP=$FF`,
writes original `BOOT!` bytes to guest `$2200..$2204`, then stops in an
explicit `BRA` checkpoint with `X=$B0`. Four interrupt vectors lead to a
**different nonreturning error checkpoint** with `X=$EE`; Reset points to
`$F000`. The startup code does not enable interrupts or call any BIOS service
(see "Interrupt vector execution" below for the separate IRQ runs).

The stage also stores the non-zero sentinel `$01` at `$2205` (`startup_cd_state`) after the
`BOOT!` marker. The value is a diagnostic marker that the store executed, **not** an encoding of
CD state. It is non-zero because Geargrafx power-on RAM reads `$00`. The code bytes contain no access
to the CD-ROM2 registers (`$1800-$1804`), so the byte does not depend on disc presence by construction.
This is not observed on the real core: disc attached is `NOT RUN` and no media is `NOT_TESTED`.
`manifest.json` records `cd_disc_presence_read: false` as a build constant, not a measurement.

All addresses except the CPU's architectural vector/stack geometry are
**diagnostic choices**, not claimed NEC/Hudson System Card allocation or
caller/callee preservation guarantees. In particular this is not an
initialized CD subsystem, display, ADPCM, filesystem, or runnable game BIOS.

## Verification and limits

The builder assembles with the pinned ca65/ld65 and compares the linked ROM
byte-for-byte against the intended test-only reset and marker instructions.
It also enforces unchanged candidate API table/fallback and unused ROM banks,
checks label addresses and all five vectors, and records the ROM SHA-256.
Host tests flip bit 0 of every byte in `$F000-$FFFF` (startup code,
padding, vectors), apply targeted semantic corruptions (SEI removed, MPR7/stack/`TXS`/marker
changed, bad reset/IRQ vectors, an added `RTS`, dirtied unused banks, API table/fallback edits),
and reject a broken base ROM or wrong sizes.

The integration runner invokes the actual Geargrafx debugger-enabled C++
frontend that Issue #6 / PR #101 established. It observes real PC, P, SP,
X, all MPRs, and RAM on cold/warm reset plus named instruction checkpoints.
Mutated ROMs are deliberately executed in the real emulator and must each fail the same
evidence check: a symbol-qualified redirection of the second marker store to `$2200`
(overwriting `B` with `O`), a wrong MPR5 value, a wrong stack-pointer value, a CD-state value equal
to power-on RAM (`$00`), a removed CD-state store (`STA $2205` → NOPs), and a redirected CD-state
store (`STA $2206`). The last three matter because Geargrafx power-on RAM reads `$00`: without a
non-zero record, a missing store would be indistinguishable from a correct one (verified in WSL
before this change: the removed and redirected store ROMs passed the old check). No Python CPU
simulation substitutes for emulator observations.

The pinned Geargrafx source SHA and source archive SHA-256 are recorded; the
runtime JSON includes the actual ROM hashes, observed states and failure
evidence. The GPL-linked local probe executable is **not distributed** or
included in Actions artifacts. Only original ROM, manifests, maps, labels and
JSON are uploaded.

### Interrupt vector execution (IRQ1/IRQ2)

`interrupt_vector_execution` in the evidence JSON holds real-core results per source:

- **Masked:** the unmodified ROM runs to `startup_checkpoint`, where the harness asserts IRQ1 or
  IRQ2. With `I` set the line stays pending (IRR `$02`/`$01`), PC/X/SP/MPR/RAM still pass the
  startup check, and the `X=$EE` handler is never reached, on cold and warm reset.
- **Dispatched:** a test-only variant replaces `LDX #$B0` with `CLI; NOP` (the fixture verifier
  rejects it). The asserted line is taken through the startup ROM's own `$FFF8`/`$FFF6` vector:
  PC reaches `startup_unexpected_interrupt` with IRR showing the requested source, SP `$FF→$FC`,
  `I` set, stacked P with `I` clear and stacked PC `startup_checkpoint`; it stops with `X=$EE`.
  The normal startup check must reject this run; the rejection message is recorded.

The variant exists only to execute the vectors. It is not the startup ROM and enables nothing in it.

### Still unverified

- Historical and production System Card physical ROM layout and startup RAM
- Final interrupt dispatch, stack conventions, and BIOS API service ABI
- CD register reset, disc protocol, CD boot, actual game compatibility
- Independent emulator or real hardware compatibility
- **Timer and NMI vector execution: `NOT_TESTED`.** The startup code never programs the timer, and
  the pinned Geargrafx HuC6280 has no NMI input (no `$FFFC` dispatch), so neither is raised.
- Unused-RAM contents at power-on are not assumed; the marker negatives are chosen to be
  independent of them.

As a result, #1/#3 remain open and `make release` / `make dev-release-gate` must
continue rejecting production output. PR #101 is merged into `main` (`6b7924f`); this
PR is based on `main`, keeps #101's `make dev-release-gate-blocked` (exit 3 + `BLOCKED:`)
and da65-based override verification, and does not change the 81-slot API table.

Sources and provenance are tracked in
[system-card-rom.md](system-card-rom.md),
[geargrafx-contract-evidence.md](geargrafx-contract-evidence.md), and
[abi-contracts.md](abi-contracts.md).
