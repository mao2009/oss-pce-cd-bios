# #862 loader-vs-shadow ownership + L5 staging record (G5 P1 Agent B)

Status: **record + L5 run — no production-code change, no ABI change, no
release impact.** `release_eligible=false`. Genuine ABI stays BLOCKED. No
commercial bytes, titles, paths, hashes, sector contents, audio, saves,
firmware bytes, or `./rom/` access of any form were used (no existence check,
listing, or read). Commercial occurrence of the false-ok path is
**UNOBSERVED** (source-derived risk, never presented as fact). The project is
not yet a compatible System Card BIOS; nothing here implies otherwise.

- Issue: #862 (follow-up of #844, which followed #842 independent review
  `6083530888` MAJOR; P1 Phase F2). #861 owns the L4 desync RED run; this
  record covers L5 + ownership only and makes no L4 claim.
- Target SHA: `b743a8c2` (origin/main at survey time; branch
  `agent/862-loader-ownership-20261128` tracks origin/main).
- Production diff: **zero bytes**. The only loader change is
  `#ifdef IPL_L5_SNAPSHOT` TEST-ONLY snapshot code plus one branch-range
  workaround inside the same ifdef (see §7); assembled with the define off,
  the ROM hash equals the committed build (`36279135…`, §9-L5). New files are
  test-only: `tests/host/test_862_loader_ownership.py`,
  `tests/integration/test_862_loader_l5.py`,
  `tests/integration/ipl_payload_l5.s`.
- Companion fixtures: host model (38 tests, pure model + source pins,
  synthetic only) and the L5 real-core module (1 case, cold+warm, synthetic
  media + synthetic core only, pinned Geargrafx per tools/dependencies.json).
  Review 6096579240 B-1/B-2 fix: seed-lifetime vs sync-state separated
  (normal sync publish maintains sync; `family_publish` fails closed in
  `shadow_valid()`), parity procedure rebuilt on base blob vs HEAD blob.

## 0. Evidence-class legend (used on every claim below)

- **SOURCE_CONFIRMED** — read directly from tracked source/arithmetic in this tree.
- **SYNTHETIC_REPRODUCED** — reproduced by the host model (L2) or the L5
  synthetic real-core run (this PR) respectively.
- **HYPOTHESIS** — conditional reasoning explicitly marked as unproven.
- **UNKNOWN** — value/state never measured in-tree; no guess is made.
- **UNOBSERVED** — a risk whose commercial occurrence has never been observed;
  never stated as fact.

## 1. Ownership / lifetime decision (adopted)

**Adopted: loader-ownership-period.** The PUBLIC loader owns BUF `$2200–$29FF`
— including the CR-shadow cells `$2502`/`$2503`/`$2504` at BUF offsets
`$0302`/`$0303`/`$0304` — for the whole staging window (first `$5A` fill
through IPLJMP). Consequences, all machine-checkable in the companion fixtures:

- Seed-ownership lifetime (`shadow_valid()`): a seeded `$2502`/`$2503` pair
  is VALID only until the next invalidating event: a staging overwrite
  (`staging_cycle` — fill + sector transfers destroy the cells) or a
  reset/warm handoff (`reset_or_warm_handoff`, UNKNOWN contents, fail
  closed). No other transition exists: no auto-seed, no timeout, no
  background refresh. A normal sync family publish is explicitly NOT a
  lifetime event: base `src/ex_cr_family.s` `cr_tail_publish` writes the same
  A to `$2502` and `a:$0002` with the marker preserved (review 6096579240
  B-1), so a synced caller stays synced with no re-seed between consecutive
  calls; passing `family_publish` to `shadow_valid()` fails closed
  (`ValueError`). Pinned by `OwnershipLifetimeTests` in
  `tests/host/test_862_loader_ownership.py` (unknown events fail closed).
- Sync state at entry (`sync_caller_ok()`): a CR-family call is synchronized
  iff marker `== $A5` AND shadow `==` real CR at entry AND MPR1 `== $F8`
  (work-RAM bank, so `$2502` is the owned cell). Every single condition is
  necessary (per-condition negative tests). A desynced caller, an unseeded
  marker, or a wrong bank each fail — marker `$A5` alone never suffices.
  A normal sync publish (`family_ok_publish()` model of the shared tail:
  `dspoff` AND `#$3F`, `irqon` ORA `#$08`, `rcoff` AND `#$FB`)
  MAINTAINS sync: `shadow == real == f(old_shadow)`, marker kept, so the
  next call is still sync. Value-match alone never proves caller intent or
  past safety: a coincidental staging-byte match is still INVALID lineage,
  an external direct CR write outside the family desyncs out of contract
  (by construction unobservable, must re-seed), and a desync publish
  reunifies the two cells without retroactively justifying the stale-derived
  value. No new caller constraints are introduced (existing discipline:
  never write CR outside the family; re-seed after staging or reset/warm).
