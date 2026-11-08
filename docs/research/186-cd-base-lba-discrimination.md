# Issue #186: CD_BASE（`$02`）LBA/INDEX01 座標系の識別実験設計と参照JSR安全ゲート確認（非実行）

対象: スロット `$02`、候補エントリ `$E006`（`CD_BASE`）。親は #11、関連は #12。依存: #173（安全境界）。
本書は**実測準備と識別能力の証明であり、純正 BIOS の実行・観測は 0 件**である。合成 fixture と
純正 callee 観測は別の証拠クラスとして管理する（§0）。ホスト算術の結果は BIOS の証拠ではない。
コード: `tools/cd_base_lba_candidates.py`（候補式と判別器）、`tests/host/test_cd_base_lba_candidates.py`
（正例・負例・mutation・ゲート fail-closed）。実装はしない（#147 の所管）。

## 0. 分類語彙と証拠の分離

状態語は `docs/research/186-cd-base-lba-coordinate.md` §0 の統一方針（SOURCE_CONFIRMED /
HYPOTHESIS / MEASURED / UNKNOWN / BLOCKED、VERIFIED は昇格させない）を使う。課題文の四値への
対応: MEASURED≈OBSERVED（実行記録）、SOURCE_CONFIRMED/HYPOTHESIS≈INFERRED/HYPOTHESIS（根拠のある
推測）、UNKNOWN、BLOCKED。合成ホスト算術で得られた「判別能力」は MEASURED（ホスト、対象は合成
fixture）であり、CD_BASE ABI の MEASURED には含めない。#635 の観測器（`docs/635-api-entry-observation.md`、
`#729`/`#730` の自動ローダー経由）が確認した商用 IPL 自身の API テーブル JSR 到達（trace 証拠）は、
CD_BASE callee の測定ではない。

## 1. 座標系の候補（block, base, disc → LBA）

最初の四つは台帳 §4 の既存候補（式は引用）、後二つは本課題が追加を要求する候補。いずれも
callee 側の式としては **HYPOTHESIS**（実測 0 件）。呼出側事実（S1〜S5、HuCC 固定版
`54f5c736`、BSL-1.0、出自 PENDING）は callee の振る舞いの証拠にならない。

| 候補 | 式 | 使う disc 定数 | 分類と根拠 |
| --- | --- | --- | --- |
| `ABS_LBA` | `lba = block` | なし | HYPOTHESIS（台帳 §4） |
| `BASE_PLUS_BLOCK` | `lba = base + block` | CD_BASE の base 入力（既定値未知） | HYPOTHESIS（台帳 §4） |
| `TRACK_START_PLUS_BLOCK` | `lba = index01 + block` | データトラック INDEX 01 | HYPOTHESIS（台帳 §4。K6/K7 の recbase 初期化記述と整合） |
| `INDEX00_START_PLUS_BLOCK` | `lba = index00 + block` | データトラック INDEX 00 / PREGAP 開始 | HYPOTHESIS（新規。K6「データトラック先頭」の PREGAP 包括読み。初回トラックでは index00 = LBA 0 で ABS と同値） |
| `MSF_FRAMES_FROM_00_00_00` | `lba = block - 150` | リードイン 150 フレーム | HYPOTHESIS（台帳 §4） |
| `CALLER_FIXUP(delta)` | `lba = block + delta`（delta は呼出側が算出） | TOC / ISO ディレクトリ由来 | HYPOTHESIS（新規。K5 の `_add_sectors` 事実と台帳 (2) の仮説 (c)） |

**同値クラス（READ LBA 単独では分離不能、記録として残す）**: 同一 disc 上でオフセットが等し
いモデルは不分離 — `ABS_LBA ≡ CALLER_FIXUP(0)`、`TRACK_START ≡ CALLER_FIXUP(index01)`、
`INDEX00_START ≡ CALLER_FIXUP(index00)`、`MSF_FRAMES ≡ CALLER_FIXUP(-150)`、
`BASE_PLUS_BLOCK(k) ≡ CALLER_FIXUP(k)`。定数を足すのが呼出側か呼出先かは、READ LBA だけでは
**任意 disc 上でも分離不能**である。§3 のステージ 2（入力感度）と呼出側ソース事実が必要。

## 2. フィクスチャ（再利用と新規）

