# Pure System Card 3.0 BIOS: reference test scenario plan (not execution evidence)

This plan is deliberately **test-first**: generate a complete *candidate inventory* for all
81 main jump-table slots and the nine identified PSG subcommands, before implementing
or running an OSS BIOS service.

Generate locally:

```sh
python3 tools/reference_bios_scenarios.py --check
python3 tools/reference_bios_scenarios.py --output build/reference-bios/scenarios.json
python3 -m unittest tests.host.test_reference_bios_scenarios -v
```

The generator is **host-only**, reads `spec/api_slots.json` and does not read or run a BIOS.
Its output is a plan, **not a set of 270 executed test cases**, observations, expectations,
verified APIs, or hardware-compatibility proof.

## Protocol before a scenario may be run

1. Manually establish target BIOS edition and provenance, choose a pinned emulator
   and a separate isolated instance; record their hashes locally. Only use a BIOS
   image the user is entitled to use. Never put its bytes in Git, CI, logs or artifacts.
2. Confirm each candidate slot/PSG selector and its calling ABI from independent evidence.
   The `0xE000 + 3 * slot` entry is only a hypothesis. The `0x48` PSG
   selectors are candidates, not independently validated dispatch semantics.
3. Replace `TO_BE_DETERMINED_FROM_ABI` with a *reviewed* per-API input fixture, set
   bounded instruction/frame budgets and a RAM write allowlist, and establish a way
   to reset state between cases. For a reset, jump, write, format, or destructive API,
   isolate storage/disc side effects and verify rollback behavior.
4. Implement an explicit *test-ROM* call wrapper, plus a real-core observer with
   registers, flags, SP, PC, MPR, bounded RAM diffs, and device-specific state.
   A host-generated constant or guest self-reported marker alone is not enough.
5. Execute the same fixture against the private reference BIOS. Store only
   non-copyrightable functional measurements and provenance metadata in a local,
   opt-in evidence store. Avoid dumping BIOS RAM/ROM contents.
6. Compare the OSS BIOS against the observed contract and report `MATCH`,
   `DIFF`, `NOT_RUN` and `BLOCKED` separately. Do not translate these
   into global compatibility `VERIFIED` without meeting the provenance gate.

## Scope and limits

- **81+9 definitions** each contain nominal, boundary and invalid/timeout
  *placeholders* requiring API-specific review. They are not runnable programs.
- **Zero** retail BIOS tests have been run by this PR.
- The existing ABI ledger is authoritative about **uncertainty**; never fill
  unknown return values or side effects by guesswork.
- CD tests require independent synthetic media (CUE/BIN); private retail games
  and BIOS images are neither fixtures nor repository assets.
- No modification is made to `spec/api_contracts.json`, executable BIOS,
  release gates, attestation verification, or compatibility claims.

## Next independent issues

Build a versioned schema for individual input fixtures, a guarded Geargrafx
standalone execution adapter, local-only capture of pure functional outputs, and
a differential result comparator. Prioritize EX_GETVER, BCD conversions and
arithmetic functions for low-risk end-to-end examples, followed by CD_BASE,
CD_READ and CD_EXEC once the call contract is sufficiently understood.
