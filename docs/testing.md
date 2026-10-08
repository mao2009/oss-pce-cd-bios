# Tests and limits of evidence

Run `make setup`, then `make test` and `make check`. These exit nonzero on a
missing tool, invalid setting, failed assertion, invalid assembler input,
timeout, or core crash. Expected negative failures are assertions that the
invalid input was rejected, never evidence that the invalid ROM works.

## Host tests

`python3 -m unittest discover -s tests/host -v` currently runs 13 tests:

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

`make check` parses Python and Bash, runs checksum-pinned actionlint 1.7.7 against
all local workflow YAML/schema/expressions/job references, runs the assembler
opcode probe, detects the core and checks Git whitespace. Optional shellcheck and
pyflakes integrations are disabled to keep the stated host prerequisites exact;
we do not claim those linters were run.

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
(fixture CPU address `$2200`). A mutated ROM with TII replaced by NOPs must fail
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

The binary verifier is deliberately specific to this fixture. It is not a
generic validator of System Card memory constraints or BIOS compatibility.
Nightly's live schedule/skip behavior can be observed only after these files
reach main; [nightly.md](nightly.md) explains the host tests and runtime policy.
