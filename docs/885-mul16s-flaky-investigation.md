# Issue #885 investigation: MA_MUL16S `T5_mpr_change` nondeterministic rejection classification

- Issue: #885 (follow-up of PR #883 / #802; related #89 / #494 / #479).
- Investigation branch: `agent/885-mul16s-ci-flaky-20261128` (from `origin/main` `672be798`).
- Failing input: PR HEAD `4b94eecb67ffab37a44905f892d828cb5493ba62`, Run 46129256183 attempt 1, job `bios-dev-full` `140930855394`.
- Rerun success: same run, attempt 2, job `bios-dev-full` `140945000649`.
- Scope: synthetic fixtures only, pinned toolchain/core. No `./rom/` access, no commercial media, no production/ABI change, no gate/expectation change. This file is the only repo change.

## 1. Failure signature (exact CI context)

Attempt-1 log (`7_bios-dev-full.txt`), step `Real Geargrafx System Card diagnostic execution`:

```text
2026-11-28T09:16:38.7669614Z python3 tests/integration/geargrafx_dev_mul16s.py --rom "build/debug/bios-dev/dev-only-not-compatible-syscard3.pce"
... (probe compile warnings, --)
2026-11-28T09:16:42.1609829Z PASS 13254 calls in 18 runs (caller SP $0E, $40, $FF), write/read trace clean, 3s
2026-11-28T09:16:43.2949089Z DEV MUL16S integration FAIL: mutant T5_mpr_change was NOT rejected as ['mpr'] (seen: ['halt'])
2026-11-28T09:16:43.3039100Z make: *** [Makefile:192: dev-integration] Error 1
```

So all 18 default table runs passed (`PASS 13254 calls`), the unmutated control run passed (the failure names T5, which runs after control), and only the `T5_mpr_change` mutant verdict differed. No `T5` detail (pc/steps/first-message) is logged by the driver on this path — only `sorted(required)` vs `sorted(errs)`.

Attempt-2 (rerun, same HEAD) for the same step:

```text
2026-11-28T10:19:20.5640590Z PASS 13254 calls in 18 runs (caller SP $0E, $40, $FF), write/read trace clean, 3s
2026-11-28T10:19:21.6665585Z PASS mutants rejected 20/20: M1, M2, M3, M4, M5, M6, M7, M8, M9, M10, T1, T2, T3, T4, T6, T7, T8, T9, T10, T5
```

ROM determinism: the DEV ROM SHA-256 in both CI attempts (`2589facb71dac90f321f1ddb5b1128bc745184d71e5dd00890ae1dc9f1750f81`, from the BCDBIN sweep evidence blocks in both logs) matches a local rebuild at investigation HEAD exactly (same SHA locally). Pinned revisions: cc65 `71746c829`, Geargrafx `b49ae82a`, actionlint `v1.7.7` (`tools/dependencies.json`).

## 2. Mutant / probe / verdict path (read-only review)

All paths below are at investigation HEAD (`tests/integration/geargrafx_dev_mul16s.py`, `tests/integration/geargrafx_mul16s_probe.cpp`).

- `mutants()` defines `T5_mpr_change` as a trampoline mutant: patch ROM offset `body+3` (`body_end = window[0] - 0xE000 + 3`, i.e. CPU `$E777` for window `$E774-$E7FE`) with `JMP trampoline`, and place payload `08 A9 F8 53 04 28 60` at the trampoline address. Decoded: `PHP; LDA #$F8; TAM #$04; PLP; RTS` — unconditionally sets **MPR2 := `$F8`**, then returns with an unbalanced stack (the routine prologue already pushed P/Y), so the guest hangs off-rails instead of reaching its halt loop. trampoline placement is deterministic (`find_trampoline_address` → `$E0F3` locally; region verified all-`$FF`, below `$E800` probe overlay, outside all `dev.map` segments).
- `trace_config(c=0x40, window, extra=0x100, trampoline_range=($E0F3,$E0FA))` extends the probe window to `$E0F3-$E8FE`, so the trampoline TAM executes **inside** the window. The `TAM` is at `$E0F6`.
- The probe single-steps the pinned core (`step_debugger`), and after every instruction compares all 8 MPRs against a baseline (`initial_mpr`, captured after the first `SEI`). An inside-window difference increments `mpr_changes` and prints `MPRCHG`. This observation is **value-sensitive**: a same-value write produces no event.
- `check()` reports kind `mpr` only from positive evidence: non-empty `mprchg` lines, `summary.mpr_changes > 0`, a `loaded`-vs-`final` MPR diff on indices `(0,2,3,4,5,6,7)` (MPR1 excluded — the guest legitimately sets `MPR1=$F8`), or a failed absolute `MPR1/MPR7` check. Kind `halt` is reported when the probe exhausts its budget (`max = 366*1000+5000 = 371000` steps for the 366-entry reduced table) without reaching the halt PC.
- The verdict (`main().do_mutant`) is `if not errs or not (set(errs) & required)` with `required={'mpr'}` for T5. Normal T5 outcome is `errs == {'halt','mpr'}` (hang **plus** MPR evidence). The CI failure `seen: ['halt']` means the run hung as designed but produced **zero** MPR evidence of any kind.