- At IPLJMP the shadow cells hold the last-transferred body sector's bytes at
  offsets `$0302–$0304` (**SYNTHETIC_REPRODUCED**, L5 cold+warm, §9-L5). The
  loaded program owns the machine from there; post-IPLJMP commercial behavior
  is **UNKNOWN** (never executed commercially, never claimed).
- Physical-bank note: the audited MPR selection (MPR1=`$F8` work RAM vs
  MPR2..6 `$80+`, `ipl_loader_rom.s` reset/switch_region/launch) shows no alias
  mechanism, and the L5 payload records entry MPR0=`$FF`/MPR1=`$F8` on the
  pinned core — but no universal non-alias theorem is proven. Physical alias
  stays **UNKNOWN**, not a fact. Logical disjointness (shadow `< $3000 =
  IPLSTA`) is **SOURCE_CONFIRMED** arithmetic and is not conflated with
  physical isolation.

What this decision does NOT do (explicit non-scope): no RAM move, no marker
change, no auto-seed, no validity token, no loader save/restore. Any of those
needs its own grounds and is forbidden groundless here (criterion 8).

## 2. Staging trace (what the L5 run proves)

For the L5 disc (n=5 DEV, IPLBLK=2, STA=`$3000`, INFO triple
`(D0,D1,D2)` at `$0302–$0304`, BODY sector *i* triple
`(C0+i,C1+i,C2+i)`), the pinned-core snapshots read, cold AND warm:

| Snapshot | Value | Ground |
|---|---|---|
| post-fill | `(5A,5A,5A)` | `$5A` fill via STA (ptr),Y, 8 pages (**SYNTHETIC_REPRODUCED**) |
| post-INFO | `(D0,D1,D2)` | INFO sector transfer overwrites BUF |
| post-pre-check | `(C4,C5,C6)` | probe_last reads the FINAL body sector |
| first BODY | `(C0,C1,C2)` | copy iteration 0 |
| last BODY | `(C4,C5,C6)` | copy iteration n-1 — last-sector-wins |
| IPLJMP report | `(C4,C5,C6)` + MPR0=`FF` + MPR1=`F8` | loader handoff state |

Cross-checks: pre-check `==` last BODY (same LBA, `body+nsec-1`), first `!=`
last (sectors distinguishable, ordering real), IPLJMP report `==` last BODY
(handoff continuity). Cold `==` warm (staging overwrites every residual).

## 3. Marker validity (applicability limits, no new mechanism)

- The gate checks *only* `marker == $A5` (**SOURCE_CONFIRMED**); the ok path
  then trusts the shadow image blindly. `$A5` at the marker offset after any
  READ(6) is a sector byte like any other — it records "someone stored `$A5`
  here" (harness seed, sector residue, warm residual, or caller data), never
  sync. Marker-`$A5`-alone-is-not-proof is pinned both in the host model
  (`test_stale_shadow_fails_despite_a5_marker`) and, for the gate shape, in the
  #844 fixture.
- No validity token beyond the `$A5` byte is introduced here. A generation
  counter, dual marker, checksum, or any new marker value would be a groundless
  marker change (criterion 8) — recorded as FOLLOW_UP candidate only (§5,
  option marker-validity), never decided here.

## 4. Synchronized-caller preconditions (machine-checkable)

`sync_caller_ok(shadow, marker, real_cr, mpr1)` — all four conjuncts required:

1. `marker == $A5` (gate necessity; rejects the `$5A` fill state cleanly);
2. `shadow == real_CR_at_entry` (sync sufficiency; stale images fail);
3. `mpr1 == $F8` (the `$2502` cell addressed must be work RAM);
4. caller discipline (unchanged, no new constraints): no direct CR write
   outside the family between seeding and the call; re-seed after every
   staging cycle or reset/warm handoff — NOT after a normal sync family
   publish, which maintains sync (consecutive `$2B`→`$2C` example in
   `FamilyPublishSyncTests.test_consecutive_publishes_need_no_reseed`:
   `04`→`00`→`08` with no seed between). A desync publish or an external
   direct write is out of contract: the post-publish `shadow == real`
   equality never retroactively proves intent or past safety, and only a
   re-seed restores a trustworthy lineage (ownership §1 — the discipline is
   the caller's, the checker only verifies the checkable conjuncts; the
   no-bypass-write part is by construction unobservable, see
   reject-if-unsync in §5).

## 5. Mitigation comparison (grounds / pros / cons / scope)

