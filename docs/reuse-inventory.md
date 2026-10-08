# External OSS reuse candidate inventory

**Purpose:** avoid reinventing reliable OSS implementations for the System Card 3.0 replacement while rigorously preserving licensing and provenance. This is a **discovery list, not approval to copy**. Each upstream snapshot was located on 2026-10-08; every entry is `PENDING` until a maintainer reviews the *exact files and complete dependencies*. Follow [reuse and provenance policy](provenance.md).

## Initial candidates

| Source / pinned snapshot | Candidate files | Use potential | License observed so far | Decision |
| --- | --- | --- | --- | --- |
| [cc65 runtime](https://github.com/cc65/cc65/tree/71746c829e77f74c2b601a7d16821170c2610df3/libsrc/runtime) · `71746c829e77f74c2b601a7d16821170c2610df3` | `umul16x16r32.s` (`cfcf82d9ea18c1e2e27c5a28f17bf52ef526f0b9`), `udiv.s` (`4115382c80c13a4fefefe77b5aae944df1c01e10`), `mulax3.s` (`3423796053e377e2dcafabf89643ff92ae39c412`) | **Potential direct ca65 assembly reuse** for integer multiply/divide helper code, but not System Card BIOS ABI by itself. `udiv.s` and others require cc65 zero-page variables, imported helpers and adapted wrapper ABI. | Upstream root [LICENSE](https://github.com/cc65/cc65/blob/71746c829e77f74c2b601a7d16821170c2610df3/LICENSE) is **zlib-style**, permits commercial use/modification with origin attribution and modified-source labeling; check precise file/dependency licensing and ROM output notices | PENDING — legal, ABI, linker and dependency audit required |
| [HuCC / HuC](https://github.com/pce-devel/huc/tree/54f5c73606c1e7142e8095c727cab2767e2b3cb3/include/hucc) · `54f5c73606c1e7142e8095c727cab2767e2b3cb3` | `hucc-math.asm` | Adapt or directly reuse HuC6280 multiply/divide routines after ABI/assembler conversion | File header: **BSL-1.0** (Boost Software License); review includes and dependencies | PENDING |
| [HuCC / HuC](https://github.com/pce-devel/huc/tree/54f5c73606c1e7142e8095c727cab2767e2b3cb3/include/hucc) · same pin | `vdc.asm`, `joypad.asm`, `core-startup.asm`, `core-kernel.asm` | Adapt VDC, input, startup, IRQ and memory mapping | Inspected file headers: **BSL-1.0**; runtime coupling and transitive sources require audit | PENDING |
| [HuCC / HuC](https://github.com/pce-devel/huc/tree/54f5c73606c1e7142e8095c727cab2767e2b3cb3/include/hucc) · same pin | `hucc-systemcard.asm`, `hucc-systemcard.h` | Calling-side ABI and edge-case tests for CD, CD-DA, ADPCM and backup RAM, possibly reusable helpers | Inspected file headers: **BSL-1.0**; these are *BIOS caller wrappers*, not System Card firmware services | PENDING |
| [Hu-Go!](https://github.com/mckayemu/hugo/tree/64ed226da2288738781743eeef228c069e8973a4) · `64ed226da2288738781743eeef228c069e8973a4` | `bios.c`, `cd.c` | HLE behavior, ABI and test-condition reference for System Card APIs | `COPYING` indicates **GPL-2.0** for default project code. No automatic inclusion in MIT-only firmware | PENDING — REFERENCE_ONLY candidate |
| [Hu-Go!](https://github.com/mckayemu/hugo/blob/64ed226da2288738781743eeef228c069e8973a4/pcecd.c) · same pin | `pcecd.c` | PC Engine CD low-level control behavior, subject to verifying HLE/hardware fidelity | `COPYING` explicitly excepts this file as **modified BSD**; inspect precise header, author, all dependencies and code boundaries | PENDING |
| [PCSX-Redux / Nugget OpenBIOS](https://github.com/pcsx-redux/nugget/tree/c950e18a168944ec2d4e6d3c408fc224317483a7/openbios) · `c950e18a168944ec2d4e6d3c408fc224317483a7` | `openbios/cdrom`, boot, file I/O and kernel code | Cross-console designs, CD states and reusable host-side tests, not machine-code transplantation | **MIT** at `nugget` repository level; audit individual files and embedded dependencies before copying. The separate `pcsx-redux` emulator repository is GPL-2.0 | PENDING |
| [NeoCD-Libretro](https://github.com/libretro/neocd_libretro/tree/b1e04c738cb48a1dae0574b8877f6a116d270ca1/src) · `b1e04c738cb48a1dae0574b8877f6a116d270ca1` | `hlebios.cpp`, `cdromtoc.cpp`, `cdromcontroller.cpp` | CD state machine, TOC rules and HLE BIOS behavior as research / test design | Root `LICENSE.md`: **LGPL-3.0**; file/third-party review still required; static ROM incorporation is **not pre-approved** | PENDING — REFERENCE_ONLY candidate |
| [Cult-of-GBA BIOS](https://github.com/Cult-of-GBA/BIOS/tree/a30e9a96df083628b650724b7d4d7112b4070b98) · `a30e9a96df083628b650724b7d4d7112b4070b98` | `bios_calls/math/`, decompression / syscalls | General API contracts and test techniques; ARM machine code is not usable as HuC6280 code | **MIT** project license inspected; verify the chosen files and dependencies before adapting | PENDING |

### New ca65-native runtime candidate: cc65

The earlier HuCC math candidate requires conversion from its PCEAS/HuCC dialect and SDCC-style ABI. The cc65 routines above already use **ca65** syntax, the assembler this repository is pinning. This makes them a more promising initial reuse candidate for integer helper algorithms, **not** an automatic plug-in replacement for a System Card call.

- Source commit: `71746c829e77f74c2b601a7d16821170c2610df3` (2026-10-04); the three file blob SHAs are pinned above. Project LICENSE blob: `88cacf1486f5b3eca553241204427913008887ab`.
- The license text requires (a) not misrepresenting original authorship, (b) marking altered source as altered, and (c) keeping the notice unaltered in source distributions. An acknowledgment is appreciated but not required. This **is not the MIT license**, and the original terms should remain separate.
- Technical fit is **PENDING**: `umul16x16r32.s` includes `zeropage.inc` and uses cc65 `ptr1`, `ptr3`, `sreg`; `udiv.s` also imports `popptr1`, `ptr4`. Importing the entire cc65 runtime to satisfy a tiny helper would defeat the purpose. Prefer a bounded audited subset only if zero-page allocation, interrupts, calling convention and clobbers are acceptable.
- Compare the specific API's documented input/output state and division-by-zero behavior before any wrapper. Never equate the cc65 primary-register return in A:X with the System Card's pseudo-register ABI.
- Do not place these routines in shipped firmware or CI artifact until a **human-reviewed APPROVED record** exists, required upstream license/attributions and exact changes are tracked, and ca65 + Geargrafx tests actually pass.
- Reuse matching synthetic math test vectors in other projects if their API contract matches; do not link 6502 code into Mega-CD's 68000 or PS1's R3000A Runtime.

### Important distinctions

- File headers, exception clauses and dependencies override any inference from a repository's license badge. For example, Hu-Go! has a specific modified-BSD exception for `pcecd.c` but this does **not** relicense `bios.c` or `cd.c`.
- `pcsx-redux/nugget/openbios` is the PS1 OpenBIOS source; do **not** confuse its MIT license with the GPL-2.0 license of the separate `grumpycoders/pcsx-redux` emulator repository.
- Upstream source/assembler dialect can differ from ca65. Do not integrate a translated routine without auditing all copied expression, dependencies, headers, and generated binary content.
- GPL/LGPL external research or test tools may be useful without embedding them in the distributed ROM; their own redistribution terms still apply.
- A directly copied third-party implementation may be technically wrong for System Card 3.0; ABI, flags, timing, bank/interrupt side effects and error paths must pass real-core tests.

## Audit template

Before copying or adapting a candidate, create one PR/Issue review record containing:

```yaml
component: "System Card API or shared subsystem"
disposition: "PENDING"  # APPROVED / PENDING / RESTRICTED
reuse_mode: "DIRECT"  # DIRECT / ADAPTED / REFERENCE_ONLY / REJECTED
source_repository: "https://github.com/owner/repo"
source_commit: "<exact 40-hex commit>"
source_paths: ["path/to/file"]
source_hashes: {}
authors_and_rightsholders: []
file_licenses_and_notices: []
external_dependencies: []
license_obligations_for_source_and_rom: []
modifications_and_conversion: []
abi_and_technical_fit: []
test_evidence: []
reviewer: null
review_date: null
related_issues: []
```

The license text, required attribution and modification information must be included in source **and any ROM distribution where required**. Undefined or pending rights block incorporation; they do not block documenting a candidate.

## Working order

1. Audit **cc65's ca65-native math routines** alongside HuCC math/VDC/joypad/startup/IRQ and prefer the smallest suitable, licensed implementation; prioritize straightforward non-proprietary self-contained modules.
2. Use HuCC System Card callers to derive independent API contract tests. Independently validate against Geargrafx and synthetic fixtures.
3. Use Hu-Go! CD HLE as a potential behavioral reference; do not copy `bios.c` into MIT-only firmware.
4. Review `pcecd.c` and other appropriately licensed source for low-level inspiration and possible reuse.
5. Apply relevant cross-console OpenBIOS/NeoCD lessons only after technical and legal fit is documented.
6. Link accepted audit entries to [API inventory](api-inventory.md), and keep an auditable list of third-party materials shipped with the firmware.

Related work: [#105](https://github.com/mao2009/oss-pce-cd-bios/issues/105), [#8](https://github.com/mao2009/oss-pce-cd-bios/issues/8), [#3](https://github.com/mao2009/oss-pce-cd-bios/issues/3).
