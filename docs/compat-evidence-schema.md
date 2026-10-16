# Compatibility evidence schema (v1)

Issue #7. Machine-checkable record for one compatibility stage, implemented by
`tools/compat_evidence.py` (`validate()`, `verdict()`) and tested in
`tests/host/test_compat_evidence.py`. Stage meanings are defined in
[compatibility.md](compatibility.md).

Usage: `python3 tools/compat_evidence.py [--ci] RECORD.json [RUNNER_ATTESTATION.json]` prints the verdict and exits 0 only on PASS.

A record is a claim, not proof. `verdict(record, runner)` accepts PASS for execution stages
(`bios-loaded` and above) only with an independent runner attestation: `verified: true`, and
`rom_sha256`, `fixture_sha256`, `emulator_commit` and `checkpoints[].observed` that match the record.
A `runner` key or `stage_evidence` key inside the record is ignored.

## Trust boundary

- The verdict cannot tell a genuine runner output from hand-written JSON. Attestations are unsigned.
  This module therefore never certifies on its own.
- Without `--ci`, the CLI output carries `"trust": "local-only, untrusted (unsigned JSON; not certifiable)"`.
  Any local PASS for a `bios-loaded` or higher stage is printed as **BLOCKED**, with the raw result in
  `local_verdict`. Local runs are for development only and must not be quoted as compatibility.
- `--ci` is accepted only when `GITHUB_ACTIONS=true` (otherwise exit 2). Its output carries
  `"trust": "ci-job runner output ..."`. A PASS is printed as PASS only in this mode.
- Certification in CI is allowed only when all of these hold (the workflow must enforce them):
  1. The record and runner JSON are produced by the Geargrafx harness (#119) inside the same job,
     from the same checkout, and uploaded as one CI artifact. No file from a PR head, fork, or
     user upload is read by the certification step.
  2. The harness and Geargrafx are pinned: `emulator_commit` equals the Geargrafx revision pinned in
     `tools/dependencies.json`, and the emulator build is rebuilt from the verified archive sha256.
  3. The ROM and fixture hashes are computed in that job. The fixture is a local proprietary disc
     or a synthetic one; neither is stored in CI. Only ids, hashes and metadata are uploaded.
  4. The workflow that runs `--ci` is taken from the protected default branch, not from the PR head.
- Limits (not closed by v1): no signature or provenance attestation; a workflow edit that is itself
  merged can forge evidence. Signed attestation is the upgrade path before any shared gate.

## Stage-specific runner evidence

`playable`, `save-tested` and `completion-tested` also need `runner["stage_evidence"]`, which the
harness emits from its own `classify()` result (#119 rules). `trace_deltas` in the record never
supports a PASS above `homebrew`. The record needs a checkpoint named after the stage label, and the
runner must report the same name as observed.

| Stage | Label (`--expect`) |
| --- | --- |
| `playable` | `playable-reached` |
| `save-tested` | `save-reached` |
| `completion-tested` | `completion-reached` |

`stage_evidence` fields: `stage`, `expect` (the label above), `probe_kind` (must be `cdread`; `api`
emits no RUN records), `exit_code` (0), `timed_out`, `output_capped` (both false), `budget`
(positive integer, the `--steps` limit), `cap` (`instruction_step`, `cdrom_hardware`, `cdrom_media`,
`disc_given`, all true), `runs` (exactly one RUN per cycle, `cold` then `warm`, each with `reason`,
`step`, `halt_confirmed`, `halt_confirmation_steps`, `halt_confirmation_required`, `halted_after_step`,
`hits_before_run`).

## Stages (ascending)

`source-only`, `rom-built`, `bios-loaded`, `startup`, `cd-detected`, `homebrew`,
`retail-boot`, `playable`, `save-tested`, `completion-tested`.

## Record fields

| Field | Type | Notes |
| --- | --- | --- |
| `schema` | string | Must be `pce-compat-evidence-v1`. |
| `stage` | enum | One stage per record. Stages are never promoted automatically. |
| `result` | enum | Reporter's claim: `PASS`, `FAIL`, `SKIP`, `BLOCKED`. The verdict is authoritative. |
| `blocker` | string or null | Required when the claim is `BLOCKED` or `SKIP`. |
| `claim_scope` | enum | `single-launch` or `title`. `all-titles` is always rejected. |
| `rom` | object or null | `{"sha256": 64 lowercase hex}` of the generated ROM. |
| `emulator` | object or null | `kind`: `real-core`, `mock` or `static`. A `real-core` record needs `name`, `version` and a 40-hex `commit`. Names matching mock/stub/fake/static/dummy/simulat are rejected as real. |
| `fixture` | object or null | `{"id", "sha256", "origin"}`. `origin` is `original-synthetic` or `local-proprietary`. |
| `region` | string or null | Target edition region. Required for `retail-boot` and above. |
| `checkpoints` | array | `{"name", "expected", "observed"}`. Verdict compares them itself; a `passed` flag is not trusted. |
| `trace_deltas` | array | `{"name", "delta"}`. Required above `homebrew`. |

Proprietary material never appears in a record. Only ids, hashes and metadata.

## Verdict rules

Findings are combined with priority FAIL > BLOCKED > SKIP > PASS.

1. Schema errors (bad hash, unknown enum, mock marked real, all-titles scope, missing blocker) give **FAIL**.
2. `rom-built` and above need a ROM. Missing ROM gives **BLOCKED**.
3. `cd-detected` and above need a fixture. Missing gives **BLOCKED**, or **SKIP** for `retail-boot` and above (local, opt-in).
4. `bios-loaded` and above need a `real-core` emulator. Mock or static gives **BLOCKED**, so a mock cannot promote a stage.
   They also need a runner attestation. Missing or unverified gives **BLOCKED**. A hash, commit or checkpoint
   that does not match the record gives **FAIL**.
5. `retail-boot` with a synthetic fixture gives **FAIL**. Missing region gives **BLOCKED**.
6. `startup` and above need at least one checkpoint (**BLOCKED** if none). Any mismatch gives **FAIL**.
7. Above `homebrew`, guest trace deltas are required (**BLOCKED** if absent). They are self-reported and
   never sufficient alone.
8. `playable`, `save-tested` and `completion-tested` need runner `stage_evidence` (see above). Missing gives
   **BLOCKED**. Contradicting it gives **FAIL**: wrong stage or label, `probe_kind` not `cdread`, non-zero exit,
   timeout or output cap, a RUN that is not the label (budget, cd_halt_fail, ...), not exactly one cold and one
   warm RUN, halt not confirmed over 64 instructions, a step that is not an integer in 0..budget, or a success
   HIT that is missing, repeated or after its RUN. A missing or false capability flag gives **BLOCKED**.
9. A claim of `PASS` that the evidence does not support is an **overclaim** and gives **FAIL**.

Source-only and rom-built records need no attestation (build checks only). A single launch never supports an all-title claim. The result is never PASS for any stage
that lacks real-core evidence.

## Not covered (v1)

- Emulator trace capture and checkpoint extraction: the record, attestation and `stage_evidence` are produced by the runner (#119), not by this module.
- Not wired into `make check` or any release gate yet. Until the #119 runner emits `stage_evidence` in CI,
  playable, save-tested and completion-tested are **BLOCKED** for every input. That is the expected status.
- Retail-boot still uses self-reported `trace_deltas` for its PASS (unchanged; needs a judgement on whether it
  should also require stage evidence).
- Retail disc handling and private fixture storage: see [provenance.md](provenance.md).
- Schema migration beyond v1.
