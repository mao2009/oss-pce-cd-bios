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
make dev-startup MODE=release
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
`$F000`. The test does not enable interrupts or call any BIOS service.

All addresses except the CPU's architectural vector/stack geometry are
**diagnostic choices**, not claimed NEC/Hudson System Card allocation or
caller/callee preservation guarantees. In particular this is not an
initialized CD subsystem, display, ADPCM, filesystem, or runnable game BIOS.

## Verification and limits

The builder assembles with the pinned ca65/ld65 and compares the linked ROM
byte-for-byte against the intended test-only reset and marker instructions.
It also enforces unchanged candidate API table/fallback and unused ROM banks,
checks label addresses and all five vectors, and records the ROM SHA-256.
Host tests mutate table, reset, MPR, marker, vectors, padding and length and
require rejection.

The integration runner invokes the actual Geargrafx debugger-enabled C++
frontend that Issue #6 / PR #101 established. It observes real PC, P, SP,
X, all MPRs, and RAM on cold/warm reset plus named instruction checkpoints.
A symbol-qualified redirection of the second marker store to `$2200` is deliberately executed in the
real emulator, overwriting `B` with `O`, and must fail the same startup-marker assertion. No Python CPU
simulation substitutes for emulator observations.

The pinned Geargrafx source SHA and source archive SHA-256 are recorded; the
runtime JSON includes the actual ROM hashes, observed states and failure
evidence. The GPL-linked local probe executable is **not distributed** or
included in Actions artifacts. Only original ROM, manifests, maps, labels and
JSON are uploaded.

### Still unverified

- Historical and production System Card physical ROM layout and startup RAM
- Final interrupt dispatch, stack conventions, and BIOS API service ABI
- CD register reset, disc protocol, CD boot, actual game compatibility
- Independent emulator or real hardware compatibility

As a result, #1/#3 remain open, #101 remains Draft, and `make release` /
`make dev-release-gate` must continue rejecting production output.

Sources and provenance are tracked in
[system-card-rom.md](system-card-rom.md),
[geargrafx-contract-evidence.md](geargrafx-contract-evidence.md), and
[abi-contracts.md](abi-contracts.md).
