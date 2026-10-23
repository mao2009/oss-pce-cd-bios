# Issue #189: CD_READ の無媒体・範囲外・count 0 の失敗契約（静的整理と合成判定器）

親: #12 / #11。前提: #148（CLOSED）、#188（PR #503 MERGED、転送先 `_dh = 3` を優先観測候補とする D1）。

## 0. 判定

* **実 CD_READ 失敗契約の実観測件数: 0。** 純正 BIOS の実行 0 件、`$E009` の呼出 0 件、Geargrafx・実機・第二エミュレータの観測 0 件。
* **#189 は完了していない。Issue #189 は OPEN のまま。本 PR は `Refs #189` のみで、Close しない。**
* **実観測: 未実施。**
* 判定は **BLOCKED**。理由: #173 の子 #365 / #366 / #367 が OPEN、slot 3 の reviewed gate 記録と独立承認がない（[188-cd-read-mode-safety.md](188-cd-read-mode-safety.md) G1-G2）。
* 本 PR は契約の整理と、合成入力だけを判定する `tools/cd_read_error_verdict.py` を追加する。合成 PASS / FAIL は純正 ABI の証拠ではない。
* `release_eligible = false`、VERIFIED は付与しない。#459 / #367 は BLOCKED のまま。

## 1. 選んだ条件（実行可能性の観点）

| ID | 条件 | 選定理由 | 既存の証跡 |
| --- | --- | --- | --- |
| C1 | 範囲外 LBA（LBA 16、合成16セクター媒体の外） | 転送層で STATUS のみ・宛先未変更が計測済み | [M] T3（`invalid_sector_lba16`）。callee は未観測。**範囲判定は Q7（LBA の基点）に依存するため暫定** |
| C2 | count 0 | 呼出側の分岐（HuCC L436-438）は確認済み。BIOS の戻りは未計測 | [C] A4、[M] なし |
| C3 | 無媒体 | 実行不能。静的 BLOCKED として残す | [U] [cd-read-contract.md](cd-read-contract.md) の T7（「no disc / zero-sector image」、UNKNOWN、計測なし。Geargrafx は媒体なしで実行できない） |

証跡ラベルと判定語の凡例（本文の [C] [M] [U] と、判定器の U / F / B / P）:

| 記号 | 種別 | 意味（証跡として） |
| --- | --- | --- |
| [C] | 呼出側ソース | 命令がそう書いていることだけを示す。コメント由来は含まない（188 文書の [C] 定義に従う） |
| [M] | 実測（既存証跡のみ） | 転送層の計測。callee の実測ではない |
| [U] | 証跡なし | UNKNOWN。計測も確定もない。正常値へ変換しない |
| U | UNKNOWN（判定） | 期待値が UNKNOWN の項目。観測があっても PASS にならない。証跡として「未実測」 |
| F | FAIL（判定） | 既知の期待値と観測が不一致。合成入力だけで起こり、純正 ABI の証拠ではない |
| B | BLOCKED（判定） | 判定不能。未到達・不正値・監視失敗・gate 不許可・不正な入力。証跡として「実施できない」 |
| P | PASS（判定） | 既知の期待値と全項目が一致。合成のみで、現行の表からは出ない。純正 ABI の証拠ではない |

本 PR の判定器は合成入力だけを判定する。F / P は「判定器が正しく動いた」ことの証拠に過ぎず、CD_READ の実挙動の証拠にはならない。

対象外: 末尾跨ぎ（T4 のみ計測、#189 残件として §5 に残す）、timeout / busy、他の転送モード、CD_EXEC。

## 2. 観測項目と契約（A / X / Y / P、MPR、stack、IRQ、RAM）

| 項目 | 観測方法 | 期待値 | 根拠 |
| --- | --- | --- | --- |
| A / X / Y / P | 戻り時のレジスタ | UNKNOWN | 呼出側は戻りの A を読む（[188-cd-read-transfer-mode-contract.md](188-cd-read-transfer-mode-contract.md) B1）。callee の値は未観測 |
| MPR0-7 | 戻り後の8値 | UNKNOWN | 呼出側は MPR3 / MPR4 のみ退避（B3）。失敗時に壊れるのは HYPOTHESIS |
| stack（SP） | 呼出前後の SP | UNKNOWN | JSR 入口・RTS 出口のみ観測（B8） |
| IRQ（P の I フラグ） | 戻り時の P | UNKNOWN | 本ラッパに `cli` / `sei` なし（B7） |
| RAM 宛先外の書込み | 監視で数える | UNKNOWN | C1 の宛先未変更は T3 の転送層 [M] のみ。callee の確定ではない |

## 3. 判定規則（`tools/cd_read_error_verdict.py`）

