# #844 CR-shadow survey: PUBLIC loader BUF overlap + marker validity (G5 P1 Agent B)

Status: **survey only — no implementation, no ABI change, no release impact.**
`release_eligible=false`. Genuine ABI stays BLOCKED. No commercial bytes, titles,
paths, hashes, sector contents, audio, saves, firmware bytes, or `./rom/` access
of any form were used (no existence check, listing, or read). Commercial
occurrence of the false-ok path is **UNOBSERVED** (source-derived risk, never
presented as fact). The project is not yet a compatible System Card BIOS; nothing
here implies otherwise.

- Issue: #844 (follow-up of #842 independent review `6083530888` MAJOR; P1 Phase F2).
- Target SHA: `d3908245` (origin/main at survey time; branch fast-forwarded to it —
  normal merge, HEAD was an ancestor so no merge commit was needed).
- Branch/worktree: `agent/844-crshadow-invest-20261127` @ `/tmp/g5-b-844-crshadow`.
- Companion fixture: `tests/host/test_844_crshadow_survey.py` (18 host tests, pure
  model, synthetic only). Ephemeral draft `/tmp/g5-b-844-falseok-demo.py` (not
  tracked) was reused as the model's starting point and superseded by the fixture.
- Agent A impl files (`src/ex_rcroff.s`, `src/ex_dspoff.s`, `src/ex_irqon.s`,
  `src/overrides/slot_2E.s`, loader, boot/reset sources): **not edited** — survey
  reads them only. Any safety note for the Agent A PR goes to that PR, not here.

## 0. Evidence-class legend (used on every claim below)

- **SOURCE_CONFIRMED** — read directly from tracked source/arithmetic in this tree.
- **SYNTHETIC_REPRODUCED** — reproduced by the synthetic host model in this PR.
- **HYPOTHESIS** — conditional reasoning explicitly marked as unproven.
- **UNKNOWN** — value/state never measured in-tree; no guess is made.
- **UNOBSERVED** — a risk whose commercial occurrence has never been observed;
  never stated as fact.

## 1. Purpose and scope

