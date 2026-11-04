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
| `c1-default-off-check.json` | C1 default-OFF check: two launches without the test seed; post-reset states distinct; no test-only string in the run directory. Basis of the C1 PASS. |
| `c3-full-stdout.txt` | Stdout of the first full T1–T5 run (T2 FAIL, others PASS). |

## Corrections and history

1. **MAJOR (review): T4 source citation.** The earlier citation `input_inline.h:174-180` is the mouse branch. The standard pad path is `input_inline.h:204-218` (the `if (!m_clr)` block; the standard-pad branch is `:211-217`). Corrected in `phase-c3-tests-r1.md` and `phase-c3-expected-r1.json`. The content of the expectation was checked against source and is unchanged.
2. **T2: first run FAIL, then a source-based correction.** The first full run failed T2 because the seeded-model comparison included the raw draw for P. A, X, Y, S and MPR matched in all runs. Source (`huc6280.cpp:232-239`) shows reset clears T, D and B and sets I after the random draw, so the model was changed to mask those bits. **The T2 PASS after this change is a post-hoc corrected result**, not a pre-registered expectation. The first FAIL is preserved in `phase-c3-results-firstpass.json`. See `t2-history.md`. **Timeline (revision after the independent full review):** the pre-launch registration of the T2 expectation is not established. The first probe launch is recorded at 17:52; `run_c3.py` was last written at 17:53:29, after it, and the earlier version is not preserved. The earlier statement that the T2 expectation was pre-registered before the first launch is withdrawn. Its status is **UNKNOWN**.
3. **T4: historical probe output not preserved.** The original probe output was not saved and cannot be restored, so its result is **UNKNOWN**. A new T4 re-run under the same conditions passed. This re-run is **new evidence** and is recorded as such in `t4-evidence.md`. The earlier draft relation for SEL=0 was corrected from source after a probe run.
4. **521/520 random draw counts: attributed from source, not observed per call.** Init-time draws (SCSI 1, `Memory::Reset` 7 + r, colour table 512) reproduce the six recorded deterministic reset states. The individual draw calls were not observed directly. The colour-table values and the init-time MPR values were not compared with any record, so **full initial-state equality is not claimed**. See `rng-521-520.md`.
5. **M-6: CD image thread timing.** The cuebin read-ahead worker is compiled out in both builds. `GG_ENABLE_CDROM_CUEBIN_READAHEAD` is not defined by the pinned desktop CMake/Ninja build (`build/build.ninja` DEFINES, `wrapper/CMakeLists.txt`); it appears only in the libretro `Makefile`, which is not the build used here. `cdrom_cuebin_image.cpp:227-233` forces read-ahead off when the macro is undefined. The worker block (call at `:191-192`, guard `:190-193`, and the thread body inside the `#if` that runs from `:1465` to `:1656`) is excluded. The C-R1 review's statement that the synthetic CUE exercises that thread is **refuted**. Citation corrected in `phase-c3-tests-r1.md` from `230-232` to `227-233`.
   - CD controller snapshot fields: identical across two test-build runs and across two pinned runs. The pinned pair differs in CPU and MPR state because the pinned build uses a time seed; this is expected and is not a CD difference.
   - CD RAM (64 KB) state: **UNKNOWN**. This build exposes no CD RAM memory area through the available tools.
6. **Expectation timing (correction to the original record).** `phase-c3-tests.md` and `phase-c3-expected.json` state that expectations were written before any launch. That is **not accurate for the T4 relation, and pre-launch registration is not established for T2 either** (see item 2): a T4 probe run (17:52) preceded the expectation file (17:53:34). The probe and the timing are recorded in `t4-evidence.md`. The `t4-expected-r1.json` record gives a `written_utc` earlier than the first re-run launch. That timestamp is the experimenter's own record; there is no independent timestamp evidence, so the ordering is not independently verified.
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

The per-test commands were run on Windows from an internal working directory (not published), each under a timeout. The raw run directories, the internal `config.ini` copies and the full stdout/stderr logs are **not** published, because they contain private paths and are not needed to read the results. The published set is the summary and record files listed above, plus the T4 re-run stdout (`t4-rerun-stdout.txt`).

