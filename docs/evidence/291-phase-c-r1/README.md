# Issue #291 Phase C-R1: deterministic-reset and synthetic evidence

**Status: evidence record only.** This folder records synthetic-input checks of a test-only deterministic reset and the repairs made after an independent review of Phase C. It is not a System Card BIOS result and not an implementation.

What this does **not** claim:

- It does not confirm the CD_BOOT (slot $00, $E000) ABI, and it does not change #291 (remains `BLOCKED_ABI`).
- It does not establish compatibility with any System Card BIOS, commercial game, or hardware behaviour. Geargrafx output is not hardware evidence.
- It does not change `release_eligible=false`, the #173 gate, or the reference JSR permission (0/81).
- It does not include a reference-BIOS run. No reference-BIOS A/B experiment was performed in C-R1.

## Evidence categories

Per the project evidence rules, these categories are kept apart:

| Category | Used here? | Notes |
|---|---|---|
| Host-model tests | Yes, limited | The seeded-generator model used in T2 is checked against recorded states; see the T2 history. |
| Synthetic-disc real-core diagnostics | Yes | Pinned Geargrafx source tree, built with a test-only deterministic-reset option (opt-in, default off, not merged). Synthetic BIOS and synthetic CUE/BIN only. |
| Local commercial-disc observations | No | None used. |
| Callable System Card BIOS API execution | No | None used. |
| Original homebrew-disc boot | No | None used. |
| Commercial-game compatibility | No | None used. |

## Inputs

- **Synthetic BIOS**: 262144 bytes written for this test (own bytes). Not a System Card BIOS. Its SHA-256 is in `synthetic_bios_manifest-r1.json`.
- **Synthetic CUE/BIN**: unmodified synthetic disc from the test set. Its SHA-256 values are in `phase-c3-tests-r1.md`.
- **Pinned Geargrafx**: commit `b49ae82a012eade566d72e9d61c9d95a5caa4da3` (version 1.8.2-10-gb49ae82), read-only for source references. Its binary is not modified.
- **Test build**: the deterministic-reset variant (`GG_TEST_RNG_SEED`, opt-in). The implementation patch (`det-reset.patch`) is **not** part of this PR and is not merged into `main`. Any future implementation PR would be separate.

No reference BIOS, no `rom/` directory, no reference JSR, no forced PC, no BIOS patch, no commercial medium, and no GitHub write was used in this step.

## Files in this folder

Public data files are listed with SHA-256 values in `MANIFEST.sha256`.

| File | Role |
|---|---|
| `phase-c0-audit.md` | C0 source audit of the pinned tree (reset paths, RNG, CD init). |
| `phase-c3-tests.md` | Original Phase C test record (before the C-R1 repairs). |
| `phase-c3-expected.json` | Expected values as first written. |
| `phase-c3-results-firstpass.json` | **First full T1–T5 run, which contains the T2 FAIL.** Kept unchanged except path sanitization. |
| `phase-c3-results.json` | Re-evaluated results after the T2 model correction (path sanitization only). |
| `phase-c3-manifests.json` | Run manifests for the Phase C runs. |
| `phase-c3-tests-r1.md` | C-R1 test record: T1–T5 status, corrections, provenance table. |
| `phase-c3-expected-r1.json` | C-R1 expected values (T4 citation corrected). |
| `c0-cd-thread-timing.md` | CD image thread timing and CD state equality (M-6). |
| `cd-measure-r1.json` | CD controller state snapshots, 4 runs (2 test-build, 2 pinned). |
| `t2-history.md` | T2 history: initial model, first FAIL, source-based correction, re-evaluation. |
| `rng-521-520.md` | Attribution of the 521/520 draw counts from source (M-3). |
| `rng-sim-result.json` | Output of the RNG attribution simulation. |
| `t4-evidence.md` | T4 input evidence: historical probe (unrecoverable) and new re-run. |
| `t4-expected-r1.json` | Expected values written before the T4 re-run. |
| `t4-rerun-r1.json` | T4 re-run results. |
| `t4-rerun-stdout.txt` | T4 re-run stdout. |
| `recentrom-isolation.md` | `RecentROM0` and portable-isolation audit (M-7). |
| `synthetic_bios_manifest-r1.json` | Corrected manifest of the synthetic BIOS (T4 wording fixed). |
| `run-manifest.json` | C-R1 run directory manifest with config path-key checks. |

