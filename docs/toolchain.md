# Toolchain selection (2026-10-08)

Adopt **ca65 + ld65**, with the cc65 C frontend available for compile probes,
from cc65 commit `71746c829e77f74c2b601a7d16821170c2610df3` (2026-10-04).
This matches the direction in #100 / Draft PR #101, without importing that PR's
API stubs or assuming its proposed BIOS layout is validated.

## Comparison

The table describes inspected official source revisions, not historical blog
claims. “Not run” means no demonstrated build result.

| Criterion | HuC/HuCC + PCEAS | cc65 / ca65 / ld65 | WLA-DX | ARM9 bass |
| --- | --- | --- | --- | --- |
| HuC6280 instructions | PCEAS target in `src/mkit/as` | Explicit ca65 HuC6280 mode; syntax probes pass, TAM/TMA/TII execute in Geargrafx | `wla-huc6280`, opcode tables and target in CMake | `pce.cpu.arch` table |
| C support | Small-C-derived HuC and current HuCC; documented language/runtime limitations | Current cc65 accepts `--cpu huc6280`; compiled original function assembles | No C compiler in this assembler/linker package | No C frontend |
| Assembly interoperation | C generates PCEAS assembly; HuCC modular library | Relocatable objects, exports/imports, linker segments; C ABI/runtime must be designed separately | WLA object/linker sections; external C ABI integration required | Direct table-based assembly; no adoption-ready cc65 object/runtime pipeline |
| BIOS layout/banking | PC Engine `.bank`/`.org` facilities; default program/runtime assumptions need review | Explicit MEMORY/SEGMENTS and fixed addresses; bank symbols/link layout can grow after #1; MPR is guest code | ROM bank/slot maps and sections; MPR is guest code | Origin/base/output controls; bank discipline must be encoded explicitly |
| Local Linux x86_64 build | PCEAS and HuCC source builds PASS | ca65, ld65, cc65 source builds PASS; fixture linked/executed | NOT_RUN: recommended CMake missing locally | `make` FAIL with GCC 13: missing `std::runtime_error` declaration in nall header; no patch imported |
| GitHub Actions | Upstream source/build automation exists; not adopted/tested here | Same pinned source build and tests used by our CI | CMake would work on a provisioned runner; not verified here | Upstream workflow exists; this revision needs build investigation |
| Reproducibility | Could pin source; not proven for a BIOS target | Full SHA + archive SHA-256; explicit build ID; two clean fixture builds and debug/release match | Could pin source; no local result | Could pin source; no successful local result |
| License | Upstream LICENSE explicitly documents unclear historical provenance; BSD applies to specified additions, assembler has older freeware notice | Upstream LICENSE is zlib-style permission/restrictions; no runtime code is linked into this fixture | GPL-2.0-or-later tool license; source/runtime incorporation would require separate review | `bass.cpp` states ISC; broader bundled-code review not completed |
| Maintenance at inspected revision | `54f5c736...`, 2026-09-09; README describes HuCC as current successor | `71746c829...`, 2026-10-04; active source | `7ae11df1...`, 2026-10-07; active source | `c3962ec0...`, 2022-05-12; this fork's inspected HEAD is older |

HuCC is a viable PC Engine C tool, but its explicit historical license caveats
and default runtime assumptions add uncertainty for this independent BIOS.
WLA-DX is a viable assembly alternative, but switching syntax/object format would
diverge from #100/#101 with no established benefit. bass's observed build failure
and incomplete license review make it unsuitable for this bootstrap.

ca65's current source contradicts third-party claims that it cannot assemble
HuC6280. We verified the claim by assembling HuC6280-specific instruction families
and executing the test's bank/transfer instructions. This is not full opcode
conformance or a verified production BIOS ABI. ld65 gives precise placement and
overflow diagnostics without adopting a game SDK startup or emulator HLE.

## Actual build evidence

On the local GCC 13 host:

```sh
# In separately fetched official source trees (never copied into this repo):
make -C <cc65-source> -j2 ca65 ld65 cc65
make -C <huc-source>/src/mkit -j2
make -C <huc-source>/src/hucc -j2
make -C <bass-source>/bass -j2
```

The first three returned 0; bass returned 2. HuCC/PCEAS binaries were built for
comparison only: no SDK runtime, sample ROM, proprietary content or alternative
assembler is incorporated into our fixture. WLA-DX was inspected, not built.
The adopted path is `make setup`, with explicit build ID `Git 71746c829`, then
`make build`, `make test`, `make check`. The 8192-byte ROM hash is
`8d659d6ffe040a10cde4fc53ae833f7705fa2526d254978783598def6072f64c`.

The cc65 C probe proves frontend-to-assembler interoperability only. A BIOS C
runtime, zero-page allocation, stack convention, banking, interrupt ABI and
cross-bank calls remain undecided; no stock game runtime is linked.

## Sources and fixed dependencies

- [cc65 ca65 documentation at inspected SHA](https://github.com/cc65/cc65/blob/71746c829e77f74c2b601a7d16821170c2610df3/doc/ca65.sgml), [C CPU selection](https://github.com/cc65/cc65/blob/71746c829e77f74c2b601a7d16821170c2610df3/src/cc65/main.c), [ld65 configuration](https://github.com/cc65/cc65/blob/71746c829e77f74c2b601a7d16821170c2610df3/doc/ld65.sgml), [LICENSE](https://github.com/cc65/cc65/blob/71746c829e77f74c2b601a7d16821170c2610df3/LICENSE).
- [HuC/HuCC README](https://github.com/pce-devel/huc/blob/54f5c73606c1e7142e8095c727cab2767e2b3cb3/README.md), [LICENSE](https://github.com/pce-devel/huc/blob/54f5c73606c1e7142e8095c727cab2767e2b3cb3/LICENSE), [assembler source](https://github.com/pce-devel/huc/tree/54f5c73606c1e7142e8095c727cab2767e2b3cb3/src/mkit/as).
- [WLA-DX CMake targets](https://github.com/vhelin/wla-dx/blob/7ae11df1657dafab1f5fa662889d005709fec15e/CMakeLists.txt), [build instructions](https://github.com/vhelin/wla-dx/blob/7ae11df1657dafab1f5fa662889d005709fec15e/INSTALL.md), [LICENSE](https://github.com/vhelin/wla-dx/blob/7ae11df1657dafab1f5fa662889d005709fec15e/LICENSE).
- [bass opcode table](https://github.com/ARM9/bass/blob/c3962ec01f4768be1667db0d09c12141c28241f3/bass/data/architectures/pce.cpu.arch), [build instructions](https://github.com/ARM9/bass/blob/c3962ec01f4768be1667db0d09c12141c28241f3/README.md), [source license marker](https://github.com/ARM9/bass/blob/c3962ec01f4768be1667db0d09c12141c28241f3/bass/bass.cpp).

[`tools/dependencies.json`](../tools/dependencies.json) records exact official
sources, revisions, archive checksums and licenses for cc65, Geargrafx and
actionlint. Geargrafx is GPL-3.0 and external verification software; actionlint
1.7.7 is MIT, distributed as its official Linux amd64 release. See
[Geargrafx](emulator-geargrafx.md) for source build and capability research.
Host GCC/Make/Python and the Ubuntu runner image are prerequisites, not hermetic
binary pins. Reproducible ROM output is tested; hermetic host-tool builds and
compiler-independent emulator binaries are not claimed.