**Repository checks for this PR:**

- `make test`, `make test-real-core` and `make check` were **not run**. `make` and cc65 are not installed on the machine used here. The repository names Linux CI (Ubuntu 24.04) as the required environment.
- A Windows host unit-suite run was made during preparation. Its log is not published, so no pass count is claimed here, and its results are not evidence for this PR. The authoritative results are those of the required CI run.

## Not published in this PR

- `det-reset.patch` (implementation; separate PR candidate).
- `phase-c1-implementation.md`. Withdrawn from this evidence-only PR because it contains C code blocks. Its provenance row remains in `phase-c3-tests-r1.md` for traceability.
- The replay and run scripts under `phase-c-r1/scripts/` (they contain private path constants). The commands they ran are described in the test records above.
- The internal C1/C3 review report (`review-c1-c3.md`). `t2-history.md` cites it, and that citation is kept so the history is traceable. The report itself is not published.
- Build and configure logs, the original (non-`-r1`) `synthetic_bios_manifest.json`.
- The internal `change-log.md` (it contains internal file hashes that do not match the published copies). The change record is in this README.

## Publication processing (what changed between the internal originals and the published copies)

The published copies were generated from the internal originals by a script run during preparation. Its per-file counts are not published, so the counts cannot be re-verified from this repository. The changes are described below and can be checked against the committed files.

| Change | Scope | Occurrences |
|---|---|---|
| Absolute path strings replaced with placeholders (`<EVIDENCE_ROOT>`, `<REPO>`, `<HOME>`, `<PROJECT_DIR>`, `<SESSION>`, `<USER>`) | several files | counts not published |
| Scratch-folder names replaced with `<SYNTH_MEDIA>`, `<SYNTH_MEDIA_ROOT>`, `<SCRATCH_WORKER>`; the scratch root prefix replaced with `<EVIDENCE_ROOT>` and escaped separators normalised | several files | counts not published |
| CRLF line endings converted to LF (the repository stores LF only) | files that had CRLF in the internal originals | counts not published; checkable: the committed files contain no CR bytes |

Logged content edits (not path substitutions):

- `phase-c3-tests-r1.md` and `phase-c3-tests.md`: references to `phase-c1-implementation.md` marked as not published in this PR.
- `phase-c3-tests-r1.md`: citation `cdrom_cuebin_image.cpp:230-232` changed to `227-233` (1 occurrence).
- `phase-c3-tests-r1.md` and `rng-521-520.md`: status label `RESOLVED` changed to `ATTRIBUTED FROM SOURCE` for M-3, because per-call draws were not observed.
- `rng-521-520.md`: the quotation of the MPR loop corrected to the source form `do { m_mpr[i] = Next8Bit() } while (m_mpr[i] == 0x00)`, which rejects zero draws (`memory.cpp:106-118`).
- `phase-c0-audit.md`: a correction sentence added. The original audit said `get_reset_value` was not inspected; it was inspected later (`platforms/shared/desktop/utils.h:71-84`, index 1 returns `0x0000`).
- `phase-c3-tests-r1.md`: a publication note below the provenance table. The table values are for the internal originals; the note lists the files that changed bytes in publication.

Because of these edits, the SHA-256 values in the provenance tables of the R1 test records are the internal values. Only files whose bytes did not change keep the same hash. For the changed files, use `MANIFEST.sha256`. Each hash in `MANIFEST.sha256` is the SHA-256 of the file as committed (LF line endings). The internal-original hashes cited in the R1 records are provenance only; they cannot be verified from this repository, and no public claim rests on them.

After publication processing, the published copies were scanned for drive-letter paths, the user directory name, the session identifier, the private directory name, email addresses, references to `rom/` or reference firmware files, and reference firmware names. A final scan of the published files found no drive-letter paths, user or session identifiers, private directory names, email addresses, or reference-firmware names. The remaining placeholders (`<EVIDENCE_ROOT>`, `<SYNTH_MEDIA>` and similar) and run-directory labels are not private paths. Remaining `rom/` mentions are negated statements ("no `rom/`").

