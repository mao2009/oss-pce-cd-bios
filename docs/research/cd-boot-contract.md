# Issue #289: slot $00（候補 `CD_BOOT`、候補エントリ `$E000`）の起動・移譲・復帰規約の caller 根拠

対象: 親 #9 の slot `$00`。機械可読台帳は [cd-boot-contract-ledger.json](cd-boot-contract-ledger.json)、検査は `tests/host/test_cd_boot_contract.py`。
本書は調査台帳である。ROM への配線、参照 System Card の callee の実行、`spec/`・`docs/api-inventory.md`・`docs/status.md`・`Makefile` の変更は行わない。
**block_status は BLOCKED_ABI のまま、release_eligible は false、DEV_CALLABLE なし。参照挙動の VERIFIED・PASS は主張しない。**

## 1. 結論

- caller 側の根拠は揃った。HuC SDK（BSL-1.0）は、CD 時にソフトリセットの処理として `Y=$00` で `JSR $E000` を呼び、呼ぶ前に MPR7 を bank `$00` へ切り替え、戻り後に呼出側の MPR7 を復元する。これは**呼出側の振る舞い**であり、callee の規約ではない。
- callee 側の規約（A/X/Y/P の入力と出力、`$20F8-$20FF`、MPR0-6 の要求、スタック、IRQ 状態、ディスク検出、移譲先、停止、エラー）は、取得した公開資料のどれにも記載がなく、**すべて UNKNOWN**。
- 「戻る」は HYPOTHESIS にとどめる。呼出側のコードが戻りを前提にしているが、callee の記述はない。
- HuC は `cd_boot = $00:E000` と宣言するのみである。`$E000` が JMP テーブルの入口かどうかは HuC に記載がなく、その説は Stum（未照合の二次資料）にのみあるため HYPOTHESIS とする。本体アドレスも同じ理由で HYPOTHESIS。`$E000` を callable とも非復帰とも断定しない。
- リセット起動（ハードウェアが MPR7=`$00` にして `$FFFE` を読む）と slot 呼出し（稼働中プログラムからの `JSR`）は別の経路である。

## 2. 出典（固定版・ライセンス）

| ID | 位置 | 版 | ライセンス | 性質 |
| --- | --- | --- | --- | --- |
| HUC_PCENGINE | `include/hucc/pcengine.inc` 8 行目（BSL-1.0 の表示）、467-468 行（`.if CDROM`、`cd_boot = $00:E000, FUNC`、`cd_reset = $00:E003, FUNC`） | pce-devel/huc `54f5c736` | BSL-1.0 | 一次（SDK の宣言） |
| HUC_SYSCARD_ASM | `include/hucc/hucc-systemcard.asm` 10 行目、85-88 行（`_cd_boot` マクロが `system cd_boot` を呼ぶ） | 同上 | BSL-1.0 | 一次（SDK の呼出しマクロ） |
| HUC_CORE_KERNEL | `include/hucc/core-kernel.asm` 10 行目、478-481 行（CD 時 `core_sw_reset`: `sei`、`cly`、`call_bios`）、500-516 行（`call_bios`） | 同上 | BSL-1.0 | 一次（呼出側の実コード） |
| HUC_CORE_INC | `include/hucc/core.inc` 10 行目、41-43 行（`system` マクロ: `ldy #<\1`、`jsr call_bios`） | 同上 | BSL-1.0 | 一次（呼出側） |
| HUC_CPU_TXT | `doc/pce/cpu.txt` 30 行（page 7 = `$E000-$FFFF`）、64-66 行（page 7 = bank `$00`、リセット後の注記）、131 行（`$FFFE RESET`） | 同上 | **ファイル内に記載なし** | コミュニティ SDK の注記。製造元マニュアルではない |
| STUM_2025_OFFSETS | <https://stum.de/2025/pcenginebiosoffsets/>（2025-07-28） | 取得日 2026-10-19 | 本文 CC BY-NC-SA 4.0、掲載コード MIT | 二次資料（版別の本体アドレス表） |
| ZEOGRAD_BIOS_NOTES | <https://www.zeograd.com/download/pce_bios.html> | 取得日 2026-10-19 | 取得内容に記載なし | 二次資料（機能説明の一文のみ。未照合のため台帳の根拠には使わない） |
| REPO_TESTING_DOC | `docs/testing.md`（DEV ROM の 81 スロット表） | 本リポジトリ | — | 本プロジェクトの記述（参照挙動ではない） |
| REPO_SYSTEM_CARD_ROM_DOC | `docs/system-card-rom.md`（リセット時 MPR7 と各ベクタ） | 本リポジトリ | — | 本プロジェクトの要約。製造元マニュアルは本 PR で未照合 |

