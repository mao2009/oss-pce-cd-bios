# Compatibility evidence schema (v1)

Issue #7. Machine-checkable record for one compatibility stage, implemented by
`tools/compat_evidence.py` (`validate()`, `verdict()`) and tested in
`tests/host/test_compat_evidence.py`. Stage meanings are defined in
[compatibility.md](compatibility.md).

Usage: `python3 tools/compat_evidence.py RECORD.json` prints the verdict and exits 0 only on PASS.

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
5. `retail-boot` with a synthetic fixture gives **FAIL**. Missing region gives **BLOCKED**.
6. `startup` and above need at least one checkpoint (**BLOCKED** if none). Any mismatch gives **FAIL**.
7. Above `homebrew`, guest trace deltas are required (**BLOCKED** if absent).
8. A claim of `PASS` that the evidence does not support is an **overclaim** and gives **FAIL**.

A single launch never supports an all-title claim. The result is never PASS for any stage
that lacks real-core evidence.

## Not covered (v1)

- Emulator trace capture and checkpoint extraction: the record is produced by the runner, not by this module.
- Retail disc handling and private fixture storage: see [provenance.md](provenance.md).
- Schema migration beyond v1.
