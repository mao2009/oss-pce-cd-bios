# G5 boot-progress measurement harness (Agent C)

Parent tracker: #635. Related: #756/#757 (slot-$02 fake RTS), #758/#759
(slot-$02+$33 fake RTS), #753 (API-entry observer), #762/#765 (PUBLIC/DEV
gap differential).

G5 = reach a commercial PC-Engine CD-ROM2 / SUPER CD-ROM2 game title screen.
This harness automatically measures how far each BIOS variant advances game
boot and identifies stop causes. It does **NOT** implement APIs, makes **NO**
compatibility claim, and lifts **NO** safety gates.

The project is **NOT** a compatible System Card BIOS. Geargrafx observations
are **not** proof of physical hardware behavior.

## Variants (no waiting for Agent A/B)

| Key | Image | Return kind | Builder |
|-----|-------|-------------|---------|
| A | normal PUBLIC loader | NONE (stubs halt) | `tools/build_dev_ipl_loader_bios.py` with `IPL_PUBLIC` |
| B | $02 fake RTS | FAKE_RTS (fabricated RTS+NOP) | `tools/build_slot02_return_loader.py` slots=`(0x02,)` issue=756 (PR #757 pattern) |
| C | $02+$33 fake RTS | FAKE_RTS | same builder, slots=`(0x02,0x33)` issue=758 (PR #759 pattern) |
| D | experimental BIOS, REAL API processing | REAL_SERVICE (per manifest) | independent input: `--experimental-rom` + `--experimental-manifest` (paths/branch/HEAD/slot-wiring); never hardcoded to A/B outputs |

B/C post-return behavior is **SIMULATED/CONDITIONAL** on the fabrication
(regs/flags/MPR preserved by construction, no side effects). D returns are
claimed **real** per the supplied manifest wiring and are proven only by
post-return execution (resumption markers + next observed call + SP
restoration), never by internal stack flags alone.

## Metrics per run (synthetic + opt-in commercial)

- API call order (`[(slot, method)]`, JSR/JMP with table + SP-delta evidence)
- real-vs-fake-RTS distinction (variant return kind + manifest wiring)
- actual return (RTS-like resumption vs halt; last stub-halted call is
  non-returning by construction)
- PC/SP/MPR/registers (caller/entry PCs, SP before/after/delta, entry
  A/X/Y/P, MPR, stack consistency flags; RUN pc/x/step)
- insn + frame counts (RUN `step`; frames `NOT_MEASURED` unless a tracer
  evidence file is supplied — never invented)
- control transfer (IPL jump: loader `reset` symbol + first `caller_pc`)
- screen-state change (claimed only with evidence; alone NEVER equals title)
- cold/warm delta (`calls_match` + `halts_match`; steps never compared)
- stop cause (`STUB_HALT_API_UNIMPLEMENTED`, `PRE_API_IPL_VALIDATION_HALT`,
  `BUDGET_EXCEEDED`, … with OBSERVED/INFERRED/UNKNOWN evidence state)
- progress vs previous run (`--baseline`: MORE_APIS/SAME/REGRESSED/FIRST_RUN)
- title-screen evidence (`--title-observed` + `--title-basis` required)

## Verdicts

`PRE_API_BLOCKER`, `API_ENTRY_CONFIRMED`, `SIMULATED_NEXT_API`,
`REAL_SERVICE_RETURN`, `NEXT_BLOCKER`, `FRAME_PROGRESS`, `G5_CANDIDATE`,
`G5_OBSERVED`, `UNKNOWN`/`BLOCKED`.

- Screen change alone **NEVER** equals title.
- `G5_OBSERVED` only with observed + confirmed title evidence **and** a
  stated confirmation basis (e.g. human-confirmed title frame + state note).
  Without both, the cap is `G5_CANDIDATE`.
- `SIMULATED_*` verdicts are never `API_ENTRY_CONFIRMED` and never
  compatibility evidence.
- Boot-failure classification is success: measurement completion exits 0
  even when every game-boot verdict is a blocker.

## Usage

```sh
# Variants and model (no core needed)
python3 tools/g5_harness.py --list-variants

# Synthetic A/B/C differential (pinned core + cc65 + DEV ROM needed)
python3 tools/g5_harness.py --mode debug --variants A B C

# With baseline progress diff
python3 tools/g5_harness.py --mode debug --variants A B C \
  --baseline build/debug/g5-harness/g5-evidence.json \
  --output build/debug/g5-harness/g5-evidence-new.json

# Variant D (independent experimental input interface)
python3 tools/g5_harness.py --mode debug --variants D \
  --experimental-rom /path/to/exp.pce \
  --experimental-manifest /path/to/exp-manifest.json

# Opt-in commercial (read-only ./rom/*.cue auto-detect; structural only;
# evidence to an outside-repo private path; sanitized in-repo output)
python3 tools/g5_harness.py --mode debug --variants A B C --commercial \
  --commercial-auto-loader \
  --commercial-output /tmp/opencode/issue635-private/g5-commercial.json
```

Manifest schema (D): `tools/g5_progress_model.py::validate_experimental_manifest`
requires `schema`, `rom_name` (*.pce), `branch`, `head` (40-hex),
`slot_wiring` (slot → `{kind: real, description}`), pinned `cc65`/`geargrafx`
equal to the pins, `release_eligible=false`, `dev_callable=false`.
Optional `rom_sha256` (verified when present), optional `labels`
(`api_unimplemented` int needed for execution; without it D records
manifest validation but execution is `BLOCKED`, never guessed).

## Evidence and pins

- Geargrafx `b49ae82a` (full `b49ae82a012eade566d72e9d61c9d95a5caa4da3`),
  cc65 `71746c8` (full `71746c829e77f74c2b601a7d16821170c2610df3`).
- Safety block in every record: `reference_bios_jsr=0`,
  `abi_verified=0`, `dev_callable=false`, `release_eligible=false`,
  `compatible_system_card_bios=false`.
- Commercial: `./rom/*.cue` auto-detect when present, read-only; **NEVER**
  stage/commit/upload/publish private ROM/BIOS/CUE/BIN/images/raw
  traces/hashes/absolute paths/saves; sanitized structural observations
  only; a fingerprint/hash is **NOT** permission to publish. Absent /
  unsupported / unimplemented boot path → `SKIP`/`BLOCKED` with reason,
  never `PASS`. All public tests reproducible without private media.

## Files

- `tools/g5_harness.py` — CLI harness (builds + runs + classifies + compares)
- `tools/g5_progress_model.py` — pure verdict/variant/manifest/sanitize model
- `tests/host/test_g5_harness.py` — host unit tests (RED→GREEN, pos/neg)
- `tests/integration/test_g5_progress.py` — focused integration (static +
  pinned real-core A/B/C differential, SKIP with reason when unavailable)
- `docs/g5-harness-*.md` — this design doc + baseline report

## Baseline results

Filled at convergence (A–C synthetic + commercial status, cold/warm both,
HEAD SHA, emulator/tool versions, which steps actually ran). See
`docs/g5-harness-baseline.md` when present.
