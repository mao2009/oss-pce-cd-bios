# CD_READ one-sector, one-mode contract (Issue #188, parent #12 / #11)

Status: **callee contract not measured. CD_READ NOT_IMPLEMENTED. BLOCKED for the genuine callee.**
Scope: one sector, transfer mode `_dh = 2` (destination RAM bank `_bl`), count 1. Other modes, counts 2..3
for this mode, and CD_BASE are separate Issues. Machine copy: `RAM_MODE_ITEMS` and `RAM_MODE_EXAMPLES` in
`tools/cd_read_contract.py`; checked by `tests/host/test_cd_read_contract.py::RamModeContractTests`.
注記（2026-10-22 オーナー判断）: 優先観測候補は `_dh = 3`（[188-cd-read-transfer-mode-contract.md](188-cd-read-transfer-mode-contract.md) の D1）。本書の `_dh = 2` 記述は既存の対象として残す。

## Evidence classes

* **Real-core transport (measured):** READ(6) on the pinned Geargrafx (`b49ae82a`), scenarios in
  [cd-read-contract.md](cd-read-contract.md) section 2 (T1-T4). This shows bus behaviour of the drive, not the BIOS
  callee. Labelled LOCAL_ONLY in that doc.
* **Real-core callee (approved verification evidence):** `CALLEE_REAL_CORE_RUNS = 0`. No approved verification evidence is registered for the genuine `$E009`.
  Reason: the safety gate is not satisfied. #173 is OPEN and its children #365 (opcode/PC pre-block), #366 (IRQ
  entry) and #367 (block transfer and device-initiated writes) are OPEN. `docs/173-safety-monitor-limits.md`
  records ST0/ST1/ST2 (`$03`/`$13`/`$23`) writes that bypass the RAM write hook, so a callee run cannot be
  contained yet.
* **Self-written READ(6) examples (not ABI evidence):** a READ(6) success on the fixture says nothing about the
  callee's `_dh` decoding, return registers or MPR. It is never shown as the System Card ABI.
* **Reference ABI VERIFIED:** none.

## Callee items (one mode, one sector)

Every row is HYPOTHESIS or UNKNOWN. None is VERIFIED.

| ID | Item | Status | Source |
| --- | --- | --- | --- |
| R1 | entry JSR `$E009` with slot `$03` | HYPOTHESIS | HuCC `pcengine.inc` |
| R2 | block number 24-bit `_cl:_ch:_dl` (`$20FC-$20FE`) | HYPOTHESIS | HuCC callers |
| R3 | `_dh = 2` selects RAM bank `_bl`, `_al` sectors | UNKNOWN | HuCC は `_dh=2` を書かないことを確認（`include/hucc/` の2ファイルの範囲）。独立根拠なし。出典は Hu-Go! HLE のみ（参照のみ、ライセンス未確定。根拠に使わない） |
| R4 | destination address in the bank (register pair, end address) | UNKNOWN | none |
| R5 | block base: CD_BASE-relative (#11) or absolute LBA | UNKNOWN | none |
| R6 | A = 0 on success | HYPOTHESIS | HuCC 呼出側の TAX/BNE（`include/hucc/hucc-systemcard.asm` L389-390 の `tax` / `bne .done`、`include/hucc/core-kernel.asm` L600-601 の `cmp #0` / `bne .retry`。A=0 を成功として扱う呼出側の期待。HYPOTHESIS の定義どおり。呼出先の成功値の証明ではない）。Hu-Go! は参照のみで根拠に使わない（ライセンス未確定） |
| R7 | error codes (no disc, bad block, read error, busy) | UNKNOWN | none |
| R8 | N/Z/C/T on return | UNKNOWN | 出典は Hu-Go! HLE のみ（参照のみ、ライセンス未確定。根拠に使わない）。呼出側の TAX/BNE は BIOS の Z/C を示さない |
| R9 | MPR state after success and after error (bank `_bl` mapping) | UNKNOWN | none (HuCC: CD_READ may corrupt MPR on error, unmeasured) |
| R10 | stack: callee depth and JSR/RTS balance | UNKNOWN | none |
| R11 | X/Y and `$20F8-$20FF` preservation | UNKNOWN | none |
| R12 | interrupt prerequisite (CLI before call) | UNKNOWN | none |

Coordinate system: R2 and R5 must be fixed together before any destination test is meaningful. Until then the
measured LBA in the transport examples is an absolute READ(6) LBA on the fixture, not a callee block number.

## Examples

Transport examples use the 16-sector fixture (`FIXTURE_SECTORS`). `expect` is (delivered sectors, early STATUS).

| ID | Polarity | LBA | Count | Expect | Measured by |
| --- | --- | --- | --- | --- | --- |
| P1 | positive | 5 | 1 | (1, False) | `single_sector_lba5` (READ(6) transport) |
| P2 | positive | 0 | 1 | (1, False) | `first_sector_lba0` |
| P3 | positive | 15 | 1 | (1, False) | `last_sector_lba15` |
| N1 | negative | 16 | 1 | (0, True), destination untouched | `invalid_sector_lba16` |
| N2 | negative | 2^21-1 | 1 | (0, True) | `invalid_sector_lba_max` |
| N3 | negative | 5 | 0 | UNKNOWN (T8: no approved count registered) | not measured |
| N4 | negative | 5 | 4 | UNKNOWN (outside counts 1..3) | not measured |

Callee-level negatives (R7, R9, R11) are UNKNOWN and have no example yet. Writing one needs a real-core callee
run, which the gate forbids.

## Missing contract items (blocking the callee)

R4 destination address, R5 coordinate base, R7 error codes, R9 MPR, R10 stack, R11 register preservation, R12
interrupts, and the `_dh = 2` return flags (R8, HLE only). Also missing: the destination end-of-transfer
behaviour for count 1, and the outcome when the bank is not mapped.

## BLOCKED and unblock condition

BLOCKED: no real-core callee run (`CALLEE_REAL_CORE_RUNS = 0`). Unblock only when #365, #366 and #367 are closed with
independent review, and the pinned Geargrafx can refuse, before the side effect, every write path the callee could
take (ST0/ST1/ST2, IRQ entry, block transfer). Even then the first run should be a single-sector synthetic
disc in mode 2 with a recorded LBA, destination and MPR diff, judged by `tools/cdread_verify.py`.
Reference-BIOS ABI stays BLOCKED_ABI and `release_eligible=false` until then.

## 変更履歴

* 2026-10-29（#573）: R3 と R8 は Hu-Go! HLE のみが出典のため HYPOTHESIS から UNKNOWN へ下げた。R6 は「Hu-Go は根拠に使わない」と書きながら Hu-Go! を根拠に挙げていた自己矛盾を解消し、HuCC 呼出側の TAX/BNE を根拠として HYPOTHESIS を維持した（PR #571 の 148 側の成功時 A と同じ扱い）。旧版の R3 の「訂正:」注記は本節へ移した。
