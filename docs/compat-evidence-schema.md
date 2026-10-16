# Compatibility evidence schema (v1)

Issue #7. Machine-checkable record for one compatibility stage, implemented by
`tools/compat_evidence.py` (`validate()`, `verdict()`) and tested in
`tests/host/test_compat_evidence.py`. Stage meanings are defined in
[compatibility.md](compatibility.md).

Usage: `python tools/compat_evidence.py [--ci] RECORD.json [RUNNER_ATTESTATION.json [PROBE.json]]` prints the verdict and exits 0 only on PASS.

A record is a claim, not proof. `verdict(record, runner, probe)` accepts PASS for execution stages
(`bios-loaded` and above) only with a runner attestation: `verified: true`, `rom_sha256` and `emulator_commit`
matching the record, `fixture_sha256` matching the record when the record has a fixture, and, for each record
checkpoint name, a runner `checkpoints[].observed` equal to the record's `expected` (duplicate runner names give FAIL).
Attestation alone never yields PASS above `rom-built`: see the stage probe section.
A `runner`, `stage_evidence` or `probe` key inside the record is ignored.
The runner output is consistency-checked, not authenticated (see the trust boundary).

## Trust boundary

- The verdict cannot tell a genuine runner output from hand-written JSON. Attestations and
  the raw probe output are unsigned and unauthenticated. `verdict()` checks only that they are consistent
  with the record and with the #119 rules. A PASS from `verdict()` is not a certification.
- Every CLI output carries `"authenticity": "not verified: ..."`. It states that the JSON is unsigned.
- `GITHUB_ACTIONS=true` is an environment variable that any local process can set. It is a guard
  against accidental use, not authentication. `--ci` does not make the input trustworthy by itself.
- Without `--ci`, the CLI output carries `"trust": "local-only, untrusted (unsigned JSON; not certifiable)"`.
  Any local PASS for a `bios-loaded` or higher stage is printed as **BLOCKED**, with the raw result in
  `local_verdict`. Local runs are for development only and must not be quoted as compatibility.
- `--ci` is accepted only when `GITHUB_ACTIONS=true` (otherwise exit 2). Its output carries
  `"trust": "ci-job context (GITHUB_ACTIONS=true is an environment variable any process can set; the JSON is still unsigned, so this is not a certification)"`.
  A PASS is printed as PASS only in this mode.
- Exception to the verdict exit codes: a refused `--ci` and a usage error exit 2 and print no verdict JSON.
  Every other invocation prints a verdict JSON and exits 1 (or 0 on PASS). That includes a missing or non-UTF-8 file,
  malformed JSON, and JSON nested too deeply for the parser, each as FAIL with reason `input unreadable`. A deeply nested
  probe log (`stdout` in the probe) is FAIL through the stage judge, not through the input-load path.
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
  edit that is itself merged can forge evidence. Signed attestation is the upgrade path before any shared gate:
  Issue #124 (PR #125, branch `feat/124-evidence-provenance`). Issue #124 is open; this PR does not close it.

## Stage-specific probe evidence

PASS is possible only for `source-only` and `rom-built` (build-only, no attestation or probe) and for `retail-boot`
through `completion-tested` with a judged probe. `rom-built` PASS is a record-format check, not a compatibility claim;
its `rom.sha256` is self-declared and unverified. `bios-loaded`, `startup`, `cd-detected` and `homebrew` are **BLOCKED** with any input:
no #119 label defines a judge for them, so no probe can yield PASS. Extending this needs a new label, which is out of scope.

`retail-boot`, `playable`, `save-tested` and `completion-tested` need `probe`, the raw #119 probe
output: `{"result": <run_job() result: status, reason, exit_code, stdout>, "forbid": [labels], "budget": int, "kind": "cdread"}`. An absent `kind` defaults to `cdread`, the only kind #119 judges.
`verdict()` passes `result` to the real #119 `geargrafx_harness.classify()`, which parses the `TAG {json}` lines
itself (`parse_records()`, which rejects a body-level `tag` field). Whatever classify() does not call PASS
(FAIL, BLOCKED, SKIP) becomes the stage finding. The verdict adds `cd_halt_fail` to the runner's `forbid`
list, so a runner cannot drop it. If `result.status` is present and not null, it must be `FAIL` or `BLOCKED`;
any other value (including `PASS`) gives FAIL. An explicit `null` is accepted, so a good log with `"status": null` can PASS.
An absent `status` key gives FAIL: the #119 judge reads `result["status"]`, the lookup raises KeyError, and that is reported as FAIL.
Above `homebrew`, a PASS requires a non-empty `trace_deltas` (missing or empty gives BLOCKED). The record needs a
checkpoint named after the stage label, and the runner must report the same name as observed.

