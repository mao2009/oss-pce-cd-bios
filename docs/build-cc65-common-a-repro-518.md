# cc65 `common.a` error reproducibility record (Issue #518)

Status: **NOT_REPRODUCED** (within the tried conditions only).
No toolchain change in this record.

## Issue and observation

- Issue: #518 (`[Build][Repro] cc65 の common.a エラーの再現性調査`).
- Original reports: PR #509 (BM_FORMAT, #303) and PR #510 (public docs, #507)
  showed the same cc65-related `common.a` error during review (Wave 24).
- CI evidence (2026-10-23, PR #510 HEAD `cfb07460167b29e6ba4c31d2859f961b4d8f4836`,
  CI run `39897852001`, verify job `120232323007`,
  step `Prepare pinned tools from source`):
  - `03:59:02.778`: `ar r ../wrk/common/common.a ...`
  - `03:59:02.901`: `ar: creating ../wrk/common/common.a`
  - `03:59:03.498`: `gcc -o ../bin/ld65 ... ../wrk/common/common.a` link start
  - `03:59:03.547`: `/usr/bin/ld: ../wrk/common/common.a: error adding symbols:
    file format not recognized`
  - `03:59:03.777`: second `ar r ../wrk/common/common.a ...`
  - `make -j2` exited with code 2.
- Hypothesis at the time: an `ar r` archive update raced a link reading the
  same archive. Log evidence strengthened the suspicion but the root cause was
  not confirmed. The failure was in tool bootstrap, not in the docs tests.

Related (not the same symptom): #332 / #333 / #398 (cc65 preinstall and
host-test separation), #542 / PR #543 (serialize cc65 build with `-j1`).

## Trial conditions (this record)

- HEAD: `origin/main` = `0f7a3257` (2026-11-18).
- cc65 pin: `71746c829e77f74c2b601a7d16821170c2610df3`
  (`tools/dependencies.json`, `cc65 V2.19 - Git 71746c829`).
- Host: Linux x86_64, gcc 13.3.0, GNU Make 4.3.
- Shared cache handling: the shared `TOOLS_DIR` was treated as read-only.
  No `make setup` rebuild, no writes to the shared cache. Each trial copied
  the pinned cc65 source to an isolated scratch directory outside the
  repository, removed `bin`/`wrk`, and ran a clean single make invocation:
  `make -C <copy> -j{1,2} "BUILD_ID=Git 71746c829" ca65 ld65 da65 cc65`
  (the same target list `tools/setup.py` uses for cc65).

## Results

Prior runs (W09, wave26, HEAD `ec496e9`, WSL2, gcc 15.2.0, nproc=20,
isolated scratch directory outside the repository, same clean-build command):

| condition | run | exit | error lines (`error adding symbols` / `file format not recognized` / `***`) |
|---|---|---:|---:|
| serial `-j1` | a | 0 | 0 |
| parallel `-j2` | a | 0 | 0 |
| serial `-j1` | b | 0 | 0 |
| parallel `-j2` | b | 0 | 0 |
| serial `-j1` | c | 0 | 0 |
| parallel `-j2` | c | 0 | 0 |

All 6 produced identical `bin/ca65`, `bin/ld65`, `bin/da65`, `bin/cc65`
SHA-256. Each run linked `ld65` against `common.a` exactly once, after the
archive was created.

This-record runs (HEAD `0f7a3257`, isolated scratch directory outside the
repository, same clean-build command, `bin`/`wrk` removed before each run):

| condition | exit | error lines | binaries |
|---|---:|---:|---|
| serial `-j1` | 0 | 0 | `ca65 525d3f51…`, `ld65 3d87e556…` |
| parallel `-j2` | 0 | 0 | identical to `-j1` and to the pinned cache |

Verdict for a **single make** invocation per clean tree: **NOT_REPRODUCED**.
Serial `-j1` and parallel `-j2` produced the same binaries with no
`common.a` error. `cc65/src/Makefile` declares `../wrk/common/common.a` as a
single target referenced by each program link, so one make process has no
observed path where two `ar r` write the same archive concurrently.

## NOT tried (outside this verdict)

- Two make processes sharing the same `wrk/` directory at the same time
  (concurrent builds against one cache; matches the CI log showing `ar r`
  twice). The supported entrypoint serializes setups with the exclusive
  `.setup.lock`, but direct concurrent `make -C` calls were NOT tried here.
- `-j` greater than 2.
- GitHub Actions `ubuntu-24.04` runner reproduction.
- Windows native (setup is Linux-only by design).

## Acceptance mapping (Issue #518)

1. Same HEAD / same command, multiple runs: W09 6 runs plus this record's
   2 runs, all exit 0 with zero `common.a` error lines.
2. Serial vs parallel: `-j1` vs `-j2` compared; no difference in exit code,
   error lines, or output hashes under the tried conditions.
3. No toolchain change: this record changes no build logic. `tools/setup.py`
   still builds cc65 with `-j1` (from #542) and holds the exclusive setup
   lock; that mitigation is locked by
   `tests/host/test_cc65_common_a_repro_518.py`. No fix is claimed.
4. Non-reproduction recorded: this file is the record. The concurrent-make
   hypothesis remains untested; the Issue stays open until the cause is
   determined or the owner closes it on this evidence.

No commercial media, no reference BIOS bytes, no secrets, and no synthetic
fixture changes are involved.
