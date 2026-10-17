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
| `no_disc` | none (BIOS result UNKNOWN); stack imbalance or budget overrun still FAIL. Real core with no media (#133) runs the guest, but READ(6) stops at error 5 / stage 5 / 0 bytes and is classified BLOCKED/NO_MEDIA; no CD_RESET run without media has been judged (NOT RUN) | OBSERVED / FAIL |
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
| 媒体なしでのコア起動（#133） | 実測: 媒体なし（`SetCDROMType(STANDARD)`、ディスク無し）でゲストが実行され、READ(6) はエラー 5・ステージ 5・0 バイトで停止（SCSI GOOD、DATA_IN なし、RAM 不変）。分類器は BLOCKED/NO_MEDIA で、PASS ではない。 |
| CD_RESET 実行（媒体なし） | 未実行（NOT RUN）。#133 の媒体なし試験は cdread と同じ期待値（`cd_halt_fail`, 5, 5, 0）で判定しており、CD_RESET の reset judge は適用していない。 |
| CD_RESET からの復帰 | 未確認（UNKNOWN）。媒体なしでの戻り（returned）は測っていない。 |
| System Card BIOS の仕様 | UNKNOWN。媒体なし時の戻り値・エラー・タイミングは出典からも実測からも確定していない。 |
| 未検証 | CD_RESET API（`$01`, `$E003`）の入口・戻り値・エラー・タイミング・割込み条件。BIOS の ready/busy 完了条件。実機と第 2 エミュレータでの比較。ドライブ busy 時の待機・中断方針。 |

判定ツール: `tools/cdreset_bus_verify.py`（パルス記録の判定）+ `tools/cdread_verify.check_success`（データ経路）。
ホスト側テスト `tests/host/test_cdreset_bus.py` は合成レコードのみで、エミュレータ成功の代わりにはしない。

## 実行前安全preflight（Issue #178, Safety-01） — slot `$01` は BLOCKED

この節は slot `$01` / 候補 `$E003` の参照 JSR を **実行する前** に判定できる副作用リスクを
1 行ずつ分離して列挙し、`tools/cdreset_safety_preflight.py` の判定と一致させる。
**この PR でゲートは解放しない。実参照 BIOS の API JSR は一切実行していない。** 実行時の
共通監視は #173（既存 slot-`$1E` ガードは #170）に委ね、ここでは再実装せず「#170 が既に
副作用前に強制しているか / していないか」だけを区別する。

pinned Geargrafx `b49ae82a` のコア読解（コード上の事実であり、実機実測ではない）:

- `Memory::Write` は先頭で CPU アドレス書込みフックを評価する（`src/memory_inline.h` L277-278 → `CheckMemoryHooks` L561-563 が `m_huc6280->CheckCpuAddressHooks(address, read)` を呼ぶ）。
- `bank == 0xFF` の Hardware Page 分岐は `m_memory_map_write` の対象外で、I/O 書込みは常に発生する（同 L290-364）。
- `$1804` は `offset & 0x1C00 == 0x1800` → `m_cdrom->WriteRegister` → `CdRom::WriteRegister` reg `0x04`（Reset, `src/cdrom.cpp` L292-307）で SCSI RST 信号を立て `AssertIRQ2()` を呼ぶ。
- `ST0/ST1/ST2` は `m_huc6202->WriteFromCPU(...)` を直接呼び（`src/huc6280_opcodes.cpp` L41-48 / L139-146 / L244-251、`src/huc6202_inline.h` `WriteFromCPU` 約 L150）、`Memory::Write`・`m_memory_map_write`・CPU アドレスブレークポイントを全て迂回する。コア内に捕捉点が無い。

| ID | 危険 | 発生機構（実コア） | #170/#173 の実行前強制 | 残存リスク（証拠区分） | slot-`$01` 判定 |
| --- | --- | --- | --- | --- | --- |
| H1 | 物理バンク別名 | 1つの物理バンクが allowlist 内/外の両 CPU ページを裏付けると `GetMemoryMapWrite()` のページ隔離が崩れる | あり: 実行前に拒否（exit 8） | 実測（合成実コア `geargrafx_reference_guard.py`） | 充足 |
| H2 | 動的 MPR 変更 | `TAM` 等が実行中にページを書込み可能バンクへ再マップ | あり: MPR スナップショット差分で次の命令前に拒否 | 実測（同上） | 充足 |
| H3 | 未監視 MMIO `$1804` | `$FF` ページ経由の `STA $1804` が副作用前に捕捉されない | 部分的: `$FF` を禁止し到達不能にするだけ（トラップ無し） | 推定（コアコード読解; トラップは #179 未検証） | BLOCKED（entry env と両立しない） |
| H4 | VDC ST0/ST1/ST2 迂回 | `WriteFromCPU` がコア内フックを全迂回し不可逆な VDC 副作用 | なし（捕捉点が存在しない） | 実測（コアコード読解） | BLOCKED |
| H5 | IRQ / 割込レジスタ | `$1804` の `AssertIRQ2()`、`$1802`・timer 書込み、ABI の I フラグ条件 | なし（I=1 前提と `$FF` 禁止のみ。実行時 IRQ 捕捉無し） | UNKNOWN（`irq` は slot-1 で UNKNOWN） | BLOCKED |
| H6 | allowlist 外 RAM 書込み | allowlist 外 CPU ページへの store | あり: `map_write` deny-by-default（防止）+ 書込み BP（検出） | 実測（同上） | 充足 |
| H7 | 命令予算 / タイムアウト | 無限ループ・未復帰・時間超過 | あり: 1 命令ずつ実行し予算超過で停止 | 実測（同上） | 充足 |

判定関数（`tools/cdreset_safety_preflight.py::decide`）は、**全危険が副作用前に充足**し、
かつ **ABI が `REVIEWED` で entry environment が再現**されている場合のみ `ALLOWED` を返す。
現行 `CURRENT_POSTURE`（#170）は H3 を「`$FF` 禁止による到達不能」でしか塞げず、その同じ
MPR 固定が HuCC 規約の entry（MPR0=`$FF`, HYPOTHESIS）を再現しないため、H3/H4/H5 と entry
再現が未充足となり `BLOCKED` になる。UNKNOWN ABI は「何でも起こりうる」ため、どの危険も
免除しない。安全不明の I/O を許可する posture（`hardware_page_policy` = `allow`）は H3
未充足として拒否する。合成 posture の正例が `ALLOWED` を返すため、この判定は反証可能
（トートロジーではない）。

`python3 tools/cdreset_safety_preflight.py --check` は上記を評価して `BLOCKED: ...` を表示し
exit 3 を返す。ホストテスト `tests/host/test_cdreset_safety_preflight.py` は合成 posture の
正例・負例のみを扱い、エミュレータも参照 BIOS も起動しない。
