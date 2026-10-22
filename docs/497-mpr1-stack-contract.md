# Issue #497: MPR1 スタック許可バンク表と初期状態の検証契約

## 位置づけ

- #473 S0（オーナー承認の検証方針）に基づく S1 の契約表。**本番の安全機構ではない**。
- 合成 ROM と pinned Geargrafx の検証ハーネス専用の契約である。純正 BIOS・商用データは扱わない。
- #459 / #367 のゲートは解除しない。`release_eligible=false` は変えない。純正 ABI は VERIFIED 0/81、参照 JSR 許可は 0/81 のまま。
- 実装上の根拠の既存文書は `docs/473-stack-safety-mechanism-comparison.md`（#473 の方式比較と S0 承認事項）、`docs/473-caller-stack-experiment.md`（合成観測）、`docs/173-safety-monitor-limits.md`（監視限界）。
- 本契約の機械可読な表は `tools/mpr1_stack_contract.py`、検査は `tests/host/test_mpr1_stack_contract.py`。
- #498〜#500 は本契約の確定を待つ。本書の値を変えるには新しい契約版が必要。

## オーナー確認が必要な選択値

次の 4 項目は Issue #497 本文から読める方向に沿うが、具体的な判定値は本 PR の選択である。2026-10-22 にオーナーがチャットで了承した。Issue #497 への記録は未。Issue への記録が揃うまで確定扱いにしない。

| # | 選択値 | Issue 本文での根拠 | 本文に明記が無い部分 |
|---|---|---|---|
| 1 | 中間（命令番号 1 以降）の直接 SetMpr は、`$F8` でも FAIL | 「TAM 以外の設定経路（ハーネスの直接 `SetMpr`、リセット初期値）も対象」「最初のゲスト命令の前に検査」 | 中間の直接設定を FAIL にする点 |
| 2 | `$F7` は FAIL（バックアップ RAM として扱う） | 「初期許可リスト：`MPR1=$F8`（WRAM）のみ。その他は検証ハーネスで拒否」 | `$F7` の種別判定。本リポジトリでは `$F7` の実挙動を検証していない（種別は既定の拒否扱い） |
| 3 | 記録済みの初期化は命令番号 0 に限る（他の番号は FAIL） | 「MPR1 はリセット直後の値を信用しない。実測して状態を記録し、最初のゲスト命令の前に検査」 | 「index 0 に限る」という番号の指定 |
| 4 | NMI は NOT_APPLICABLE（pinned コアに経路が無い）、実機の NMI は UNKNOWN | 「NMI は pinned コアに実行経路が無ければ NOT_APPLICABLE。実機の NMI は UNKNOWN」 | なし（本文の文言どおり）。残る判断は、実機 NMI を後続の Issue で扱うかどうか |

## 1. 許可バンク表（スタック用 MPR1）

| MPR1 | 種別 | 判定 | 根拠 |
|---|---|---|---|
| `$F8` | WRAM | **PASS**（スタックとして許可） | 473 実験の結果 1 で RAM として許可を検出 |
| `$FF` | I/O ページ | FAIL（拒否） | 473 実験の結果 2。スタックが I/O に当たり、副作用は防がれなかった |
| `$F7` | 種別は未検証（バックアップ RAM とされることが多いが、本リポジトリでは検証していない） | FAIL（拒否） | 許可表に無い。fail-closed。オーナー確認事項 2 |
| その他の全値 | 未知または未検証 | FAIL（拒否） | 許可表に無い |

### バンク種別の既知・未知（Issue 作業 1 の表）

本リポジトリで確認できた範囲だけを書く。未確認の種別は「未検証」と書き、実機の仕様として断定しない。

| バンク種別 | 本リポジトリでの扱い | 根拠・限界 |
|---|---|---|
| WRAM（`$F8`） | 既知。スタックとして許可 | 473 実験の結果 1（合成 ROM、pinned コア） |
| I/O ページ（`$FF`） | 既知。拒否 | 473 実験の結果 2（違反時にスタックが I/O に当たった） |
| バックアップ RAM（`$F7` 相当） | 未検証。拒否 | 許可表に無いことによる fail-closed。種別の実挙動は未確認 |
| ROM バンク（`$00`〜 等） | 未検証。拒否（許可表に無い） | 本リポジトリでスタック用途を観測していない |
| CD RAM | 未検証。拒否（許可表に無い） | 本リポジトリでスタック用途を観測していない |

