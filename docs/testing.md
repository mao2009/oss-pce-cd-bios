# Tests and limits of evidence

Run `make setup`, then `make test` and `make check`. These exit nonzero on a
missing tool, invalid setting, failed assertion, invalid assembler input,
timeout, or core crash. Expected negative failures are assertions that the
invalid input was rejected, never evidence that the invalid ROM works.

## Host tests

`python3 -m unittest discover -s tests/host -v` currently runs 89 tests: the original
13 retained tests, 46 review-regression tests and 30 API diagnostic tests.

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
| System Card BIOS load/boot and 256 KiB layout | NOT_TESTED; #1/#4/#6 |
| BIOS API semantics, CD reads/homebrew disc | NOT_IMPLEMENTED/NOT_TESTED; #3/#5 and API Issues |
| Independent emulator or real hardware | NOT_TESTED; #7 |
| Commercial games | NOT_TESTED; no inputs required or distributed |

`tools/rom.py` validates only caller-supplied headerless byte count, bank/CPU
window and placement constraints. It does not inspect instruction semantics or
establish a System Card contract. `tools/build.py::verify_fixture` adds the strict
all-byte smoke-content regression on top. Size-valid random bytes can meet the
structural contract but fail fixture validation; neither layer proves BIOS
compatibility. No new BIOS layout/API specification is introduced.
Nightly's live schedule/skip behavior can be observed only after these files
reach main; [nightly.md](nightly.md) explains the host tests and runtime policy.

## API scaffold additions (#101)

The existing 59 host tests remain. `tests/host/test_api_table.py` adds the prior
9 scaffold checks; `test_api_build.py` adds 21 checks using real pinned assembly,
for 89 total host tests. `make test` discovers all 89 and keeps the existing
HuCARD cold/warm/negative real-core tests. `make dev-test` runs the 30 API host
tests and the separate real-core System Card diagnostics: reset, three slots,
PC/X/MPR/physical bank observations and four negative execution cases.
These are development experiments, not caller ABI or CD boot passes.
The independent BIOS DEV workflow saves its own artifact; Nightly's original
smoke success criteria stay unchanged. [Scope](api-stub-scaffold.md).
