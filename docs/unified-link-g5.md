# Triple-link experimental BIOS: $02 CD_BASE proto -> $03 CD_READ link + $33 EX_DSPOFF (Issue #787)

**Status: EXPERIMENTAL DEV-ONLY. NOT a compatible System Card BIOS. NOT the genuine
CD_READ ABI. NOT game compatibility. NOT G5 success. `release_eligible=false`,
DEV_CALLABLE 0, reference JSR 0/81, ABI VERIFIED 0/81. Genuine ABI stays
UNKNOWN/BLOCKED; Issues #173/#367/#459 intact.**

Prior art: #783 (unified $02+$33, PR #784), #785 (CD_READ link V1, PR #786).
This issue wires the three existing fragments into ONE experimental image without
re-implementing them: `src/cd_base_proto.s`, `src/cd_read_link.s`,
`src/ex_dspoff.s` (via the `experimental_slot_33` glue) and the host mirror
`tools/cd_read_link.py` are reused unchanged.

## 1. What now executes (G5 contribution)

Before: the unified image booted synthetic media and a test payload could call
$02 (proto state) and $33 (display fragment), but any sector transfer still needed
test-seeded `$2400-$2402` plus a direct-JSR helper outside any slot. After: a test
payload calls `JSR $E006` (proto computes base state), then `JSR $E009` (link
consumes that state with block/window inputs -- no test pre-seeding of `$2400`),
which issues a real READ(6) of the sector AFTER the boot sector, transferring
exactly 2048 bytes to `$2800` (so a missing copy cannot compare equal), then
`JSR $E099` (dspoff), then halts clean. Order markers prove 02-then-03-then-33,
SP/MPRs intact, cold+warm repeatable.

Fake vs real distinction: every table entry is `JMP` (`0x4C`); every body starts
with real processing (`PHP/PHA...`), never `RTS`. A bare RTS at any entry could
return but could not produce the callee-exclusive side effects (proto state update,
LINK_RES + `$2800` transfer, dspoff shadow update + flag discipline).

## 2. The $03 experimental entry (NOT the genuine ABI)

The `$E009` table entry is redirected from the linked `$03` fallback stub to the
BOOT-region `cd_read_link_entry` body (same direct-redirect discipline as `$33`).
The wired entry keeps the link's EXPERIMENTAL contract only
(`docs/cd-read-link-v1.md` CD_READ_LINK_V1: proto base `$2400-$2402` + block
`$20F2-$20F4` + window `$20F5-$20F7`, `MPR1=$F8`, work-RAM stack, IRQs masked by
the caller). It never claims the genuine CD_READ ABI: slot/entry/return semantics
stay UNKNOWN/BLOCKED, `spec/api_contracts.json` is untouched, and the manifest
keeps `"cd_read_api_slot_03": "NOT_IMPLEMENTED"`. The experimental wiring is
recorded in the manifest `cd_read_link` block (`slot $03`, `entry $E009`,
`table_entry 0xE009`, link-derived `link_entry`, fallback stub, contract text).

## 3. Union mechanics (`tools/build_experimental_bios_boot.py`, extended)

- `REQUIRED` carries all three callees; link units add `src/cd_read_link.s`.
- Ordered fail-closed patches: slot-$02 5-byte `JMP proto+NOPs` first, then
  slot-$33 3-byte `$E099` table redirect, then slot-$03 3-byte `$E009` table
  redirect. Each patch verifies its pre-image (fallback bytes / linked stub) and
  the callee prologue (`PHP,PHA`); anything else refuses.
- Pre-patch bank0 still equals the normal DEV ROM across the first 4 KiB (all
  callees live in BOOT, offsets `>= 0x1000`); the normal ROM is byte-identical.
- Post-image API-area assertion allows EXACTLY the union of the three windows
  (5+3+3 bytes), requires all three windows to take effect, and verifies each
  byte-exact (coincidental single-byte equality tolerated, unexpected deltas fail).
- Manifest: `cd_read_link` native block, `labels` gain `api_slot_03`,
  `cd_read_link_entry`, `slot_03_table_entry`; `merged_heads` gains
  `origin/agent-b/cd-read-link-g5` (confirmed integrated HEAD = merge second
  parent when the ref is absent, ancestry-checked at build time, never a
  subject-match guess; `merge_commits` keeps the integrating merge separately;
  undeterminable cases are UNRESOLVED, never hardcoded); `unified_bios`
  records slots `$02/$03/$33`, patch order, and the linkage note (proto state
  flows into the link with no test seeding).

## 4. Harness-D consumption (`tools/g5_progress_model.py`, extended)

- Native `cd_read_link` block handling mirrors `$33`: slot derivation (with
  slot/entry cross-check), unanimous alias resolution (every present
  `link_entry`/body/routine/... plus labels/symbols `cd_read_link_entry` must
  agree; malformed/out-of-BOOT/disagreeing aliases BLOCKED, omission allowed),
  expected-target mapping, and a direct `$E009 -> link` verifier (wrong dest /
  fake RTS / stub-equality / missing target / BOOT self-loop / stray BOOT JMP /
  bad PHP,PHA prologue all BLOCKED; entry-shape check alone never proves
  execution/return). The pre-triple two-block manifest still validates
  (backward compatible).
