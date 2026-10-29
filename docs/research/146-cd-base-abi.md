# Issue #146: CD_BASE 呼出側 ABI 六項目の独立根拠と観測可能性

対象: スロット `$02`、エントリ `$E006`（`CD_BASE`）。親は #11。本書は調査台帳であり、ROM 実装・`spec/`・`docs/abi-contracts.md`・
`docs/cd-base-abi-ledger.md` は変更しない。**実コアでの測定は一件もない。このため本書に VERIFIED はない**
（既存台帳の VERIFIED はすべて「呼出側ソースにこの記述がある」という意味で、そのまま維持する）。

## 1. 追加した独立候補の出典（#128 台帳に無かったもの）

| ID | 出典 | 日付・ライセンス | 性質 |
| --- | --- | --- | --- |
| W1 | Michael Stum「Why do PC Engine CD-ROM² games have a second data track?」<https://www.stum.de/2026/pc-engine-second-data-track/> | 2026-05-01。本文 CC BY-NC-SA 4.0、掲載コードは MIT（明記がある場合を除く） | 二次資料。`CD_BASE` を呼ぶ例コードと説明文（`cd_base` は LBA・時刻・トラック番号を取れ、`cd_read`・`cd_exec`・`cd_seek` 等の基準を決める、と記す。`$80` は例コードのコメント `_BH = CD-TNO mode` に出る）。16回読込み失敗後は `retry?`（`$2273`）により `recbase1` を使う。根拠は「著者自身のゲームと System Card の解析」で、archive.org の System Card 1.0 開発資料へのリンクがある。ただしどの記述がどの資料に基づくかは**記載なし**（逆アセンブル・トレースの有無も不明） |
| W2 | 同「PC Engine CD-ROM² BIOS Method offsets」<https://stum.de/2025/pcenginebiosoffsets/> | 2025-07-28。同上 | 二次資料。`cd_reset $E003`、`cd_base $E006`、`cd_read $E009` のジャンプテーブル入口と、各 System Card 版の本体アドレス表のみ。**引数・戻り値の記載なし**。著者は System Card 1.0 マニュアルを所持し公式名称を得たと記す |

W1・W2 は本文を言い換えて引用し、BIOS のバイト列・逆アセンブルは取り込まない。公式 NEC/Hudson 資料（System Card 1.0 マニュアル等）の本文は今回の検索（2026-10-16）で
入手できなかった（W1 が archive.org へのリンクを持つと記すが、本文は未確認）。HuCC 側の出典 S1〜S7 は `docs/cd-base-abi-ledger.md` §2 の固定版 commit `54f5c736…` のとおり。

出典間の相反: W1 の例（`_BH=$80`、`_AL`=BCD のトラック番号、`_CL=$01/$02`）は HuCC S1（`_bh=$80`、`_cl=2`、`_al`=`tnomax`）と値が整合する。
ただし W1 の著者が HuCC を参照した可能性を排除できないため、**独立した二つの証拠とは数えない**（独立性は UNKNOWN）。
相反する記述は見つからなかった。W1 の「`recbase1` は読込み失敗後のみ使う」は、#128 台帳 §3 補足の「2ISO 経路の矛盾」を解く**候補**にとどまる（下記 (4)）。**矛盾は OPEN のまま**（実コア証拠なし）。

参照のみ（補強には使わない）: Hu-Go! `mckayemu/hugo@64ed226d` `bios.c` の HLE は、ライセンス・帰属が未確定のため内容を参照しない（`docs/cd-base-abi-ledger.md` §1）。HLE であり実 BIOS の証拠にならないため、HYPOTHESIS の根拠にも独立した補強にも数えない。

**#148 台帳（`docs/research/148-cd-read-abi.md`）との関係**: #148 は当初、基点を UNKNOWN としていた。本書の「相対」は W1 に依るのみで、両台帳とも §6 / 項目4 で同じ状態（HYPOTHESIS、実コア未測定、2ISO 矛盾 OPEN）に揃えた。
## 2. 六項目の判定

状態語: VERIFIED（実コア証拠あり。今回 0 件）/ HYPOTHESIS（出典の記述はあるが呼出先未測定）/ UNKNOWN / BLOCKED（観測手段が現状ない）。

