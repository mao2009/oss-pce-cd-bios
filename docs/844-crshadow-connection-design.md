# #844 CR-shadow L4/L5 connection design (Agent A — constrained design-only)

Status: **design-only — no BIOS change, no ABI change, no release impact.**
`release_eligible=false`. Genuine ABI stays BLOCKED (`$2B` genuine semantics
UNKNOWN/BLOCKED). `G5_OBSERVED=false`. Reference JSR 0/81, genuine ABI
VERIFIED 0/81 unchanged (real JSR `$E081` only through the experimental table
entry in synthetic runs, never a reference callee, never a fake RTS). ROM
256 KiB, `$E800`/`$FFF6` layout guards unchanged. `$1C` BLOCKED_ABI,
display-4 BLOCKED. No commercial bytes, titles, paths, hashes, sector
contents, audio, saves, firmware bytes, or `./rom/` access of any form were
used (no existence check, listing, or read). Commercial occurrence of the
false-ok path is **UNOBSERVED** (source-derived risk, never presented as
fact). The project is not yet a compatible System Card BIOS; nothing here
implies otherwise.

- Issue: #844 (Agent A connection wave; follows Wave-1 survey design).
- Wave-1 design: `docs/844-crshadow-survey.md` L4/L5 sections (§9-L4/L5),
  L1–L3 grounds (§2), writer/ownership tables (§§3–4).
- L4 evidence: `docs/861-crshadow-desync-l4.md` + cases
  `tests/integration/test_g5_rcroff_2b.py::Rcroff2bSynthetic#test_861_*`.
- L5 evidence: `docs/862-loader-ownership-record.md` + module
  `tests/integration/test_862_loader_l5.py` + host model
  `tests/host/test_862_loader_ownership.py` (38 tests).
- Branch: `agent/844-cr-loader-connect-20261128` (single writer). No PR yet in
  this task.
- Production diff: **zero bytes**. New files are doc + test-only
  (`docs/844-crshadow-connection-design.md` = this file,
  `tests/integration/test_844_crshadow_connection.py` structural oracle +
  staging + source pins). One test-infra-only manifest line adds the new
  module to the existing shard `rc-3` (required so `check-manifest` stays
  green; not a rebalance, no existing entry moved). No `src/` edit, no loader
  bytes change, no RAM relocation, no marker change, no auto-seed, no
  `$20F3`/`$20F7` use anywhere in the new files.

## 0. Evidence-class legend (used on every claim below)

- **SOURCE_CONFIRMED** — read directly from tracked source/arithmetic in this tree.
- **SYNTHETIC_REPRODUCED** — reproduced by synthetic runs in this tree
  (L4 pinned-core desync run, L5 pinned-core staging run, host models).
- **HYPOTHESIS** — conditional reasoning explicitly marked as unproven.
- **UNKNOWN** — value/state never measured in-tree; no guess is made.
- **UNOBSERVED** — a risk whose commercial occurrence was never observed;
  never stated as fact.

## 1. What L4 proved (recap, not rerun here)

**SYNTHETIC_REPRODUCED** on the pinned core
(geargrafx `b49ae82a…`, cc65 `71746c82…` per `tools/dependencies.json`):

- Gate + publish shape **SOURCE_CONFIRMED** in `src/ex_cr_family.s`
  (single-source; `lda $2503` at `:138,:158,:178`, `cmp #$A5` at
  `:139,:159,:179`, `bne cr_reject` reject with SEC and no write at
  `:196–200`; shared tail publishes `sta $2502` BEFORE `sta a:$0002`,
  restores MPR0, CLC at `:201–209`; `$2B` mask `and #$FB` at `:189`) and in
  `src/overrides/slot_2E.s` (`lda $2503` at `:82`, `cmp #$A5` at `:83`,
  publish-first at `:99`). Marker `$2503` is never written by any body;
  reject writes nothing.
