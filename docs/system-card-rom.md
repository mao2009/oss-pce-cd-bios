# System Card ROM research contract — Issue #1

The [machine-readable contract](../spec/rom-contract.json) separates CPU source
facts, pinned Geargrafx rules, an observed development diagnostic, and UNKNOWN
historical/final firmware properties. A file accepted by a loader is not valid
firmware. This research adds no upstream implementation or copyrighted BIOS
bytes. It does not close #1, #4 or #6 or approve a production release.

Research date: 2026-10-09 JST. Read Issues
[#1](https://github.com/mao2009/oss-pce-cd-bios/issues/1),
[#3](https://github.com/mao2009/oss-pce-cd-bios/issues/3),
[#4](https://github.com/mao2009/oss-pce-cd-bios/issues/4),
[#6](https://github.com/mao2009/oss-pce-cd-bios/issues/6), and
[#100](https://github.com/mao2009/oss-pce-cd-bios/issues/100).
Baseline is `493b2eaec0e2fc3d690f4767f3ada792135bb94a`; the existing
[diagnostic observations](api-stub-scaffold.md) are evidence already recorded
there, not new execution claimed by this host suite. Issue #3 owns API contracts;
the parent work owns further real-core observations and CPU experiments.

## CPU addressing and reset

The primary manufacturer source is Hudson Soft's *HuC6280 CMOS 8-bit
Microprocessor Hardware Manual*, sections 2.2, 2.3, 2.4 and 2.5
(printed HB-7/8, HB-14 onward, HB-18), preserved in
[MiSTer at revision 827d45f45799b8ab3a536510847f2108a92e81bd](https://github.com/MiSTer-devel/TurboGrafx16_MiSTer/blob/827d45f45799b8ab3a536510847f2108a92e81bd/docs/HuC6280%20-%20CMOS%208-bit%20Microprocessor%20Hardware%20Manual.pdf).
The publisher is Hudson, not MiSTer; repository hosting does not grant rights to
the scan. Only facts are described here. No scan or manual excerpt is shipped.

Eight 8-bit MPRs select physical 8 KiB banks for the eight logical 8 KiB
windows in the 64 KiB CPU address space. The physical space is 2 MiB:

`physical = MPR[logical >> 13] * 8192 + (logical & 8191)`.

Thus MPR0 selects `$0000-$1FFF`, MPR1 `$2000-$3FFF`, through MPR7
`$E000-$FFFF`. This yields a bus address; a mapper may translate it to another
ROM offset or RAM/IO. Never equate physical address with file offset globally.
Reset sets MPR7 to `$00`, then fetches low/high PC bytes at logical `$FFFE/$FFFF`,
physical `$001FFE/$001FFF`. MPR0–6 are unspecified by reset. Their randomized
values in Geargrafx are a core choice, not hardware random-number semantics.
Initialize each required register before using its window.

| Vector | Logical low-byte address | File offset with bank0 in MPR7 |
| --- | --- | --- |
| IRQ2 / BRK | `$FFF6` | `$1FF6` |
| IRQ1 | `$FFF8` | `$1FF8` |
| Timer | `$FFFA` | `$1FFA` |
| NMI | `$FFFC` | `$1FFC` |
| Reset | `$FFFE` | `$1FFE` |

Each vector is a two-byte little-endian logical destination. After remapping
MPR7, interrupt vector reads use that current window; interrupt entry does not
make all addresses bank0. Vector addresses do not specify BIOS handler ABI or
console NMI wiring. The pinned [HuC CPU notes](https://github.com/pce-devel/huc/blob/54f5c73606c1e7142e8095c727cab2767e2b3cb3/doc/pce/cpu.txt)
corroborate geometry/vector names but are community SDK notes, not manufacturer
proof. SDK startup conventions are not hardware reset defaults.

## Exact pinned Geargrafx loader and mapping

Official repository: `https://github.com/drhelius/Geargrafx`, exact revision
`b49ae82a012eade566d72e9d61c9d95a5caa4da3`.
[Media loader](https://github.com/drhelius/Geargrafx/blob/b49ae82a012eade566d72e9d61c9d95a5caa4da3/src/media.cpp)
`LoadBiosData`, `GatherBIOSInfoFromDB`, `InitRomMAP`, together with
[size constants](https://github.com/drhelius/Geargrafx/blob/b49ae82a012eade566d72e9d61c9d95a5caa4da3/src/defines.h), establish these source facts:

- `syscard=true` expects `0x40000` (262144) payload bytes. Exactly 262656
  input bytes causes a 512-byte prefix discard. No signature/content check of
  that prefix occurs. Exactly 262144 bytes is taken unchanged. Other sizes,
  null buffers and nonpositive sizes fail. This describes ordinary BIOS file/
  buffer loading, not ZIP/HuCARD/MCP front-end acceptance conditions.
- Loading first unloads the previous BIOS, including on failure. Payload CRC
  is computed and database classification performed. A missing CRC match sets
  the recognition flag false but does not prevent successful loaded state.
  Known-BIOS identification is distinct from loader acceptance and execution.
- On the ordinary CD System Card path there are 32 file banks. Default ROM
  mapping selects file bank `physical_bank % 32` for banks `$00-$7F`.
  Logical `$E000` with MPR7 `$00` selects offset zero; MPR7 `$01` selects
  offset `$2000`; bank `$20` mirrors file bank zero unless another mapped
  device supersedes it. This is pinned core behavior, not retail bank decoding.

The project diagnostic is explicitly headerless. Its validator may reject a
512-byte-prefixed image even though the external loader strips it; those are
separate contracts. Loader edge behavior above was inspected in official source;
this work does not label a host formula as an actual core loader experiment.
The parent's integration harness owns execution-based edge checks.

## Peripheral RAM and IO are outside the ROM image

Pinned [memory setup](https://github.com/drhelius/Geargrafx/blob/b49ae82a012eade566d72e9d61c9d95a5caa4da3/src/memory.cpp)
and [read/write routing](https://github.com/drhelius/Geargrafx/blob/b49ae82a012eade566d72e9d61c9d95a5caa4da3/src/memory_inline.h)
are the evidence for the following Gear-specific map. Firmware does not supply
these RAM bytes simply by making its ROM larger.

| Core region | Physical banks / addresses | Condition and limit |
| --- | --- | --- |
| CD RAM, 64 KiB | `$80-$87`, `$100000-$10FFFF` | CD hardware enabled |
| Super CD expansion, 192 KiB | `$68-$7F`, `$0D0000-$0FFFFF` | Media/configuration selects `0x30000` card RAM; overrides ROM |
| PCE work RAM, 8 KiB | `$F8-$FB`, `$1F0000-$1F7FFF` | Four mirrors; SuperGrafx instead has distinct 32 KiB |
| Backup RAM window | `$F7`, `$1EE000` | Access gate; core reserves 8 KiB window and initializes 2 KiB; not a BIOS workspace claim |
| Hardware IO | `$FF`, `$1FE000-$1FFFFF` | Logical address depends on the chosen MPR |
| CD dispatch window | `$FF:$1800-$1BFF`, `$1FF800-$1FFBFF` | CD enabled; mapper may intercept offsets >= `$1A00` |

The CD dispatch window is not a claim that every address is an implemented CD
register. `$1800` means logical `$1800` only with MPR0 `$FF`; mapping another
window to `$FF` exposes IO there instead. Source `GatherMediaInfo`/media setup
selects CD type/expansion; loading an arbitrary System Card blob alone does not
establish standard/Super/Arcade hardware capabilities. CD controller transactions,
ADPCM and BIOS scratch RAM remain outside this ROM-format contract.

## Observed diagnostic versus historical System Cards

The development image has one linked 8 KiB bank plus 31 `$FF`-padded banks,
262144 bytes total. Its reset vector is at file `$1FFE`, targets CPU `$F000`,
and the existing pinned-core probe observes `$F003`, X `$FF`, MPR7 `$00`
after bounded frame execution. The default libretro Makefile defines
`GG_DISABLE_DISASSEMBLER`: its `step_debugger=true` request does not prove
single-instruction stepping. Prior documentation calling this real stepping
was inaccurate. The stable halt checkpoints remain observed; a separate
debugger-enabled build and one-instruction PC increment proof are owned by
the parent and are not claimed here.
The 81 candidate three-byte entries start at CPU `$E000`, last entry `$E0F0`,
exclusive end `$E0F3`. Synthetic JMP variants reach slots `$00/$48/$50` and
stop with distinct diagnostic X markers. These are project choices and observed
execution boundaries. Padding, reset target, vector handler destinations and
API table placement are not mandated historical BIOS internals. No JSR/RTS ABI,
CD boot or retail title behavior follows from them.

| Historical target | Unique ROM capacity / physical decode / version layout |
| --- | --- |
| System Card 1.x | UNKNOWN |
| System Card 2.x (including region/revision differences) | UNKNOWN |
| System Card 3.x / integrated Duo firmware | UNKNOWN |

The [HuC Memory Map wiki](https://github.com/pce-devel/huc/wiki/Memory-Map),
accessed 2026-10-09, reports v1/v2 ROM as 256 KiB mapped at `$00-$1F` and v3
as 512 KiB mapped at `$00-$3F`. This is an unpinned community statement about
mapped extents, not verified unique storage capacity. It cannot resolve the
relationship to Geargrafx's 256 KiB payload. Neither the core size condition nor
its database labels establish historical v1/v2 capacities. Distinguish chip
capacity, unique payload length, physical decoding/mirroring and dump headers.
No historical capacity hypothesis is enforced by tests. Manufacturer card
schematics/specifications or independently reproducible hardware observations
are still needed; no proprietary dumps/disassemblies were consulted.

## Provenance, validation and remaining work

Every machine claim carries scope, status, references and a limit. Source records
include exact SHA/path/URL and SHA-256 for inspected local files where available.
`SOURCE_FACT` means the referenced source says/implements it; `OBSERVED` applies
only to the existing diagnostic evidence; neither means universal compatibility.
UNKNOWN has a null value and an explicit explanation. Final capacity, metadata,
checksums, region, caller bank preconditions, interrupt/startup ABI, CD handoff
and compatibility remain UNKNOWN.

Geargrafx files carry Ignacio Sanchez's 2024 copyright and GPL-3.0-or-later
notices ([LICENSE](https://github.com/drhelius/Geargrafx/blob/b49ae82a012eade566d72e9d61c9d95a5caa4da3/LICENSE)).
The HuC [root LICENSE](https://github.com/pce-devel/huc/blob/54f5c73606c1e7142e8095c727cab2767e2b3cb3/LICENSE)
explicitly records mixed and uncertain legacy rights; its BSD terms cover
Ulrich Hecht's changes, not the entire SDK. The inspected
[`include/hucc/pcengine.inc`](https://github.com/pce-devel/huc/blob/54f5c73606c1e7142e8095c727cab2767e2b3cb3/include/hucc/pcengine.inc)
has a Boost Software License 1.0 notice and candidate bank-qualified interfaces;
these remain API research inputs, not copied definitions. All external reuse is
REFERENCE_ONLY/PENDING. No adoption request or implementation import is made.

Run `python3 -m unittest discover -s tests/host -p test_rom_contract.py -v`.
The host checks reject dangling/unpinned references, contradictory geometry,
vector offsets, RAM extents, loader lengths and misplaced UNKNOWN assertions.
Mutation tests exercise those failures. They do not emulate a CPU or certify
firmware. Existing real-core tests remain independent and unchanged. Remaining
blockers are historical evidence, production bank/ABI/boot validation, CD
transactions and independent emulator/hardware corroboration.