## 3. Candidate mechanism: time-seeded power-on MPR randomization can mask the T5 write (theoretical 1/255 under nonzero-uniform assumption)

The failing first-CI attempt logged no MPR values, so the power-on `MPR2` value at that time is UNKNOWN and the natural cause of the first-CI failure is UNDETERMINED. What the PoC in Sec. 6 shows is a reproducible failure mechanism under a forced condition, not a settled natural cause.

Pinned Geargrafx core (`src/memory.cpp`, `src/geargrafx_core.cpp`, `src/random.h`):

- `GeargrafxCore::Init` seeds `m_random` with `(u32)time(NULL)` — a new, wall-clock-dependent seed per probe process.
- `Memory::Reset` sets `m_mpr[7] = 0x00` deterministically but fills `m_mpr[0..6]` with random **non-zero** bytes (`do { Next8Bit(); } while (== 0x00)`), i.e. code reads as uniform over `1..255` (nonzero-uniform assumption used for the theoretical rate below; the actual distribution was not measured).
- Nothing in the mul16s probe, test driver, or tools pins these values (`SetResetValues` is never called on this path).

Consequence for T5: the payload writes `MPR2 := $F8`. If the power-on roll already left `MPR2 == $F8` (theoretical probability **1/255 ≈ 0.4%** per T5 probe execution **under the nonzero-uniform assumption only; the measured natural rate is UNKNOWN**):

1. the TAM is a same-value write → `mpr_changes` stays `0`, no `MPRCHG` line;
2. `final.MPR2 == loaded.MPR2 == $F8` → the `loaded`-vs-`final` diff path stays silent (MPR1/MPR7 absolute checks still pass);
3. the designed hang still yields kind `halt`;
4. verdict computes `{'halt'} ∩ {'mpr'} = ∅` → exactly `was NOT rejected as ['mpr'] (seen: ['halt'])`.

On the reviewed oracle paths, no other mutant shows the same value-sensitivity: M1–M10 expect value/trace verdicts; T1–T4/T6–T10 are observed via value-agnostic CPU-address breakpoints (a same-value store or a write-and-restore still trips the breakpoint flag), and none of them touches MPRs. Default/control runs never expect `mpr`, and the routine/guest never touch MPR2, so a `$F8` roll produced no visible effect there in the observed runs — consistent with the failed CI run passing everything except T5 (first-CI MPR values remain UNKNOWN).

## 4. Hypotheses tested