## Corrections and history

1. **MAJOR (review): T4 source citation.** The earlier citation `input_inline.h:174-180` is the mouse branch. The standard pad path is `input_inline.h:204-217`. Corrected in `phase-c3-tests-r1.md` and `phase-c3-expected-r1.json`. The content of the expectation was checked against source and is unchanged.
2. **T2: first run FAIL, then a source-based correction.** The first full run failed T2 because the seeded-model comparison included the raw draw for P. A, X, Y, S and MPR matched in all runs. Source (`huc6280.cpp:232-240`) shows reset clears T, D and B and sets I after the random draw, so the model was changed to mask those bits. **The T2 PASS after this change is a post-hoc corrected result**, not a pre-registered expectation. The first FAIL is preserved in `phase-c3-results-firstpass.json`. See `t2-history.md`.
3. **T4: historical probe output not preserved.** The original probe output was not saved and cannot be restored, so its result is **UNKNOWN**. A new T4 re-run under the same conditions passed. This re-run is **new evidence** and is recorded as such in `t4-evidence.md`. The earlier draft relation for SEL=0 was corrected from source after a probe run.
4. **521/520 random draw counts: attributed from source, not observed per call.** Init-time draws (SCSI 1, `Memory::Reset` 7 + r, colour table 512) reproduce the six recorded deterministic reset states. The individual draw calls were not observed directly. The colour-table values and the init-time MPR values were not compared with any record, so **full initial-state equality is not claimed**. See `rng-521-520.md`.
5. **M-6: CD image thread timing.** The cuebin read-ahead worker is compiled out in both builds. `GG_ENABLE_CDROM_CUEBIN_READAHEAD` is not defined by the pinned desktop CMake/Ninja build (`build/build.ninja` DEFINES, `wrapper/CMakeLists.txt`); it appears only in the libretro `Makefile`, which is not the build used here. `cdrom_cuebin_image.cpp:227-233` forces read-ahead off when the macro is undefined. The worker block (`:190-192`, and the thread body inside the `#if` that runs from `:1465` to `:1656`) is excluded. The C-R1 review's statement that the synthetic CUE exercises that thread is **refuted**. Citation corrected in `phase-c3-tests-r1.md` from `230-232` to `227-233`.
   - CD controller snapshot fields: identical across two test-build runs and across two pinned runs. The pinned pair differs in CPU and MPR state because the pinned build uses a time seed; this is expected and is not a CD difference.
   - CD RAM (64 KB) state: **UNKNOWN**. This build exposes no CD RAM memory area through the available tools.
6. **Expectation timing (correction to the original record).** `phase-c3-tests.md` and `phase-c3-expected.json` state that expectations were written before any launch. That is **not accurate for the T4 relation**: a T4 probe run (17:52) preceded the expectation file (17:53:34). The probe and the timing are recorded in `t4-evidence.md`. The C-R1 T4 re-run used an expectation file written before its first launch (`t4-expected-r1.json`).
7. **Original T4 citation.** `phase-c3-expected.json` (the original, kept) still cites `input_inline.h:174-180` for T4. That citation is the mouse branch; the corrected citation is in `phase-c3-expected-r1.json`.
8. **M-7: `RecentROM0`.** The config key `RecentROM0` stores the synthetic CUE path. In the source it is written at exit to the run-local config and used only by the GUI menu and the MCP `list_recent_media` call; neither opens the file. The harness starts each run from an empty directory, so the key is not read back during these runs. The T5 PASS covers file writes; the config reference is recorded as found.

## Tests (as recorded in C-R1)

| Test | Result | Scope |
|---|---|---|
| T1 reproducibility | PASS | Three launches with the same seed give the same recorded state. The pinned baseline varies (three distinct post-reset states). |
| T2 seeds | PASS after post-hoc correction (first run FAIL, see above) | Seed model against recorded states. |
| T3 trace | PASS | 16 of 16 expected PCs in every run; no zero-entry trace. |
| T4 input | PASS (after one relation correction); C-R1 re-run PASS; historical probe output UNKNOWN | Up on SEL=1 read, using the standard pad path. |
| T5 portable isolation | PASS | File writes only; `RecentROM0` audited separately. |
| CD thread timing / state equality | Compile-out verified; CD controller fields identical; CD RAM UNKNOWN | See M-6 above. |