- 参照先は `docs/473-stack-safety-mechanism-comparison.md`（#473 の方式比較。**PR #491 で main に統合済み**）と `docs/473-caller-stack-experiment.md`（合成観測）の 2 文書。方式比較の文書は main 側に存在する（統合コミット `f243e38`）。本 PR のブランチ起点は統合前（`89ee26e`）だが、マージ後は main の文書が使われる。

- 許可は `$F8` のみ。拡大には新しい契約版と独立レビューが必要。
- 許可外のバンクへの push（スタックが許可外バンクへ向く状態）は、検証ハーネスで拒否する。
- MPR1 の値が取得できない場合は PASS にせず BLOCKED。

## 2. 初期状態

- **リセット直後の MPR1 の値は契約の入力にしない（未確定・UNDETERMINED）**。実測値は記録してよいが、判定には使わない。
- ハーネスは、最初のゲスト命令（RunInstruction index 0）の前に **MPR1 = `$F8` を直接設定（DIRECT_SETMPR）し、その書込みを記録すること**（契約。現状の実装ではない）。
- 初期検査の判定:
  - 書込みの記録が無い → BLOCKED
  - 記録された初期化が命令番号 0 以外で行われた → FAIL（命令番号 0 に限る。オーナー確認事項 3）
  - 記録された初期値が `$F8` 以外 → FAIL
  - 命令番号 0 で記録された `$F8` かつ実測のリセット値が何であっても → PASS（リセット値は信用しない）
- 実測のリセット値が `$F8` であっても、記録された設定が無ければ PASS にしない。
- 実測のリセット値は**判定に使わない**。型が不正（文字列・`None` 等）でも、記録された初期化が正しければ判定は PASS のまま（リセット値を信用しないため）。判定値を決めるのは記録された初期化だけ。

### 2.1 入力の型と範囲（fail-closed）

判定関数は例外を投げず、常に `(verdict, detail)` を返す。入力の型と範囲は次のように扱う。

| 入力 | 受け付ける型と範囲 | 型違い（str・`None`・`bool`・リスト等） | 範囲外の数値 |
|---|---|---|---|
| MPR1 値 | 0..255 の `int` | BLOCKED（証拠が読めない） | FAIL（1 バイトの値にならない） |
| 命令番号 | 0 以上の `int` | BLOCKED（証拠が読めない） | FAIL（命令番号にならない） |

- `bool` は MPR1 値としても命令番号としても**不正な型**として扱う。`True` が 1、`False` が 0 として通ることはない（`isinstance` 系の緩い判定を使わない）。
- 契約違反の根拠が「値が契約に無い（範囲外・`$F8` 以外・命令番号 0 以外）」なら **FAIL**、根拠が「証拠が読めない・無い（型違い・記録が無い）」なら **BLOCKED**。この使い分けは §6 の判定値の定義と一致する。
- 例外停止で処理しない: 文字列などを渡しても `TypeError` にはならず、構造化した判定値を返す（`tests/host/test_mpr1_stack_contract.py` の負例で検査する）。

## 3. TAM と直接 SetMpr の区別

| 経路 | 対象 | 判定 |
|---|---|---|
| RESET（リセット初期値） | 契約の入力にしない | 常に BLOCKED（ハーネスが先に設定しなければならない） |
| TAM（ゲストの命令） | 命令境界で MPR1 を比較し、許可表で判定 | `$F8` は PASS、それ以外は FAIL |
| DIRECT_SETMPR（ハーネスの直接設定） | 命令番号 0 で `$F8` の初期設定のみ PASS | 命令番号 1 以降の直接設定は、`$F8` でも FAIL |

- ハーネスの直接設定は初期化の 1 回だけに限る。実行中に MPR1 を変えるなら、新しい契約版で経路を追加する。
- TAM は命令ごとの比較で検出できる。ただし次節の IRQ の穴は別に扱う。

