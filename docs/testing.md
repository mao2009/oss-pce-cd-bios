# Tests and limits of evidence

Run `make setup`, then `make test` and `make check`. These exit nonzero on a
missing tool, invalid setting, failed assertion, invalid assembler input,
timeout, or core crash. Expected negative failures are assertions that the
invalid input was rejected, never evidence that the invalid ROM works.

## Host tests

`python3 -m unittest discover -s tests/host -v` runs the current host
suite. **Do not use a fixed count here**: new tests merge independently; the
`unittest` summary and exact CI commit identify the true current count.

- Two independent debug builds and a release build yield identical ROM bytes.
- Headerless size, reset code, checkpoint loop, marker, vector words and all
  unused padding match the **fixture contract**. Truncation, fake 8 KiB data,
  512-byte headers, mutated vectors/code/markers/padding are rejected.
- Real ld65 rejects overlapping segments and bank overflow. Real ca65 rejects
  invalid syntax and accepts the HuC6280-specific opcode probe.
- Tools/core are detected, missing tool CLI execution fails, invalid mode fails
  before output, unpinned assembler and in-repository tool source paths fail.
- cc65 compiles an original C function for HuC6280 and ca65 accepts the result.
  This is compile/assemble interoperability only; no C runtime linkage or guest
  execution is claimed.
- `make release` refuses an incomplete BIOS release.
- Nightly first/changed/identical/legacy/other-branch decisions and success-only
  baseline lookup are tested with host fixtures. API mocks test the planner,
  never stand in for emulator execution or a live scheduled Actions run.
- Cache integrity tests cover normal incremental output reuse, same-size source
  edits, truncation/deletion, unexpected source/symlinks, corrupt/forged manifests,
  legacy migration, interrupted extraction/retry, archive checksum failures and
  actual exclusive-lock blocking.
- Nightly recovery tests exercise expiry/absence, failed/in-progress/other-branch
  workflows, download/missing-file/JSON failures, full state validation, owning
  commit matching, paginated multiple successes, manual/scheduled parity and API
  retry/exhaustion. API errors produce no skip output and fail as indeterminate.
- Actual temporary Git repositories prove clean committed PR diffs are checked
  from their merge base, push ranges include earlier commits, staged/unstaged
  whitespace is rejected, valid changes pass, and missing comparisons or shallow
  history fail explicitly.
- Independent ROM-structure tests reject invalid format/size/bank/window/placement
  contracts. Strict fixture tests still reject changed code/data/vectors/padding.
  Symbol-driven TII mutation tests reject missing/conflicting/out-of-range labels
  and wrong opcodes/operands, and follow a deliberately relocated instruction.

`make check` parses Python and Bash, runs checksum-pinned actionlint 1.7.7 against
all local workflow YAML/schema/expressions/job references, runs the assembler
opcode probe, detects the core and checks Git whitespace. Optional shellcheck and
pyflakes integrations are disabled to keep the stated host prerequisites exact;
we do not claim those linters were run.

Git checks run on both committed and uncommitted changes. CI uses full checkout
history (`fetch-depth: 0`); PR events supply base SHA for merge-base-to-HEAD checks,
push events supply before/after SHA for the pushed range. Local checks prefer
origin/main's merge base, fall back to HEAD's parent or a true root commit, and
also check staged/unstaged changes. Missing event metadata, unavailable refs,
disconnected PR history, zero/missing push bases or shallow checkouts fail instead
of silently reducing the scope. New untracked files are checked once staged or
committed; these checks are whitespace validation, not semantic review.

## Real Geargrafx integration

`make integration` runs `tests/integration/geargrafx_smoke.py` against the official
source-built Geargrafx libretro core, with a minimal original Python/ctypes
frontend. It provides video/audio/input callbacks without a GUI, uses a fresh
temporary system/save directory per process, and never substitutes CPU behavior.
Each worker has a 30-second process timeout; normal completion calls unload and
deinit. Crashes, missing core, version mismatch and timeouts fail.

Two independent cold starts each execute two frames, then repeat after warm
reset. Before each run the frontend seeds the six observed RAM bytes with `$CC`;
the guest must replace them with `50 43 45 21 F8 5A` at working-RAM offset `$0200`
(fixture CPU address `$2200`). The negative runner reads `smoke.lbl` (or
`--labels`), resolves exported `marker_transfer` and `marker`, and asserts the
TII opcode and all operands before replacing that instruction with NOPs. A
mutated ROM with TII replaced by NOPs must fail
at the guest marker assertion. Thus a loader acceptance or constant host response
cannot satisfy this check.

Evidence records ROM/core SHA-256, reported/pinned Geargrafx commit, actual RAM
observations, frames/reset and explicit untested fields. Only normalized RAM and
version observations are compared; arbitrary power-on RAM is not deterministic.
`make clean` removes evidence, so run `make integration` again if needed.

## DEV ROM verification layers

