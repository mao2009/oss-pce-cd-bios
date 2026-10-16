# CD_RESET calling contract (Issue #10) — research and bus-reset diagnostic (#121); no callable CD_RESET

Slot `$01`, candidate CPU-visible entry `$E003`. **spec=HYPOTHESIS, provenance=PENDING,
implementation=NOT_STARTED, verification=NOT_RUN.** Nothing below was measured on an executed
CD_RESET callee; this page fixes what is known, what is guessed and what a future run must show.

## Source trace and admissibility (#8)

| Claim | Source (exact revision) | Kind | Admissibility |
| --- | --- | --- | --- |
| Name `CD_RESET` at `$E003` | [HuCC pcengine.inc L468-552](https://github.com/pce-devel/huc/blob/54f5c73606c1e7142e8095c727cab2767e2b3cb3/include/hucc/pcengine.inc#L468-L552) | SDK equate (caller side) | REFERENCE_ONLY, PENDING |
| Name `CD_RESET` as selector | [Hu-Go bios.c L30-133](https://github.com/mckayemu/hugo/blob/64ed226da2288738781743eeef228c069e8973a4/bios.c#L30-L133) | HLE emulator | REFERENCE_ONLY, PENDING; HLE is not firmware proof |
| `void cd_reset(void)`, no explicit arguments | [HuCC hucc-systemcard.asm L96-100](https://github.com/pce-devel/huc/blob/54f5c73606c1e7142e8095c727cab2767e2b3cb3/include/hucc/hucc-systemcard.asm#L96-L100) | caller wrapper | REFERENCE_ONLY, PENDING |
| Disc-present marker `$222D=1` | [Hu-Go bios.c L356-376](https://github.com/mckayemu/hugo/blob/64ed226da2288738781743eeef228c069e8973a4/bios.c#L356-L376) | HLE emulator | REFERENCE_ONLY, PENDING; not adopted |
| Slot list / revision offsets | [Zeograd notes](https://www.zeograd.com/download/pce_bios.html), [Stum 2025](https://www.stum.de/2025/pcenginebiosoffsets/) | reverse-engineering-derived articles | names only; no behaviour adopted, PENDING |
| `$1804` bit 1 pulses SCSI bus reset, bus then free | [docs/cd-oss-survey.md](cd-oss-survey.md) (ares `9408cb43`, HuC, Geargrafx) + #5 diagnostic | hardware register fact | measured **in Geargrafx `b49ae82a` only** by the #5 diagnostic ([cd-read-diagnostic.md](cd-read-diagnostic.md)) |

HuCC and Hu-Go are two observations of the same caller convention; neither independently
corroborates the other's callee behaviour. The full source objects (file SHA-256, URLs) are in the
slot-1 row of [spec/api_contracts.json](../spec/api_contracts.json).

## Contract items

`VERIFIED` = measured on an executed callee; currently none. Ledger rows must keep the
[api_contracts.json](../spec/api_contracts.json) status (checked by `tests/host/test_cdreset.py`).

| Item | Status | Content |
| --- | --- | --- |
| `entry` | HYPOTHESIS | `$E000 + 3*1 = $E003`, jump-table convention; ROM mapping owned by #1 |
| `arguments` | HYPOTHESIS | No explicit A/X/Y or `$20F8-$20FF` (`al..dh`) inputs per HuCC; Y carries the trampoline selector only |
| `returns` | HYPOTHESIS | HuCC treats as void; any A/X/Y/flag result UNKNOWN |
| `clobbered` | UNKNOWN | — |
| `preserved` | UNKNOWN | No A/X/Y/P or `al..dh` preservation claim |
| `stack` | HYPOTHESIS | Entered by JSR, expected to return by RTS; callee depth/balance UNKNOWN |
| `mpr` | HYPOTHESIS | HuCC maps bank 0 to MPR7 and restores it itself; MPR0=`$FF`/MPR1=`$F8` are SDK startup choices, not proven prerequisites |
| `ram` | HYPOTHESIS | Hu-Go HLE writes `$222D`; real BIOS writes UNKNOWN |
| `flags` | UNKNOWN | — |
| `irq` | UNKNOWN | Whether it runs with I set, enables `$1802` IRQs or relies on IRQ2 handlers |
| `errors` | HYPOTHESIS | Hu-Go host exit is not a BIOS error; no-disc/timeout result UNKNOWN |
| `synchronization` | HYPOTHESIS | Hu-Go returns after media init; real ready/busy completion condition UNKNOWN |
| `timing` | UNKNOWN | Reset pulse width, wait budget, whether it blocks |
| `hw_bus_reset` | HYPOTHESIS | That CD_RESET pulses `$1804` bit 1 at all is unproven; only the register effect is Geargrafx-measured |

## Synthetic scenarios (`tools/cdreset_verify.py`)

The judge takes one execution record and returns `PASS_HYPOTHESIS / OBSERVED / FAIL / SKIP / BLOCKED`.
Plain `PASS` is unreachable until the contract above is reviewed.

| Scenario | Defined pass condition | Verdict range |
| --- | --- | --- |
| `disc_ready` | entry `$E003`, returns to JSR+3, SP balanced, polls ≤ budget, `$1800 & $F8 == 0` | PASS_HYPOTHESIS / FAIL |
| `no_disc` | none (BIOS result UNKNOWN); stack imbalance or budget overrun still FAIL. Geargrafx does not run the card without media, so real-core runs are BLOCKED in practice | OBSERVED / FAIL |
| `drive_busy` (timeout) | none (wait/abort policy UNKNOWN); same structural FAIL rules | OBSERVED / FAIL |
| invalid input | not meaningful: the hypothesis contract has no inputs | — |
| any non-`geargrafx-real` source | — | SKIP |
| core revision ≠ pinned Geargrafx or no ROM hash | — | BLOCKED |

Record validation (fail-closed, `ValueError`): all `REQUIRED` keys present; `rom_sha256` empty (unidentified,
BLOCKED) or exactly 64 lowercase hex; `returned` is a bool; PCs are 0..`$FFFF`, SP/bus bytes 0..`$FF`, `polls`
≥ 0 and `poll_budget` ≥ 1 (bool and float are rejected). The `reason` from `classify()` separates `timeout`
(not returned, `polls == poll_budget`), `not_returned` (not returned, bound not reached), and `budget_overrun`
(`polls > poll_budget`, FAIL for every scenario). Verdicts are never a plain `PASS` and do not verify firmware.

Host tests: `python3 -m unittest tests.host.test_cdreset -v` (records are synthetic; no emulator).

## Why no ROM routine yet

Returns, preservation, IRQ, error and completion semantics are UNKNOWN, so any routine would invent
an ABI that games may contradict. Prerequisites: #1 jump-table mapping, #8 adoption decision for the
sources above, a reviewed answer for returns/no-disc/busy behaviour from System Card 3.0 observation,
and a real-core harness that executes `$E003` and emits the record fields `tools/cdreset_verify.py`
requires (`REQUIRED`). The #5 bus-reset + bus-free sequence in `src/cd_read_probe.s` is the intended
building block once that review exists.

## 実コア実測と未検証（Issue #10, B2）

対象: 独自作成の HuC6280 診断 `src/cd_reset_probe.s`（`make dev-cdreset-integration`）。合成 CD のみ使用、ゲーム由来データなし。
Geargrafx `b49ae82a` の実行で確認した。**これは低レベルのバスリセット（`$1804` bit 1）の動作確認であり、
System Card CD_RESET（`$01` / `$E003`）の完全互換は証明していない。** エントリ、戻り値、エラー、タイミング契約は一切測っていない。

| 区分 | 内容 |
| --- | --- |
| 実測（Geargrafx） | 診断は 2 回のバスリセットパルスを発行し、各パルスの後に BUS FREE を待つ。`$1804` 読み戻しは assert 中 `$02`、解放後 `$00`。パルス後のバスは FREE。その後 READ(6) LBA 5 x1 を完走し、セクタ全バイトが合成ディスクと一致（cold / warm）。 |
| 実測（負例） | `$1804` assert を NOP に置換した ROM は、データ経路が同一に成功しても reset judge が「reset not asserted」で拒否。SELECT を `$1805` にリダイレクトすると、リセット後の選択がタイムアウト（code 2, stage 3）し、リセット自体は成功として記録。リセット後の data-in でのドライブ側 abort は #5 と同じ失敗経路（code 4, stage 5）。改ざんディスクは RAM 比較で拒否。 |
| 実測の観測限界 | リセットは「既に FREE のバス」に対してのみ発行。PHASE ログに リセット起因の遷移は出ない。BUSY 状態からのリセットによる解放は**未検証**。 |
| 仮説（未確定） | 実機でも `$1804` bit 1 が SCSI バスリセットとして効くこと。`$1804` 読み戻しが Geargrafx の `m_reset` 由来であること（実機では未確認）。 |
| 未検証 | CD_RESET API（`$01`, `$E003`）の入口・戻り値・エラー・タイミング・割込み条件。BIOS の ready/busy 完了条件。実機と第 2 エミュレータでの比較。ドライブ busy 時の待機・中断方針。 |

判定ツール: `tools/cdreset_bus_verify.py`（パルス記録の判定）+ `tools/cdread_verify.check_success`（データ経路）。
ホスト側テスト `tests/host/test_cdreset_bus.py` は合成レコードのみで、エミュレータ成功の代わりにはしない。