## 4. IRQ 条件（同一命令内の穴）

### 4.1 穴そのもの

- 命令境界での MPR1 比較だけでは、**同じ `RunInstruction` の中で TAM による MPR1 変更と IRQ の push が起きた場合を防げない**。
- 次の 2 つの場合を、いずれも穴として扱う:
  1. **新規 assert**: 同じ `RunInstruction` 内で IRQ が新たに assert され、かつ MPR1 が変わる。
  2. **pending**: 命令開始前から IRQ が pending（命令境界で保留中）で、TAM が MPR1 を変え、その後の HandleIRQ が `$21xx` へ push しうる。
- 判定: MPR1 の変更と（新規 assert または pending）が重なる → **UNMEASURED**（PASS にしない。穴として保持する）。
- MPR1 の変更が無い場合は PASS（命令境界の比較で足りる範囲）。pending で MPR1 が変わらない場合も PASS（push 先は命令境界で判定済みの MPR1 のまま）。ただし **IRQ の観測自体**が無い場合は §4.2 のとおり BLOCKED とする。
- 出典の限定: 473 実験の結果 3 は、8 バイト TII の 1 例の単一観測である。転送の途中（TII 中）では IRQ は受理されず、最後の転送ステップの中でベクタ分岐が起きた。この挙動はソース `huc6280_inline.h:90` の予測と一致したが、一般化はしない。合成コアのもので、実機は未確認。
- NMI: pinned コアに実行経路が無いため NOT_APPLICABLE。実機の NMI は UNKNOWN（#473 受入 7 のまま、オーナー確認が必要）。

### 4.2 IRQ 状態は 3 値（観測あり / 観測なし）

IRQ の状態は **観測結果** としてだけ扱う。3 値表現:

| 値 | 意味 |
|---|---|
| `True` | assert されたことを**観測した** |
| `False` | assert されていないことを**観測した** |
| `IRQ_UNKNOWN`（`None`） | **観測していない**（状態を取得できなかった、または値が渡されなかった） |

- `IRQ_UNKNOWN`（None）を「assert が無い」「pending が無い」と同一視しない。**観測が無いことは安全の確認にならない。**
- `tools/mpr1_stack_contract.py` の `judge_irq_window` の IRQ 引数の既定値は `IRQ_UNKNOWN`。既定値の省略は「安全」の意味ではなく「観測が無い」の意味であり、BLOCKED（証拠が無い）に倒す。
- `IRQ_UNKNOWN` を含む判定は **PASS にしない**（MPR1 変更の有無によらない）。
- 判定は MPR1 変更の有無で先に分岐し、分岐内の規定は**上に書いた順に優先**して適用する。各行の「条件」は `tests/host/test_mpr1_stack_contract.py` が文書プロースを 18 セルに機械適用するための表記である。
  - **MPR1 変更がある場合**:
    - 条件「観測 True あり」→ **UNMEASURED**（観測済みの `True`（assert または pending）が 1 つでもあれば、穴が確定するので穴として保持する。もう片側の未観測は判定を左右しない）。
    - 条件「観測 True 無し・未観測あり」→ **BLOCKED**（観測済みの `True` が無く、未観測（`IRQ_UNKNOWN`）が 1 つでもあれば、証拠が無い）。
    - 条件「両方 観測 False」→ **PASS**（assert も pending も観測済みの `False` なので、穴が無い）。
  - **MPR1 変更が無い場合**:
    - 条件「未観測あり」→ **BLOCKED**（未観測（`IRQ_UNKNOWN`）が 1 つでもあれば観測欠落）。
    - 条件「観測済み」→ **PASS**（push 先は命令境界で判定済みの MPR1 のまま。§4.1）。
- 判定表（`tests/host/test_mpr1_stack_contract.py` が文書とコードの一致を検査する。18 セル = MPR1 変更 2 値 × assert 3 値 × pending 3 値）:

| MPR1 変更 | assert | pending | 判定 |
|---|---|---|---|
| あり | 観測 False | 観測 False | PASS |
| あり | 観測 True | 観測 False | UNMEASURED |
| あり | 観測 False | 観測 True | UNMEASURED |
| あり | 観測 True | 観測 True | UNMEASURED |
| あり | 観測 True | IRQ_UNKNOWN | UNMEASURED |
| あり | IRQ_UNKNOWN | 観測 False | BLOCKED |
| あり | IRQ_UNKNOWN | 観測 True | UNMEASURED |
| あり | 観測 False | IRQ_UNKNOWN | BLOCKED |
| あり | IRQ_UNKNOWN | IRQ_UNKNOWN | BLOCKED |
| なし | 観測 False | 観測 False | PASS |
| なし | 観測 True | 観測 False | PASS |
| なし | 観測 False | 観測 True | PASS |
| なし | 観測 True | 観測 True | PASS |
| なし | 観測 True | IRQ_UNKNOWN | BLOCKED |
| なし | IRQ_UNKNOWN | 観測 False | BLOCKED |
| なし | IRQ_UNKNOWN | 観測 True | BLOCKED |
| なし | 観測 False | IRQ_UNKNOWN | BLOCKED |
| なし | IRQ_UNKNOWN | IRQ_UNKNOWN | BLOCKED |

- 「MPR1 変更なし」で**観測済み**の IRQ 状態が如何であれ PASS するのは、push 先が命令境界で判定済みの MPR1 のままであるため（4.1）。ただし IRQ の**観測自体**が無い場合は観測欠落なので BLOCKED とする。
- 上の `IRQ_UNKNOWN` は「IRQ 状態が読めなかった」場合の判定値であり、4.1 末尾の NMI の `UNKNOWN`（実機 NMI は未確認）とは別物である。NMI の `UNKNOWN` は注記で、判定値ではない。
- **運用上の帰結（S2 以降）**: 判定対象の**全命令窓**で IRQ 状態（assert と pending）を観測しないと、観測を欠ける命令窓が 1 つでも BLOCKED になり、判定全体は PASS にしない（§7 の未検証項目）。

## 5. 検査基準（S2 以降が共有する入力）

各命令境界（または事象の時点）で、次を記録し照合する。

| 区分 | 検査基準 |
|---|---|
| A / X / Y | 呼出し前後で一致（ゲストの呼出し規約に従う範囲） |
| P | B を除く（マスク `$EF`）で前後一致 |
| S | 呼出し側の SP 下限と、CPU スタック範囲（`$2100`〜`$21FF`）の内外。表記は §5.1 |
| PC | 検査点の PC（ラベル到達時） |
| MPR0〜MPR7 | 命令ごとに比較。MPR1 は §1 の許可表で判定 |
| VDC / WRAM | 書込みの宛先と値の変化（変化のみの記録は、値が変わらない書込みを見ない） |
| IRQ | assert と deassert、ベクタ分岐の時点。§4 の穴は UNMEASURED。状態が取得できない場合は IRQ_UNKNOWN（BLOCKED） |
| 宛先 | スタック push の宛先が許可バンク（`$F8`）内か |

### 5.1 スタックアドレスの表記（取り違え防止）

| 用語 | 範囲 | 意味 |
|---|---|---|
| CPU スタックページ | `$2100`〜`$21FF` | 推定（ソース読み、実測なし）: pinned Geargrafx の実スタック。MPR1=`$F8` の窓（`$2000`〜`$3FFF`）を通して見える。`docs/473-caller-stack-experiment.md` :78 でも `$21FC` への書込みは推定扱い |
| テストラベル | `$0100`〜`$01FF` | テスト ROM と記録（WRAM オフセット表記）で使う別名。CPU のスタックアドレスではない |

- 473 文書の WRAM 書込み記録（`$01FC`〜`$01FF`）は WRAM 側の表記であり、CPU の push 先は `$21xx` として扱う。
- `tools/mpr1_stack_contract.py` の `check_stack_address` は、`$2100`〜`$21FF` だけを PASS にする。`$0100`〜`$01FF` は FAIL（取り違えの負例）にする。