## Safety gates (unchanged)

- #291 remains `BLOCKED_ABI`.
- `release_eligible=false`.
- #173 gate: unchanged. Reference JSR permission: 0/81.
- No reference-BIOS A/B experiment. That step remains owner-gated and is not started.

## Revision history

The earlier text of this branch remains in its git history (commit `7ed4d53`). Nothing was erased; the changes below are recorded here.

- **T2 timeline.** The statement that the T2 expectation was pre-registered in code before the first launch is withdrawn. Pre-launch registration is **UNKNOWN**: the first probe is recorded at 17:52, `run_c3.py` was last written at 17:53:29, and no preserved record shows the P term earlier. The T2 PASS stays a post-hoc corrected result, and the first FAIL stays on record (`t2-history.md` §2, §7).
- **Historical labels.** The headings and labels "pre-registered … written before any launch" in `phase-c3-tests.md`, `phase-c3-tests-r1.md`, `phase-c3-expected.json` and `phase-c3-expected-r1.json` are kept as the original wording. For T4 they are not accurate (item 6), and for T2 pre-launch registration is UNKNOWN (item 2). Read them with those corrections.
- **Unverifiable hash.** The internal-original SHA-256 that `t2-history.md` cited for the first-FAIL file is no longer used as a verification value. The published copy's hash is in `MANIFEST.sha256`.
- **Published records.** `c1-default-off-check.json` and `c3-full-stdout.txt` are now published. They are the basis of the C1 default-OFF PASS and of the first-run summary, so those statements can be checked against files in this folder.
- **Citations.** The standard-pad range is `input_inline.h:204-218` (the `if (!m_clr)` block, with the standard-pad branch at `:211-217`); the earlier `204-217` cut off the closing brace. The cuebin call is at `:191-192` and its guard at `:190-193`.
- **Removed as unverifiable.** The placeholder and CRLF counts (their log is not published), and the Windows host test pass/fail numbers (their log is not published; no pass count is claimed).
- **Scratch residue.** The scratch root placeholder is now `<EVIDENCE_ROOT>`, escaped separators are normalised to `/`, and the scratch-tree references in the test records are reworded or generalised.
- **Inline source expressions.** A few one-line source expressions are quoted in the analysis (for example `while (m_mpr[i] == 0x00)`). They are citations of the pinned source for the analysis, not reproduced code blocks. No code block is present in this folder.

## Unpublished inputs and verification limits (R2)

The records below were **not** published. A SHA-256 value is shown only as the experimenter's record. None of these items is a publicly verified artifact, and no public claim in this folder rests on them alone.

| Item | Kind | Published? | SHA-256 as recorded | Reproducible from this repository? | Publicly verifiable? | Reason not published / note |
|---|---|---|---|---|---|---|
| Synthetic BIOS `synthetic_bios.bin` | synthetic input (own bytes; not a System Card BIOS) | No | `e122f015…` (also in the published `synthetic_bios_manifest-r1.json`) | No: its generator is not published | No (the record is published; the bytes are not) | Experimenter input. Not added in this PR. |
| Synthetic CUE/BIN | synthetic disc | No | CUE `a751990e25…`, BIN `7ba14aad…` (in the published `phase-c3-manifests.json`) | No | No (the record is published; the files are not) | Not added in this PR. Publication would need its own content review. |
| Deterministic-reset test binary | built from the pinned source plus the unpublished patch | No | `f773867a…` (in the published `c1-default-off-check.json`) | No: the patch is not published | Partly: the hash is published, the binary is not | Test build only. |
| Pinned Geargrafx binary | built locally from upstream commit `b49ae82a` | No | `4933885c…` | In principle, from the upstream commit and a local build; the build configuration is not published | No | Local build. |
| `det-reset.patch` | implementation | No | `3df2af97…` | Not applicable | No | Separate PR candidate. |
| Original (pre-publication) T4 expectation file | experimenter record, used before the T4 re-run | No | `d2373258…` | Not applicable | No | The published copy `t4-expected-r1.json` (SHA-256 `60d1309d…`) differs in line endings and in one citation string; the expected values are identical. |
| Original non-`-r1` synthetic BIOS manifest | experimenter record | No | `c498afa2…` | Not applicable | No | The `-r1` copy is published. |
| Original C1 check record | experimenter record | No | `7ef497ee…` (1291 bytes) | Not applicable | No | The published copy `c1-default-off-check.json` (SHA-256 `a628d52f…`, 1235 bytes) differs in 56 CRLF line endings after LF normalisation, according to the experimenter's internal comparison (self-report; not reproducible from this repository). The byte counts 1291 − 1235 = 56 are consistent with that. |
| Raw run directories, `config.ini` copies, full stdout and stderr logs | experimenter records | No | not cited | Not applicable | No | Contain local paths. |
| C1 build log, review report, replay and run scripts, reeval and rng simulation scripts | experimenter records | No | not cited as verification | Not applicable | No | Contain local paths or are internal reports. |

