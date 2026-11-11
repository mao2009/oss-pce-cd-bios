# Issue #726: #367 evidence handoff and transfer-safety scope match (P11)

Parent: #473. Refs: #367, #419, #648, #714, `docs/367-block-transfer-audit.md`,
`docs/research/367-device-write-audit.md`, `docs/648-block-transfer-prestart-midtransfer.md`.

Status: **ledger only, BLOCKED**. No production mechanism, no production gate change,
no gate opening for #173 / #367 / #459. `release_eligible=false`, `BLOCKED_ABI`
unchanged, ABI VERIFIED 0/81, DEV_CALLABLE 0, reference JSR permitted 0/81 unchanged.
This project is not a compatible System Card BIOS.

Evidence categories in this file: host-model checks (synthetic inputs, no emulator)
and synthetic-disc real-core diagnostics with verification-only patches on the pinned
Geargrafx core `b49ae82a012eade566d72e9d61c9d95a5caa4da3`. No private `./rom/`,
no commercial BIOS/ROM bytes, no reference JSR execution, no real hardware.
Geargrafx observations are not proof of physical hardware behaviour.
G5-class: **NOT G5 success** — no commercial-game compatibility claim.

PR #714 is design only. Production method selection and any #173/#367/#459 gate
release require separate Owner approval. This ledger does not approve them.

## Acceptance mapping (issue #726)

- [x] multi-byte, other-bank, TIA/TAI order, device-origin DMA W17 each list
      #367 source / observed version / judgment (§1 table).
- [x] Unmeasured, evidence-insufficient, and verification-patch-only rows are
      explicitly excluded from SAFE (§2). Unmeasured ranges are never promoted
      to SAFE.
- [x] Guest CPU writes and DMA/device-origin writes are not confused (§3).
- [x] #367 incompleteness caps transfer safety claims at SAFE_IN_SCOPE (§4).
- [x] Contradiction review vs #367 is recorded; no Owner-level judgment is
      taken unilaterally (§5).

## 1. Item-by-item handoff table

`Observed version` is the pinned source or driver revision this ledger was
checked against at base `864d979c`. `Judgment` is the #367-side verdict.
`Handoff ceiling` is the maximum claim #473 may consume; it is never higher
than the judgment.

