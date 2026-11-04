# T2 history: seed-dependence test and the P-flag term (C-R1, Issue #291, review item M-2)

Scope: synthetic BIOS and synthetic CUE only. No reference BIOS, no `rom/`, no reference JSR. Nothing in this document is a System Card or commercial-game claim.

## 1. What T2 tests

T2 (`phase-c3-tests.md`, "T2 (different seeds)") asks whether seed A (`0x00C0FFEE`) and seed B (`0x0BADF00D`) give different reset CPU/MPR states, and whether each deterministic run matches the xorshift32 draw-order model.

## 2. Initial expected model (pre-launch registration: UNKNOWN)

- The P term of the initial model (line 232 of the `run_c3.py` version that was later overwritten): `p = (L[k + 1] >> 24) & 0xFF`. The expected P is the raw `Next8Bit` draw with no flag masking.
- **Timeline correction.** An earlier version of this section said the model was pre-registered in code before the first launch. That statement is withdrawn. The first probe launch is recorded at 17:52. `run_c3.py` was last written at 17:53:29, after the probe, and its earlier version is not preserved. `phase-c3-expected.json` was written at 17:53:34, also after the probe. Its T2 text says only "each det run matches xorshift32 draw-order model" and does not state the P term. No independent record shows the P term before the first launch. **Pre-launch registration of the T2 expectation: UNKNOWN.**
- Other fields of the model (A, X, Y, S from the 32-bit draw after 512 colour-table draws; MPR from the first seven non-zero draws) are at `run_c3.py:208-237`.

## 3. First full run: FAIL (historical record, kept)

- Command: `timeout 1200 py -3 synthetic/run_c3.py full`. Stdout (published as `c3-full-stdout.txt`):

  ```
  T1_reproducibility PASS
  T2_seeds FAIL
  T3_trace PASS
  T4_input PASS
  T5_isolation PASS
  ```

- Records in `phase-c/phase-c3-results-firstpass.json` (`tests.T2_seeds`, `runs.det_*`):
  - Seed A (`det_S1_r1..r3`): `rnd_index` 1040, `mpr_first_index` 521. Predicted P 209 (0xD1), observed P 197 (0xC5). A, X, Y, S and MPR matched.
  - Seed B (`det_S2_r1..r3`): `rnd_index` 1039, `mpr_first_index` 520. Predicted P 120 (0x78), observed P 68 (0x44). A, X, Y, S and MPR matched.
  - `cpu_regs_match_model` false for all six det runs. `differing_fields` A, X, Y, S, P, MPR. `result` FAIL.
- The mismatch was in P only. This first FAIL is preserved in `phase-c3-results-firstpass.json` (the internal original is not published, and its hash is not cited as a verification value; the published copy's SHA-256 is in `MANIFEST.sha256`). It is not overwritten by the correction below.

## 4. Source-based reason for the change

The reset path in the pinned source (`src/huc6280.cpp`):

- `:221` `m_P.SetValue(m_random->Next8Bit());` the raw P draw (the model's expectation).
- `:232-236` `#if defined(GG_TESTING) SetFlag(FLAG_TRANSFER); #else ClearFlag(FLAG_TRANSFER); #endif`. In both builds `GG_TESTING` is not defined (`build.ninja` DEFINES of the pinned and test builds), so T is cleared.
- `:237` `ClearFlag(FLAG_DECIMAL)`.
- `:238` `SetFlag(FLAG_INTERRUPT)`.
- `:239` `ClearFlag(FLAG_BREAK)`.
- Flag values `src/huc6280.h:30-37`: I = 0x04, D = 0x08, B = 0x10, T = 0x20.

So the post-reset P is `(P_raw & ~(0x20|0x08|0x10)) | 0x04 = (P_raw & ~0x38) | 0x04`.

Line range note: the earlier records cite `huc6280.cpp:232-240`. Line 240 is `m_cycles = 0`, which is not a flag operation. The flag block is `232-239`. This is a citation width issue only; the content is unchanged.

The review (`review-c1-c3.md`, not published, M-2) confirmed the cited flag lines. This document reproduces the arithmetic:

| Seed | Raw draw | Mask model | Observed | Check |
|---|---|---|---|---|
| A | 0xD1 | 0xD1 & 0xC7 = 0xC1, \| 0x04 = 0xC5 | 0xC5 (197) | match |
| B | 0x78 | 0x78 & 0xC7 = 0x40, \| 0x04 = 0x44 | 0x44 (68) | match |

What the two samples cover: seed A has bit 0x10 set and bits 0x08 and 0x20 clear in its raw draw, so it shows B cleared and I set, but cannot show D or T being cleared. Seed B has 0x08, 0x10 and 0x20 set, so it shows all three clears and the I set. Both samples are consistent with the same mask.

## 5. Corrected expectation

`synthetic`-side corrected rule (`synthetic/reeval_t2.py:37-41`): `p_model = (p_raw & ~0x38) | 0x04`, with the same stream-index logic as before (`k = i_last + 1 + 512`). The corrected expectation is recorded in the -r1 documents (`phase-c3-tests-r1.md`, Model corrections item 2).

## 6. Was a re-run done, and what it shows

- **No new emulator launch was made for the correction.** `synthetic/reeval_t2.py` (written 17:54:58) recomputed T2 from the saved per-run states in `phase-c3-results-firstpass.json`, and wrote `phase-c3-results.json` (17:55:01). That file holds `tests.T2_seeds.result = PASS` with a `model_revision` field pointing at this correction.
- The first-pass results file is kept unchanged.
- C-R1 check (no launch): `phase-c-r1/scripts/rng_sim.py` (script not published) runs the source-order draw simulation and checks A, X, Y, S, MPR and the masked P for all six det runs (`phase-c-r1/rng-sim-result.json`). All six match. The raw-P model fails for all six. This is a second computation from source, not a new observation.

## 7. Classification (honest)

- **T2 result:** PASS **after a post-observation, source-based model correction**. It is not a pre-registered PASS. Pre-launch registration of the expectation: **UNKNOWN** (not established).
- The first full run was FAIL and stays FAIL in the historical record.
- Evidence for the mask: two raw draws (0xD1, 0x78) across two seeds. The mask is justified by the source; the two samples agree with it.
- Would settle it as a pre-registered PASS: a new seed (with the P draw written to the expected file before launch) whose raw P has bits 0x08, 0x10, 0x20 and 0x04 in a combination not yet seen. This was not run in C-R1 (no new launch was requested or made for T2).

## 8. Summary line

T2: first full run FAIL (P term, seeds A and B, observed 197 and 68 against predicted 209 and 120). Source correction `(P & ~0x38) | 0x04` (`huc6280.cpp:232-239`), re-evaluated without launch, PASS. Post-observation; pre-launch registration UNKNOWN.
