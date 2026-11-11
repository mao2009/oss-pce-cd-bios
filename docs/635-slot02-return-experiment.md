# Issue #756: slot-$02 RETURNING experiment (SIMULATED, DEV-only)

**NOT System Card CD_BASE. NOT a BIOS ABI. NOT game compatibility. NOT G5 success.**

## Question

After the #635 finding (commercial IPL's first call is a JSR to slot `$02`,
entry `$E006`, cold/warm identical, halted at the non-returning stub), what
does the IPL do if slot `$02` *returns*? The genuine callee is UNKNOWN/BLOCKED
(#186, #735 NOT_READY, #173 gate), so the return is FABRICATED and every
result is reported as `SIMULATED_*` / CONDITIONAL.

## Method (containment)

- A distinctly named loader variant (`dev-ipl-loader-bios-not-compatible-slot02ret.pce`)
  is built through the NORMAL pipeline, then EXACTLY 5 bytes — the
  `api_slot_02` fallback (`ldx #$02; jmp api_unimplemented`) — are replaced
  with the assembled `tests/integration/slot02_return_stub.s` (immediate RTS +
  NOP pad). Whole-image diff is asserted to be exactly those 5 bytes; all
  labels, vectors, loader code, and other slots are untouched.
- The stub source lives OUTSIDE `src/overrides/` (never linked into the normal
  DEV ROM or normal loaders). No `src/overrides/slot_02.s` exists; the normal
  images are byte-identical (guarded by host tests + the base-image control).
- Fabrication assumption (explicit): slot `$02` returns with all registers,
  flags, MPRs and RAM preserved; no side effects. Post-return behavior is
  CONDITIONAL on this assumption.

## What counts as return evidence (A-2)

- `stack_check == MATCH` / `jsr_confirmed` prove only INTERNAL stack
  consistency: a JSR pushed `caller+2` while MPR1 selected the WRAM bank.
  They never prove a callee executed a genuine return (on the base ROM the
  same MATCH is observed while the stub never returns).
- An actual control return is proven only by POST-RETURN execution: the
  resumption marker (`A=$CC`), SP restoration, and the next observed call.
- The LAST call of a stub-halted sequence is NON-RETURNING by construction;
  `SIMULATED_NEXT_API` claims only the slot-$02 return (via resumption),
  never the return of the final call.
- `SIMULATED_NEXT_API` additionally requires the halt PC to equal the
  verified stub address and the halt X to equal the last call slot, in BOTH
  cycles. A stub halt after a single recorded slot-2 call, or mixed
  stub/non-stub halt outcomes, is classified as unexpected/divergent, never
  as a clean return.

## Synthetic proof (instrument check)

`make slot02-return-integration` (PUBLIC loader, cold+warm):

- `seq_return`: JSR `$E006` returns, resumption marker observed, JSR `$E009`
  follows in order, SP balanced, payload halt reached.
- `seq_control`: the SAME payload on the unpatched base image halts at
  `api_unimplemented` X=2 with exactly 1 APICALL (the difference comes from
  the 5-byte patch, nothing else).
- `spin2_budget`: post-return spin is a budget, never a halt.

## Conditional commercial run (opt-in, local-only)

`geargrafx_slot02_return.py --commercial --commercial-auto-loader
--commercial-loader-format public --commercial-output <private-parent>/<new-file>`
with the same read-only/sanitized discipline as #635. Verdicts:

- `SIMULATED_NEXT_API`: ordered post-return call sequence ending at a stub halt
- `SIMULATED_RETURN_THEN_HALT`: returned, then a non-stub halt, no further API
- `SIMULATED_PRE_HALT`: halted before any API (experiment changed nothing)
- `SIMULATED_UNEXPECTED_FIRST` / `SIMULATED_DIVERGENT` / `SIMULATED_BUDGET` /
  `SIMULATED_INCOMPLETE` / `FAIL`: assumption violated or no usable result

Evidence JSON carries the fabrication assumption and the `SIMULATED_` prefix;
media bytes/paths/hashes/titles and raw logs are never recorded. Reference
BIOS is never mounted (JSR count 0). `release_eligible=false` and
`BLOCKED_ABI` are unchanged.
