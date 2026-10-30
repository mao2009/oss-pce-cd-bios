# 候補入口の出典と管理状態（81 スロット）

この文書は、`spec/api_contracts.json` の候補入口と名称について、**管理状態だけ**を記録する。名称の対照表は掲載しない（オーナー裁定）。

## 管理状態

| 項目 | 状態 |
|---|---|
| 入口（JSR 入口） | DEV ROM の生成テーブルの構造規則（`tools/build_rom.py` の配置規則）に従う。入口の値は本書に記載しない。純正 BIOS の ABI 検証証拠として扱わない |
| 名称 | 二次資料に基づく候補。一次資料（System Card の原典）では未確認 |
| provenance | **PENDING**（二次資料の再利用は未承認） |
| ABI 検証 | **UNVERIFIED** |
| 参照 JSR の実行 | **BLOCKED** |
| release_eligible | false |

## 出典

- 二次資料: Michael Stum, "PC Engine CD-ROM² BIOS Method offsets", *Not Rocket Science* (stum.de), 2025-07-28 公開。ライセンス CC BY-NC-SA 4.0。
  <https://www.stum.de/2025/pcenginebiosoffsets/>
- 上記ページの内容は、この文書とリポジトリの公開ファイルに転載しない。

## 名称の状態

名称の二択・別名・著者の仮称といった状態は、契約台帳の既存フィールドと `tools/public_api_ledger.py` の検証で表現する。この文書には、スロットごとの状態を列挙しない。

## 公開版の扱い

- 公開台帳は名称を出力しない（`name_publication: WITHHELD_PROVENANCE_PENDING`）。名称の公開は、provenance の承認後にオーナーが判断する。
- 公開台帳の名称状態（`name_source`）と出典の確度（`source_confidence`）は、全スロットで同一の値（`WITHHELD_PENDING` / `PENDING`）とする。スロット別の分類は公開せず、内部の契約検査でだけ使う。
- 公開管理出力は入口値を列挙しない（オーナー裁定）。入口の配置規則は、契約台帳側の整合検査にだけ使う。