| Option | Grounds | Pros | Cons | Scope verdict |
|---|---|---|---|---|
| loader-ownership-period | §1–§2, L5 cold+warm, host pins | zero code change; matches SOURCE_CONFIRMED behavior; machine-checkable preconditions; last-sector-wins grounded | does not prevent misuse by itself; relies on caller discipline | **adopted** (this record) |
| shadow-ownership (family owns `$2502` always; loader must preserve/restore) | none measured | keeps shadow stable across staging in theory | loader necessarily overwrites via sector transfer; save/restore needs a reserved home + a restore point with grounds; no measured need on the synthetic path | **rejected** here; FOLLOW_UP only with measured need + full audit |
| marker-validity (counter/dual-marker/checksum/new value) | none (would be a new mechanism) | could detect stale in theory | groundless marker change per criterion 8; new ABI-ish surface; ungrounded on real core | **not decided**; FOLLOW_UP candidate only — no RAM move, no marker change, no auto-seed in this PR |
| reject-if-unsync (detect bypassing CR write) | absence is SOURCE_CONFIRMED (no observation mechanism exists; slot_2E.s discipline note) | — | impossible by construction: nothing can observe a bypassing write | **rejected** with grounds |
| RAM-relocation (move shadow out of BUF) | none | removes this one overlap | groundless move per criterion 8; changes the shared contract; does not fix sync (stale still possible); adjacent range writes need re-audit | **not decided**; FOLLOW_UP only with full audit + measured need — no RAM move in this PR |

## 6. Cold / warm safety on synthetic grounds

- Cold: pre-first-READ(6) BUF is `$5A` fill → the gate rejects with no write
  (host model; structural fill pin). Post-transfer cold state == warm state
  (L5: cold `==` warm on every snapshot and the IPLJMP report).
- Warm: residual at `$2502`/`$2503` was never measured in-tree
  (**UNKNOWN**); the record never assumes zero. Staging overwrites whatever
  was there (L5 determinism), so the ownership rule (§1) covers warm without
  guessing the residual.