注記:

- 上の HuC 行番号は、固定 commit の raw ファイルを `gh api` で取得して照合した。WebFetch による要約からは行番号を確定できなかったため使っていない。
- Stum と Zeograd の本文は、ツールによる抽出・要約を経ている。引用文は、版を固定して原文と照合するまで確定扱いにしない。
- **除外**: `include/huc/cdrom.asm`（ライセンス不確定、未読・未引用）。Hu-Go! `bios.c`（GPL-2、HLE、BIOS ABI の証拠ではないため未読）。`./rom/` の内容（未読）。

## 3. 根拠の種類

- **SOURCE_FACT**: 上記の公開資料に、その記述があると引用できるもの。主張の範囲は「その資料がそう書いている」まで。
- **HYPOTHESIS**: 資料の記述から推測できるが、どの資料にも明示されていないもの。昇格させない。
- **UNKNOWN**: 資料に記載がない、または矛盾があるもの。値は null とし、埋めない。
- **PROJECT_DEV_FACT**: 本リポジトリの開発用 ROM についての記述。参照 System Card の挙動の証拠ではない。

## 4. 項目別の判定

| 項目 | 判定 | 根拠（種類別） | 残る UNKNOWN / 未決 |
| --- | --- | --- | --- |
| エントリ | SOURCE_FACT | HUC_PCENGINE: `cd_boot = $00:E000` と宣言 | 入口の意味（JMP テーブルかどうか）は HuC に記載なし。JMP テーブル説は HYPOTHESIS（STUM のみ） |
| 本体アドレス | HYPOTHESIS（二次・版別・未照合） | STUM: SC1.0 は `$E0E7`、SC2.x/3.0 は `$E0F3`（`$E0F3` は本リポジトリの DEV 表の終端（`docs/system-card-rom.md` 125 行、`docs/api-stub-scaffold.md` 10 行）と偶然一致するが、同一視しない） | 一次資料での照合なし、実行による確認なし |
| 機能の説明 | UNKNOWN | — | 二次資料の一文のみで未確認のため採らない。移譲・検出・復帰も記載なし |
| 呼出し前の MPR7 | SOURCE_FACT（呼出側） | HUC_CORE_KERNEL: `cla; tam7` で bank `$00` を選ぶ | callee が要求する MPR は UNKNOWN |
| 戻り後の MPR7 | SOURCE_FACT（呼出側） | HUC_CORE_KERNEL: 保存した MPR7 を復元 | callee が MPR0-6 を保存するかは UNKNOWN |
| A・X・Y（呼出側の提供） | SOURCE_FACT（呼出側） | `call_bios` は A を push/pull して退避し、X には触れない。Y は `system` マクロの低位バイト（slot 0 は `cly` で `$00`） | callee の入力要求は UNKNOWN |
| P（呼出側） | SOURCE_FACT（呼出側） | CD ソフトリセット経路のみ `sei` | callee の I フラグと IRQ 扱いは UNKNOWN |
| スタック | HYPOTHESIS | 呼出側は `jsr` 後にコードを続けるため、戻りとスタックの釣合いを前提にしている | callee のスタック使用量と規約は UNKNOWN |
| 戻る | HYPOTHESIS | HUC_CORE_KERNEL の復元コード。ただし HuC の `cd_boot` は `void` 宣言で、戻りの保証ではない | 非復帰かどうかは UNKNOWN |
| 入力レジスタ（callee） | UNKNOWN | — | 全資料に記載なし |
| 戻りレジスタ・フラグ・エラー表示 | UNKNOWN | — | 全資料に記載なし |
| `$20F8-$20FF` | UNKNOWN | 取得した HuC ファイルと二次資料のいずれにも記載なし。本リポジトリも定義していない | 作業域かどうか、書換えの有無は不明 |
| MPR0-6 の要求 | UNKNOWN | — | — |
| IRQ 状態（入場時・ブート中） | UNKNOWN | 呼出側の `sei` は CD ソフトリセット経路だけ | callee 側は不明 |
| メディア検出 | UNKNOWN | — | 検出方法と条件は不明 |
| ディスク未挿入時の動作 | UNKNOWN | — | 任意の資料に記載なし |
| 移譲先（ブート後の制御先） | UNKNOWN | — | 移譲先・MPR・スタック・IRQ の状態は不明 |
| 停止条件 | UNKNOWN | DEV ROM の stub は PROJECT_DEV_FACT（参照の証拠ではない） | callee の停止・待機・ループは不明 |
| エラー規約 | UNKNOWN | — | 失敗の定義と戻し方は不明 |
| callable・非復帰（参照） | UNKNOWN | 台帳で `callable: UNKNOWN` | BLOCKED_ABI を維持 |
| DEV ROM の slot 0 | PROJECT_DEV_FACT | REPO_TESTING_DOC: 80 個の非復帰 stub の一つ | 参照 System Card の挙動とは無関係 |

