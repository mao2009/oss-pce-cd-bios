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

## Coverage status

| Area | Status |
| --- | --- |
| Fixture assembly/format/layout/reproducibility | PASS when the above host checks run |
| Real Geargrafx HuCARD reset/marker/TAM/TMA/TII | PASS when integration produces its evidence |
| PC/MPR register trace through standalone MCP | NOT_RUN locally; SDL3 missing; #103 |
| System Card diagnostic load/reset/bank/RAM/IRQ | Scoped real-core PASS; not production BIOS |
| Independent startup/MPR/stack marker | Real-core diagnostic `dev-startup-integration`; not BIOS initialization |
| Guest synthetic CD READ(6)→RAM | Real-core diagnostic `dev-cdread-integration`; not BIOS `CD_READ` |
| Synthetic CD→RAM code execution/return | `dev-cdload-exec-integration` (merged #122); not BIOS service, not CD boot |
| Production BIOS boot and caller ABI | NOT_IMPLEMENTED/NOT_TESTED; #1/#3/#4 |
| BIOS APIs and homebrew disc boot | NOT_IMPLEMENTED/NOT_TESTED on main |
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
make dev-test                   # 81-slot diagnostic and real-core slot observations
make dev-startup-integration    # separate reset/MPR/stack marker
make dev-cdread-integration     # guest READ(6), full RAM and SCSI checks
make dev-trace-integration      # isolated pinned real-core trace and negative checks
make dev-release-gate-blocked   # intentional non-release assertion
```

`.github/workflows/api-source.yml` runs these experiments with related
host tests, debug/release binary comparison and the release refusal.
`.github/workflows/ci.yml` covers HuCARD/host/provenance; Nightly only
covers the HuCARD fixture. **Nightly PASS does not imply CD/BIOS PASS.**

The CD read judge requires cold and warm success PC and result block, all
expected bytes in guest RAM, unchanged sentinel tail, SCSI READ(6) LBA/count
and phase order, and **64 confirmed consecutive instructions in the success
halt loop**. Budget/timeout/incomplete-halt runs are not successful. No-media
guest execution is NOT_TESTED because the pinned Geargrafx loader does not
execute that ROM without media. The audio-track READ(6) observation applies
only to Geargrafx. See [CD diagnostics](cd-read-diagnostic.md),
[CPU evidence](geargrafx-contract-evidence.md) and [status](status.md).

CD status classification (#115), evidence schema (#116), real-core trace harness (#119),
host-only CD_BASE model (#120), CD_RESET bus-reset diagnostic (#121) and synthetic RAM payload
execution (#122), the startup CD-state record (#127) and the CD_BASE ABI evidence ledger (#128) are **on main** at snapshot `c20ac06`. Draft #118 (and #125, #126) remain **branch-only**; #122's
RAM payload execution is *not* boot.