| #726 item | #367 source (exact) | Observed version / case set | Judgment | #473 handoff ceiling |
|---|---|---|---|---|
| multi-byte (length > 1, same transfer) | `tools/block_xfer_audit.py` `destination_set` + `docs/367-block-transfer-audit.md` "ブロック転送の書込み先モデル" | Static model only. TII/TDD/TIN exact sets, TIA over-approx `dst..dst+length` (+1), TAI dest = TII set, length 0 = 0x10000 worst case. Host suite `tests/host/test_block_xfer_audit.py` (19 tests): `verdict` is `REFUSED_STATIC` / `PERMITTED_STATIC_ONLY`, `gate` always `BLOCKED`, `mid_instruction_stop` always `UNMEASURED`. | Static only, BLOCKED, UNMEASURED | **Not SAFE**. Static range check alone is not a stop proof. |
| multi-byte (real-core, lengths 1/2/3/4/8) | `tests/integration/geargrafx_block_transfer_matrix.py` + `tests/host/test_367_matrix_runner.py` | 51-case matrix: 25 length cases (5 ops x 1/2/3/4/8), 4 page-boundary, 1 reverse (`tdd-reverse-4000`), 1 physical alias, 1 overlap, 2 TAM, 1 `tii-len0` (deny path), 15 deny (`{op}-deny-{hw,rom,backup}`), 1 `tia-deny-nonmono`. Deny predicate = 18 cases (`DENY_CASES` + `tii-len0` + `tii-tam-deny` + `tia-deny-nonmono`), allow = 33. Verification-only preflight probe; production gate unchanged. | Verification-only, gate stays BLOCKED | **At most SAFE_IN_SCOPE for pre-start refusal only** (nothing changed yet, §4). Lengths outside 1/2/3/4/8 (except len0-as-deny) are UNKNOWN. |
| multi-byte (real-core, length-3 page crossing, per-byte) | `tests/integration/geargrafx_block_midrefusal.py` (`CROSS` / `CROSS_ALL`, 30 cases: 5 ops x cross/srcread/srcreadff x armed/unguarded) + `tests/host/test_367_midrefusal.py` (14 tests) | `cross-armed` length 3 crossing `$3FFF/$4000` (MPR2=`$FF`): exactly the `$FF`-page dest bytes refused; allowed WRAM bytes before AND after a refused byte still land; TransferEnd restores A/X/Y, S balanced, VDC AR/R0/R7/SR unchanged. `cross-unguarded` RED: `$FF` bytes land on VDC (AR set, 5-bit masked). | Per-byte refusal does NOT stop the transfer; continuation measured. BLOCKED (must not resume). | **BLOCKED, never SAFE**. Proves a dest-only per-byte guard is not a safe stop. |
| other-bank (hardware page `$FF`) | Same three drivers + #648 policy T | Matrix `deny-hw` (5 ops), midrefusal `cross` (`$4000` dest, MPR2=`$FF`), preblock `tia-wrap-forbidden` / `tii-hwpage-fetch-blocked`, #648 pre-start range check (any byte in non-`$F8` page refused before fetch; unpeekable refused fail-closed). Static host test `test_hardware_page_refused_even_when_bank_listed`. | Measured only for `$FF`-page dest via verification-only patch. BLOCKED. | Pre-start refusal at most SAFE_IN_SCOPE; mid-transfer refusal BLOCKED. Non-`$FF` forbidden RAM and MPR-alias holes are UNKNOWN (see §2). |
| other-bank (ROM bank, backup/BRAM) | Matrix `deny-rom` / `deny-backup` (5 ops each) + static `test_rom_bank_not_in_allowed_set_refused` | Deny path for ROM-mapped and backup-mapped dests in the matrix; static window+allowlist refusal. Verification-only. | Verification-only refusal, BLOCKED | Same ceiling as above. Not a production allowlist. |
| bank-cross boundary + wrap | Matrix `tii-boundary-1fff`, `tia-boundary-3fff`, `tii-boundary-7fff`, `tai-boundary-ffff`, `tdd-reverse-4000`, `tii-boundary-alias`; host static `test_tdd_walks_down_with_wrap`, `test_tii_walks_up` | Boundary-crossing fixtures measured in matrix (verification-only). 16-bit wrap is exact in the static model; real-core wrap coverage is only the listed boundary fixtures. `tii-len0` routes to deny (worst-case 0x10000, exact length-0 rule UNCONFIRMED). | Partial. Full 64 KiB wrap and length-0 exact rule UNMEASURED. | **BLOCKED / UNKNOWN** for unlisted wrap/len0 behaviour. Never SAFE. |
| TIA/TAI order (alternating) | `CROSS` / `CROSS_ALL` per-op sequences in `geargrafx_block_midrefusal.py` + `addr_lists` in matrix + static over-approx note | Length-3 measured walks differ per opcode (`tii==tai` dest, `tdd` descending, `tia` middle-byte `$4000`, `CROSS_ALL` source alternation for `tai`/`tia`). Static model deliberately does NOT assert pair order or base advance (UNCONFIRMED) and covers TIA with `dst..dst+length` superset. Host test `test_cross_sequences_are_distinct_per_op` pins `tii==tai`, `tii!=tdd`. | Length-3 order observed (verification-only); general alternating order/base advance UNCONFIRMED. | **BLOCKED**. The superset is conservative (over-refusal allowed); allow must never be optimistic. Longer multi-byte TIA/TAI order is UNKNOWN. |
| device-origin DMA W17 (source audit) | `docs/research/367-device-write-audit.md` + `tests/fixtures/367_device_write_sites.tsv` (25 rows) + `tests/host/test_367_device_write_audit.py` (10 tests) | Searched scope only (pinned `b49ae82a` tool dir, read-only). DEV-01/02/03 `device_store` to ADPCM_RAM/VRAM/VDC_SAT: `NOT_MAIN_RAM`. DEV-04..10 `reset_store`: `RESET_ONLY`. DEV-11 + CPU-01..06 `cpu_store`: `CPU_ORIGINATED`. HOST-01..03 `host_store`: `HOST_ONLY`. BLK-01..05 `cpu_block_store`: `PER_BYTE_STEP`. `runtime_wram_device_writers` = []. `UNKNOWN` rows = []. Former UNK-01/02 resolved as CPU-01/02. Doc lists every row (group expansion check) and the mutant `device_store→WRAM` is reported. | No runtime device-autonomous writer to WRAM **in the searched scope**. Scope limits remain (function-pointer/macro-indirect writes not excluded; state-restore path separate). Real-core device-DMA measurement UNMEASURED. | **UNKNOWN / BLOCKED, never SAFE and never PASS**. #648 explicitly lists device-originated RAM writes as Not covered. |
| transfer-source read side effect | Midrefusal `srcread` / `srcreadff` (source `$4104` = VDC status, read clears it) | `srcread-armed` (dest WRAM allowed): dest receives status value, status clears — dest-only guard cannot prevent the read side effect. `srcreadff-armed` (dest `$FF` refused): refusal skips source read too, status not cleared, AR unchanged. `srcread-unguarded` controls pin landing. | Read side effect measured (verification-only). | **BLOCKED**. A destination-only hook is insufficient; no SAFE claim. |
| whole-instruction preblock (boundary) | `tests/integration/geargrafx_block_preblock.py` (#742, 7 armed families + `pos-write`, resume 3/3) + `tests/host/test_block_preblock.py` (44 tests) | `tii-entry`, `tdd-unknown`, `tin-unknown-far`, `tia-wrap-forbidden`, `tai-mpr2-unknown`, `tii-hwpage-fetch-blocked`, `block-irq-handler`: block opcode refused at instruction boundary before the first step via side-effect-free peek; 7 opcode+operand bytes captured; resume re-refused 3/3; `pos-write` lands unflagged. Verification-only monitor, MPR0 never `$FF`. | Verification-only boundary refusal. | At most SAFE_IN_SCOPE for the refused-before-first-step families; any mid-transfer stop stays BLOCKED. Not a production gate. |
| #648 T method (pre-start vs mid-transfer) | `docs/648-block-transfer-prestart-midtransfer.md` (`make stack-s6`) | Policy AT: pre-start peeks 6 operands (TDD decrement, TIN fixed, TIA alternate dest, TAI alternate src, len0=65536), refuses whole instruction before fetch (incl. own-7-byte overlap and unpeekable fail-closed) + MPR1=`$F8` precondition; mid-transfer opcode+MPR check. Mutants `mut-tresume/tnomid/tfirst/tseq/tnoself` pin necessity. Expectations fixed from pinned source + supplementary MinGW build; final probe revision requires CI `verify` (until then, expectations). | Pre-start refusal can be SAFE_IN_SCOPE; mid-transfer refusal BLOCKED (bytes already written counted, not undone; no resume). Device DMA, ST0/1/2, newly-asserted-inside-step IRQ, libretro, real hardware UNKNOWN. | Consumed exactly as stated: **pre-start at most SAFE_IN_SCOPE, mid-transfer BLOCKED**. |

## 2. Explicitly NOT SAFE (unmeasured / insufficient / patch-only)

The following are never promoted to SAFE, and no transfer safety claim may
cite them as a stop proof:

- Static `tools/block_xfer_audit.py` results (`REFUSED_STATIC` /
  `PERMITTED_STATIC_ONLY`, gate `BLOCKED`, `mid_instruction_stop UNMEASURED`).
- Any verification-only patch behaviour (block-write guard, midrefusal per-byte
  hook, matrix preflight, preblock monitor, #648 T): the refusal policy lives
  in the probe, not in a production gate. The hook point being pre-write does
  not make the stopped state safe to resume.
- Per-byte mid-transfer refusal (`cross-armed` continuation: post-refusal bytes
  land, TransferEnd runs). Not a safe stop.
- Lengths outside the measured 1/2/3/4/8 set (except `tii-len0` as a deny-path
  worst case). Length-0 exact rule, 16-bit wrap outside the listed boundary
  fixtures, and full 64 KiB behaviour are UNMEASURED / UNKNOWN.
- TIA/TAI alternating order and base advance beyond the length-3 observed walks.
  The static superset is conservative by design.
- Non-`$FF` forbidden RAM, MPR-alias holes (e.g. MPR0 fetch hole), `m_mpr_buffer`
  beyond #654, and any TAM/IRQ path that changes MPR mid-transfer outside the
  #648 measured paths. #648 mid-transfer MPR table is consumed as stated;
  TAM-inside-transfer via self-overwrite is refused pre-start, otherwise UNKNOWN.
- Device-originated RAM writes (W17): no real-core measurement exists. Fixture
  verdicts are source-audit records in the searched scope, not execution proof.
- ST0/ST1/ST2 (`WriteFromCPU` bypass), newly asserted IRQ inside a transfer
  step, libretro build, real hardware: UNKNOWN, never PASS.
- Push/pop-only guards (#459): push refusal leaves TransferEnd pops reading
  `$2102/$2103/$2104` (A/X/Y destroyed, VDC status cleared). Not a safe stop;
  gate stays BLOCKED.

## 3. Guest CPU writes vs DMA/device-origin writes (no confusion)

- Guest CPU writes reach RAM through `Memory::Write` / register writes and are
  recorded as `cpu_store` (DEV-11, CPU-01..06) or `cpu_block_store` (BLK-01..05,
  one byte per `RunInstruction` step, `PER_BYTE_STEP`). Block transfers
  (TII/TIN/TDD/TIA/TAI) are CPU-originated multi-write instructions: one
  instruction performs `length` byte stores with control returning between
  steps (source structure confirmed, stop safety NOT confirmed).
- DMA/device-origin writes are recorded as `device_store` only when no CPU
  store is in the same call. In the searched scope none reaches WRAM at runtime:
  DEV-01 ADPCM_RAM, DEV-02 VRAM, DEV-03 VDC_SAT (`NOT_MAIN_RAM`); DEV-04..10 are
  `Memory::Reset` fills (`RESET_ONLY`); UNK-01/02 were resolved to CPU-01/02
  (`CPU_ORIGINATED` via `Input::WriteO` / mapper `Memory::Write`), not
  device-autonomous. Host-only `HOST-01..03` (`HOST_ONLY`) are not device DMA.
- The host check `runtime_wram_device_writers` (role `device_store` /
  `unknown_store` with target WRAM) is empty, and any `UNKNOWN` row would fail
  the `UNKNOWN`-is-not-safe test. CPU-04 (`cpu_store` → WRAM via
  `memory_inline.h:293`) is real but gated by the same MPR/bank policy as any
  CPU store; it must not be mislabelled as device-autonomous.
- Consequence for #473: CPU-path guards (A/D/E/T/C) say nothing about device
  paths. Device DMA stays on its own UNKNOWN/BLOCKED row.

## 4. #367 incompleteness caps transfer claims

#367 is consumed as an audit with residual BLOCKED reasons (no mid-instruction
safe-stop proof, no production gate, verification-only patches, searched-scope
source audit, no device-DMA execution). Therefore every transfer safety claim
handed to #473 keeps **SAFE_IN_SCOPE as the absolute ceiling**, and only for
the pre-start / boundary-before-first-step refusals where nothing has changed
yet (T pre-start, #742 boundary families, matrix/T preflight deny path). All
mid-transfer stops, per-byte skips, abort-after-fetch paths (#613 re-evaluated
in #648: PC/state differ, guest continues — neither SAFE_IN_SCOPE), and any
row in §2 are BLOCKED or UNKNOWN. No row in this ledger opens a gate.

## 5. Contradiction review (no unilateral Owner judgment)

Checked at base `864d979c`: `docs/367-block-transfer-audit.md` (30-case #612
record vs 40-case #613 extension distinguished; TII 1-byte boundary observed,
TIN/TDD/TIA/TAI equivalents + whole-instruction safe stop UNMEASURED),
`docs/research/367-device-write-audit.md` + fixture (25 rows, no UNKNOWN), and
`docs/648-block-transfer-prestart-midtransfer.md` (pre-start SAFE_IN_SCOPE vs
mid-transfer BLOCKED; #613 neither SAFE_IN_SCOPE). No contradiction requiring
an Owner decision was found. Known intentional conservatism is not a
contradiction: the static TIA `dst..dst+length(+1)` superset over-refuses
rather than risking an optimistic allow, and the length-3 per-op walks
(`CROSS`/`CROSS_ALL`) do not upgrade the general order claim. If a future
measurement contradicts this ledger, the fix proposal goes to the Owner; this
PR takes no gate decision.

## 6. What was (not) run for this ledger

- Host-only: `tests/host/test_block_xfer_audit.py`,
  `tests/host/test_367_device_write_audit.py`,
  `tests/host/test_367_matrix_runner.py`,
  `tests/host/test_367_midrefusal.py`,
  `tests/host/test_block_preblock.py`, and the new
  `tests/host/test_726_367_evidence.py` (ledger consistency, no emulator).
- Real-core suites (`make block-write-guard`, `make block-transfer-matrix`,
  `make block-mid-refusal`, `make block-preblock`, `make stack-s6`,
  `make integration` / `make test-real-core`) were NOT re-run here; their
  results are cited from the pinned drivers and docs above, with the #648
  CI-`verify`-until-passed caveat inherited where stated.
- No `make setup` network fetch, no private media, no BIOS execution, no game
  boot, no second emulator, no hardware run.
