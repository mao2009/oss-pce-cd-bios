# Building the development fixture

The supported bootstrap host is **Linux x86_64**, Python 3.10 or newer, GNU Make,
GCC/G++, binutils (`ar`) and curl. Tested locally on a Linux distribution,
x86_64, GCC 13.3.0 and Python 3.12.3. CI uses `ubuntu-24.04` with the same build
scripts. No Docker, proprietary BIOS or commercial game is needed.

If prerequisites are missing, review and run this yourself (setup never runs sudo):

```sh
sudo apt-get update
sudo apt-get install build-essential python3 curl git
```

Host tests (`python3 -m unittest discover -s tests/host`) also need PyYAML (`pip install pyyaml`, or `python3-yaml` on
Debian/Ubuntu). Without it the artifact-guard checks report `PyYAML unavailable` and the YAML-based tests fail with an
explicit import message; they are not skipped. `tools/setup.py` is POSIX-only (it uses `fcntl`), so run it on Linux or WSL.
On Windows, `make` and cc65 are not present, so the cc65 and `make release` tests are NOT_RUN.

## Commands

```sh
make setup
make build
make test              # host tests only; Geargrafx NOT required
make test-real-core    # required real-core gate; needs Geargrafx (make setup)
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
not deleted: if a `<dependency>.extracting` path is left behind (a directory, file,
or symlink), setup refuses to run and changes nothing there. Before removing or
moving it, confirm its canonical path, its contents, who owns it, and what depends
on it, and make sure it can be restored; deleting or quarantining it needs an
individual approval, and nothing here removes it for you. An existing unverified
final directory is refused, never overwritten.

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
setup. `make test-real-core` requires the real Geargrafx core; absence is FAIL, never a
passing integration test. `make test` does not need Geargrafx. Desktop/MCP requirements are separate in
[Geargrafx setup](emulator-geargrafx.md).

## Separate BIOS DEV diagnostics

`make dev-rom` and `make dev-rom MODE=release` use the same pinned toolchain and
write only `build/<mode>/bios-dev/`. `make dev-test` includes real Geargrafx
System Card loader/reset/slot diagnostics. Neither mode produces compatible
firmware. `make dev-release-gate` must fail while API/ABI/boot approval is absent.
See [diagnostic contracts](api-stub-scaffold.md).

## Standalone startup and CD-read diagnostics

After `make setup`, these commands build prerequisite DEV ROMs automatically:

```sh
make dev-startup-integration  # real-core reset/MPR/stack marker
make dev-cdread-integration   # real-core guest READ(6)→RAM, synthetic CUE/BIN
make dev-trace-integration    # isolated Geargrafx trace/timeout negatives
make dev-cdread MODE=release
cmp build/debug/cdread-probe/cdread-probe-not-bios.pce build/release/cdread-probe/cdread-probe-not-bios.pce
make dev-release-gate-blocked # must refuse incomplete firmware
```

The CD diagnostic writes `build/<mode>/cdread-probe/cdread-evidence.json`
with RAM/phase/halt evidence. This is **not** BIOS `CD_READ`, nor disc boot.
The CD→RAM payload experiment (#122) is merged on main as of 2026-10-16 (`c20ac06`) as a diagnostic only.
`make test` covers host tests only; the required real-core checks are in `make test-real-core`
(CI `ci.yml` and `nightly.yml` run it as a step after `make test`). The separate BIOS DEV CI covers the DEV ROMs.
[Status](status.md).

### 実コア検証の依存関係（Issue #398）

| 実行 | 中身 | Geargrafx | CI |
| --- | --- | --- | --- |
| `make test` | ホスト試験 (`tests/host`) + `build`（cc65 必須） | 不要 | `ci.yml` / `nightly.yml` の `make test` ステップ |
| `make test-real-core` | `make integration`（`geargrafx_*.py` 7本）+ `make integration-test`（`tests/integration`） | 必須、無ければ FAIL | 同じ required ジョブ内の `make test-real-core` ステップ |
| `make routine-real-core` (opt-in, #330) | ルーチン実コア 7本: EX_BINBCD / MA_MUL8S / MA_MUL8U / MA_MUL16U / MA_DIV16U / MA_DIV16S / EX_BCDBIN | 必須、`TOOLS_DIR` 必須 | CI には含まれない（ローカル/手動） |
| `make host-test` | ホスト試験のみ、cc65 不在時は SKIP（理由付き） | 不要 | なし（ローカル用） |

`make test-real-core` は Geargrafx が無ければ `FAIL/BLOCKED` で終了する。SKIP を PASS として記録しない。

`make routine-real-core` の補足:

- Linux 専用。Linux 以外は `BLOCKED: real-core target requires Linux` を stderr に出して失敗する。
- 失敗時の終了コードは **make が返す 2**（レシピ内の `exit 1` は `make: *** ... Error 1` と表示されるだけ）。`TOOLS_DIR` 未指定（空を含む）は `ERROR: TOOLS_DIR is required ...`、スクリプト欠落は python の `can't open file` を出して、いずれも終了コード 2 で止まる。
- `make test` / `make test-real-core` / `make check` / CI には含まれない（PR #331 の opt-in 方針を維持）。
- 前提: Geargrafx セットアップ済みで `TOOLS_DIR` を指定、各ルーチンのテストスクリプトが main にマージ済みであること（未マージだと欠落で失敗する。fail-closed）。
- 実行: `TOOLS_DIR=$HOME/oss-pce-cd-bios-tools make routine-real-core`

## Instruction debugger and contract probes

`make setup` additionally builds a separate debugger-enabled Geargrafx tree
from the same verified source archive; no SDL installation or source patch is
needed. The normal HuCARD core stays separate. `make dev-test` now verifies
actual one-instruction stepping, registers/stack/RAM, guest bank switches,
IRQ1/IRQ2, loader edge cases and additional negative procedures.
[Exact conditions and API limitations](geargrafx-contract-evidence.md).