| # | 項目 | 判定 | 独立した根拠（出典） | 観測可能性 | 決着に必要な観測 |
| --- | --- | --- | --- | --- | --- |
| 1 | 引数の場所 | HYPOTHESIS（呼出側の書込みのみ既存どおり VERIFIED-source。呼出先の読取りは UNKNOWN） | S1 と W1 が整合: `_BH=$80` が「CD-TNO モード」、`_AL`=BCD のトラック番号、`_CL` が設定先（`$01`=recbase0、`$02`=recbase1）。LBA・MSF 指定時のレジスタ割当と `$80` 以外のモード値は W1 に**記載なし**。`_BL`・`_DL`・`_CH` の役割も不明 | 現状 BLOCKED（実行可能な呼出先なし。`docs/cd-base-abi-ledger.md` §4a） | 実 System Card を載せたコアで `JSR $E006` を実行し、`_AL/_BH/_BL/_CL/_DL/_CH` を一つずつ変えて、後続 CD_READ の SCSI READ LBA と作業 RAM 差分を記録（入力感度試験） |
| 2 | ブロック番号は絶対 LBA か相対か | HYPOTHESIS: 相対（データトラック先頭基準） | W1「CD_READ は recbase0 を加算」「LBA はデータトラック先頭からの相対」。ただし「先頭」が INDEX 01 か PREGAP 先頭か、LBA の 0 が MSF 00:02:00 かは**記載なし**。HuCC S5/S4 は ISO 由来値を渡す（既存台帳どおり） | BLOCKED | INDEX 01 ≠ 0 の合成ディスク（PREGAP 付き。`tools/cd_base_probe.py`）で、リセット直後に CD_BASE なしで CD_READ を二つ以上のブロック番号で実行し、READ LBA − ブロック番号を記録。既存の四署名（`ABS_LBA` 等）のどれに一致するかで判定 |
| 3 | 基準値の設定と保持 | HYPOTHESIS（部分） | W1: 基準は `recbase0`/`recbase1` の二つ。System Card が起動時に両方を「最初のデータトラックの LBA」に初期化する。保持期間、格納アドレス、CD_RESET での扱いは**記載なし**。「最初のデータトラックの LBA」の LBA が INDEX 01 か PREGAP 先頭かも不明 | BLOCKED | リセット直後、CD_BASE 後、CD_RESET 後、二回目の CD_READ 後に作業 RAM 全体を取り、`recbase0/1` の位置と値を特定。PREGAP 付きディスクで初期値が 150 か 0 かを読む |
| 4 | CD_READ との関係 | HYPOTHESIS | W1: CD_BASE は `cd_read`・`cd_exec`・`cd_seek` 等の基準を変える。通常の読込みは recbase0、recbase1 は失敗後のみ。**含意**: HuCC の 2ISO 経路（S1、`_cl=2`）は recbase1 だけを設定するので、通常のオーバーレイ読込み（S5）の基準は変わらず、既存台帳の「矛盾」は成立しない可能性がある。ただしこれは W1 の読み方に依存する推論で未測定であり、**矛盾は OPEN として残す** | BLOCKED | `_CL=1` と `_CL=2` のそれぞれで CD_BASE → CD_READ（正常系）を実行し READ LBA を比較。さらに読込み失敗を人為的に起こし（READ 失敗の注入）、再試行が recbase1 を使うかを SCSI 層で観測 |
| 5 | MPR・作業 RAM の副作用 | UNKNOWN | CD_BASE について記述した資料は見つからない（W1/W2 とも記載なし）。CD_READ の MPR3 保存はHuCC 呼出側の報告にとどまる（既存台帳 S4） | BLOCKED | 一回の CD_BASE 呼出しの前後で MPR0–7・作業 RAM（`$2000–$3FFF` の関連範囲）・CPU フラグ・SP の全差分。成功・失敗経路の両方 |
| 6 | 戻り値と失敗の規約 | UNKNOWN | W1 に「記載なし」。HuCC S1 は呼出後に戻り値を読まない（呼出側事実のみ）。失敗の定義も不明 | BLOCKED | 範囲外トラック番号（0、存在しない番号、BCD として不正な値）、無ディスク、データでないトラックを指定して A/X/Y/P と C フラグを記録。「戻り値なし」の確定には全入力で値が不変であることが要る |

## 3. 観測可能性の評価

- **現状はすべて BLOCKED**: 本リポジトリの DEV ROM の `$E006` は非復帰の fail-closed fallback（`make dev-cd-base-integration` で測定済み、`docs/cd-base-abi-ledger.md` §4a）で、固定版 Geargrafx に BIOS 呼出しの HLE はない。観測に必要なのは、利用者が私的に所有する System Card を非配布・読取専用で使う実コア実行のみ（Issue #146 の私的入力規則）。
- 観測手段は既存の `tools/cd_base_probe.py` の `OBSERVATION_PLAN` と判定器で足りる。**追加の検証器は作っていない**。本書の項目 4 の「失敗注入」は既存計画に無く、必要なら別 Issue とする。
- 正例: W1 と S1 のレジスタ値が整合した（項目 1）。負例: LBA・MSF 指定の割当、戻り値、MPR 副作用は、どの資料にも記載がなく埋めなかった。
- 実コア診断は完成 BIOS の互換性を意味しない。これらの観測は呼出先 ABI の測定に限る。

## 4. 非実行範囲

- 実コア実行、公式マニュアル本文の確認、W1 著者への根拠確認、ISO 9660（ECMA-119）本文の確認は未実施。
- 不明な ABI に成功値・既定値は定義していない。

## 5. 次の作業

1. 公式 System Card マニュアル（archive.org 等）を入手できれば、項目 1・2・6 の記述を再照合する（版・ライセンスを記録）。
2. maintainer が私的 System Card を用いた実コア測定を許可した場合のみ、§2 の「必要な観測」を実行し `tools/cd_base_probe.judge` に渡す。

## 変更履歴

- 2026-10-29（PR #571 の修正）: 監査で追加した Hu-Go! HLE の補強記述を「参照のみ」へ変更し、HLE の式の転記を削除した。#148 との整合根拠を W1 のみとした。旧版は Hu-Go! HLE を補強として挙げていたため、その記述は撤回する。
