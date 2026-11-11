# Issue #717 (P02 of #473): side-effect-free peek and fail-closed operand fetch — measurement and design

Status: **design + host-model measurement + one fresh real-core run**.
No production guard is wired. No production accessor is added. No change to
`src/`. `#173` / `#367` / `#459` remain BLOCKED. `release_eligible=false`,
ABI VERIFIED 0/81, DEV_CALLABLE 0, referenced JSR 0/81 are unchanged. The
project is not a compatible System Card BIOS.

- Issue: #717 (parent #473, plan item P02). Dependencies #647 (D) / #648 (T).
  P01 wiring positions are inherited from #716, not re-decided here.
- Pinned Geargrafx: `b49ae82a012eade566d72e9d61c9d95a5caa4da3` (source-built
  debugger core, local GPL-3.0 link; no core source is copied into this
  repository). Synthetic BIOS/CD only. No private `./rom/`, no commercial
  BIOS/ROM bytes, no reference JSR. Geargrafx observations are not proof of
  physical hardware behaviour (real hardware #655 UNKNOWN).
- Evidence classes are separated in every section below: `HOST_MODEL`
  (this document + `tools/peek_table.py` + `tests/host/test_717*`),
  `REAL_CORE_EXECUTION` (pinned debugger core, synthetic media,
  verification-only probe), `PINNED_SOURCE_READING` (restated contract of
  the core source, no code copied), `NOT_RUN` / `UNKNOWN` (never PASS).

## 1. What was verified

| #717 acceptance item | Where | Class | Result |
|---|---|---|---|
| peek possible/impossible addresses, MPR alias, bank boundary, memory types | §2 table + `tools/peek_table.py::is_peekable` + `test_717` `PeekTableTests` | HOST_MODEL + PINNED_SOURCE_READING | table fixed; WRAM peekable, hardware page and mapper banks fail closed |
| opcode/operand fetch order, fail-closed when unpeekable | §3 + `fetch_d` / `fetch_t` + `FetchOrderTests` | HOST_MODEL | opcode-first, increasing order; any unpeekable byte fails the whole fetch |
| fetch itself causes no VDC-status etc side effect, pos/neg on the real core | §4 + fresh `block_preblock` run (§4.1) + recorded `st_boundary` hazard rows | REAL_CORE_EXECUTION (synthetic) | armed refusals leave VDC AR/SR/R/READ_BUFFER unchanged; unguarded/unsafe-read runs show the contrast |
| byte ranges (TDD, TIN, TIA/TAI, len=0) consistent with the follow-up transfer plan | §5 + `transfer_sequences` + `ByteRangeTests` vs `addr_lists` | HOST_MODEL (matrix-consistent) | exact match on lengths 1/2/3/4/8; len=0 = 65536 computed but never executed |
| production wiring / accessor enablement | §6 | NOT_RUN (owner decision) | no `src/` change, no accessor added, gate stays BLOCKED |

## 2. Peek-possibility table (pinned-source reading, host-model judgment)

Restated from the pinned `Memory::TryPeek` contract (two overloads in
`memory_inline.h`, `b49ae82`): the call takes `(address)` or
`(address, bank)`, resolves `bank = m_mpr[address >> 13]` in the first form,
and then, outside the `GG_TESTING` test build:

1. `bank == 0xFF` returns `false` immediately (hardware page: no device
   access, no `ReadRegister`, no `io_buffer` update, no cycle injection).
2. `bank < 0x80` with a mapper installed is delegated to the mapper peek
   (`SF2` / `Arcade Card`); without that mapper it returns `false`. The
   synthetic fixtures install no mapper.
3. A null `m_memory_map[bank]` returns `false`.
4. Otherwise the byte is read directly from `m_memory_map[bank][offset]`
   with no memory hooks, no device-register read and no state change.

A successful peek therefore never calls `Memory::Read`, never calls
`HuC6270::ReadRegister` (whose status case clears `m_status_register` and
deasserts IRQ1, and whose data-MSB case with `AR == VRR` queues a VRAM
read), never touches `m_io_buffer`, and never fires hooks. The hardware-page
`Read` path, by contrast, does all of those things (see §4 for the measured
contrast via the `st_boundary` unsafe-read hazard).

| CPU address | MPR-resolved bank | Memory type (synthetic setup) | `TryPeek` | Notes |
|---|---|---|---|---|
| `$0000-$1FFF` | MPR0 (`$00` in fixtures) | ROM / zero-page backing | peekable | page 0 is ROM in the measured setup; never the hardware page (fixtures pin MPR0) |
| `$2000-$3FFF` | MPR1 (`$F8` steady state) | WRAM | peekable | D/T fetch pages in all armed families |
| `$4000-$5FFF` | MPR2 (guest-remapped) | WRAM when `$F8`, hardware page when `$FF`, unmapped otherwise | peekable iff `$F8`-mapped (or any `$80-$FE` mapped bank) | the fail-closed boundary: `$FF` and unmapped banks return `false` |
| `$6000-$7FFF` | MPR3 | WRAM in fixtures | peekable | not exercised by D/T guests; same rule |
| `$8000-$9FFF` … `$E000-$FFFF` | MPR4-7 | ROM / WRAM mix | peekable iff mapped bank `≥ $80` | bank-boundary wrap `pc+i & 0xFFFF` is part of the fetch |
| any page with bank `$FF` | `$FF` | hardware (VDC/VCE/PSG/timer/IRQ/CD) | **never peekable (fail closed)** | no status clear, no VRAM queue, no `io_buffer` change |
| any page with bank `< $80`, no mapper | e.g. `$00-$7F` unmapped | mapper window | **fail closed in the synthetic setup** | conservative: a real mapper case needs its own measurement (UNKNOWN) |
| any page with null backing | unmapped `≥ $80` | absent | **fail closed** | e.g. negative test `unmapped=(0xF8,)` |

MPR aliasing: the table is keyed by the *resolved bank*, not by the page
number. Two pages aliased to the same WRAM bank share one backing store and
one peekability outcome (measured: `tai-mpr2-unknown` fetches `$4010`
through page 2 aliased to `$F8`; `tia-wrap-forbidden` / `hwpage` fail closed
when page 2 is aliased to `$FF`). Bank-boundary crossing inside the 7-byte
fetch (`$3FFA` + 6 crossing into an `$FF` page) fails the whole fetch even
though the first 6 bytes were readable (host negative test
`test_t_late_unpeekable_byte_fails_whole_fetch`).

## 3. Fetch order and fail-closed rule

D (TAM `$53`): fetch `pc` first; it must be `$53`, otherwise `not_tam`.
Then fetch `pc+1` (the bitmask operand). Either byte unpeekable →
`fail_closed`. The refused TAM never runs, so MPRs and `m_mpr_buffer` are
unchanged (inherited S5/S8 record, not re-measured here).

T (block `TII $73` / `TDD $C3` / `TIN $D3` / `TIA $E3` / `TAI $F3`): fetch
`pc` .. `pc+6` in increasing address order with 16-bit wrap. Any byte
unpeekable → `fail_closed` with no partial use (the 6 readable bytes in the
`$3FFA` negative test never count as evidence). Opcode not in the block set
→ `not_block`. On success the 6 operand bytes decode to `src = raw[1] |
raw[2]<<8`, `dst = raw[3] | raw[4]<<8`, `len = raw[5] | raw[6]<<8` (7-byte
instruction encoding used by all block guests).

Resume rule (inherited from `block_preblock` / `st_boundary`): a refused or
fail-closed boundary re-enters through the same guarded execution entry, and
a resume attempt must also be refused with no counter/PC/VDC/RAM change.
The host model does not execute resumes; the real-core runs in §4 check
`resume_refused == 3/3` and `ram_unchanged_during_resume`.

## 4. Real-core evidence: the fetch causes no side effect

### 4.1 Fresh run (this PR): `geargrafx_block_preblock` (T, 7-byte fetch)

One heavy real-core invocation was run for this issue (heavy-emu budget 1
of max 2; the second slot is left unused):

```
python3 tests/integration/geargrafx_block_preblock.py
```

It single-steps the pinned debugger core and resolves the opcode at every
boundary through `Memory::TryPeek` before stepping. Every armed family
(`tii-entry`, `tdd-unknown`, `tin-unknown-far`, `tia-wrap-forbidden`,
`tai-mpr2-unknown`, `tii-hwpage-fetch-blocked`, `block-irq-handler`) holds
the VDC sentinels unchanged across the refusal: `AR_before == AR_after`,
`SR_before == SR_after`, `R0` and `READ_BUFFER` unchanged, `ram_diff == []`
(or the 3 architectural stack pushes only for the IRQ family), and the 7
operand bytes captured complete with `fetch_via == peek`, `fetch_safe ==
true`. The `hwpage` family proves fail-closed: `fetch_failed == true`,
`refused_opcode == 0`, `operand_bytes == []`, `stop_pc == $4000`,
`executed == 3`, no RAM/VDC change, resumes `3/3` refused. The positive
control `pos-write` runs unrefused and lands. Every `-unguarded` /
`-no-entry-check` / `-no-recheck` / `-wrong-opcode` / `-knownpc-only` /
`-resume-allowed` mutation makes the transfer observable
(`stop == refused_pc + 7`, exact transfer-trace bytes in `ram_diff`),
and the armed classifier rejects each mutated row. Full per-case rows are
printed as `EVIDENCE` JSON by the driver; the summary lines are quoted in
the run log attached to the PR.

### 4.2 Recorded contrast: MMIO-read side effects are real

`Memory::Read` on the hardware page *does* have side effects by pinned
source (VDC status case clears `m_status_register` to 0 and deasserts IRQ1;
data-MSB with `AR == VRR` queues a VRAM read; timer/IRQ paths update
`io_buffer`). The `st_boundary` probe demonstrates the hazard on the real
core with its `-unsafe-read` mutation (a normal bus read at the hardware
page): the fixed-JMP `$4000` status read moves `SR $3F -> $00`, the BBR3
`$4002` data-port read is distinguished by its own signature, and the armed
rows pin `mmio_read_attempts == 0` while each unsafe-read row records
exactly 1 (no-latch variants repeat the read). The `-peek-fail-step`
mutation proves the fail-open alternative is load-bearing: stepping after a
peek failure lands the hazard. The same `TryPeek` API that fails closed in
§4.1 is what the unsafe path deliberately does not use.

Claim boundary: the contrast shows the *fetch path* (peek) causes no VDC
change while the *bus-read path* does. It does not show that every device
DMA, ST `WriteFromCPU` bypass (#173), or mapper-bank read is safe — those
remain BLOCKED/UNKNOWN (§6).

## 5. Byte ranges (TDD, TIN, TIA/TAI, len=0)

`tools/peek_table.py::transfer_sequences` mirrors `addr_lists` in
`tests/integration/geargrafx_block_transfer_matrix.py` exactly (emit then
advance; TII/TIN/TIA source `+1`, TDD source `-1`, TAI source alternates
`+1/-1`; TII/TAI destination `+1`, TDD destination `-1`, TIN fixed, TIA
alternates `+1/-1`). `ByteRangeTests::test_matrix_consistency_all_ops`
checks all five ops at `(src $3E80, dst $3E90, len 4)` against the matrix
model, plus the directed cases:

| Op | Source walk | Destination walk | Measured / modelled |
|---|---|---|---|
| TII | `src+i` | `dst+i` | matrix lengths 1/2/3/4/8 + boundary/alias cases |
| TDD | `src-i` | `dst-i` | reverse `$4000` case; `$3E90` 4-byte trace `60/5F/5E/5D` in `block_preblock` |
| TIN | `src+i` | fixed `dst` | last-byte-lands trace `(0x3E90, 0x63)` |
| TIA | `src+i` | alternates `dst, dst+1` | measured VDC wrap `AR 5 -> 2`, `SR` intact, single stack-cell delta |
| TAI | alternates `src, src+1` | `dst+i` | 4-byte alternating trace `60/61/60/61` |
| len=0 | 65536 bytes | 65536 destinations | computed exactly (wrap `dst+0xFFFF`) but **never executed on-core** |

Self-overwrite: any destination byte in `pc .. pc+6` (16-bit wrap) must be
refused before the first step (`overlaps_instruction`; host tests both
directions). Length 0 is always refused before any step: the 64 KiB walk
cannot be measured on-core and no 64 KiB guest is executed in any suite.
This matches the follow-up transfer plan (whole-range preflight before
`TransferStart`, no mid-transfer resume): the pre-start check needs the
full computed range, and an uncomputable/unpeekable range fails closed.

## 6. Non-goals and production gate

* No `src/` change, no read-only accessor added, no wiring position enabled.
  A future accessor (if ever approved) would follow the #648 counters-accessor
  precedent and stay outside the GPL-linked probe; this PR adds only the
  host-only `tools/peek_table.py` model.
* Unmeasured and therefore never PASS: mapper banks `< $80` with a real
  mapper installed, libretro build (#103), real hardware (#655), device DMA
  (#367 W17), ST `WriteFromCPU` bypass (#173), IRQs newly asserted inside the
  fetch window (#653 TAM-inside-assert NOT_MEASURED), `m_mpr_buffer` aliasing
  beyond the inherited S8 record, and true free-run without a per-instruction
  stop.
* Overall production verdict for P02: **BLOCKED (fail closed)**. The fetch
  operation itself is side-effect-free on the measured WRAM/hardware-page
  paths (SAFE_IN_SCOPE for the fetch, pos/neg/mutation on the real core),
  but production use of D/T pre-decode stays blocked until the owner approves
  wiring, the accessor, and the §6 gaps. Over-refusal (unpeekable → refuse,
  including possible IRQ starvation on non-peekable code) is accepted as the
  safe direction.

## 7. Files, tests, review

* Added: `tools/peek_table.py` (host peek/fetch/range model, no GPL copy),
  `tests/host/test_717_peek_failclosed.py` (28 tests: peek table, fetch
  order, ranges, pos/neg/mutation), this document.
* Not added: `src/*peek*` (production wiring needs owner approval),
  `tests/fixtures/*717*` (no new fixture; the document cites the live
  `block_preblock` run and the recorded `st_boundary` hazard rows).
* Ran: `python3 -m unittest discover -s tests/host -p 'test_717*' -v`
  (28 OK, host-only); `python3
  tests/integration/geargrafx_block_preblock.py` (1 heavy real-core run,
  all armed/mutation/pos rows as §4.1); `git diff --check` clean.
  `make test` / `make check` equivalents are left to CI (cc65-gated host
  suite and provenance guards).
* Independent review: requested on the Draft PR; CRITICAL 0 / MAJOR 0
  required before any follow-up. Review must confirm no GPL text was copied,
  no `./rom` input was used, and no production gate string changed.

G5 class: peek-fail-closed / MMIO-read-safety (verification-planning track).
`release_eligible=false`, ABI VERIFIED 0/81, DEV_CALLABLE 0 unchanged.