| Hypothesis | Method | Result |
|---|---|---|
| Halt-vs-MPR observation order | Code review + local T5 trace: TAM fires at step ~808 of a 371000-step budget; MPR is polled after every single-stepped instruction | Not needed to explain the observed PoC on this evidence scope: ordering gives MPR ample opportunity; normal runs show both `halt` and `mpr` |
| Budget/timeout boundary | Compared budgets: T5 exhausts `max` (371000) **by design** in both pass and fail shapes; `records: 0` in both | Not discriminative; T5 always ends in budget exhaustion |
| Cold/warm init state | The mul16s probe performs a single boot per run; no cold/warm phases exist on this path | N/A to this test |
| Pinned core state transitions / CD media | Media `IsReady` is a synchronous bool set at `LoadMedia`; synthetic media bytes are fixed; ROM SHA identical across attempts and local rebuild | No evidence for this path in the reviewed code/logs on the observed PoC scope (unlike the BCDBIN sweep's documented unchecked-P-bit path dependence, which concerns P, not MPR); not excluded generally |
| Parallel-load / tmpfile / ROM-generation races | Review: per-thread unique `g{n}`/`run{index}` files, per-run ROM copies, shared trampoline address but disjoint payloads; plus serial PoC below reproduces without any parallelism | Not needed to explain; no evidence of collision |
| Mutant reachability / window coverage | Verified locally: JMP at `$E777` → trampoline `$E0F3`, TAM at `$E0F6` inside window `$E0F3-$E8FE`; normal runs record `MPRCHG {pc 57590 ($E0F6), mpr 2}` | Reachable and covered; observed to produce `halt`-only under the forced same-value condition in the PoC (natural-case exclusivity not established) |
| Power-on MPR2 == `$F8` collision | Empirical MPR variance + forced-condition PoC (Sec. 5–6) | **Reproducible mechanism under the forced condition; exact CI signature regenerated in the PoC; first-CI natural cause UNDETERMINED** |

## 5. Bounded repeats (same fixture/condition)

Environment: local worktree at `672be798`, `TOOLS_DIR` with pinned cc65/Geargrafx builds, ROM SHA `2589facb…f1750f81` (matches CI). No `./rom/` used at any point.

- Full `geargrafx_dev_mul16s.py` (18 default runs + control + 20 mutants), 7 iterations: **7/7 PASS** (`20/20` mutants each time).
- T5-only driver (same reduced 366-entry table, `c=$40`, same patches/window): 3 iterations: **3/3** `errs == ['halt','mpr']`. Loaded (power-on) MPRs varied every run, confirming nondeterminism, e.g. MPR2 ∈ `{13, 238, 43}` with MPR7 always `0`; normal T5 record: `mpr_changes: 1` at `pc $E0F6 (mpr 2)`, `steps: 371000`, `records: 0`, hang `pc $14ED`.
- Total: 10 bounded real-core iterations (7 full + 3 focused). Zero natural occurrences were observed locally (0/10). Under the nonzero-uniform assumption **plus an independence assumption**, the theoretical chance of ≥1 collision in 10 trials is 1-(254/255)^10 ≈ 3.85%; the `time(NULL)` seed can correlate closely-spaced trials so independence is unverified, and the measured natural rate is UNKNOWN.

## 6. Controlled-condition reproduction (PoC, `/tmp`-only, not merged)

To test the Sec. 3 mechanism end-to-end, a copy of the probe was built in `/tmp` with one added line after `LoadMedia` — `SetMpr(2, 0xF8)` — faithfully simulating the unlucky power-on roll (loaded OBS then shows `MPR2=$F8`, baseline identical). The unmodified T5 driver was run against this PoC probe, serially:

```text
forced-loaded mpr: [133, 222, 248, 139, 126, 255, 217, 0]
final mpr:         [133, 248, 248, 139, 126, 255, 217, 0]
mpr_changes: 0  mprchg lines: 0  steps: 371000  final_pc: 0xF009
errs kinds: ['halt']
DEV MUL16S integration FAIL: mutant T5_mpr_change was NOT rejected as ['mpr'] (seen: ['halt'])
```

The regenerated message is character-identical to the CI failure. (Hang PC differs from the normal `$14ED` — `$F009` here — because the MPR2 mapping demonstrably steers the post-mutant garbage execution; the verdict-relevant fact is `halt`-only either way.) PoC sources/binary live only in `/tmp/poc*` and are intentionally **not** part of this branch.

## 7. Verdict

- **Same signature reproduced under the forced condition**: with power-on `MPR2` forced to `$F8`, the PoC run produced the exact CI failure verdict `['halt']` from the normal T5 verdict `['halt','mpr']` (observed PoC scope only; generalization beyond the forced condition not established). The 1/255 figure is a theoretical value under the nonzero-uniform assumption, not a measured rate. The first-CI power-on `MPR2` value is UNKNOWN, so the first-CI natural cause is UNDETERMINED.
- **Natural occurrence NOT observed** in 10 bounded local iterations; per the task contract this is therefore recorded without a production/test-expectation change: **no fix is made here** (no `halt` allowance, no strictness reduction, no mutant deletion/skip, no budget bump, no gate change, no #882/#857 touch).
- Strictly, the natural flake remains an unobserved event in 10 bounded local trials (0/10 natural occurrences); the PoC evidences a reproducible failure mechanism (exact-signature reproduction under the forced condition plus observed MPR variance in normal runs), but the measured natural rate is UNKNOWN and no repo behavior was altered on this basis.

## 8. Open items (for owner)

1. Decide the harness strategy for value-sensitive MPR oracles under randomized power-on state. Options (not implemented here): (a) pin MPR reset values for this probe via `SetResetValues` so `MPR2 != $F8` deterministically — note blast radius (all MPR observations on this path assume randomization); (b) add a value-agnostic MPR-write watch (e.g. extend the breakpoint harness to TAM writes, which transfer A to an MPR; TMA reads, which transfer an MPR to A, are the converse direction and do not observe MPR writes), making T5 robust like the trace mutants; (c) keep randomization and accept theoretical ~1/255 (nonzero-uniform assumption only; measured rate UNKNOWN) T5 flakes with a documented rerun policy (NOT recommended: masks signal). Any of these needs owner review plus RED→GREEN demonstration; (a)/(b) must prove no weakening of the `mpr` oracle.
2. Diagnostic-only improvement (no gate change): log the T5 `first`/pc/steps and loaded/final MPRs in the driver output or evidence JSON so the next occurrence is classifiable from the log alone (the failing CI line carried none of this).
3. Consider recording per-mutant `mpr_changes`/MPR vectors in `geargrafx-dev-mul16s-evidence.json` (already records kinds; vectors would let a future flake be checked against the Sec. 3 candidate mechanism post-hoc).
4. If a natural recurrence appears, capture the full probe stdout (`MPRCHG`/`SUMMARY`/`OBS`) for that mutant run before rerunning.

## 9. Compliance notes

- Pinned revs honored (cc65 `71746c829`, Geargrafx `b49ae82a`); `./rom/` never accessed; no proprietary bytes in logs/artifacts (only SHA-256, PCs, MPR values, step counts).
- Banned actions avoided: `halt` is not accepted for `mpr`; rejection strictness untouched; no mutant deleted/skipped; no reruns-as-success claimed (10 bounded iterations reported with outcomes); no budget bump; no production-code guess-fix; no release/ABI statement change.
- Single writer: new branch from `origin/main`, new worktree; only this new file is added.