- No WRAM clear on reset paths (`src/boot.s`, `src/reset_entry.s` store no
  RAM — **SOURCE_CONFIRMED** absence, see #844 §2).

## 7. Implementation notes (TEST-ONLY, zero production bytes)

- `tests/integration/ipl_loader_rom.s`: four snapshot sites under
  `#ifdef IPL_L5_SNAPSHOT` (post-fill in `read_sector`, post-INFO in `reset`,
  post-pre-check in `probe_last`, first/last BODY in `copy_body`) writing RB
  spare `RB+$24–$32` plus the IPLJMP report at `RB+$34–$38` (span
  `RB+$24–$38` with one padding byte at `$33`), plus one branch-range
  workaround (`beq`/`jmp` replacing `bne @sector`, same ifdef). With the
  define off the ROM hash equals the committed build: `36279135…` both ways
  (§9-L5). Pinned by `test_l5_snapshot_code_is_ifdef_contained` (every `L5_`
  line inside the ifdef) and `test_loader_never_stores_shadow_cells_directly`
  (no `sta $2502/3/4` anywhere in the loader, snapshots included —
  snapshots target RB spare only).
- `tests/integration/ipl_payload_l5.s`: PRES behavior identical to
  `ipl_payload.s`, plus the IPLJMP report (`RB+$34–$38`: shadow triple,
  entry MPR0/MPR1). Synthetic harness only, never linked into any ROM.
- `tests/integration/test_862_loader_l5.py`: one DEV case (n=5) asserting the
  standard loader contract (via the shared `judge()`) plus §2 snapshots and
  the IPLJMP report, cold AND warm; registered in `tools/ci_sharding.py`
  shard rc-3 (manifest self-consistency is fail-closed).
- `tests/host/test_862_loader_ownership.py`: 38 host tests (geometry,
  indirect-writer structure, tracking model, preconditions, seed-lifetime
  judge, family-publish sync separation (`family_ok_publish` + tail-mask
  pins), record pins). No cc65, no core.

## 8. Relations (merge-blocking: none in either direction)

- #843 (BOOT commonization): code sharing ≠ writer unification; the adopted
  ownership record constrains #843 to preserve the gate+publish order but
  blocks nothing.
- #861 (L4 desync RED): separate run, separate module, separate verdict. L5
  does not claim false-ok on core; L4 does not claim staging traces. Neither
  gates the other; #844 L1–L3 stand on their own.
- #844 survey doc is read-only here (not edited); Agent A wording, if any,
  may be cited for the final judgment but this record + fixtures stand alone.

## 9. Repro steps

Pre-change base (§9-L5 parity): commit `53d9a31`, blob
`0972bec8910c0d026e4607a7847bec6659ed4558`
(`tests/integration/ipl_loader_rom.s` before this PR). PR HEAD: commit
`d61b264f`, blob `ef5d27aa9dee2d878d77660d61cc90bd4be5e0b9` (same path after
this PR). Different sources for base vs HEAD (review 6096579240 B-2): the
first build reads the base blob from git, the second reads the PR-HEAD blob
from git (the worktree file at a clean checkout is identical to the HEAD
blob, never substituted for the base). Both builds use the same pinned
cc65 (`tools/dependencies.json`: `71746c829e77f74c2b601a7d16821170c2610df3`),
the same linker script (`tests/integration/ipl_loader_rom.cfg`, unchanged
base→HEAD), the same define set (production: `IPL_L5_SNAPSHOT` DISABLED —
no `-D IPL_L5_SNAPSHOT`), the same build (`IPL_SEL=$0109`, `-g`), and the
same `cmp` + `sha256sum` checks. The host string-pins
(`test_l5_snapshot_code_is_ifdef_contained`) are NOT cited as byte-parity
proof; only the two built ROMs below count. Each ROM is compared separately
against the recorded hash (old-base vs new-main); either mismatch FAILS.

```sh
git rev-parse HEAD             # expect d61b264f (PR HEAD at fix time)
git rev-parse 53d9a31^{commit}  # expect 53d9a31 (pre-change base)
python3 -m unittest tests.host.test_862_loader_ownership -v   # 38 host tests
# production-byte parity, base blob vs PR-HEAD blob (define off both ways):
CC65_BIN=/tmp/oss-pce-cd-bios-tools-1000/cc65-71746c829e77f74c2b601a7d16821170c2610df3/bin
mkdir -p /tmp/parity-check
 git show 53d9a31:tests/integration/ipl_loader_rom.s > /tmp/parity-check/loader_base.s
 git show HEAD:tests/integration/ipl_loader_rom.s > /tmp/parity-check/loader_head.s
 git show HEAD:tests/integration/ipl_loader_rom.cfg > /tmp/parity-check/ipl_loader_rom.cfg
 "$CC65_BIN/ca65" /tmp/parity-check/loader_base.s -D IPL_SEL=\$0109 -o /tmp/parity-check/base.o -g
 "$CC65_BIN/ld65" -C /tmp/parity-check/ipl_loader_rom.cfg -o /tmp/parity-check/base.pce -Ln /tmp/parity-check/base.lbl /tmp/parity-check/base.o
 "$CC65_BIN/ca65" /tmp/parity-check/loader_head.s -D IPL_SEL=\$0109 -o /tmp/parity-check/head.o -g
 "$CC65_BIN/ld65" -C /tmp/parity-check/ipl_loader_rom.cfg -o /tmp/parity-check/head.pce -Ln /tmp/parity-check/head.lbl /tmp/parity-check/head.o
 cmp /tmp/parity-check/base.pce /tmp/parity-check/head.pce  # byte-identical or FAIL
 sha256sum /tmp/parity-check/base.pce /tmp/parity-check/head.pce
# both lines must read 362791357869d199c50b2b44453822bcadf5913eaa73703f563ca561167c7563 or FAIL
python3 -m unittest discover -s tests/integration -p 'test_862_loader_l5.py' -v  # pinned core, cold+warm
```

Reproduced in this worktree: `cmp base==head PASS`, both `sha256sum`
`362791357869d199c50b2b44453822bcadf5913eaa73703f563ca561167c7563`
(also equal to `build/debug/ipl-loader/ipl-loader-rom-main.pce`).

## 10. Light-check record (Phase 1, this worktree)

- `python3 -m unittest tests.host.test_862_loader_ownership -v`: 38/38 pass
  (after the review-6096579240 fix; the old
  `test_family_publish_and_reset_invalidate` RED-fails against the new
  `family_publish`-fails-closed judge, and the new `FamilyPublishSyncTests`
  RED-fail without `family_ok_publish` — GREEN only with the separation).
- define-off ROM parity (§9-L5, actually reproduced): base blob
  (`53d9a31`, `0972bec8…`) and PR-HEAD blob (`d61b264f`, `ef5d27aa…`)
  built with the same pinned cc65, same cfg, `IPL_L5_SNAPSHOT` disabled,
  `IPL_SEL=$0109`: `cmp` byte-identical, both `sha256sum`
  `362791357869d199c50b2b44453822bcadf5913eaa73703f563ca561167c7563`
  (also equal to the committed `build/debug/ipl-loader/ipl-loader-rom-main.pce`).
- `git diff --check`: clean (verified at PR time).
- Full `make test` / `make check` and Required CI run on the PR SHA (Phase 4),
  not claimed here. No `./rom/` access in any form was used or needed.