- `tools/g5_harness.py` itself is unchanged (read-only consumer): variant D
  accepts the triple manifest through the same gates. Note: the harness CLI's
  synthetic-D run cross-checks the ROM's embedded medium against the
  harness-generated SEQ23 disc; a ROM built for a different (e.g. chain/refuse)
  payload BLOCKs there by construction -- the D measurement for such ROMs is the
  model validators plus the real-core runs below, exactly as for #784.
- G5 A/B/C/D comparison status (Issue #787 acceptance, NOT claimed complete):
  harness synthetic SEQ23 D with this triple ROM is BLOCKED by the embedded-vs-
  executed selection gate (different payload/disc by construction; recorded as
  BLOCKED, never PASS). A/B/C synthetic SEQ23 differential (1/2/3 APIs, fake
  RTS) is NOT re-measured in this PR (NOT_RUN here); D real processing
  (proto state without pre-seed, valid READ(6) + 2048B RAM match, dspoff return,
  order, cold/warm, next-blocker on refusals) is measured by the real-core
  chain/refuse cases below on their own synthetic media, not by the harness
  SEQ23 disc. Fake-RTS vs real progress is distinguished by callee-exclusive
  side effects (LINK_RES + `$2800` transfer vs no transfer), never by manifest
  declaration alone. A matched-selection harness A/B/C/D run remains open and
  needs Owner judgment (possible child issue); this doc does not claim it done.
- Commercial media: opt-in `--commercial` + safe detector reuse
  (`tools/commercial_media.py`: worktree-aware `rom/` resolution with main-
  worktree fallback, recursive CUE detection, symlink/traversal rejection).
  No CUE -> NOT_RUN. Any CUE present but without a commercial boot path in
  this image -> BLOCKED (media never opened, read, executed, or published:
  status and count only, never bytes/hashes/paths/traces).

## 5. Verification status

- Host (toolchain-free unless noted): `tests/host/test_unified_builder.py`
  (updated: 3 units, 3 windows, 3 merged refs + strict merge-parent/second-
  parent checks with UNRESOLVED on fake/ambiguous/shallow cases),
  `tests/host/test_unified_link_builder.py` (`$03` redirect, triple
  windows/manifest/model incl. BOOT-range/self-loop/prologue negatives,
  alias-unanimity negatives, and two-block backward compat),
  `tests/host/test_experimental_bios_boot.py` + `tests/host/test_ex_dspoff_wired.py`
  (updated: 3 units / 3 windows). Full `tests/host` discover in CI.
- Real-core (pinned `b49ae82a`, cc65 `71746c8`, synthetic only):
  `tests/integration/test_unified_link_realcore.py` (new) -- chain case (proto
  state without pre-seed, valid READ(6) + 2048-byte RAM match, dspoff return,
  order, 4-gate return proof, cold/warm) and refuse case (`$60/$62/$63/$64`,
  stage-1 refusals, no new SCSI DATA_IN, `$2800` untouched, state/SP preserved,
  cold/warm) -- plus current-model harness-D validators (triple manifest,
  byte/SHA verification, per-slot targets, tamper negatives) and a sanitized
  commercial NOT_RUN/BLOCKED gate. Requires pinned Linux + cc65 + Geargrafx;
  SKIPs otherwise. Genuine ABI stays UNKNOWN/BLOCKED.

## 6. Remaining blocker (single next item)

A commercial-disc boot path in an experimental image (game IPL load plus
CD_BASE/CD_READ with genuine layouts). The triple image only boots its embedded
synthetic selection record, so commercial media -- even when present -- has no
boot path here. That work needs genuine-ABI research under the still-closed
safety gates (#173/#367/#459); it is out of scope for this issue.

## 7. Follow-up matched-selection verification (merged 2026-11-13)

[PR #790](https://github.com/mao2009/oss-pce-cd-bios/pull/790), following this issue's [PR #788](https://github.com/mao2009/oss-pce-cd-bios/pull/788), adds an A/B/C/D **same-synthetic-medium** comparison with cold/warm Geargrafx evidence. In #790's dedicated comparison payload the call sequence is `$02 -> $33 -> $03`; this is intentionally distinct from the `$02 -> $03 -> $33` sequence documented above for #788. A/B/C demonstrate baseline and fake-RTS stopper positions, while D demonstrates three real *experimental* effects and a 2,048-byte READ(6) sector transfer. New matched-selection verification covers IPL INFO-before-BODY PHASE order, ROM-hash-bound SEL, D track/media identity, mandatory payload hash/length, DSP shadow, link transfer mutation negatives and SP/MPR1 observations.

All four GitHub workflow runs for #790 head `38e00a53` completed successfully; the PR reported 50 focused host cases and nine pinned real-core integration cases. **Those are earlier PR results, not rerun by this documentation update.** Commercial comparison remained NOT_RUN (no opt-in). Neither the reference ABI nor a retail title screen is proven. For full evidence scope and limitations, see [2026-11-13 G5 progress](g5-progress-2026-11-13.md).
