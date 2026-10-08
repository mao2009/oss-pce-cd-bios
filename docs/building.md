# Building the development fixture

The supported bootstrap host is **Linux x86_64**, Python 3.10 or newer, GNU Make,
GCC/G++, binutils (`ar`) and curl. Tested locally on Zorin OS 18.1 / Ubuntu 24.04,
x86_64, GCC 13.3.0 and Python 3.12.3. CI uses `ubuntu-24.04` with the same build
scripts. No Docker, proprietary BIOS or commercial game is needed.

If prerequisites are missing, review and run this yourself (setup never runs sudo):

```sh
sudo apt-get update
sudo apt-get install build-essential python3 curl git
```

## Commands

```sh
make setup
make build
make test
make check
make clean
make build
make build MODE=release
cmp build/debug/smoke-test-not-bios.pce build/release/smoke-test-not-bios.pce
```

Setup downloads SHA-256-verified archives from the exact revisions in
[`tools/dependencies.json`](../tools/dependencies.json), builds cc65 tools and the
Geargrafx libretro core from source, and installs the pinned actionlint binary
into its external cache. Upstream licenses stay in each extracted tree.
Archive symlinks (Geargrafx agent metadata only at this pin) are omitted; they
are unnecessary for these build targets. Setup serializes concurrent invocations,
reuses verified downloads and sources, and safely repeats incremental builds.
Failed checksum or build checks exit nonzero. Interrupted extraction staging is
recreated; an existing unverified final directory is refused, never overwritten.

Reusing an extracted tree also hashes every original archive file and verifies
its size/executable permission, rejects missing/changed files, extra source files and symlinks, and
compares `.source-integrity.json` against the checksum-verified archive inventory.
The archive is the authority even if both a source file and its saved manifest
were edited. Old caches acquire this inventory only after full source validation.
Known compiler objects/dependency files and target binary locations are separate
generated outputs; their normal incremental updates do not invalidate source
integrity. This does not attest arbitrary generated binaries or a compromised
host compiler. Source checks read a few MiB compressed archive plus original
files; they do not remove build objects or force compilation. The setup lock
covers verification, atomic publication of complete trees and incremental builds.

The default external cache is `/tmp/oss-pce-cd-bios-tools-<uid>`. It may be cleared
by the OS. Set `TOOLS_DIR` to a writable absolute directory **outside this
repository** for persistent storage; use the same value for every command:

```sh
export TOOLS_DIR=/tmp/my-pce-development-tools
export JOBS=2
make setup
```

Only the cache and ignored `build/` are written. No system install or environment
specific include path is required. `JOBS` is 1–64; default 2 limits memory use.
`CA65`, `LD65`, `CC65`, `ACTIONLINT` and `GEARGRAFX_CORE` allow explicit paths for
diagnostics. Firmware tools and the emulator must report the pinned revision;
missing or mismatched tools fail. A version string is not a cryptographic audit
of a locally modified override, so use the verified setup for CI/evidence.

## Outputs and build modes

`make build` writes `build/debug/`; `MODE=release` writes `build/release/`.
Both generate a **headerless 8192-byte HuCARD test fixture** named
`smoke-test-not-bios.pce`, object, linker map, labels and JSON manifest.
Debug adds ca65 debug information and an assembly listing. Release omits those;
the fixture's executed code and ROM bytes are identical. There is no optimizer
or BIOS behavior distinction to imply in this assembly-only target.

The manifest records source HEAD, dirty status, mode, fixed tool revision, format,
size and ROM hash. Integration writes separate `geargrafx-evidence.json` with
actual runtime observations. No timestamp, checkout path or host name enters the
ROM. Maps/listings and host binaries may contain local paths or compiler-specific
information and are not claimed byte-identical across hosts. ROM reproducibility
is checked in fresh output directories and across build modes.

`make clean` removes only generated `build/`; external tools are retained.
`make release` deliberately exits nonzero: System Card APIs, boot, CD behavior and
release gates are not ready. No tag or GitHub Release is generated.

## Failure diagnosis

Read the first nonzero tool error; `make build` never silently runs setup or
falls back to an unrelated PATH assembler. Download DNS/network failures require
network access to GitHub/codeload; checksum mismatches require inspecting/removing
the corrupt archive before retry. `make check` requires actionlint installed by
setup. `make test` requires the real Geargrafx core; absence is BLOCKED/FAIL, not
a passing integration test. Desktop/MCP requirements are separate in
[Geargrafx setup](emulator-geargrafx.md).

## Separate BIOS DEV diagnostics

`make dev-rom` and `make dev-rom MODE=release` use the same pinned toolchain and
write only `build/<mode>/bios-dev/`. `make dev-test` includes real Geargrafx
System Card loader/reset/slot diagnostics. Neither mode produces compatible
firmware. `make dev-release-gate` must fail while API/ABI/boot approval is absent.
See [diagnostic contracts](api-stub-scaffold.md).