## 5. リセット起動と slot 呼出しの違い

- リセット: CPU リセットで MPR7 は `$00` になり、PC は `$FFFE/$FFFF` から読まれる（HUC_CPU_TXT 64-66、131 行、REPO_SYSTEM_CARD_ROM_DOC の表）。ただし primary-sdk の出典はこれを述べておらず、コミュニティ注記と未照合の repo 要約のみのため、台帳の `reset_vs_slot_call` は HYPOTHESIS に留める。割込みと D フラグの初期状態は、本書では製造元マニュアルを照合していないため扱わない。
- slot 呼出し: 稼働中プログラムからの `JSR $E000`。HuC の呼出側は MPR7 を `$00` にする処理を自分で行う（HUC_CORE_KERNEL 500-506）。ハードウェアのリセット処理はここで行われない。
- したがって `$E000` の挙動を、リセットベクタの挙動から推定しない。

## 6. 機械検査

`tests/host/test_cd_boot_contract.py` は、台帳に次を課す。

- `block_status` は BLOCKED_ABI、`release_eligible` は false、`DEV_CALLABLE` と `VERIFIED`・`PASS` の文字列はない。
- 各項目の状態は、この PR で決めた値（UNKNOWN・HYPOTHESIS の項目）から変わらない。UNKNOWN は `value: null`、出典なし、説明あり。
- SOURCE_FACT は出典が 1 件以上あり、すべて台帳の出典表に存在し、そのうち 1 件以上が適格である。適格とは、tier が primary-sdk の出典、または tier が project で `verified_primary: true` の出典（リポジトリ文書のうち一次資料として照合済みのもの）を指す。secondary-web・community-sdk-notes のみ、または未照合の project 要約のみに基づく SOURCE_FACT は不合格。現時点で `verified_primary` を true とする project 出典はない。
- 本体アドレス・JMP テーブル説・機能の説明の 3 行は、上の tier 規則とは別に、REQUIRED_STATUS で状態（HYPOTHESIS・UNKNOWN）を固定している。
- 候補エントリは `callable: UNKNOWN`、`returns_to_caller: UNKNOWN`。`true` は不合格。
- `include/huc/cdrom.asm`、`./rom/`、Hu-Go `bios.c` を出典として参照しない。

テストには、UNKNOWN の昇格、HYPOTHESIS の昇格、callable の設定、`release_eligible`/`dev_callable` の変更、除外出典の参照、二次資料のみに基づく SOURCE_FACT、未照合の project 要約のみに基づく SOURCE_FACT を、それぞれ不合格にする変異テストを含む。

## 7. 未実施・制限

- **NOT_RUN**: 参照 System Card の callee の実行、実コアでの測定、ROM への配線。いずれも行っていない。
- **NOT_RUN**: 独立レビュー（別エージェントによる検証は未実施）。
- **NOT_RUN**: `make check`、`make test`、`make build`、`make setup`、関連 CI（実行していない）。
- 一次資料（製造元マニュアル、System Card 本体のドキュメント）は未入手。台帳の SOURCE_FACT はすべて、公開された SDK・二次資料に限る。
- Hu-Go! の HLE は、BIOS ABI の証拠として扱えないため、意図的に読んでいない。
- 本書の結論は、caller の振る舞いと、callee 規約の UNKNOWN の範囲に限る。System Card 互換や reference BIOS の正しさを示すものではない。

## 8. 次の作業

1. 製造元 System Card マニュアル（版とライセンスを記録できるもの）を入手し、本体アドレスと入力規約を照合する。
2. 私的に所有する System Card を用いた実コア測定（maintainer の許可があるもののみ）で、`$20F8-$20FF`、IRQ 状態、MPR 副作用、メディア未挿入時の挙動を観測する。
3. 観測が揃うまで、`callable` と `returns_to_caller` は UNKNOWN のままにする。
