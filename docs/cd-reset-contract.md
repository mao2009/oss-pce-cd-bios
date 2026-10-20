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
| `entry` | HYPOTHESIS | Caller-side HuCC equate `$E000 + 3*1 = $E003` (pcengine.inc @54f5c736, L468-552). Callee entry detail at `$E003` UNKNOWN: the callee is not executed. |
| `arguments` | HYPOTHESIS | Caller-side only: HuCC `cd_reset(void)` takes no explicit A/X/Y or `$20F8-$20FF` (`al..dh`) inputs; Y carries only the trampoline selector (pcengine.inc/hucc-systemcard.asm @54f5c736). Callee argument contract unobserved. |
| `returns` | UNKNOWN | Callee never executed; HuCC `void` is caller-side only. Return registers/flags UNKNOWN. |
| `clobbered` | UNKNOWN | — |
| `preserved` | UNKNOWN | No A/X/Y/P or `al..dh` preservation claim; callee not executed. |
| `stack` | HYPOTHESIS | Caller-side only: JSR entry with expected RTS to the caller's continuation; trampoline balances PHA/PLA around bank changes (core.inc/core-kernel.asm @54f5c736). Callee depth/balance UNKNOWN. |
| `mpr` | HYPOTHESIS | Caller-side only: HuCC maps bank 0 into MPR7 for the call and restores caller MPR7 (core.inc/core-startup.asm @54f5c736). Callee MPR prerequisites/preservation UNKNOWN. |
| `ram` | HYPOTHESIS | Hu-Go HLE writes `$222D` as its disc-present marker — HLE, not adopted as BIOS behavior (bios.c @64ed226da). Real BIOS RAM writes/range UNKNOWN. |
| `flags` | UNKNOWN | — |
| `irq` | UNKNOWN | #201 measured IRQ2 non-arrival only in a synthetic `$1802`+`$1804` guest on Geargrafx b49ae82a — not a CD_RESET callee run; callee IRQ conditions UNKNOWN. |
| `errors` | HYPOTHESIS | Hu-Go host exit code 4 is not a BIOS error return (bios.c @64ed226da). BIOS no-disc/timeout/error values UNKNOWN. |
| `synchronization` | HYPOTHESIS | Hu-Go HLE returns after media init — not callee behavior (bios.c @64ed226da). Real ready/busy completion condition UNKNOWN. |
| `timing` | UNKNOWN | Reset pulse width, wait budget, whether the callee blocks: unmeasured. |
| `hw_bus_reset` | OBSERVED | `$1804` bit 1: assert → readback `$02`, release → `$00`, bus FREE after pulse (PHASE_MASK `$F8` clear). Geargrafx b49ae82a, #121 diagnostic ROM (synthetic CD, cold + warm). Register fact only — NOT evidence that the CD_RESET callee pulses `$1804`. |

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

## Adjudication basis and non-promotion rules (Issue #182)

Caller side vs callee: every `HYPOTHESIS` row above is a caller-side HuCC SDK claim or a Hu-Go HLE model
(pcengine.inc / hucc-systemcard.asm / core-*.asm @54f5c736; bios.c @64ed226da). It documents what callers
*expect*, not what the `$E003` callee does; the callee has never been executed (`verification: NOT_RUN`,
`executed_callee: false`) and none of its register/flag/SP/RAM/MPR behavior is observed.

Hardware measurements are kept strictly separate from callee behavior:
- `hw_bus_reset` is OBSERVED only as a `$1804` register/bus fact measured by the #10/#121 original HuC6280
  diagnostic on Geargrafx b49ae82a (synthetic CD). It is not evidence that CD_RESET pulses `$1804`.
- #201's IRQ2 non-arrival was measured in a synthetic `$1802`+`$1804` guest, not a CD_RESET run; `irq`
  stays UNKNOWN.
- #194: `$1804` write side effects are detectable but cannot be stopped at an instruction boundary → the
  pre-side-effect trap is BLOCKED.
- #202: ST0/ST1/ST2 `WriteFromCPU` bypasses all MMIO/address-hook capture points, even with MPR0≠`$FF` → ST
  interception is BLOCKED.

VERIFIED is not granted for any item: no reference-ROM (`rom/syscard3.pce`) callee execution exists, and the
schema cannot represent VERIFIED. Success values, flags and completion conditions are not inferred.

## #183 への引き継ぎ: 実装可能な最小範囲と前提条件

全 CD_RESET callee 契約項目（returns, flags, clobbered, preserved, irq, errors, synchronization,
RAM 差分, MPR 変化, 無媒体時動作, ready/busy 完了条件）が UNKNOWN のため、**ROM の CD_RESET
(`$01`/`$E003`) 実装は BLOCKED**。#183 で実装できるのは、#121 で計測済みの**低レベルバスリセット
原始処理のみ**（独立した HuC6280 ルーチン、`$1804` bit 1 アサート → BUS FREE 待ち → リリース）:

- 開始条件: バスは既に FREE であること（BUSY からの解放リセットは未検証・範囲外）
- 計測条件: Geargrafx b49ae82a, 合成 CD, cold/warm; `$1804` 読み戻し `$02`→`$00`, パルス後バス FREE
- ブロッカー周知: `$1804` 書き込みの副作用前捕捉は不可能 (#194); ST0/1/2 書き込みはフック迂回 (#202);
  IRQ2 到達は保証されない (#201) → ルーチンは I=1 前提か IRQ 非依存とする
- 副作用範囲: 他 80 スロット、EX_GETVER (`$1E`)、ROM 配置への副作用なし。`$01` ジャンプテーブル配線
  や CD_BASE/CD_READ には関与しない。`release_eligible=false` を維持。
- これは CD_RESET API 実装ではない: エントリ/戻り値/エラー/タイミング契約は保留。

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

pinned Geargrafx `b49ae82a` の静的コード確認（コード上の事実であり、実機実測ではない。実行時の
捕捉・遮断は #179 で未検証）：

- `Memory::Write` は先頭で CPU アドレス書込みフックを評価する（`src/memory_inline.h` L277-278 → `CheckMemoryHooks` L561-563 が `m_huc6280->CheckCpuAddressHooks(address, read)` を呼ぶ）。
- `bank == 0xFF` の Hardware Page 分岐は `m_memory_map_write` の対象外で、I/O 書込みは常に発生する（同 L290-364）。
- `$1804` は `offset & 0x1C00 == 0x1800` → `m_cdrom->WriteRegister` → `CdRom::WriteRegister` reg `0x04`（Reset, `src/cdrom.cpp` L292-307）で SCSI RST 信号を立て `AssertIRQ2()` を呼ぶ。
- `ST0/ST1/ST2` は `m_huc6202->WriteFromCPU(...)` を直接呼び（`src/huc6280_opcodes.cpp` L41-48 / L139-146 / L244-251、`src/huc6202_inline.h` `WriteFromCPU` 約 L150）、`Memory::Write`・`m_memory_map_write`・CPU アドレスブレークポイントを全て迂回する。コア内に捕捉点が無く、**実行時遮断は #179 の課題であり本 PR では実装しない**。

| ID | 危険 | 発生機構（実コア） | #170/#173 の実行前強制 | 残存リスク（証拠区分） | slot-`$01` 判定 |
| --- | --- | --- | --- | --- | --- |
| H1 | 物理バンク別名 | 1つの物理バンクが allowlist 内/外の両 CPU ページを裏付けると `GetMemoryMapWrite()` のページ隔離が崩れる | あり: 実行前に拒否（exit 8） | 実測（合成実コア `geargrafx_reference_guard.py`） | 充足 |
| H2 | 動的 MPR 変更 | `TAM` 等が実行中にページを書込み可能バンクへ再マップ | あり: MPR スナップショット差分で次の命令前に拒否 | 実測（同上） | 充足 |
| H3 | 未監視 MMIO `$1804` | `$FF` ページ経由の `STA $1804` が副作用前に捕捉されない | 部分的: `$FF` を禁止し到達不能にするだけ（トラップ無し） | 推定（コアコード読解; トラップは #179 未検証） | BLOCKED（entry env と両立しない） |
| H4 | VDC ST0/ST1/ST2 迂回 | `WriteFromCPU` がコア内フックを全迂回し不可逆な VDC 副作用 | なし（捕捉点が存在しない） | 静的コード確認（コード読解；実機実測なし。捕捉・遮断は #179 未検証） | BLOCKED |
| H5 | IRQ / 割込レジスタ | `$1804` の `AssertIRQ2()`、`$1802`・timer 書込み、ABI の I フラグ条件 | なし（I=1 前提と `$FF` 禁止のみ。実行時 IRQ 捕捉無し） | UNKNOWN（`irq` は slot-1 で UNKNOWN。実行時捕捉は未検証） | BLOCKED |
| H6 | allowlist 外 RAM 書込み | allowlist 外 CPU ページへの store | あり: `map_write` deny-by-default（防止）+ 書込み BP（検出） | 実測（同上） | 充足 |
| H7 | 命令予算 / タイムアウト | 無限ループ・未復帰・時間超過 | あり: 1 命令ずつ実行し予算超過で停止 | 実測（同上） | 充足 |

判定関数（`tools/cdreset_safety_preflight.py::decide`）は、**全危険が副作用前に充足**し、
かつ **ABI が `REVIEWED` で entry environment が再現**されているモデル入力に対してのみ
`MODEL_ELIGIBLE` を返す。これは**合成モデル上の適格性であり、純正 BIOS JSR の実行許可ではない**。
実行許可には #179 / #173 の独立した実コア側の副作用前強制と承認済み ABI 証拠が別途必要で、
本判定関数は `ALLOWED` を返さず、slot `$01` の実行ゲートも解放しない。
現行 `CURRENT_POSTURE`（#170）は H3 を「`$FF` 禁止による到達不能」でしか塞げず、その同じ
MPR 固定が HuCC 規約の entry（MPR0=`$FF`, HYPOTHESIS）を再現しないため、H3/H4/H5 と entry
再現が未充足となり `BLOCKED` になる。UNKNOWN ABI は「何でも起こりうる」ため、どの危険も
免除しない。安全不明の I/O を許可する posture（`hardware_page_policy` = `allow`）は H3
未充足として拒否する。また `forbid` が候補エントリの MPR0=`$FF` 仮説と衝突するときは
`H3:` 固有の拒否理由を返す。合成 posture の正例は `MODEL_ELIGIBLE` になるためモデル判定は
反証可能（トートロジーではない）が、実コアの副作用前遮断や純正 BIOS の動作を検証した証拠ではない。

`python3 tools/cdreset_safety_preflight.py --check` は上記を評価して `BLOCKED: ...` を表示し
exit 3 を返す。ホストテスト `tests/host/test_cdreset_safety_preflight.py` は合成 posture の
正例・負例のみを扱い、エミュレータも参照 BIOS も起動しない。

## 非実行ケース fixture と入力スキーマ preflight（Issue #180）

`tools/cdreset_slot01_fixture.py` は slot `$01` / 候補 `$E003` の**単一 API** に絞った、媒体あり/なしの
2 件のローカルケース定義（`slot01_media_present` / `slot01_media_absent`）と、実行前の fail-closed
入力検証だけを持つ。**どちらも非実行 fixture で実行経路は存在しない**（エミュレータ起動・参照 JSR
実行・ROM バイト読取を含まない。ROM は path/sha256 の形式検査のみ）。entry は固定 `$01` / `$E003`、
`execution` は `NOT_RUN`。A/X/Y/P, work-RAM, MPR, stack, IRQ, ディスク状態、期待結果は証拠がないため
全て `UNKNOWN` のまま（推測値を書いた case・命令列・戻り値は構造検証で拒否される）。

実行前に拒否する入力（`malformed_configuration` として `BLOCKED` / exit 3）: フィールド欠落・型違い、
未知の schema、ROM path/hash 欠落（run config 欠如含む）、`unsafe_io_allow` /
`side_effects_allowed` / `execution_allowed` が true。安全ゲート（#179）未承認なら slot `$01`
ケースの実行は `BLOCKED`。構造が valid でも出力は `NOT_RUN`（構造）+ `BLOCKED`（実行）で exit 3
のままで、ゲート承認だけでも解放しない。`MODEL_ELIGIBLE`（#178/#191/#193）は合成モデル上の適格性
であり実行許可ではないため、この preflight では権限として扱わない／表示しない。

公開 CI はこの fixture を合成ホストテスト（正/負例）としてのみ検査し、ケース実行・参照 JSR 実行は
行わない。ケース追加によって既存参照カタログ（#170/#172）のシナリオ数が「実測済み」になる表示は
しない（`execution` は `NOT_RUN` のまま）。`spec/api_contracts.json` slot 1 は HYPOTHESIS/PENDING/
NOT_STARTED/NOT_RUN のまま変更しない。
ホストテスト: `python3 -m unittest tests.host.test_cdreset_slot01_fixture -v`（合成のみ）。

## 観測プランと実行ゲート判定（Issue #181） — 2条件とも NOT_RUN / BLOCKED

`tools/cdreset_slot01_reference_plan.py` は #180 の2件 fixture（媒体あり/なし）から LOCAL_ONLY の
**観測レコード構造**だけを導き、実行可否を安全ゲート材料から判定する。**実行経路は存在しない**
（エミュレータ起動・純正 BIOS JSR 実行・ROM バイト読取を一切含まない）ため、2条件は実測ではなく
`execution: NOT_RUN` のままである。

### 観測レコード（事実のみ・全項目 UNKNOWN）と期待値の分離

| レコード | 内容 | 値 |
| --- | --- | --- |
| `observation.caller_pc` / `jsr_pc` / `return_pc` | 呼出元 PC・JSR・復帰 | 全て `UNKNOWN` |
| `observation.A/X/Y/P`・`sp`・`mpr` | レジスタ・スタック・MPR | 全て `UNKNOWN` |
| `observation.ram_diff` | 許可RAM差分 | `UNKNOWN` |
| `observation.bus_state`・`timeout` | バス状態・タイムアウト | `UNKNOWN` |
| `observation.core_sha256` | 実コアSHA | `UNKNOWN`（未実行のため） |
| `expectation.entry/returns/stack/mpr/ram/errors` 等 | 期待値の**タグ**（内容は本ページ上部の契約表） | `HYPOTHESIS` または `UNKNOWN`（観測値と混ぜない） |

推測値（`UNKNOWN` でない observation 値）、タグなしの期待値、`$01` / `$E003` 以外の API は
構造検証で拒否（`malformed_configuration` / `BLOCKED`）。実行禁止は `$01` 以外すべてに及ぶ。

### 安全ゲート判定材料（4種）と現時点の結論

| 材料 | 現実の値 | 状態 |
| --- | --- | --- |
| (a) 実コア `$1804` 副作用前トラップ | `blocked_side_effect_before_stop`（#194 実測） | 未充足 |
| (b) hazard posture（#191 `decide()`） | #170 posture + slot-`$01` ABI `UNKNOWN` → `BLOCKED` | 未充足 |
| (c) ST/IRQ/MPR/alias 未保証フラグ | ST0/1/2・IRQ は未保証（H4/H5）。MPR/alias は #170 で充足（H1/H2） | 未充足 |
| (d) 独立レビュー承認証拠（reviewer・PR番号・ラベル） | なし | 未充足 |

自己申告の `safety_gate_approved: true` は証拠として一切扱わず（(d) 欠落として `BLOCKED`），
4材料が揃っても (a) の実コア未充足など1つでも残れば `BLOCKED`。出力は常に `NOT_RUN` +
`BLOCKED`（理由を個別列挙）で exit 3。なお `EXIT_MODEL_ELIGIBLE=4` は事前安全チェック CLI（`tools/cdreset_safety_preflight.py`）専用で、その `MODEL_ELIGIBLE` のみに割り当てる。本 plan ツール（`tools/cdreset_slot01_reference_plan.py`）は `MATERIALS_COMPLETE` / `BLOCKED` のみを扱い、終了コードは常に `EXIT_BLOCKED=3` で 4 を返さない。判定を出す経路（`--check`）では、いずれの CLI も exit 0 は返さない（`-h`/`--help` のみ argparse のヘルプ表示で 0 で終了し、判定ではない）。`MODEL_ELIGIBLE`（#191）は材料 (b) の適格性であり実行
許可ではなく、権限語（ALLOWED/PASS/VERIFIED）や270シナリオ実測を意味する表示は出力しない。
全材料が充足した**合成**入力でのみ判定が `MATERIALS_COMPLETE` に反転する（反証可能性。実行路
は依然として無い）。(d) は reviewer・PR番号・ラベルの**形状のみ**を検証し、**独立性は検証していない**
（自己申告の `{"reviewer":"self",...}` も形状が合えば (d) を形式的に満たす）。`MATERIALS_COMPLETE` は
いかなる場合も実行許可ではなく、`ALLOWED` は本リポジトリのどのゲートからも返さない。独立性の強制は
runner を作る前に実装しなければならない（未実装のままでは実行路を作らない）。**slot `$01` の実行は
禁止のままであり、純正 BIOS JSR は一切実行していない。** `spec/api_contracts.json` slot 1 は変更しない。

### 状態名の対応表（実行ゲート系の語彙、Issue #228）

似た名前の状態は**別の意味**を持つため統合しない。いずれも実行許可ではない。

| 状態名 | 出所 | 意味 | 実行許可 |
| --- | --- | --- | --- |
| `MODEL_ELIGIBLE` | `tools/cdreset_safety_preflight.py` (#191) | 合成 posture が全危険を覆い ABI が `REVIEWED` というモデル適格性 | なし |
| `MATERIALS_COMPLETE` | `tools/cdreset_slot01_reference_plan.py` (#181) | 合成の4材料（(a)-(d)）が全て充足したときの判定反転（反証可能性）。実材料では到達不能 | なし（実行路なし） |
| `MATERIALS_COMPLETE` | `tools/reference_jsr_gate.py` (#228) | 呼び出し元が渡した**未認証**の承認入力が整形済みで、台帳も全危険 CONTROLLED 、かつ台帳の callee verification が `NOT_RUN` という意味のみ。承認・認可ではなく真正性は未検証 | なし（runner `NONE`、`jsr_count=0`） |
| `ALLOWED` | どこからも返さない | 予約語。独立した実コア強制が存在しないため、現状のゲートでは到達不能 | 本リポジトリでは生成しない |

`MATERIALS_COMPLETE` は改名しない。同名でもゲートごとに意味が異なるため、上表で意味を明示する
（名称は「材料が揃った」だけを表し、承認・認可・実行許可を意味しない）。

ホストテスト: `python3 -m unittest tests.host.test_cdreset_slot01_reference_plan -v`（合成のみ、
エミュレータ・ROM 不使用）。
