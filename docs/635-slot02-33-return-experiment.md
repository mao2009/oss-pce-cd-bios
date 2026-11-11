# Issue #758: slots $02+$33 RETURNING experiment (SIMULATED, DEV-only)

**NOT System Card CD_BASE/EX_DSPOFF. NOT a BIOS ABI. NOT game compatibility. NOT G5 success.**

## Question

After #756 (hypothetical slot-$02 return leads to a JSR to slot `$33`),
what does the IPL do if slot `$33` *also* returns? The genuine callees are
UNKNOWN/BLOCKED (#735 NOT_READY, #186 BLOCKED, #173 gate), so both returns
are FABRICATED and every result is `SIMULATED_*` / CONDITIONAL.

## Method (containment, #757 method reused)

One distinctly named loader variant (`...-slot02-33ret.pce`) is built through
the NORMAL pipeline, then EXACTLY the 5 fallback bytes of `api_slot_02` AND
`api_slot_33` are replaced with the assembled immediate-RTS stub. The
whole-image diff is asserted to be exactly those two windows (10 bytes at the
label-derived `$E10A` and `$E1F0`, verified byte-for-byte against the base
image: `A2 02/33 4C 45 E2` -> `60 EA EA EA EA`, same `api_unimplemented`
`$E245` for both). Hardcoded patch offsets are forbidden.

## Comparator discipline (Cycle 3 Phase B)

- Cold/warm call fields must match exactly: table membership, slot,
  caller/entry PCs, SP before/after + delta, entry A/X/Y/P, MPR, and the
  internal return flags. Step counts are never compared. Any mismatch is
  `SIMULATED_DIVERGENT`, never a success.
- Only two (slots, issue) combinations exist: `(0x02,)`/#756 and
  `(0x02, 0x33)`/#758. Anything else (other slots, order, duplicates,
  mismatched issue) is refused fail-closed: no generic any-slot patcher.

## Return-evidence discipline (from #756 Phase A)

- `stack_check == MATCH` is internal stack consistency only, never proof of a
  genuine return. Real returns are proven by resumption + the next call.
- `SIMULATED_NEXT_API` requires the halt PC to equal the verified stub and
  the halt X to equal the last call slot, in BOTH cycles; the last call is
  non-returning by construction.
- Exit codes follow the shared table in `docs/635-slot02-return-experiment.md`
  (only completed conditional observations exit 0). The commercial path
  requires the matching synthetic PASS receipt for slots `(0x02, 0x33)`
  (issue 758); stale or missing receipts BLOCK the run.

## Synthetic proof (three-way differential)

`make slot02-return-integration` (PUBLIC, cold+warm) with the SEQ23 payload
(JSR `$E006`; JSR `$E099`; JSR `$E009`):

- unpatched base: stops at the FIRST API (stub X=2, 1 APICALL);
- slot-02-only patch: stops at the SECOND API (stub X=51, 2 APICALLs);
- both patched: both return, THIRD call observed (stub X=3, 3 APICALLs),
  resumption markers AA/CC/DD, full SP restoration.

Each step of the differential is explained by the allowed patches only.

## Conditional commercial run (opt-in, local-only)

Same read-only/sanitized/private-output discipline as #635/#756, bounded
budget, fail-closed. Observed: `SIMULATED_NEXT_API` with post-return sequence
`[(2,'JSR'),(51,'JSR'),(74,'JSR')]` in both cycles — after two hypothetical
returns the IPL's next call is a JSR to entry `$E0DE` (slot 74 = `$4A`,
candidate `KEY_BIOS / EX_MEMOPEN`), halting at the stub with X=74. No
truncation, no budget, deterministic across runs. Per Phase C the RTS chain
is NOT extended further without Owner direction.