- Desync false-ok RED: real CR `$0B00` + stale shadow `$FF` + marker `$A5`
  via `$2B` → `CR=$0BFB`, `shadow_post=[$FB,$A5]`, `C=0`, `AR=5`, cold==warm
  (`test_861_desync_false_ok_red`). Non-target bits `$FB & ~$04 != 0` with
  real `$00` prove stale-image damage.
- Sync positive controls GREEN: `04/04→00`, `FB/FB→FB`
  (`test_861_sync_positive_control`) — the harness discriminates sync from
  desync; a blind oracle would conflate them.
- Marker-corrupt rejects with the ACTUAL seeded byte: `$00` and `$5A` each
  reject → CR/RCR/AR unchanged, shadow untouched, `C=1`, both cycles
  (`test_861_marker_corrupt_reject`, `test_861_marker_5a_loader_fill_reject`).
  The `$5A` case seeds `$2502=$5A/$2503=$5A` (loader-fill shape), not a `$00`
  stand-in; the fixed-`$00` reject oracle blind spot is retired.
- Strict separation: `M_NOVDC` REDs the VDC oracle while shadow stays GREEN;
  `M_NOPUBLISH` REDs the shadow oracle while VDC stays GREEN
  (`test_861_strict_real_vs_shadow`). Oracles are separated by construction,
  never conflated.
- Mutants `M_WRONGBIT`/`M_DESTROY`/`M_NOOP`/`M_BYPASS` each RED the desync
  oracle on both cycles (`test_861_mutant_detection_desync`).
- `wram_pre==argv` at `jsr_pc` proves seeding dominated residual;
  `shadow_pre`/`real_pre` are argv echoes, `wram_pre` is observed
  (`test_861_cold_warm_equality`, `test_861_regs_preserved_desync`,
  `test_861_desync_probe_argv_guard`).
- Limits: synthetic guest constructs desync directly; no loader staging, no
  commercial disc/WRAM, no `./rom/`; commercial `$A5`-at-marker occurrence
  **UNKNOWN/UNOBSERVED**, never an occurrence claim.

## 2. What L5 proved (recap, not rerun here)

**SYNTHETIC_REPRODUCED** on the pinned core (DEV, n=5, IPLBLK=2, STA=`$3000`):

- Geometry **SOURCE_CONFIRMED**: BUF `$2200–$29FF` (one 2048-byte staging
  sector), shadow cells at offsets `$0302/$0303/$0304`
  (`$2502-$2200=0x302`, in range); `$5A` prefill + every READ(6) transfer
  into the same BUF (`tests/integration/ipl_loader_rom.s` `read_sector`;
  direct-address grep misses this indirect range writer).
- Trace, cold AND warm: `FILL=(5A,5A,5A)` → `INFO=(D0,D1,D2)` →
  `PRE=(C4,C5,C6)==LAST` → `FIRST=(C0,C1,C2)!=LAST` → `LAST=(C4,C5,C6)` →
  `RPT=(C4,C5,C6)+MPR0=$FF+MPR1=$F8` at IPLJMP (last-sector-wins;
  `test_l5_staging_trace_cold_warm`). At IPLJMP the shadow cells hold the
  last-transferred body sector's bytes at `$0302–$0304`.
- Ownership adopted: loader-ownership-period (loader owns BUF first-`$5A`-fill
  through IPLJMP); `shadow_valid()` lifetime vs `sync_caller_ok()` sync
  separated; normal sync publish maintains sync, never a lifetime event;
  staging coincidence / desync-publish equality never proves intent; MPR1
  must be `$F8`; no RAM move, no marker change, no auto-seed
  (`docs/862-loader-ownership-record.md` §1).
- Production parity: `#ifdef IPL_L5_SNAPSHOT` TEST-ONLY snapshot code only;
  define-off ROM hash identical (`36279135…`); no `sta $2502/3/4` anywhere in
  the loader including snapshots (snapshots target RB spare `RB+$24–$38`,
  report at `RB+$34–$38` via `tests/integration/ipl_payload_l5.s`).
- Limits: the L5 image performs no CR programming and never calls `$E081`;
  post-IPLJMP commercial behavior **UNKNOWN**; commercial occurrence
  **UNOBSERVED**.