**What the published files alone cannot re-run or independently verify:**

- The T1–T5 raw outputs. Only the summaries, the first-run stdout (`c3-full-stdout.txt`) and the T4 re-run stdout (`t4-rerun-stdout.txt`) are published.
- Any result that depends on the synthetic inputs or binaries above.
- The C1 build result (the build log is not published).
- CD RAM state (UNKNOWN).
- Pre-launch registration of the T2 expectation (UNKNOWN).

## Hash status (R2)

- **Published copies.** Use `MANIFEST.sha256`. Each value there is the SHA-256 of the committed file.
- **Internal originals.** Values in the provenance tables and in `t4-evidence.md` are the internal originals' hashes. Where a published copy differs, the difference is stated above; the T4 and C1 cases were checked by comparing the published and original contents.
- **Sanitised stdout (R3).** `c3-full-stdout.txt` differs from its internal original only in 5 CR bytes (experimenter's internal comparison; not reproducible here). `t4-rerun-stdout.txt` is a sanitised copy: only its first line changed (path separators and the placeholder were normalised); the result lines are identical. The SHA-256 `d2373258…` in that line identifies the unpublished original expectation file; the experimenter's internal copy matches it, but it cannot be checked from this repository.
- **phase-c3-expected-r1.json (R3).** Differs from its internal original in line endings, in the R2 `label` field, and in the T4 citation string (`204-217` to `204-218`, branch `211-217`). The expected values are unchanged.
- **Line-ending conversion.** The 56-byte difference for the C1 check is consistent with exactly 56 CRLF line endings removed. The experimenter's internal comparison (self-report, not reproducible here) found the two files identical after LF normalisation; the byte counts are consistent with it. The cause is therefore not independently verified. The first-run stdout differs by 5 bytes (89 → 84), consistent with 5 CRLF.

## Revision history (R2)

The earlier text is kept in git history (commit `7ed4d53`). R2 corrected the following. Nothing else changed in the results.

- **M1 (T2 pre-registration).** The headings and labels that said the expectations were pre-registered before the first launch were corrected in place: `phase-c3-tests.md` and `phase-c3-tests-r1.md` (section "Expectations"), and the `label` field in `phase-c3-expected.json` and `phase-c3-expected-r1.json`. The correction says the T4 relation was probed before the file was written, and that pre-launch registration of the T2 expectation is UNKNOWN.
- **M2 (T4 hashes).** `t4-evidence.md` and `t4-rerun-r1.json` now state that `d2373258…` is the unpublished original used for the run; the published copy is `60d1309d…`, and the expected values are identical. `c498afa2…` is labelled unpublished and unverifiable.
- **M3 (C1 hash).** The publication note now lists the table files whose published bytes differ from the internal originals, with the published SHA-256 for the C1 check.
- **M4 (unpublished inputs).** The table above lists each unpublished input and what can and cannot be verified.
- **M5 (residue).** The `path_kind` labels in `run-manifest.json` now read `evidence-root-relative`. The README no longer names the internal working directory as a location; the "no matches" statement now describes what the scan checked.
- **M6 and LOW.** The unverifiable CRLF count was removed. The `t4-rerun-stdout.txt` separators were normalised. The standard-pad citation is `204-218` (branch `211-217`) in the records that cited `204-217`. The C1 build line is marked as not verifiable publicly.

## Referenced files that are not published (R2)

Some records name files that are not in this folder. Each is listed here so that the reference is not mistaken for a published artifact.

- Also unpublished (named in the records): `run_c3.py`, `default_off.py`, `manifests.py` (scripts); `phase-c3-tests.md.sha256` (a checksum file for an earlier record); `diffs/synthetic_bios_manifest-r1.diff` (an internal edit diff). Their names or hashes appear in the records and cannot be checked from this repository.
- Unpublished records and scripts: `cd-measure-stdout.txt`, `cd-measure-stderr.txt`, `t4-rerun-stderr.txt`, `stderr.txt` (raw logs); `env-manifest.md` (environment record); `make_synthetic_bios.py`, `reeval_t2.py`, `rng_sim.py`, `r1_runs.py`, `r1_manifest.py` (scripts; not published, see the list above); `media-hashes.json` (a block of `phase-c3-manifests.json`, which is published, so the hashes are in a published record).
- Unpublished synthetic inputs: `synthetic_cd.cue` and `synthetic_cd.bin` (see the table above).
- Upstream source citations: `cdrom_media.cpp`, `media.cpp`, `random.h`, `mcp_server.cpp`, `trace_logger.cpp`. These are files of the pinned upstream tree (commit `b49ae82a`). They are cited for their code and are not evidence files.

## Hashes inside the run records (R2)

Many run records contain digests that are not publicly verifiable, because they describe unpublished artifacts:

- `run-manifest.json`, `phase-c3-manifests.json` and `phase-c3-results*.json`: digests of per-run `config.ini` copies, binaries and inputs (the copies are not published; the binaries and inputs are in the table above).
- `cd-measure-r1.json`: digests of memory and state snapshots. The snapshots are summarised, not published in full.
- `synthetic_bios_manifest-r1.json`: the synthetic BIOS digest (unpublished input).

These digests are experimenter records. They support internal consistency but are not public verification values. The only digests that a reader can check against the files in this folder are those in `MANIFEST.sha256`, plus the published copies stated in the revision history.

## Revision history (R3)

Earlier text is kept in git history (commit `7ed4d53` and its successors). R3 changed no experimental result. Corrections:

- **MEDIUM-1.** The CUE SHA-256 prefix in the unpublished-input table was wrong (`a7519900…`); it is `a751990e25…`, as in `phase-c3-manifests.json` and `phase-c3-tests-r1.md`.
- **MEDIUM-2.** The C1 statements overclaimed. The status is now: the C1 default-OFF check is recorded in the published `c1-default-off-check.json`. The equality with the internal original (identical after line-ending normalisation) is the experimenter's self-report and is not reproducible here. The C1 build binary and build log are not published and are not verified by hash.
- **LOW-1.** The publication note now distinguishes line endings from content: `phase-c3-expected.json` changed in line endings and in the R2 `label` field; `phase-c3-manifests.json` changed in line endings only.
- **LOW-2.** The stdout copies are identified as sanitised, and the `d2373258…` line is explained.
- **LOW-3.** The historical T4 probe result is UNKNOWN in both test records, not an observation.
- **LOW-4.** The T2 PASS is labelled post-hoc and not demonstrated as pre-registered in both test records.
- **LOW-5.** The T4 expectation ordering rests on the experimenter's own `written_utc`; no independent timestamp evidence exists.
- **LOW-6.** The flag-operation citation is `huc6280.cpp:232-239` in all records, including the result file's description string (line 202, a text label, not a result value).
- **LOW-7.** Files named in the records but not published are now listed.