- 上表は**検査基準の列挙**である。宛先・A/X/Y/P/S/PC/VDC/WRAM/IRQ の照合を実装した検査は本 PR に無い（MPR1 の許可判定と初期化の順序のみ `tools/mpr1_stack_contract.py` と Host 試験で検査する）。
- **未接続の判定関数**: `check_stack_address` と `judge_irq_window` は、Host 試験からのみ呼ばれる。検証ハーネス（`tests/integration/` の実コア試験を含む）には接続していない。
- **`check_stack_address` の PASS は安全判定ではない**: 値の範囲（`$2100`〜`$21FF`）だけを見て、MPR1 は見ない。MPR1 の判定は `check_stack_bank` が別に行う。両方が PASS でも、この PR の範囲では安全性の証明にはならない。

## 6. 証拠レベル

- 本書の判定は、合成 ROM と合成データによる Host 検査の範囲に限る。実コアの観測は #473 実験の結果を参照する。
- 証拠レベル:
  - Host 試験（`tests/host/test_mpr1_stack_contract.py`）: 契約の内部整合性の検査。実コアの振る舞いは証明しない。
  - 実コア試験（`tests/integration/`）: 本 Issue では追加しない（後続の S2 以降で必要に応じて扱う）。
- 実機の挙動（TAM、IRQ、NMI）は未確認。

### 判定値の列挙

- `PASS` / `FAIL` / `BLOCKED` / `UNMEASURED`（コード `tools/mpr1_stack_contract.py` の判定値）。
- 判定の意味（本契約で一貫して使う）:

| 判定 | 意味 | 使う条件 |
|---|---|---|
| `PASS` | 契約どおりで、安全の確認が取れている | 許可表の値（`$F8`）、正しい初期化位置（命令番号 0）、CPU スタックページ内、IRQ の必要項目が観測済みで陰（negative） |
| `FAIL` | 証拠は**読める**が契約違反 | 許可表に無い値、0..255 の範囲外、`$F8` 以外、命令番号 0 以外、テストラベルページ（`$0100`〜`$01FF`） |
| `BLOCKED` | 証拠が**無い / 読めない** | 型違い（str・bool・None 等）、記録が無い、IRQ 状態が未観測（`IRQ_UNKNOWN`） |
| `UNMEASURED` | **既知の穴** | 同じ命令内で IRQ が**観測され**、MPR1 変更と重なった（命令境界の比較では push が見えない） |

- `BLOCKED`（証拠が無い）と `UNMEASURED`（既知の穴）は区別する。前者は「判定に必要な情報が無い」、後者は「情報は揃っているが命令境界の比較では見えない」ときに使う。
- `NOT_APPLICABLE` は判定値ではなく注記である。NMI について「pinned コアに実行経路が無い」ため使う（§4）。実機の NMI は `UNKNOWN`（未確認）。
- 参照: #470 / #481 の観測について、`docs/` に `470` / `481` で始まる文書は無い。本書は #470 / #481 の観測そのものを引用しない（#470 / #481 は `docs/473-stack-safety-mechanism-comparison.md` が整理している）。実装上の根拠は `docs/473-stack-safety-mechanism-comparison.md`、`docs/173-safety-monitor-limits.md`、`docs/473-caller-stack-experiment.md`。

## 7. 未検証（UNMEASURED / 未確認）

- 同一命令内の TAM と IRQ push の重なり（§4）。
- IRQ 状態の実コアでの観測手段（§4.2）。`judge_irq_window` は Host 試験からのみ呼ばれ、実コアの IRQ 観測は S2 以降の作業（§5 の未接続の判定関数）。S2 以降は**全命令窓**で IRQ 状態を観測しないと全体が BLOCKED になる（§4.2 末尾の運用上の帰結）。
- 実機のリセット直後の MPR1 の値（判定に使わないため、測る必要は無い）。
- 許可外バンク（`$F7` を含む）の実際の振る舞い。
- 実機の NMI。
- VDC の読出しと I/O 読出しの副作用（473 実験の未観測項目）。
- 契約違反の他の型（S 不足、T=1 など）。

## 8. 変更しないもの

- #459 / #367 のゲートは BLOCKED のまま。
- 純正 ABI VERIFIED 0/81、参照 JSR 許可 0/81、`release_eligible=false`。
- GPL の pinned コアの検証パッチと製品コードの混入はしない。純正 ROM や商用データを使わない。