Decide nothing about RAM relocation, marker change, or auto-seeding. This survey
exists to (per #844 acceptance criteria 1–8): audit all RAM writers/usage ranges
(direct + indirect, incl. loader staging/transfer); make shadow vs loader-buffer
ownership and lifetime explicit; state shadow ownership and validity period;
characterize the accidental-marker false-ok (or explicit applicability limits);
make synchronized-caller conditions verifiable; record cold/warm safety state on
synthetic grounds; keep normal DEV / experimental / safety gates green with
commercial media undisclosed; and propose no groundless RAM destination or new
marker value.

## 2. Source grounds (all line numbers vs target SHA `d3908245`)

- Staging geometry: `tests/integration/ipl_loader_rom.s:15` (BUF `$2200–$29FF`,
  one 2048-byte staging sector), `:42` (`SECTOR = 2048`), `:78` (`BUF = $2200`).
- Indirect fill: `:647–662` (`read_sector`: `lda #<BUF / #>BUF` → `ptr`,
  `lda #$5A`, 8 pages of `sta (ptr),y` at `:655–658`, then READ(6) sector
  transfer into the same BUF). Direct-address grep misses this writer.
- Gate + publish, identical in all four bodies:
  `$2B` `src/ex_rcroff.s:92–93` (`lda $2503` / `cmp #$A5`),
  `:107–109` (`lda $2502` / `and #$FB` / `sta $2502`);
  `$33` `src/ex_dspoff.s:90–91`, `:105–107` (`and #$3F`);
  `$2C` `src/ex_irqon.s:85–86`, `:100–102` (`ora #$08`);
  `$2E` `src/overrides/slot_2E.s:82–83`, `:97–99` (`ora #$80`).
  Reject path: `SEC`, no write anywhere (caller stack aside).
- No intentional firmware init: `src/boot.s` (stop loop only, no RAM store),
  `src/reset_entry.s` (MPR0=`$FF`, MPR1=`$F8`, SP=`$FF`, no RAM store),
  `src/dev_boot_internal.s` / `src/experimental_bios_boot.s` /
  `src/ipl_loader_g5_glue.s` (MPR/SP/handoff only; `$2502`-family untouched —
  verified by grep for `2502|2503|2504`, which hits only comments/contract text
  outside the four bodies). **SOURCE_CONFIRMED** absence of an intentional
  CR-shadow init store anywhere in firmware/BOOT/reset/glue.
- No WRAM clear on reset paths: neither `src/boot.s` nor `src/reset_entry.s`
  stores to RAM at all (**SOURCE_CONFIRMED** absence; warm residual is therefore
  **UNKNOWN**, never assumed zero).
- Load area: `ipl_loader_rom.s:18` (`$3000–$7FFF`), `:248` (IPLSTA range check),
  `:290` (`IPLSTA <= entry < IPLSTA+N*2048`); MPR setup `:99–101`
  (MPR0 I/O, MPR1 work RAM `$2000–$3FFF`), `:577–593` (MPR2..6 bank switching).

## 3. Writer list ($2502 / $2503 / $2504)

| Writer | Kind | Evidence |
|---|---|---|
| CR-family ok paths (`$2B`/`$33`/`$2C`/`$2E`): `sta $2502` of the masked image | Intentional, in-contract (caller-seeded sync assumed) | **SOURCE_CONFIRMED** (§2 gate lines) |
| Synthetic-guest seeds (`sta $2502` / `sta $2503` in G5/CR harness payloads, e.g. `tests/integration/test_g5_irqon_2c.py:112–118`) | Caller-side test setup, never firmware | **SOURCE_CONFIRMED** |
| PUBLIC loader `read_sector`: `$5A` fill of BUF `$2200–$29FF` + every READ(6) sector transfer into BUF | Unintentional range writer; `$2502`/`$2503`/`$2504` = offsets `$0302`/`$0303`/`$0304` (arithmetic: `0x2502-0x2200=0x302`, in-range) | **SOURCE_CONFIRMED** |
| Commercial caller writes to `$2502`/`$2503` | — | **UNOBSERVED** (never read any commercial bytes) |
| Warm residual at `$2502`/`$2503` | — | **UNKNOWN** (no WRAM clear, no measurement) |
| Any firmware/BOOT/reset/glue intentional `$2502`/`$2503` seeding store | None exists | **SOURCE_CONFIRMED** absence (§2) |

"No writer" is withdrawn (the loader range writer exists); "no intentional
firmware CR-shadow init" stands. `$2503` is never written by any body
(publish-only `$2502`); `$2504` is family-reserved but loader-overwritten in
practice — reservation is convention, not protection.

## 4. Ownership / lifecycle

- `$2502` (CR-low image): owned by the *synchronized caller* during the CR-family
  call window; each ok path republishes the masked image before the VDC write.
- `$2503` (marker, `$A5` iff seeded): written only by the seeding party
  (synthetic harness today); read-only for every body. Records "someone stored
  `$A5` here" — nothing about sync (see §7).
- `$2504` (reserved, CR-high-image candidate): no body/guest target; loader
  overwrites it in practice.
- Validity period: from the seeding store to the next event that reuses the
  addresses — a loader staging cycle (`$5A` fill + sector transfer), any family
  ok-path publish, or reset/warm handoff (UNKNOWN). There is no validity token
  beyond the `$A5` byte itself, which is why §7 matters.
- Pre-IPLJMP, last-sector-wins: every body-sector READ(6) (plus the #670
  media-end pre-check final-sector read) fully overwrites BUF, so at IPLJMP the
  shadow cells hold the last-transferred sector's bytes at offsets
  `$0302–$0304` (**SOURCE_CONFIRMED** logic from `read_sector` + call sites
  `:401`, `:430`). Post-IPLJMP commercial behavior is **UNKNOWN** — the loaded
  program owns the machine and no commercial execution was observed.

## 5. Staging relation (why direct-address grep was insufficient)

`STA (ptr),Y` with `ptr=$04` walks the whole BUF; `grep -rn '\$2502'` can never
see it. Any audit that counts writers by direct address alone undercounts by at
least this one definite indirect writer (the #842 review MAJOR finding). Future
writer audits must include range writes: indirect `(ptr),Y` fills, DMA/transfer
targets, and copy loops whose destination range covers the cell — not just
absolute `sta $xxxx`.

## 6. Logical vs alias (IPLSTA disjointness)

- Logical: `$2502`/`$2503`/`$2504` < `$3000` = IPLSTA low bound, so no BODY load
  directly overlaps the shadow cells (**SOURCE_CONFIRMED** arithmetic, pinned by
  `test_iplsta_logically_disjoint_from_shadow`). Note the PUBLIC exclusive-load
  end `≤$E000` subtlety (equality allowed when IPLSTA low byte is `$00` per
  `validate_info` / `@eq`) does not affect this: the shadow is below, not above.
- Physical alias: the audited MPR selection (MPR1=`$F8` work RAM vs MPR2..6
  `$80+` bank switching, §2) shows no alias mechanism, but no universal
  non-alias theorem was proven — physical alias stays **UNKNOWN**, not a fact.
  Conflating "logically disjoint" with "physically isolated" is the exact error
  this section forbids.

## 7. Sync / false-positive conditions

- Gate semantics (**SOURCE_CONFIRMED**): the entry gate checks *only*
  `marker == $A5`; the ok path then trusts the shadow image blindly. The gate is
  necessary (without it every stale write lands) but not sufficient (a
  coincidental `$A5` still lands).
- SYNC condition (**SYNTHETIC_REPRODUCED**): ok-path correctness requires
  `marker == $A5 AND shadow == real_CR_at_entry`. Fixture: `$2B` with
  real=`$04` shadow=`$04` → CR=`$00`, non-target bits preserved.
- FALSE-OK mechanism (**SYNTHETIC_REPRODUCED**): stale shadow `$FF` + sector-
  residue `$A5` at the marker offset → ok path publishes the stale image.
  `$2B`: real=`$00` → CR=`$FB`; every non-target bit set comes from the stale
  image, not from the caller's intent.
- `$A5` may be sector-derived, not only random/warm (**HYPOTHESIS** grounded in
  the staging geometry: the marker offset holds sector bytes after READ(6), and
  `$A5` is one of 256 possible byte values — the old "~1/256 uniform" figure is
  withdrawn except as an explicitly conditional model, since sector bytes are
  not uniform random). `$A5` ≠ sync proof, in any of its four possible origins:
  harness seed (in-contract), sector residue, warm residual, caller data.
- `$5A` fill rejects cleanly (**SYNTHETIC_REPRODUCED**): pre-first-READ(6) BUF
  state takes the reject path with no write, for every family slot.

## 8. Damage impact (per-slot stale-image effect, stale `$FF`, real `$00`)

| Slot | Mask | Stale `$FF` → CR | Damage class |
|---|---|---|---|
| `$2B` | `AND $FB` | `$FB` — non-target bits 0,1,3–7 set from stale image | sets wrong bits (needs clear reasoning per bit) |
| `$33` | `AND $3F` | `$3F` — bits 0–5 set from stale image | sets wrong bits; and with a zero-like stale image CR reads `$00`, which **masks** the ok-path write (see below) |
| `$2C` | `ORA $08` | `$FF` — bit 3 forced plus all stale bits kept | keeps + sets wrong bits |
| `$2E` | `ORA $80` | `$FF` — bit 7 forced plus all stale bits kept | keeps + sets wrong bits |

`CR=0 is not reject proof` (**SYNTHETIC_REPRODUCED**): `$33` ok-path with a
zero-like stale image leaves CR=`$00` while having written — indistinguishable
on CR alone from a reject that left an already-zero CR untouched. Likewise a
commercial CR=0 held through `$33`/`$2C` (ExpF structural record, PR #840) is
*consistent with* an unseeded shadow but proves nothing: `$33` ok-write-zero
also yields CR=0 and a caller may rewrite CR between boundaries. Marker/shadow
values there are **UNKNOWN**.

## 9. L1–L6 evaluation (L6 rationale explicit)

- **L1 source-arithmetic grounding — DONE (SOURCE_CONFIRMED).** Geometry,
  offsets, gate shape, masks, MPR selection, absence of intentional init and
  of WRAM clear all read from tracked source (§2). Re-verified after merging
  origin/main `d3908245` (delta: only the unrelated #283 CD_FADE fixture).
- **L2 host-model synthetic repro — DONE in this PR (SYNTHETIC_REPRODUCED).**
  18 host tests: SYNC ok, FALSE-OK stale incl. `$2B` non-target damage, `$33`
  masking (CR=0 proves nothing), `$5A` reject-no-write, cold-known (`$5A` fill →
  reject) / post-read-UNKNOWN / warm-UNKNOWN judge behavior, plus structural
  pins (gate shape, masks, `$5A` fill) tying the model to the sources.
- **L3 synchronized synthetic real-core — CITED, not rerun.** P0 `$2B` D2
  evidence (`51b7cd55` local 47-test GREEN, `145b182e` review-fix, merge
  `c30146e1`, CI per Actions) and the `$2C` chain suites cover the synchronized
  contract. This survey adds no new L3 claim and reruns nothing commercial.
- **L4 desync/cold/warm synthetic real-core — PROPOSED (HYPOTHESIS-level
  design, acceptance criterion 6).** Seed desynced shadow + residue `$A5` on a
  synthetic core; assert the wrong-image CR write lands (RED) to confirm the
  L2 model end-to-end. Not run here.
- **L5 loader-staging interaction on synthetic real core — PROPOSED
  (acceptance criteria 1–2).** Drive `read_sector` against synthetic media and
  assert BUF offsets `$0302–$0304` track fill-then-sector contents across info
  / pre-check / body reads (last-sector-wins). Not run here.
- **L6 commercial-media confirmation — BLOCKED, with rationale.** L6 would ask
  whether a real disc ever places `$A5` at the marker offset with a desynced
  shadow. It is BLOCKED because (a) this task forbids `./rom` access in any
  form, (b) no new commercial measurement was Owner-permitted in this task, and
  (c) commercial WRAM was never acquired at any point — so the occurrence
  question is **UNOBSERVED**/**UNKNOWN** by construction, and Geargrafx
  observations would not prove hardware behavior anyway. L6 needs a separate
  Owner-permitted observation task with sanitized structural records only; it
  must never gate the L1–L3 findings above, which stand on their own grounds.

## 10. #843 impact (explicit: merge-blocking vs future independent item)

#843 (G5 P0 BOOT capacity/commonization, F1) asks to share CR-family
(`$33`/`$2C`/`$2B`) duplicate processing so full ExpG links within BOOT. This
survey's relation to it:

- **Not merge-blocking in either direction.** This PR is docs + a host-only
  model fixture: zero bytes of BOOT/CODE change, zero linker-map movement,
  zero contract change (`A/X/Y/P`, MPR, stack, IRQ, AR, CR-high, RCR, masks,
  shadow/marker discipline, reject-no-write all untouched). It cannot shrink
  or grow #843's 19 B gap, and #843's outcome cannot invalidate L1–L3 above
  (source readings + synthetic model of the *current* bodies).
- **Code sharing ≠ writer unification — do not conflate.** Even a fully
  commonized #843 implementation keeps the same four logical writers of
  `$2502` plus the loader range writer; commonization changes code layout, not
  the ownership/lifetime table in §3–§4. #843 must preserve the gate + publish
  order the L2 model pins; that is a constraint on #843, recorded here so it
  is not silently weakened later.
- **No real #843 hazard is deferred to FOLLOW_UP.** The hazards #843 tracks
  (BOOT overflow, contract drift, wiring/allowlist errors) are untouched by a
  docs-only diff — there is nothing of #843's to fix here, so nothing is being
  parked. Genuinely new follow-ups from *this* survey (L4/L5 runs, ownership
  resolution, validity mechanism) are #844's own, listed in §11 — they are
  future independent items, not #843 blockers, and none of them is a known
  live hazard being shelved: the latent false-ok risk lives in the
  experimental commercial path only (normal DEV unaffected, synthetic contract
  unaffected), exactly as #844 already records.

## 11. Future evidence needed (maps to #844 criteria 1–8)

1. Range-write audit pass over loader/DMA/copy loops (criterion 1; method §5).
2. Ownership/lifetime decision record — or explicit "unresolved, applicability
   limited to synchronized callers" (criteria 2–3; L5 feeds it).
3. Validity mechanism or explicit applicability limits for the marker (criterion
   4; must not be blind seeding/relocation without grounds).
4. Machine-checkable synchronized-caller preconditions (criterion 5; L4 feeds it).
5. L4 + L5 synthetic real-core runs recorded against their own SHAs (criterion 6).
6. L6 observation task — Owner-permitted, sanitized records only, only if the
   Owner wants the commercial question answered at all (criterion 6/§9-L6).

## 12. Repro steps (all host-only, no setup beyond a checkout)

```sh
git -C /tmp/g5-b-844-crshadow rev-parse HEAD          # expect d3908245
python3 /tmp/g5-b-844-falseok-demo.py                 # ephemeral narrative demo (untracked)
python3 -m unittest tests.host.test_844_crshadow_survey -v   # 18 tests, tracked fixture
grep -n 'lda $2503' src/ex_rcroff.s src/ex_dspoff.s src/ex_irqon.s src/overrides/slot_2E.s
grep -n 'lda #$5A\|sta (ptr),y' tests/integration/ipl_loader_rom.s
python3 -c "print(hex(0x2502-0x2200))"                # 0x302: inside BUF $2200-$29FF
git diff --check && python3 tools/check.py 2>/dev/null || true  # light checks (see §13)
```

## 13. Light-check record (Phase 1, this worktree)

- `python3 -m unittest tests.host.test_844_crshadow_survey -v`: 18/18 pass
  (pre-existing ephemeral demo also passes; superseded, not tracked).
- `git diff --check`: clean (no whitespace errors).
- Full `make test` / `make check` and Required CI run on the PR SHA (Phase 4),
  not claimed here.