## 3. Connection oracle table (for the future execution wave; unit-pinned here)

`$2B` mask `$FB`. Entry-C classes: both `C=0` and `C=1` entries forced to the
path convention (ok `C=0`, reject `C=1`); `A!=P` vectors prove A/P
independence. Boundary on every row: CR-high + RCR (`$0B`/`$55` sentinels in
L4) preserved, all 20 VDC regs except R5 equal before/after on ok (all 20
equal on reject), `AR_after==5` on ok / `AR_after==AR_entry` on reject, RCR
sentinel kept, `MPRs`/`IDR`/`IRR`/`SR` unchanged (frozen clock), `SP=$FF`,
cold==warm on all security state.

| # | Marker (actual byte) | Shadow vs real | Path | CR low after | shadow_post | C | Verdict |
|---|---|---|---|---|---|---|---|
| C1 | `$00` | any | reject (SEC, no write) | unchanged (real kept) | `[shadow,$00]` untouched | 1 | GREEN reject-correct |
| C2 | `$5A` (loader-fill shape, actual `$5A` seed) | any | reject (SEC, no write) | unchanged | `[shadow,$5A]` untouched | 1 | GREEN reject-correct; oracle compares the actual byte, never fixed `$00` |
| C3 | `$A5` synced (`shadow==real`) | equal | ok (publish + VDC write) | `shadow&$FB` (correct image) | `[CR,$A5]` | 0 | GREEN positive control (e.g. `04/04→00`, `FB/FB→FB`) |
| C4 | `$A5` desync (`shadow!=real`) | differ | ok (publish + VDC write) | `stale&$FB` (wrong image, e.g. real `$00`+stale `$FF`→`$FB`) | `[stale&$FB,$A5]` | 0 | RED false-ok confirmed; non-target bits prove stale damage |

Separation rows (mutant ROMs, same argv as C4): `M_NOVDC` must RED the VDC
oracle (`vdc_r_before==vdc_r_after`) while shadow stays GREEN
(`shadow_post==[$FB,$A5]`); `M_NOPUBLISH` must RED the shadow oracle
(`shadow_post==[$FF,$A5]`) while VDC stays GREEN (`CR==$FB`). Head mutants
`M_WRONGBIT`/`M_DESTROY`/`M_NOOP`/`M_BYPASS` must each RED the matching
oracle on both cycles. Fail-closed argv: out-of-domain marker/ncalls/steps
refused with no RESULT.

Positive control via post-staging intentional seed (execution-wave shape,
unit-modeled here): after `L5RPT` observes the residue triple, the payload
explicitly re-seeds `shadow==real` + `$A5` — a documented harness seed, never
firmware init, never an auto-seed — then `JSR $E081`. Sync GREEN alongside
residue-desync RED proves discrimination end to end. At unit level the
observed `(C4,C5,C6)` triple is passed with its actual `$C5` marker (reject,
`C=1`); the false-ok RED uses only the hypothetical synthetic `(C4,$A5,C6)`
triple, never a marker-substituted residue. No loader save/restore,
no marker change, no `$20F3`/`$20F7`.

## 4. Loader-transfer residue vs direct seed (labels)