| Disc | 定義 | 出所と状態 |
| --- | --- | --- |
| D1 | 単一データトラック、仮想 PREGAP 00:02:00、INDEX 01 = LBA 150、16 セクタ（payload = 自身の LBA） | #138 流用（`tools/cd_base_probe.build_fixture`）。パース結果は K9（MEASURED、ホスト）。Geargrafx mount 済（#138） |
| D2 | 2トラック: t1 = MODE1 150 セクタ（LBA 0..149）、t2 = データ、仮想 PREGAP 150（LBA 150..299、非格納）、INDEX 01 = LBA 300、16 セクタ | 新規（本課題）。`cue_analysis` で Valid（findings 空、`user_sector` 自己命名をテストで固定）。2ISO 呼出側（S1/S3）と 2 データトラック現実配置に対応 |

D2 が必要な理由: `tools/cue_analysis.py` の `starts[0] == 0` 規則により**初回トラックの
INDEX 00 は常に LBA 0** で、`INDEX00_START_PLUS_BLOCK` は `ABS_LBA` と一致する。INDEX 00 候補を
分離するにはデータトラックが後続トラック（index00 = 150 ≠ 0）である必要がある。D2 は最小入力
差分（D1 に TRACK 01 を 150 セクタ追加し t2 の INDEX 01 を 00:02:00 へ）で達成する。INDEX 00 を
BIN に格納する変形（`INDEX 00 00:00:00` + `INDEX 01 00:02:00`）は index01 = 150 で D1 と同一座
標になり分離に寄与しないため不採用（`docs/cd-base-contract.md` §3 第 3 例と同じ結果）。
D2 の実コア mount と PREGAP 付き媒体の SCSI LBA 対応は **NOT RUN**（台帳 U12/N5 そのまま）。

## 3. 最小判別設計（合成 fixture 上の判別能力、MEASURED: ホスト）

**ステージ 1（オフセット探針）**: 探針 universe 21 組（block ∈ {5,145,150,155,300,305,315} ×
base ∈ {0,150,300}）に対し貪欲最小集合を計算すると、**D1 は 2 探針 `{(150,0),(5,150)}`**（9 モデル
中 28 ペアを分離、残余 8 ペア = 3 同値クラス）、**D2 は 2 探針 `{(5,0),(150,150)}`**（32 ペア分離、
残余 4 クラス）。2 モデル間なら **1 探針で十分**（例: `ABS_LBA` vs `TRACK_START` ← D1 `(5,0)`、
`INDEX00_START` vs `MSF_FRAMES` ← D1 `(150,0)`）。`BASE_PLUS_BLOCK` 族は base 入力の変化
（`(5,0)` vs `(5,150)`）で他の 4 族から分離する。代表 witness（真/誤の READ 結果）:
`ABS` vs `TRACK_START` = D1 `(5,0)` → None/155、`ABS` vs `INDEX00_START` = D2 `(5,0)` → 5/None、
`TRACK_START` vs `INDEX00_START` = D1 `(5,0)` → 155/None。

**ステージ 2（入力感度）**: CD_BASE 入力バイト `_al`〜`_dh` を 1 バイトだけ変えた CD_BASE →
CD_READ（`tools/cd_base_probe.py` の `OBSERVATION_PLAN` (1)、research/186 C2〜C5）。callee が
base 入力を読めば `BASE_PLUS_BLOCK`、読まなければ定数オフセット族（または呼出側補正）に縮む。

**観測値→残余クラス**（`consistent()` の実出力、D2、合成記録）:

| 観測パターン（合成記録） | `consistent()` の出力 |
| --- | --- |
| base 入力が READ LBA を変える: `[(5,0,5),(5,150,None)]` | `[BASE_PLUS_BLOCK]`（定数 delta の `CALLER_FIXUP` は除外） |
| index01 一定オフセット: `[(5,0,305),(5,150,305),(6,0,306)]` | `[CALLER_FIXUP(+index01), TRACK_START_PLUS_BLOCK]` |
| 絶対: `[(5,0,5),(5,150,5),(300,0,300)]` | `[ABS_LBA, CALLER_FIXUP(0)]` |
| index00: `[(150,0,300),(155,0,305),(5,0,None)]` | `[CALLER_FIXUP(+index00), INDEX00_START_PLUS_BLOCK]` |
| MSF: `[(150,0,0),(155,0,5),(300,0,None)]` | `[CALLER_FIXUP(-150), MSF_FRAMES_FROM_00_00_00]` |

単独 model 名を返さず、VERIFIED を出さない（research/186 §2.3 の規則をクラスに一般化）。注意:
base 入力が効く観測でも、呼出側が**自分が書いた base 値を delta として読む**（呼出側 echo）変種は
同一観測になりうる。callee 起因か呼出側起因かの帰属は、callee 作業 RAM の観測（問い (1)/(5)）か
呼出側ソース事実がなければ UNKNOWN のまま。本書は帰属を確定しない。

