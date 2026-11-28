# #861 L4: desync/cold/warm synthetic real-core run — false-ok RED confirmation (Issue #844 FOLLOW_UP)

Status: **synthetic real-core evidence only — no BIOS change, no ABI change, no release impact.**
`release_eligible=false`. Genuine ABI stays BLOCKED (`$2B` genuine semantics UNKNOWN/BLOCKED).
`G5_OBSERVED=false`. Reference JSR 0/81, genuine ABI VERIFIED 0/81 unchanged
(real JSR `$E081` only through the experimental table entry, never a reference
callee, never a fake RTS). ROM 256 KiB, `$E800`/`$FFF6` layout guarded by the
existing `$2B` builder pins (unchanged). ALLOW/DENY, provenance/artifact guard
unchanged. No commercial bytes, titles, paths, hashes, sector contents, audio,
saves, firmware bytes, or `./rom/` access of any form (no existence check,
listing, or read) were used. Commercial occurrence of the false-ok path is
**UNOBSERVED** (source-derived risk, never presented as fact). The project is
not yet a compatible System Card BIOS; nothing here implies otherwise.

- Issue: #861 (follow-up of #844 / PR #859 survey-only SHA `ce3726f9`).
- Target: origin/main `b743a8c2` at branch time (this PR records its own HEAD SHA
  in the PR body + CI).
- Branch: `agent/861-crshadow-desync-20261128` (single writer; single PR for 1 Issue).
- Scope: synthetic media + synthetic core only. No `src/` behavior change
  (only test + probe + docs added). PR #856/#857 scope untouched, no CI
  sharding change (new cases live in the existing `test_g5_rcroff_2b` module
  already in shard `rc-3`; no manifest edit), no ABI promotion, no gate
  relaxation, no dummy RTS.

## 0. Evidence-class legend (same as #844 §0)

- **SOURCE_CONFIRMED** — read directly from tracked source/arithmetic in this tree.
- **SYNTHETIC_REPRODUCED** — reproduced by the synthetic real-core runs in this PR.
- **HYPOTHESIS** — conditional reasoning explicitly marked as unproven.
- **UNKNOWN** — value/state never measured in-tree; no guess is made.
- **UNOBSERVED** — risk whose commercial occurrence was never observed; never stated as fact.

## 1. Grounds (why this run exists)