The per-test commands were run from the internal scratchpad on Windows (each under a timeout). The raw run directories, the internal `config.ini` copies and the full stdout/stderr logs are **not** published, because they contain private paths and are not needed to read the results. The published set is the summary and record files listed above, plus the T4 re-run stdout (`t4-rerun-stdout.txt`).

**Repository checks for this PR:**

- `make test`, `make test-real-core` and `make check` were **not run**. `make` and cc65 are not installed on the machine used here. The repository names Linux CI (Ubuntu 24.04) as the required environment.
- The host unit suite (`py -3 -m unittest discover -s tests/host`) was run on the same Windows machine with PyYAML installed. Result: 3240 tests; 9 non-passing results (2 failures, 7 errors), all in the Windows-only frame-tracer and slot-allocator tests (`os.getuid` missing, symlink privilege, path semantics). No failing test reads `docs/evidence`. This PR changes only documentation files, so these results are environmental and are not claimed as PASS. Authoritative results are those of the required CI run.

## Not published in this PR

- `det-reset.patch` (implementation; separate PR candidate).
- `phase-c1-implementation.md`. Withdrawn from this evidence-only PR because it contains C code blocks. Its provenance row remains in `phase-c3-tests-r1.md` for traceability.
- The replay and run scripts under `phase-c-r1/scripts/` (they contain private path constants). The commands they ran are described in the test records above.
- The internal C1/C3 review report (`review-c1-c3.md`). `t2-history.md` cites it, and that citation is kept so the history is traceable. The report itself is not published.
- Build and configure logs, `c1-default-off-check.json`, `c3-full-stdout.txt`, the original (non-`-r1`) `synthetic_bios_manifest.json`.
- The internal `change-log.md` (it contains internal file hashes that do not match the published copies). The change record is in this README.

## Publication processing (what changed between the internal originals and the published copies)

The published copies were generated from the internal originals by a logged script. The per-file rule counts are in the internal log; the totals are:

| Change | Scope | Occurrences |
|---|---|---|
| Absolute path strings replaced with `<SCRATCHPAD>`, `<REPO>`, `<HOME>`, `<PROJECT_DIR>`, `<SESSION>`, `<USER>` | 7 files | 284 |
| Scratch-folder names replaced with `<SYNTH_MEDIA>` / `<SYNTH_MEDIA_ROOT>` | 10 files | 65 |
| CRLF line endings converted to LF (the repository stores LF only) | 12 files | 7480 lines |

Logged content edits (not path substitutions):

- `phase-c3-tests-r1.md` and `phase-c3-tests.md`: references to `phase-c1-implementation.md` marked as not published in this PR.
- `phase-c3-tests-r1.md`: citation `cdrom_cuebin_image.cpp:230-232` changed to `227-233` (1 occurrence).
- `phase-c3-tests-r1.md` and `rng-521-520.md`: status label `RESOLVED` changed to `ATTRIBUTED FROM SOURCE` for M-3, because per-call draws were not observed.
- `rng-521-520.md`: the quotation of the MPR loop corrected to the source form `do { m_mpr[i] = Next8Bit() } while (m_mpr[i] == 0x00)`, which rejects zero draws (`memory.cpp:106-118`).
- `phase-c0-audit.md`: a correction sentence added. The original audit said `get_reset_value` was not inspected; it was inspected later (`platforms/shared/desktop/utils.h:71-84`, index 1 returns `0x0000`).
- `phase-c3-tests-r1.md`: a publication note below the provenance table. The table values are for the internal originals; the note lists the files that changed bytes in publication.

Because of these edits, the SHA-256 values in the provenance tables of the R1 test records are the internal values. Only files whose bytes did not change keep the same hash. For the changed files, use `MANIFEST.sha256`. Each hash in `MANIFEST.sha256` is the SHA-256 of the file as committed (LF line endings).

After publication processing, the published copies were scanned for drive-letter paths, the user directory name, the session identifier, the private directory name, email addresses, references to `rom/` or reference firmware files, and reference firmware names. No matches remained other than the placeholders. Remaining `rom/` mentions are negated statements ("no `rom/`").

## Safety gates (unchanged)

- #291 remains `BLOCKED_ABI`.
- `release_eligible=false`.
- #173 gate: unchanged. Reference JSR permission: 0/81.
- No reference-BIOS A/B experiment. That step remains owner-gated and is not started.