## 4. 負例（誤った基点を使う実装の必検出）

誤り実装 → 検出 witness（全て 1 探針）: PREGAP 分ずれ（`INDEX00_START` を `TRACK_START` と誤認）
= D1 `(5,0)`、リードイン符号誤り（`MSF` を `ABS` と誤認）= D1 `(150,0)`、絶対想定（`TRACK_START`
を `ABS` と誤認）= D1 `(5,0)`、base 入力無視（`BASE` を `ABS` と誤認）= D1 `(5,150)`。24bit 超で
**wrap する実装**（候補は拒否）は D2 `(0xFFFFFF,1)` で検出（wrap 実装は LBA 0 = t1 ユーザデータ
を読み、候補は拒否）。INDEX 01 = 0 の対照 disc では `ABS`/`TRACK_START`/`INDEX00_START` が一致す
る（#138 の regression guard を継承）。mutation 負例: D2 の PREGAP 除去 → `INDEX00_START` と
`TRACK_START` が併合（fixture の非零 PREGAP が load-bearing）、観測 payload ±1 改ざん →
`consistent()` から真モデル除外、ゲート台帳改ざん → `validate_ledger` が ValueError。

## 5. 安全ゲート（#173 現状、CD_BASE 固有評価）

`docs/173-safety-monitor-limits.md` の実コア裁定（pinned Geargrafx `b49ae82a`、合成 ROM）と
`spec/reference_jsr_gate.json` の危険定義（H1〜H7、`tools/cdreset_safety_preflight.py`）を CD_BASE
（slot `$02`）に適用する。**他の API で PASS していても CD_BASE の実行許可とみなさない**。

| 項目 | 状態 | 根拠（#173 の実測・既存 guard） |
| --- | --- | --- |
| H1 物理バンク別名 RAM 書込 | CONTROLLED | #170 exit 8、`geargrafx_reference_guard.py` PASS |
| H2 動的 MPR 変更 | CONTROLLED | #170 MPR snapshot、同上 |
| H3 MMIO `$1804` | PARTIAL | `$FF` 禁止のみ。既知 PC の実行 breakpoint は既知コードに限定（`geargrafx_mmio1804_preblock.py`） |
| H4 ST0/ST1/ST2 迂回 | UNCONTROLLED | `WriteFromCPU()` が hook を迂回、書込完遂（`geargrafx_st_bypass.py` BLOCKED） |
| H5 IRQ 副作用 | UNCONTROLLED | `$1802`/`$1402`/`$1403` 書込完遂、IRQ2 着地（`geargrafx_irq_register.py` BLOCKED） |
| H6 許可域外 RAM 書込 | CONTROLLED | #170 `map_write`/breakpoints |
| H7 命令予算・timeout | CONTROLLED | #170 stepped budget（機構のみ。予算超過・timeout 負例の処理は #173 で未実測） |

CD_BASE 固有の fail-closed 要因: (a) callee ABI 全項目 UNKNOWN（台帳六項目）のため**実験に必要な
I/O 許可範囲が定義不能**（allowlist 未定義）；(b) entry 環境（MPR0=`$FF` 仮説等）が未確認で再現
不能；(c) 一般 MMIO/IRQ 書込の事前阻止が pinned コアに不可能（H4/H5）；(d) **ブロック転送と
デバイス起点書込み**（#367 の guard は verification-only core patch であり pinned stock コアの
機構ではない。CD 機器由来の DMA 書込みは CPU hook を迂回しうる）と命令予算超過・timeout・異常終了の
fail-closed（H7 の機構は整備済みだが負例処理は #173 で未実測）も、callee の I/O 需求未知のうちは
許可範囲を定義できない。よって **純正 BIOS への参照 JSR は一切実行しない**。ゲートは今日 fail-closed: `resolve(2)` → `BLOCKED`
（`NO_REVIEWED_GATE_RECORD`）、`require_allowed("slot-02")` → `GateRefused`、`is_execution_permitted`
は厳密文字列 `"ALLOWED"` のみ許可、偽造 complete row + 未認証 approval でも最高
`MATERIALS_COMPLETE`（`execution_permitted=False`、`runner=NONE`、`jsr_count=0`）。台帳
`spec/reference_jsr_gate.json` への slot `$02` 行追加は #173/#228 の所管とし本 PR では行わず、
上記を host test で実証する。**純正 System Card 3.0 ローカル参照実測: BLOCKED**（実際の理由:
H4/H5 の事前阻止不能 + (a)(b)）。