`docs/844-crshadow-survey.md` §9-L4 proposed (HYPOTHESIS-level design) seeding a
desynced shadow + residue `$A5` on a synthetic core and asserting the wrong-image
CR write lands (RED) to confirm the L2 host model end to end. L1–L3 stand on
their own; this is future independent work, not a merge blocker for #844.
Acceptance (#861 body): seed shadow `$FF` + marker `$A5` with real CR `$00`
through `$2B`; expect `CR=$FB` (wrong-image publish). Record run against its own
SHA with sanitized structural record; must not gate #844 L1–L3 findings.

Source grounds reused (SOURCE_CONFIRMED, vs `src/ex_cr_family.s` single-source):
gate `lda $2503 / cmp #$A5 / bne cr_reject` (reject: SEC, no write anywhere);
ok path `lda $2502 / and #$FB / bra cr_tail_publish` (shared tail publishes
`sta $2502` BEFORE `sta a:$0002`, restores MPR0, CLC). Marker `$2503` never
written by any head/tail; reject writes nothing. Full pins in
`tests/host/test_844_crshadow_survey.py` + `test_g5_rcroff_2b.py::test_2b_body_bytes_pinned`.

## 2. Method (what was built)

- New probe `tests/integration/geargrafx_rcroff2b_desync_probe.cpp` (test tool
  only, never linked into any BIOS/ROM): separates the shadow image from the
  real VDC CR value. Guest seeds `$2502=shadow_lo` + `$2503=marker` (explicit
  byte argv; the retired `seed_en?$A5:$00` encoding could never seed `$5A`,
  so the old `$5A` test exercised marker `$00` while the fixed-`$00` reject
  oracle could not tell — blind spot fixed per review 6096579529),
  then programs the REAL VDC CR low=`real_lo` (independent of the shadow) +
  high=`crhi` + RCR=`rcr_lo` sentinel through the window BEFORE the JSR, so
  sync vs desync is constructed, never inferred. Snapshots VDC `R[0..19]`+AR+SR
  +MPRs+IDR/IRR at the real call boundary (`PC==jsr_pc`) and after the halt.
  At the boundary the probe also records observed pre-JSR WRAM
  `wram_pre=[$2502,$2503]` (must equal the argv seeds when seeding
  dominates); `shadow_pre`/`real_pre` in RESULT remain the argv echoes for
  oracle derivation, `wram_pre` is the observed counterpart.
  Runs cold then warm (`ResetMedia(true)` between, RAM preserved like the
  return probe) and reports one `RESULT` per cycle with
  `cycle/shadow_pre/real_pre/wram_pre` (echoes + observed pre-JSR WRAM).
  Frozen clock (`dummy_clock`), `GetState` reads only (no destructive VDC read).
- New cases in the existing module `tests/integration/test_g5_rcroff_2b.py`
  (`Rcroff2bSynthetic`, already in CI shard `rc-3`; no sharding change):
  desync false-ok RED, sync positive control, marker-corrupt + `$5A` reject,
  non-target bits, AR/RCR/IRQ/SP/MPR/regs, strict real-vs-shadow (M_NOVDC vs
  M_NOPUBLISH separation), desync mutant detection, explicit cold/warm equality,
  argv fail-closed guard. Each asserts cold==warm for the security-relevant
  state (VDC CR, shadow, regs).
- Pinned dependencies (SHA/input/env pinning): Geargrafx
  `b49ae82a012eade566d72e9d61c9d95a5caa4da3` (via `tools/dependencies.json`,
  same pinned core + debugger build as the `$2B` suite), cc65
  `71746c829e77f74c2b601a7d16821170c2610df3` (builder refuses unpinned
  toolchain), synthetic 32-sector zero-filled CUE media built in-test,
  256 KiB ROM (`0x40000` size guard in probe), guest fits before `$FFF6`
  (probe refuses otherwise), return-probe `BUDGET=20M` steps (host-side
  APICALL harness) vs desync/VDC probe `steps=4096` instructions per cycle
  (in-guest argv, fail-closed guards cover out-of-domain values),
  `SP=$FF`, `MPR1=$F8`,
  `entry_ar/areg/xreg/pseed` fixed per case (recorded in RESULT).
- No `src/` edit. No commercial media. No `./rom/` access of any form.

## 3. Conclusion (what the core showed — SYNTHETIC_REPRODUCED)

- **Desync false-ok RED confirmed**: real CR `$0B00` + shadow `$FF` + marker
  `$A5` via `$2B` → `CR=$0BFB` (low `$FB`), shadow `[$FB,$A5]`, `C=0`, `AR=5`,
  on BOTH cold and warm (identical `vdc_r_after` + `shadow_post`). The ok path
  published the stale image; every set bit in `$FB` except the cleared bit2
  comes from the stale shadow (real was `$00`). Hypothesis `CR=$FB` held —
  nothing was fudged (oracle derives from the independent guest seeds +
  masks, never from the implementation output).
- **Sync positive control GREEN**: shadow==real `$04/$04` → `$00`;
  `$FB/$FB` → `$FB`; `C=0`, `AR=5`. Proves the harness distinguishes sync
  from desync (a blind oracle would conflate them).
- **Marker-corrupt rejects (measured, synthetic)**: ACTUAL marker `$00` and
  ACTUAL marker `$5A` bytes each reject → `CR/RCR/AR` unchanged, shadow
  untouched, `C=1`, on both cycles. The `$5A` case seeds `$2502=$5A` +
  `$2503=$5A` (loader-BUF-fill shape) and is observed on the pinned core
  (not a `$00` stand-in: the old stand-in is retired). The loader connection
  itself (`lda #$5A` prefill) stays structural SOURCE_CONFIRMED; whether any
  commercial disc ever presents `$5A` at the marker offset is
  UNOBSERVED/UNKNOWN. Gate compares against `$A5` only.
- **Non-target damage explicit**: `$FB & ~$04 != 0` with real `$00` proves
  stale-image bits 0,1,3–7 were set by the ok path.
- **Regs preserved**: entry `AR=9` at boundary → `AR=5` after; RCR `$55`
  sentinel kept; `IDR/IRR/MPRs/SR` unchanged (frozen-clock SR invariance;
  pending-SR survival + IRQ timing NOT_RUN by design); `A/X/Y/SP/P` per the
  D2 convention (`C=0` ok / `C=1` reject, `T` masked).
- **Strict real-vs-shadow separation**: `M_NOVDC` (tail `STA $0002` dropped)
  leaves real CR at `$00` while shadow publishes `$FB` (VDC oracle REDs,
  shadow GREEN); `M_NOPUBLISH` (tail `STA $2502` dropped) writes real CR
  `$FB` while shadow stays `$FF` (shadow oracle REDs, VDC GREEN). Oracles
  are separated by construction, never conflated.
- **Mutation detection**: `M_WRONGBIT`/`M_DESTROY`/`M_NOOP` (head) and
  `M_BYPASS` (head `BNE→BEQ`) each RED the desync oracle on both cycles
  (never GREEN by masking). `M_CLC_DROP`/`M_SEC_DROP`/`M_MPR` coverage stays
  in the existing return-probe mutants (unchanged).
- **Cold/warm equality**: `vdc_r_after/shadow_post/A/P/X/Y/SP/AR` identical
  across `ResetMedia(true)` (seeding dominates warm residual, as designed).

## 4. Limits (what this does NOT prove)

- Synthetic only: desync values are constructed by the synthetic guest
  (`shadow_pre`/`real_pre` argv echoes + observed `wram_pre` at the call
  boundary in RESULT). No commercial disc was read, no commercial WRAM was
  acquired, no `./rom/` access occurred. Whether any real disc places `$A5`
  at the marker offset with a desynced shadow is
  **UNKNOWN/UNOBSERVED** — never claimed as occurrence.
- Loader-staging interaction (L5) not run here: this probe constructs the
  desync directly; it does not drive `read_sector` against synthetic media
  (that is #862, separate).
- Same-value-store invisibility (documented residual in the writes-tracer)
  applies: a same-value store is invisible by construction. State-changing
  writes are what this probe asserts.
- Frozen-clock SR invariance only (dummy clock, `GetState` reads). Pending-SR
  survival and IRQ timing are NOT_RUN (same scope as the synced `$2B` VDC
  suite; body has no VDC read opcode by pinned construction).
- No ownership/lifetime resolution, no RAM relocation, no marker change, no
  auto-seed decided here. If a BIOS change is ever needed, it belongs to a
  separate Owner-approved proposal — explicitly NOT smuggled into this
  evidence-only PR.

## 5. Repro (synthetic, no setup beyond a checkout + `make setup`)

```sh
git rev-parse HEAD   # this PR HEAD (recorded in PR body + CI)
export TOOLS_DIR=/tmp/oss-pce-cd-bios-tools-1000  # pinned tools (make setup)
export OSS_PCE_REQUIRE_CC65=1
python3 -m unittest tests.integration.test_g5_rcroff_2b.Rcroff2bSynthetic.test_861_desync_false_ok_red -v
python3 -m unittest tests.integration.test_g5_rcroff_2b.Rcroff2bSynthetic.test_861_sync_positive_control \
  tests.integration.test_g5_rcroff_2b.Rcroff2bSynthetic.test_861_marker_corrupt_reject \
  tests.integration.test_g5_rcroff_2b.Rcroff2bSynthetic.test_861_regs_preserved_desync \
  tests.integration.test_g5_rcroff_2b.Rcroff2bSynthetic.test_861_strict_real_vs_shadow \
  tests.integration.test_g5_rcroff_2b.Rcroff2bSynthetic.test_861_mutant_detection_desync \
  tests.integration.test_g5_rcroff_2b.Rcroff2bSynthetic.test_861_cold_warm_equality -v
make test && make check   # host gate + static gates (no Geargrafx needed for host)
```

## 6. Safety-gate record (unchanged)

`release_eligible=false`; `G5_OBSERVED=false`; reference JSR 0/81;
genuine ABI VERIFIED 0/81; `DEV_CALLABLE` 0; `$1C` BLOCKED_ABI;
ROM 256 KiB; `$E800`/`$FFF6`; ALLOW/DENY; provenance/artifact guard;
no commercial disclosure. `JSR $E081` is the experimental table entry
(synthetic), never a reference callee.