| Value at `$2502–$2504` | Origin | Label |
|---|---|---|
| `(5A,5A,5A)` post-fill | loader `STA (ptr),Y` prefill structure | **SOURCE_CONFIRMED** structure, **SYNTHETIC_REPRODUCED** trace |
| INFO / PRE / FIRST / LAST / RPT triples | sector bytes via READ(6) transfer | **SYNTHETIC_REPRODUCED** (L5 cold+warm) |
| Guest `$2502`/`$2503` stores in probe guests | explicit harness argv seeding | **SOURCE_CONFIRMED** test-setup fact (caller-side, never firmware) |
| Residue triple *as the seed* of a CR call | untested single-ROM continuity | **HYPOTHESIS** (this design proposes the run; §6 BLOCKED) |
| Warm residual before first staging | no WRAM clear, never measured | **UNKNOWN** (never assumed zero) |
| Any commercial disc placing `$A5` at the marker offset with desync | never observed | **UNOBSERVED**/**UNKNOWN** |

Value equality is never lineage proof: a coincidental staging-byte match is
still INVALID lineage; a desync-publish equality never retroactively
justifies the stale-derived value; `$A5`-alone (any of its four origins:
harness seed, sector residue, warm residual, caller data) never proves sync.
The new structural tests pin this distinction at unit level
(`LineageLabelTests`: equal bytes via staging vs via seed carry different
lineage labels).

## 5. Harness composition (existing pieces + the gap)

What the existing harness can do unchanged:

- (a) Stage arbitrary triples at `$0302–$0304` via `with_triple`/`body_for` +
  `IPL_L5_SNAPSHOT` snapshots + `L5RPT` (structural last-sector-wins proven).
- (b) Decide any fixed triple as reject/ok/false-ok via desync probe argv
  (`shadow_lo/marker/real_lo/crhi/rcr_lo`) with `wram_pre` + VDC boundary
  oracles + cold/warm equality + mutants.

What it cannot do unchanged — the gap (§6): no single ROM run chains
`synthetic BODY bytes → PUBLIC transfer → IPLJMP residue as live seed →
$2B JSR → CR/shadow/marker/Carry/boundary` with independent real-CR
programming. The L5 image has no CR body; the `$2B` image has no loader
staging; the L5 payload never programs real CR nor calls `$E081`; the
desync probe never drives `read_sector`. Composing them needs a new
test-only probe/payload pair (future execution wave, own review) — never
`src/` edits.

Safe-connect conditions for that future run: synthetic CUE only; pinned
geargrafx `b49ae82a…` + cc65 `71746c82…`; 256 KiB / `$E800` / `$FFF6`
guards; `SP=$FF` / `MPR1=$F8`; frozen-clock `GetState` reads;
`ResetMedia(true)` cold/warm; fail-closed argv; no `./rom/`; no commercial
bytes; no WRAM dump beyond the residue triple + result block.

## 6. BLOCKED rationale (end-to-end single-ROM continuity)

End-to-end loader→CR continuity is **BLOCKED in this wave: harness gap, not
a BIOS defect**. Building the composed run requires a new test-only guest
that (i) drives `read_sector` against synthetic media, (ii) carries the
residue triple at `$2502–$2504` across the IPLJMP-equivalent handoff without
a firmware save/restore (no such mechanism exists by design — ownership §2),
(iii) programs the real CR independently of the residue, and (iv) calls
`$E081` through the real table entry — none of which any in-tree probe or
payload does today. Forcing the connection with the existing pieces (e.g.
hand-copying an observed `L5RPT` triple into desync-probe argv) would prove
only direct-seed behavior already covered by L4, mislabeled as
loader-transfer evidence — exactly the confusion §4 forbids. The new tests
therefore stay structural (staging geometry + oracle unit + source pins),
and the composed run is deferred to a future execution wave with its own
probe/payload review. This BLOCKED does not gate L4/L5 findings, which stand
on their own grounds.

## 7. New structural tests (this wave)

`tests/integration/test_844_crshadow_connection.py` (stdlib only; no cc65,
no Geargrafx, no `./rom/` access; new payload explicitly NOT added per §6):

- `EvidenceLabelTests`: the five-label taxonomy is closed and every
  module-level claim table entry carries a valid label.
- `ConnectionOracleTests`: pure `$2B` model (`marker!=$A5`→reject else
  publish `shadow&$FB`) checked against oracle table C1–C4, both entry-C
  classes, AR discipline, cold/warm equality, `M_NOVDC`/`M_NOPUBLISH`
  separation, head-mutant REDs, and the post-staging intentional-seed
  positive control (residue-desync RED vs reseeded-sync GREEN).
- `StagingGeometryTests`: BUF `$2200`+2048 range pin, `$0302–$0304` offsets,
  `with_triple`-equivalent splice, last-sector-wins, `PRE==LAST`,
  `FIRST!=LAST`, `MPR1=$F8` observation pin, cold/warm determinism shape.
- `SourcePinTests` (read-only): gate shape in all four bodies, the three
  family masks + `$2E` mask, publish-before-VDC order, no direct
  `sta $2502/3/4` in `ipl_loader_rom.s`, every `L5_` line inside its ifdef,
  RB spare span `RB+$24–$38` with `L5_RPT=RB+$34`, pinned toolchain/core
  revisions, probe `0x40000`/`$FFF6`/argv guards, own-file `$20F3`/`$20F7`
  and `./rom` absence, same-PR shard listing.
- `LineageLabelTests`: §4 table pinned; equal-bytes≠equal-lineage;
  `$A5`-alone≠sync for all four marker origins.
- Shard: listed in `tools/ci_sharding.py` `rc-3` beside `test_862_loader_l5`
  and `test_g5_rcroff_2b` (same-PR listing keeps `check-manifest` green; no
  rebalance, no existing entry moved).

## 8. Acceptance (this wave meets all of the following)

- No `src/` bytes changed; define-off loader hash unchanged; no direct
  `sta $2502/3/4` in the loader (snapshots included); no RAM relocation, no
  marker/new-token, no auto-seed, no `$20F3`/`$20F7`.
- No loader-transfer vs direct-seed confusion: every claim labeled per §0/§4;
  residue value ≠ sync proof; `$A5`-alone ≠ proof.
- Independent observation preserved in the oracle design: real CR (VDC
  `R[5]`+high at `jsr_pc` boundary + after), shadow (`wram_pre` +
  `shadow_post`), actual marker byte, Carry (`C=0` ok/false-ok vs `C=1`
  reject, both entry-C classes), boundary (`AR`, RCR sentinel, `MPRs`,
  `IDR`/`IRR`, `SR` frozen, `SP`, `A!=P` vectors), cold/warm equality.
- Safety gates unchanged: `JSR 0/81, VERIFIED 0/81`,
  `release_eligible=false`, `G5_OBSERVED=false`, `$1C` BLOCKED_ABI,
  display-4 BLOCKED, 256 KiB/`$FFF6`/`$E800`, synthetic-only.

## 9. Repro (synthetic, no setup beyond a checkout + `make setup`)

```sh
git rev-parse HEAD   # this PR HEAD (recorded in PR body + CI)
export TOOLS_DIR=/tmp/oss-pce-cd-bios-tools-1000  # pinned tools (make setup)
export OSS_PCE_REQUIRE_CC65=1
python3 -m unittest discover -s tests/integration -p 'test_844_crshadow_connection.py' -v  # new structural module
python3 -m unittest tests.host.test_862_loader_ownership -v        # L5 host regression (38 tests)
python3 -m unittest discover -s tests/integration -p 'test_862_loader_l5.py' -v  # L5 real-core regression
python3 -m unittest tests.integration.test_g5_rcroff_2b.Rcroff2bSynthetic -v    # L4 real-core regression
git diff --check && git status --porcelain  # clean tree expected after commit
```

## 10. Light-check record (Phase 1, this worktree)

- New structural module: all tests pass (recorded in the task report; host
  gate + `git diff --check` clean).
- Existing regressions rerun in this wave: `test_862_loader_ownership`
  (host), `test_862_loader_l5` (L5 real-core), `Rcroff2bSynthetic` (L4
  real-core) — results in the task report; any failure is a regression
  signal, never redefined as success.
- Full `make test` / `make check` and Required CI run on the PR SHA
  (Phase 4), not claimed here. No `./rom/` access in any form was used or
  needed.

## 11. Verdict

- Design-only: **PROCEED** (constrained) — this doc + structural module are
  the deliverable; they pin the oracle, the lineage discipline, and the
  source guards with zero production bytes.
- End-to-end single-ROM execution: **BLOCKED** with rationale (§6) — needs
  a new test-only probe/payload pair with its own review; do not force the
  connection with existing pieces.
- Independent PR value: **yes** — the doc fixes the oracle/lineage contract
  for the future run, and the structural tests guard it in CI, without
  touching firmware, loader bytes, RAM layout, markers, or gates.