## 6. 観測手順（ゲート解除時のみ、機械的手順）

1. D1/D2 の CUE/BIN を合成し Geargrafx に mount（媒体のみ。BIOS JSR を伴わないこの段階は安全）。
2. ステージ 1: 最小探針組で CD_BASE → CD_READ を実行し READ LBA（SCSI 層の値）を記録。null READ
   は証拠にしない（台帳 §4a の規則）。
3. ステージ 2: 入力感度プローブ（`_al`〜`_dh` の 1 バイト差分）で問い (1) と base 入力の有無を記録。
4. 判定: `consistent()` で残余クラスを報告。単独 model 名・成功値・VERIFIED は出さない。
5. 合成計算結果と純正 BIOS 観測は別証拠として台帳分類語で記録する。
6. CD_RESET 前後保持（C7/C8）と失敗注入（C9）は #187/#173 の別ゲートが必要で、本課題では実行しない。

## 7. 対応表（課題の完了条件 1: 観測 READ LBA/呼出条件と候補の対応）

| 条件（research/186 C1〜C11 から抜粋） | 探針 | 分離される候補 | 合成判別能力 | 純正 callee |
| --- | --- | --- | --- | --- |
| C1 既定基準（CD_BASE なし READ） | D1/D2 `(5,0)`,`(150,0)` | 固定オフセット族の既定値推定 | MEASURED（ホスト） | UNMEASURED → UNKNOWN |
| C6 媒体対照（INDEX 01 ≠ 0） | D1 vs D2 | `ABS` vs `TRACK_START`、`INDEX00_START` 分離 | MEASURED（ホスト） | UNMEASURED → UNKNOWN |
| C2〜C5 入力感度 | `_al`〜`_dh` 1 バイト差分 | `BASE_PLUS_BLOCK` vs 固定族 | 手順定義のみ（OBSERVATION_PLAN (1)） | BLOCKED |
| C4 `_cl`=1/2（recbase0/1） | C2 に含む | K8 の 2ISO 矛盾の検証 | 手順定義のみ | BLOCKED |
| C7/C8 保持・復帰 | CD_RESET / 連続 READ | 基準の保持期間 | 手順定義のみ | BLOCKED（#187 依存） |
| C9 失敗注入 | 再試行 READ LBA | recbase1 切替（K6） | 手順定義のみ（注入手段未確立） | BLOCKED |
| C10 戻り値、C11 副作用 | A/X/Y/P、MPR/RAM 全差分 | 座標系ではなく契約項目 | 手順定義のみ | BLOCKED（#187） |

未判別は UNKNOWN のまま。同値クラス（§1）に属する候補は、判別できたとしてもクラス単位で報告する。

## 8. UNKNOWN / BLOCKED 一覧と境界

- UNKNOWN（callee 側）: 台帳六項目全部、U1〜U12（research/186 §3）、`CALLER_FIXUP` の delta 値、
  base 既定値、ECMA-119 extent 基準（規格本文未確認）、`$E006` 実機マッピング（#1 所管）。
- BLOCKED: 純正 callee JSR 全般（§5）、D2 の実コア mount / PREGAP 付き媒体の SCSI LBA 対応（NOT RUN）。
- 境界: CD_BASE 実装・CD_READ 純正 callee 振る舞いの断定をしない。VERIFIED/DEV_CALLABLE へ昇格
  しない。`release_eligible=false` 維持。私有 `rom/`・純正 BIOS・商用ディスクのバイト/ハッシュ/
  絶対パス/内部 trace は読まず公開しない。HuCC（BSL-1.0、出自 PENDING）は呼出側記述のみ引用。
  Hu-Go/HuC ライブラリ/kickc（ライセンス未確定）と stum.de（二次資料、独立性なし）は根拠にしない
  （台帳 §1 および `docs/research/186-cd-base-lba-coordinate.md` §1.2 と同様の扱い）。

## 関連

- 計画: `docs/research/186-cd-base-lba-coordinate.md`（C1〜C11、N6 BLOCKED）、台帳: `docs/cd-base-abi-ledger.md`
- 契約: `docs/cd-base-contract.md`、#187: `docs/research/187-cd-base-return-state.md`
- 安全: `docs/173-safety-monitor-limits.md`、`tools/reference_jsr_gate.py`、`spec/reference_jsr_gate.json`
- 合成: `tools/cd_base_lba_candidates.py`、`tests/host/test_cd_base_lba_candidates.py`、#138 の
  `tools/cd_base_probe.py` / `tests/host/test_cd_base_abi_candidates.py`（流用、改変なし）