| 判定 | 条件 |
| --- | --- |
| BLOCKED | ケースが dict でない、`executable` が厳密に `True` でない、監視失敗（`monitor_ok` が `True` でない）、観測が dict でない、未到達（`reached` が `True` でない）、gate が ALLOWED でない、期待値表が dict でない、既知の期待値に対する観測の欠落、観測・期待値の型・範囲・長さの不正（`bool` を `int` として扱わない）、期待値の欠落。例外を投げず、PASS にはならない |
| FAIL | 既知の期待値と観測が不一致（現行の表は全て UNKNOWN のため、合成期待値でのみ起こる） |
| UNKNOWN | 期待値が UNKNOWN の項目。観測があっても正常値へ変換しない |
| PASS | 全項目が既知の期待値と一致。現行の表からは出ない |

**観測キーの欠落の規則（正式）:** `monitor_ok` / `reached` / `gate` が全て通過した状態で、項目キー（`a` … `ram_outside_dest`）が観測に無い場合:

* 期待値が UNKNOWN の項目 → **UNKNOWN**（未観測＝未実測。BLOCKED にしない）。
* 期待値が既知の項目 → **BLOCKED**。
* どちらの場合も PASS にはならない。

UNKNOWN とした根拠: #189 の完了条件は「監視失敗は FAIL/BLOCKED」「mock のみの場合は未実測」と書く。`monitor_ok=False` は監視失敗に当たり BLOCKED。キーの欠落は監視が成功したうえでの未観測であり、「未実測」に当たる。UNKNOWN 期待の項目は元々正常値へ変換しないため、判定の結果は UNKNOWN（非 PASS）になる。欠落を BLOCKED に変える案は、現行 3 ケースの全項目 UNKNOWN の判定を BLOCKED に変えるため、本 PR では採らない。

**優先順位（コードの実際の順序）:** 判定は 2 段階で行う。

1. **全体を止める早期 BLOCKED**（項目を判定せず BLOCKED を返す）: `executable` 不許可、ケースが dict でない、観測が dict でない、`monitor_ok` 不通過、`reached` 不通過、`gate` 不許可、期待値表が dict でない。この段階の BLOCKED は、項目に FAIL があっても FAIL より優先する。
2. **項目ごとの判定**: 各項目は FAIL / BLOCKED / UNKNOWN / PASS のいずれか。全体は `max` で FAIL > BLOCKED > UNKNOWN > PASS の順に決まる。

例: C1 に `a = 1`（既知の期待値、観測は 0 で FAIL）を与え、`reached = False` とすると、全体は **FAIL ではなく BLOCKED**（段階 1）。同じ期待値で `reached = True` とすると FAIL（段階 2）。

`tests/host/test_cd_read_error_verdict.py` は段階 2 の FAIL・BLOCKED・UNKNOWN・PASS が混在する実ケースを `judge()` に通して順序を検証し、段階 1 の早期 BLOCKED が FAIL より優先することも検証する。

現行の 3 ケースはいずれも期待値が全て UNKNOWN のため、どの観測を与えても PASS にならない（テストで確認）。C1 の範囲判定は Q7 に依存するため暫定。

## 4. 既存の呼出側・既存文書との整合

* 呼出側の事実: HuCC の cd_read 呼出側は戻り値が 0 のとき成功、非0をエラーとして扱う（[cd-base-abi-ledger.md](cd-base-abi-ledger.md) (6)、HYPOTHESIS の期待）。これは **呼出側の期待** であり、BIOS の戻り値の証拠ではない。
* 既存の `cd-read-contract.md` の失敗値（no disc / bad block / read error / busy）の値は未確定（[abi-contracts.md](abi-contracts.md) CD_READ 節）。
* 本 PR は既存文書を書き換えない。
* Q1〜Q9 はすべて未解決のまま残す。本 PR は解決したと主張しない。

## 5. 未解決事項

| ID | 未解決の問い | 影響 | 解消条件 | 担当 |
| --- | --- | --- | --- | --- |
| Q1 | count 0 の戻り A は 0 か非0か。転送を行うか | C2 | 独立 gate 付きの実測（BLOCKED） | #189 |
| Q2 | 範囲外 LBA の A / X / Y / P、宛先の不変 | C1 | 同上 | #189 |
| Q3 | 無媒体の戻り値と副作用 | C3 | 媒体なしのローダー、または別手段の観測 | #189 |
| Q4 | 失敗時に MPR が壊れるか（HYPOTHESIS） | MPR | 観測（BLOCKED） | #189 / #190 |
| Q5 | 戻り時の P.I、IRQ 前提 | IRQ | 観測（BLOCKED） | #190 |
| Q6 | BIOS 内のスタック深さ | stack | 観測（BLOCKED） | #189 |
| Q7 | LBA の基点（絶対か CD_BASE 相対か） | C1 の解釈 | #11 の測定 | #11 |
| Q8 | 末尾跨ぎの部分書込み | 対象外（#189 残件） | 別 Issue へ分割 | #189 |
| Q9 | count 単位（セクターか）の確定 | C2 の解釈 | コードの事実からは導けない（A4） | #188 |

## 6. 実施していないこと

* 純正 BIOS の実行、`$E009` の呼出し、Geargrafx の実行、実機、第二エミュレータの観測。
* 判定器はネットワーク・ROM・媒体イメージ・BIOS を読まない。入力は合成の辞書のみ。
