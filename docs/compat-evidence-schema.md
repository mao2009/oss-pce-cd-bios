# Compatibility evidence schema (v1)

Issue #7. Machine-checkable record for one compatibility stage, implemented by
`tools/compat_evidence.py` (`validate()`, `verdict()`) and tested in
`tests/host/test_compat_evidence.py`. Stage meanings are defined in
[compatibility.md](compatibility.md).

Usage: `python3 tools/compat_evidence.py [--ci] RECORD.json [RUNNER_ATTESTATION.json]` prints the verdict and exits 0 only on PASS.

A record is a claim, not proof. `verdict(record, runner)` accepts PASS for execution stages
(`bios-loaded` and above) only with a runner attestation: `verified: true`, and
`rom_sha256`, `fixture_sha256`, `emulator_commit` and `checkpoints[].observed` that match the record.
A `runner` key or `stage_evidence` key inside the record is ignored.
The runner output is consistency-checked, not authenticated (see the trust boundary).

## Trust boundary

- The verdict cannot tell a genuine runner output from hand-written JSON. Attestations and
  `stage_evidence` are unsigned and unauthenticated. `verdict()` checks only that they are consistent
  with the record and with the #119 rules. A PASS from `verdict()` is not a certification.
- Every CLI output carries `"authenticity": "not verified: ..."`. It states that the JSON is unsigned.
- `GITHUB_ACTIONS=true` is an environment variable that any local process can set. It is a guard
  against accidental use, not authentication. `--ci` does not make the input trustworthy by itself.
- Without `--ci`, the CLI output carries `"trust": "local-only, untrusted (unsigned JSON; not certifiable)"`.
  Any local PASS for a `bios-loaded` or higher stage is printed as **BLOCKED**, with the raw result in
  `local_verdict`. Local runs are for development only and must not be quoted as compatibility.
- `--ci` is accepted only when `GITHUB_ACTIONS=true` (otherwise exit 2). Its output carries
  `"trust": "ci-job runner output ..."`. A PASS is printed as PASS only in this mode.
- A future CI certification gate would be allowed only when all of these hold. **This module enforces none
  of them**; the workflow must, and no such workflow exists yet:
  1. The record and runner JSON are produced by the Geargrafx harness (#119) inside the same job,
     from the same checkout, and uploaded as one CI artifact. No file from a PR head, fork, or
     user upload is read by the certification step.
  2. The harness and Geargrafx are pinned: `emulator_commit` equals the Geargrafx revision pinned in
     `tools/dependencies.json`, and the emulator build is rebuilt from the verified archive sha256.
  3. The ROM and fixture hashes are computed in that job. The fixture is a local proprietary disc
     or a synthetic one; neither is stored in CI. Only ids, hashes and metadata are uploaded.
  4. The workflow that runs `--ci` is taken from the protected default branch, not from the PR head.
- Limits (not closed by v1): no signature or provenance attestation. Anyone who can write the record and
  runner JSON can forge a PASS-consistent pair, and `GITHUB_ACTIONS=true` does not prevent that. A workflow
  edit that is itself merged can forge evidence. Signed attestation is the upgrade path before any shared gate.

## Stage-specific runner evidence

`retail-boot`, `playable`, `save-tested` and `completion-tested` also need `runner["stage_evidence"]`,
which the harness emits from its own `classify()` result (#119 rules). `trace_deltas` in the record never
supports a PASS above `homebrew`. The record needs a checkpoint named after the stage label, and the
runner must report the same name as observed.

| Stage | Label (`--expect`) |
| --- | --- |
| `retail-boot` | `retail-boot-reached` (no #119 probe emits it yet, so retail-boot is BLOCKED until one does) |
| `playable` | `playable-reached` |
| `save-tested` | `save-reached` |
| `completion-tested` | `completion-reached` |

`stage_evidence` fields: `stage`, `expect` (the label above), `probe_kind` (must be `cdread`; `api`
emits no RUN records), `exit_code` (0), `timed_out`, `output_capped` (both false), `budget`
(positive integer, the `--steps` limit), `cap` (`instruction_step`, `cdrom_hardware`, `cdrom_media`,
`disc_given`, all true), `forbid_hits` (list, empty), `runs` (exactly one RUN per cycle, `cold` then
`warm`, each with `reason`, `step`, `halt_confirmed`, `halt_confirmation_steps`,
`halt_confirmation_required`, `halted_after_step`, `hits_before_run` and `hits` (total success HITs
for that cycle), both exactly 1).

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
8. `retail-boot`, `playable`, `save-tested` and `completion-tested` need runner `stage_evidence` (see above).
   Missing gives **BLOCKED**. Contradicting it gives **FAIL**: wrong stage or label, non-zero exit (except 6),
   timeout or output cap, a forbidden label, a RUN that is not the label (budget, cd_halt_fail, ...), not
   exactly one cold and one warm RUN, halt not confirmed over 64 instructions, a step that is not an integer
   in 0..budget, or a success HIT that is missing, repeated or after its RUN. A missing or false capability
   flag, `probe_kind` other than `cdread` (NOT_SUPPORTED) and exit 6 (debugger capability missing) give
   **BLOCKED**, as in #119. A `retail-boot` record with no local fixture stays **SKIP** (nothing was run).
9. A claim of `PASS` that the evidence does not support is an **overclaim** and gives **FAIL**.

Source-only and rom-built records need no attestation (build checks only). A single launch never supports an all-title claim. The result is never PASS for any stage
that lacks real-core evidence.

## Not covered (v1)

- Emulator trace capture and checkpoint extraction: the record, attestation and `stage_evidence` are produced by the runner (#119), not by this module.
- Not wired into `make check` or any release gate yet. Until the #119 runner emits `stage_evidence` in CI,
  retail-boot, playable, save-tested and completion-tested are **BLOCKED** for every input. That is the expected status.
- Consistency with #119: `tests/host/test_compat_119_consistency.py` restates the #119 rules from the issue spec
  (no code copied) and checks that both judges give the same category for each probe-log scenario.
- Retail disc handling and private fixture storage: see [provenance.md](provenance.md).
- Schema migration beyond v1.