**Not implemented:** no shipped producer emits `probe`. The #119 `trace` command writes parsed events and
`counts`, not the stdout that `classify()` needs, and it does not write a `stage_evidence` summary. So a
PASS above `homebrew` cannot be produced from the CLI as shipped. Without a `probe`, these stages are **BLOCKED**.
A summary such as `stage_evidence` is not read by this module, so a hand-written summary cannot yield PASS.

| Stage | Label (`--expect`) |
| --- | --- |
| `retail-boot` | `retail-boot-reached` (no #119 probe emits it yet, so retail-boot is BLOCKED until one does) |
| `playable` | `playable-reached` |
| `save-tested` | `save-reached` |
| `completion-tested` | `completion-reached` |

The `classify()` rules that apply (see `tests/integration/geargrafx_harness.py`): probe `kind` must be `cdread`
(`api` is NOT_SUPPORTED, BLOCKED); exit 6 is BLOCKED (debugger missing); other non-zero exits, timeouts and
output caps are FAIL; a `BLOCK` record is BLOCKED; exactly one `CAP` record with `instruction_step`,
`cdrom_hardware`, `cdrom_media` and `disc_given` all true (else BLOCKED); exactly one RUN per cycle, `cold`
then `warm`, each ending on the label with a confirmed 64-instruction halt; `step` a non-negative integer
within `budget`; exactly one success HIT per cycle before its RUN.

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
| `checkpoints` | array | `{"name", "expected", "observed"}`. Verdict requires the record's own `expected` to equal its `observed`; the runner's `observed` for that name must equal the record's `expected`. A `passed` flag is not trusted. A missing or null field is absent; any other non-array value is FAIL. |
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
8. `retail-boot`, `playable`, `save-tested` and `completion-tested` need the raw #119 `probe` (see above).
   Missing gives **BLOCKED**. Judged by #119 `classify()`: any outcome other than PASS is kept as its own
   status, so a FAIL (non-zero exit, timeout, output cap, forbidden label, a RUN for another reason such as
   budget or cd_halt_fail, not one cold then warm RUN, unconfirmed halt, a step outside 0..budget, a success HIT
   missing, repeated or after its RUN, malformed log) gives **FAIL**. A BLOCK record, a missing or false
   capability flag, `kind` other than `cdread` (NOT_SUPPORTED) and exit 6 (debugger capability missing)
   give **BLOCKED**, as in #119. A `retail-boot` record with no local fixture is **SKIP** (fixture missing; no stage
   check runs). With a local fixture, a record whose stage-label checkpoint is missing is **BLOCKED**.
9. A claim of `PASS` that the evidence does not support is an **overclaim** and gives **FAIL**. A claim of
   `BLOCKED`, `SKIP` or `FAIL` that complete evidence would otherwise pass is kept as the result, with a
   `conflict` reason. Complete evidence never silently overrides the reporter's own non-PASS claim.

Source-only and rom-built records need no attestation (build checks only). From `bios-loaded` to `homebrew`
every result is BLOCKED or FAIL, never PASS (no judge label; see the stage probe section). A single launch never supports an all-title claim.
From `bios-loaded` and above, PASS requires real-core evidence; source-only and rom-built need none.

## Not covered (v1)

- Emulator trace capture and checkpoint extraction: the record, attestation and raw probe output are produced by the runner (#119), not by this module.
- Not wired into `make check` or any release gate yet. Until a runner emits the raw `probe` in CI,
  retail-boot, playable, save-tested and completion-tested are **BLOCKED** for every input that lacks it.
  A `probe` that passes the #119 judge gives PASS (covered by unit tests with synthetic probe logs).
  No shipped runner emits it, so this is the expected status.
- Consistency with #119: `tests/host/test_compat_119_consistency.py` imports the real `geargrafx_harness.classify()`
  and checks that the verdict gives the same category for each synthetic probe-log scenario. No #119 logic is copied.
- Signed attestation (Issue #124, PR #125 branch `feat/124-evidence-provenance`) is not in this version.
- `docs/compatibility.md` defines BUILT as a reproducible ROM hash plus structural validation. This module does not
  implement that: `rom-built` checks only the record format and a self-declared `rom.sha256`, which it does not verify.
  That compatibility.md definition is left unchanged here; the gap is stated in this document.
- Retail disc handling and private fixture storage: see [provenance.md](provenance.md).
- Schema migration beyond v1.