Since merged [PR #174](https://github.com/mao2009/oss-pce-cd-bios/pull/174), the
256 KiB development ROM has **81 candidate slots: one original callable OSS
`EX_GETVER` override at slot `$1E` (candidate entry `$E05A`) and 80 nonreturning
stubs**. This is development-only functionality, not a verified reference BIOS
ABI or release-eligible firmware. The on-disk
`build/debug/bios-dev/dev-only-not-compatible-syscard3.pce` image is checked in
three distinct layers; only the last is a byte-identity acceptance gate:

| Function (`tools/build_rom.py`) | Checks | Blind to |
| --- | --- | --- |
| `verify_dev_rom_layout` | 81-slot table, remaining stub markers, vectors, padding | every override byte |
| `verify_dev_rom_structure` | the above plus ld65/da65 instruction boundaries, target ranges and fall-through | same-length substitutions (e.g. `LDX #3`→`LDX #4`, `LDY #0`→`LDY #1`, `RTS`→`RTI`) |
| `verify_dev_rom` | layout plus **byte identity against an independent rebuild** of the trusted source tree | nothing in the ROM image |

`verify_dev_rom` never hashes the input and compares it to itself, and it never trusts a
`manifest.json` shipped beside the ROM as proof of authenticity: the expected bytes come from a
fresh `build()` of `src/` into a temporary directory. Debug and release builds emit identical
ROM bytes, so the rebuild is a pure function of the trusted sources. When a `manifest` path is
supplied, only its `rom_sha256` is cross-checked against that rebuild; a tampered ROM and a
tampered manifest are both rejected. ld65 debug info is structural evidence only, never a
byte-authenticity proof. A ROM produced by `build()` in-process is already trusted; every ROM
read back from disk goes through `verify_dev_rom` before any code assumes a trusted DEV ROM.

## HuC6280 コア検証ハーネス

Linux 上のクリーンクローンで、リポジトリルートから `TOOLS_DIR` を設定して次の順に実行する。

1. `make dev-rom MODE=debug`（DEV ROM を先にビルドする。省略するとハーネスは失敗または BLOCKED）
2. `python3 tests/integration/geargrafx_core_harness.py --mode debug [--cases cases.json]`

共有 Makefile ターゲットは PR #331 の `routine-real-core`。ハーネスは検証済み DEV ROM の
`$E800-$EFFF` テストオーバーレイ（`tests/fixtures/core-harness.s`）から bank0 の
ルーチンを JSR し、合成入力 A/X/Y/P とゼロページ `$20E0-$20FF` 32 バイトを与えて、
呼出前後の A/X/Y/P/SP/MPR とゼロページを実 Geargrafx コア（ディスクなし HuCard
ロード、cold/warm、各 2048 命令）で記録する。各サイクルは `RETURNED` / `TRAPPED`
/ `TIMEOUT` に分類し、`RETURNED` かつ全期待値一致のみ PASS。`TIMEOUT` は 1 サイクル 2048 命令の予算超過（長いルーチンを含む）と無限ループを区別しない。プラグインケースは `expect` で a/x/y/p（非ゼロ `p_mask`）/ram のいずれかを必ず照合する。未完了は PASS に
ならない。ケース JSON の形式と組込み自己検証ケースは
`tests/integration/geargrafx_core_harness.py` の docstring を参照。リファレンス
BIOS は実行せず、BIOS ABI 互換の証拠ではない。

実コア実行は opt-in で上記コマンド 1 つに限り、Linux 固定（pinned Geargrafx の
`.so`。他 OS では BLOCKED で終了し PASS にならない）。`make test` / `make check` /
host unittest には含まれない（`tests/host/test_core_harness.py` は合成記録の判定器のみ）。
ジャンプテーブル未接続の内部ルーチンは `target` に解決できないため、ハーネスへ載せるには
別途リンク手段が要る（各ルーチンの Issue で扱う）。

## Coverage status

| Area | Status |
| --- | --- |
| Fixture assembly/format/layout/reproducibility | PASS when the above host checks run |
| Real Geargrafx HuCARD reset/marker/TAM/TMA/TII | PASS when integration produces its evidence |
| PC/MPR register trace through standalone MCP | **NOT_RUN** (SDL3/CMake missing, no standalone binary). Protocol client `tools/mcp_checkpoint.py` + synthetic fake-server tests only (`tests/host/test_mcp_checkpoint.py`); synthetic PASS is not real-core evidence; #103 |
| System Card DEV ROM load/reset/bank/RAM/IRQ | Scoped real-core diagnostic observations; not production BIOS initialization |
| Callable DEV-only `EX_GETVER` (`$1E`, candidate `$E05A`) | Real Geargrafx cold/warm caller → OSS callee → RTS return observed by `make dev-integration`: X=3, Y=0, A preserved and SP balanced ([#174](https://github.com/mao2009/oss-pce-cd-bios/pull/174)); **reference BIOS ABI remains UNVERIFIED** |
| Independent startup/MPR/stack marker | Real-core diagnostic `dev-startup-integration`; not BIOS initialization |
| Guest synthetic CD READ(6)→RAM | Real-core diagnostic `dev-cdread-integration`; not BIOS `CD_READ` |
| Synthetic CD→RAM code execution/return | `dev-cdload-exec-integration` (merged #122); not BIOS service, not CD boot |
| Production System Card initialization and BIOS-mediated CD boot | NOT_IMPLEMENTED/NOT_TESTED; #1/#3/#4; synthetic CD→RAM execution is not BIOS-mediated boot |
| Remaining 80 candidate BIOS API slots and CD services | 80 nonreturning stubs; callable `CD_RESET`/`CD_BASE`/`CD_READ` NOT_IMPLEMENTED despite separate transport/host diagnostics |
| Reference-BIOS caller ABI (including full `EX_GETVER` contract) | UNVERIFIED/PENDING independent comparison; the DEV-only return observation does not certify production ABI or IRQ/NMI/hardware behavior |
| Independent emulator or real hardware | NOT_TESTED; #7 |
| Commercial games | NOT_TESTED; no inputs required or distributed |

`tools/rom.py` validates only caller-supplied headerless byte count, bank/CPU
window and placement constraints. It does not inspect instruction semantics or
establish a System Card contract. `tools/build.py::verify_fixture` adds the strict
all-byte smoke-content regression on top. Size-valid random bytes can meet the
structural contract but fail fixture validation; neither layer proves BIOS
compatibility. These generic validators do not establish BIOS layout/API specifications; the scoped research ledgers are separate.
Nightly's live schedule/skip behavior is separate from host recovery tests; [nightly.md](nightly.md) explains the host tests and runtime policy.

## Separate System Card/CD diagnostics and CI

`make test` runs the host suite **and original HuCARD Geargrafx smoke**,
not every standalone System Card diagnostic. After `make setup`:

```sh
make dev-test                   # 81 candidate slots, host API tests and real-core DEV checks
make dev-integration            # EX_GETVER DEV-only caller return; other 80 slots are stubs
make dev-startup-integration    # separate reset/MPR/stack marker
make dev-cdread-integration     # guest READ(6), full RAM and SCSI checks
make dev-trace-integration      # isolated pinned real-core trace and negative checks
make dev-release-gate-blocked   # intentional non-release assertion
```

`.github/workflows/api-source.yml` runs DEV `EX_GETVER` real-core integration
and the independent CD/startup experiments with related host tests,
debug/release binary comparison and the release refusal. A passing DEV test
is not a complete caller-ABI verification or a playable BIOS.
`.github/workflows/ci.yml` covers HuCARD/host/provenance; Nightly only
covers the HuCARD fixture. **Nightly PASS does not imply CD/BIOS PASS.**

The CD read judge requires cold and warm success PC and result block, all
expected bytes in guest RAM, unchanged sentinel tail, SCSI READ(6) LBA/count
and phase order, and **64 confirmed consecutive instructions in the success
halt loop**. Budget/timeout/incomplete-halt runs are not successful. No-media
runs load the ROM as the card with CD hardware forced on
(`SetCDROMType(STANDARD)` + `LoadHuCardFromBuffer`), because `LoadBiosFromBuffer`
alone never resets the core; they are expected failures, never a pass. The
audio-track and no-media READ(6) observations apply only to Geargrafx. See [CD diagnostics](cd-read-diagnostic.md),
[CPU evidence](geargrafx-contract-evidence.md) and [status](status.md).

Evidence-scoped snapshot: merged [#118](https://github.com/mao2009/oss-pce-cd-bios/pull/118)
provides a `CD_READ` contract ledger/model, **not** a callable firmware callee;
[#120](https://github.com/mao2009/oss-pce-cd-bios/pull/120) is a host-only
`CD_BASE` model; [#121](https://github.com/mao2009/oss-pce-cd-bios/pull/121)
probes CD bus reset but is **not** callable `CD_RESET`; [#122](https://github.com/mao2009/oss-pce-cd-bios/pull/122)
executes a synthetic RAM payload but is **not** firmware-mediated disc boot.
[#125](https://github.com/mao2009/oss-pce-cd-bios/pull/125) and later producer/consumer
work supply attestation and signed-verdict infrastructure, not a guarantee of
compatible behavior. Trusted `VERIFIED` promotion remains BLOCKED pending
[#171](https://github.com/mao2009/oss-pce-cd-bios/issues/171). The guarded
reference-BIOS scenario **plan** in [#170](https://github.com/mao2009/oss-pce-cd-bios/pull/170)
is not evidence that all planned scenarios ran. Private reference ROMs are not
part of CI or public deliverables. Consult the [dated implementation status](status.md)
for the scope of any later changes.
