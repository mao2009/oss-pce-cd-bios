# CD_PLAY (`$06`, 候補 `$E012`) 呼出ABI台帳 (Issue #150)

対象: System Card `CD_PLAY` の引数・終了条件・レジスタ副作用の整理。親 Issue は #15 (`Refs #15`)。
入力: [abi-contracts.md](../abi-contracts.md), [api-inventory.md](../api-inventory.md), [cd-oss-survey.md](../cd-oss-survey.md),
[cdda-unresolved-facts.md](../cdda-unresolved-facts.md) (#140), [cdda-core-observation.md](../cdda-core-observation.md) (#135)。

## 結論と範囲

- 本書は文書・ソース調査のみ。実コア (Geargrafx) でも実機でも、`CD_PLAY` を呼ぶゲストコードは実行していない。
  よって VERIFIED の項目はない。既存ドキュメントのステータスは変更しない。
- 証拠区分: `CALLER` = 呼出側SDKの期待 (被呼出側の挙動を証明しない)、`HLE` = エミュレータのモデル (原本ファームウェアではない)、
  `UNKNOWN` = 根拠なし、`BLOCKED` = 前提観測が未実施。ステータスは全て HYPOTHESIS 以下で、provenance は PENDING のまま。
- 商用BIOSのバイト列・逆アセンブルは使用していない。出典は公開ソース (固定SHA) とブログ文書の言い換えのみ。

## 出典

| 略称 | 出典 |
| --- | --- |
| HuCC | [hucc-systemcard.asm @54f5c73](https://github.com/pce-devel/huc/blob/54f5c73606c1e7142e8095c727cab2767e2b3cb3/include/hucc/hucc-systemcard.asm) (定数 L28-40, `cd_pause` L107-111, `cd_unpause`/`cd_playtrk`/`cd_playmsf` L133-276) |
| Hu-Go | [bios.c @64ed226](https://github.com/mckayemu/hugo/blob/64ed226da2288738781743eeef228c069e8973a4/bios.c) (`CD_PAUSE` L574-590, `CD_SEARCH` L631-665, `CD_PLAY` L897-967) |
| stum-1 | [PC Engine CD-ROM² BIOS Method offsets](https://stum.de/2025/pcenginebiosoffsets/) (ジャンプテーブル経由で呼ぶ互換性の説明) |
| stum-2 | [Why do PC Engine CD-ROM² games have a second data track?](https://www.stum.de/2026/pc-engine-second-data-track/) (cd_base 呼出で `_BH=$80`=トラック番号指定、`_AL`=BCDトラック番号。CD_PLAYではなく cd_base の記述) |
| #135 | [cdda-core-observation.md](../cdda-core-observation.md): 生SCSI `$D8/$D9/$DA/$DD` のコア観測 |

公開の System Card 公式マニュアル相当は、前作業者の WebSearch では確認できなかったとの記録がある (本レビューでは再検索していない)。
stum-1/2 は CD_PLAY の引数を記述していない。

## 1. 呼出パラメータ (CALLER 証拠)

引数はHuCCの8バイトワーク領域 (`_al.._dh`, CPU `$20F8-$20FF`、[abi-contracts.md](../abi-contracts.md) 参照) に置かれる。
これは SDK の規約で、物理A/X/Yではない。被呼出側が実際にこのバイトを読むことは未検証。

| 項目 | 報告されている内容 | 証拠 | 状態 |
| --- | --- | --- | --- |
| 開始位置の種別 | `_bh`: `$00`=セクター(LBA), `$40`=MSF, `$80`=トラック, `$C0`=現在位置(再開) | HuCC定数 `CD_SECTOR/MSF/TRACK/CURRPOS` | CALLER。`$C0` と `CD_LEADOUT` が同値であり、終了種別側の意味は区別不能 |
| 開始位置 (トラック) | `_al`=BCDトラック番号、`_ah`=0、`_bl`=0 | HuCC `_cd_playtrk` | CALLER |
| 開始位置 (MSF) | `_al`=分, `_ah`=秒, `_bl`=フレーム (全てBCD。HuCCは `ex_binbcd` で変換) | HuCC `_cd_playmsf` | CALLER |
| 開始位置 (セクター) | HuCC に呼出経路なし。引数配置 UNKNOWN | HuCC (欠如), Hu-Go は `HCD_play_sectors` がコメントアウト | UNKNOWN |
| 終了位置の種別・モード | `_dh`: 上位2bit=終了種別 (上表と同じ符号)、下位6bit=再生モード | HuCC `CD_PLAYMODE=%00111111` | CALLER |
| 終了位置 (トラック) | `_cl`=BCDトラック番号、`_ch`=0、`_dl`=0。HuCCは終了トラック0を「ディスク末尾まで」として `CD_LEADOUT` を終了種別に設定 | HuCC `_cd_playtrk` | CALLER |
| 終了位置 (MSF) | `_cl`=分, `_ch`=秒, `_dl`=フレーム (BCD) | HuCC `_cd_playmsf` | CALLER |
| 再生モード値 | 0=`CDPLAY_MUTE`, 1=`CDPLAY_REPEAT`, 2=`CDPLAY_NORMAL`。`CDPLAY_ENDOFDISC=0` には HuCC 自身が「XXX: really?」とコメント | HuCC | CALLER。HuCC の「XXX: really?」は `CDPLAY_ENDOFDISC=0` にのみ付いており、MUTE/REPEAT/NORMAL の値自体への表明ではない (ENDOFDISC=0 は MUTE=0 と同値で意味が重なる) |
| MUTE の意味 | 「ミュートで再生位置だけ進める」等の解釈は根拠なし | - | UNKNOWN |
| 組合せの有効範囲 | 開始=トラックで終了=MSF 等の混在可否、終了<開始、範囲外 | - | UNKNOWN |

Hu-Go の差異 (HLE、原本の証拠ではない): `_bh==$80` ならトラック再生 (`_al`のみ使用、`_dh&1` をリピート相当として
HCDバックエンドにのみ渡す)。`_bh==192` なら「一時停止中なら再開、そうでなければ `_al` のトラックを再生」。
それ以外は全て MSF 範囲として扱い、**`$00`(セクター)と`$40`(MSF)を区別しない**。`_dh` のモード/終了種別は読まない。
したがって Hu-Go は HuCC の `_dh` 解釈の裏付けにならない。

## 2. 戻り値と終了条件

| 項目 | 内容 | 状態 |
| --- | --- | --- |
| 戻り値の場所 | HuCC は呼出直後に `TAX` (A→X コピー、ソースコメントは「return code in X」) して Y:X (Y=0) で返す。BIOS が戻り値を A に置くことと 0=成功/非0=失敗は、いずれも HuCC 側の用途からの推定にすぎない。`cd_pause` マクロは戻りを加工せず `CLY` のみ | CALLER。成功/失敗コードの値、A以外のレジスタ・キャリーは UNKNOWN |
| 戻り値の個別エラー | データトラック指定、存在しないトラック、範囲外LBA、ディスクなし、busy | UNKNOWN (#140 の未解決 4-5 と同一) |
| Hu-Go の戻り | 常にA=0、N/T/Zを再計算して RTS。エラー経路なし | HLE。原本の成功値が0であることの証拠にはならない |
| 終了位置到達時 | 自動停止/次トラックへ進行/無音継続 | UNKNOWN。ホストモデル (#140) は最終トラックのみ `ENDED` を持つが、モデル内部状態でありBIOSの観測ではない |
| `REPEAT` の動作 | 終了位置到達で開始位置へ戻るのか、トラック単位かは不明 | UNKNOWN |
| 完了待ち | CD_PLAY が再生開始まで同期的に待つか、即時復帰か | UNKNOWN |
| タイムアウト | 戻り値・待機上限 | UNKNOWN |
| 停止・一時停止・再開との関係 | HuCC `cd_unpause` は直前の終了種別/終了位置4バイトを `_dh/_cl/_ch/_dl` に戻し、`_bh=$C0` で CD_PLAY を呼ぶ。HuCC は `cd_pause` を別の `CD_PAUSE` (`$08`) 呼出として実装 | CALLER。`$C0` による再開が CD_PLAY の仕様かは UNKNOWN。再開位置を被呼出側が保持するかも UNKNOWN (HuCC は終了位置だけ自前で保持) |
| CD_PLAY 単独での停止 | `CD_STOP` に当たる専用スロットは inventory に無い。停止手段は未確定 | UNKNOWN |

## 3. レジスタ・フラグ・MPR・スタック・IRQ・RAM副作用

HuCCのラッパー (`call_bios` 等) による保存・復元は、BIOSの保存規則と同一視しない ([abi-contracts.md](../abi-contracts.md) の方針を踏襲)。

| 項目 | 状態 | 備考 |
| --- | --- | --- |
| A/X/Y の入力・保存 | UNKNOWN | HuCCは呼出後にXを上書きしY=0にする (ラッパー側の都合) |
| P (C/N/Z等) の出力・保存 | UNKNOWN | Hu-Go は N/T/Z のみ更新 (HLE) |
| スタック使用量 | UNKNOWN | |
| MPR (バンク) の前提・破壊 | UNKNOWN | HuCC は MPR7 にバンク0を割付けて JSR する (ラッパー側)。CD_PLAY 固有の要件は不明 |
| IRQ/割込許可 (CLI/SEI) の前提 | UNKNOWN | CD_READ 側のみ CLI を呼出側で行う報告あり。CD_PLAY には該当記述なし |
| ワークRAM書込み (`$20F8-$20FF` ほか) | UNKNOWN | 引数領域を破壊するかも不明。HuCC が終了位置を `cdplay_end_*` (HuCC 側変数。配置アドレスは未確認) に別保存する理由は「呼出側で再開に使うため」の想定で、被呼出側の破壊の証拠ではない |
| 実行時間・同期 | UNKNOWN | |

## 4. 生SCSIコマンドとBIOS ABIの切り分け

#135 の観測 (`$D8` 再生開始、`$D9` 停止位置/モード、`$DA` 一時停止、`$DD` サブQ) はコアのSCSI層で、
**ゲストの CD_PLAY 呼出を経由していない**。次を対応づけとして主張しない。

| 生SCSI観測 | BIOS ABIへの含意 | 状態 |
| --- | --- | --- |
| `$D8` 開始LBA/MSF/トラック指定 | CD_PLAY の `_bh` 種別と1対1とは限らない。BIOSが内部でどの形式のコマンドを発行するかは未観測 | UNKNOWN |
| `$D9` mode 0/1/2/3 | `CDPLAY_MUTE/REPEAT/NORMAL` との対応は未観測 (#140 未解決13: mode 1-3 は未測定) | BLOCKED (ゲストROM経由の観測が必要) |
| `$D8` のデータトラック指定 → PLAYING かつ無音 | SCSI層の挙動。CD_PLAY のエラー返却の有無とは別 | UNKNOWN |
| `$DA` で位置保持 | CD_PAUSE/`$C0` 再開の位置保持の証拠にはならない | UNKNOWN |

台帳検査案 (今回は未実装): 本書のモード/種別値を `tests/host` で他の文書・仕様JSONと照合し、
`spec/api_contracts.json` の CD_PLAY が全て UNKNOWN/HYPOTHESIS のままであることと、本書が VERIFIED を含まないことを確認する。

## 5. 項目別ステータス (全件 実コア検証なし)

| # | 項目 | 状態 | 確定に必要な観測 |
| --- | --- | --- | --- |
| 1 | 開始位置の種別コード (`$00/$40/$80/$C0`) | CALLER (HuCC) | System Card を搭載した実コアで、ゲスト側 `_bh` を変えて CD_PLAY を呼び、SCSI `$D8` の引数形式と開始LBAを記録 |
| 2 | 引数レジスタ配置 (`_al.._dh`) | CALLER | 同上。ワークRAMの各バイトを1つずつ変えて、`$D8` が変わる/変わらないを観測 |
| 3 | セクター(LBA)指定の配置 | UNKNOWN | `_bh=$00` で配置候補を試行し `$D8` の開始LBAを読む |
| 4 | 再生モード値 (0/1/2) の意味 | CALLER (一部作者不確実) | 各モードでトラック末尾到達後の状態とSCSI `$D9` の mode を記録 |
| 5 | 終了トラック0 = ディスク末尾 | CALLER | 末尾までの終了位置を `$D8` 発行内容から読む |
| 6 | 戻り値コード (成功/失敗) | UNKNOWN | 正常/データトラック/範囲外/ディスクなしの各呼出直後のA・X・Y・Pを記録 |
| 7 | 終了位置到達時の挙動 | UNKNOWN | 途中トラック終端・最終トラック終端それぞれで状態 (`CURRENT_STATE`) とサブQ位置を連続観測 |
| 8 | `$C0` 再開と一時停止位置 | UNKNOWN | CD_PAUSE → CD_PLAY(`$C0`) の前後で `CURRENT_LBA` を比較 |
| 9 | 同期/完了待ち・タイムアウト | UNKNOWN | 呼出復帰時点の `CURRENT_STATE` と経過フレーム数、busy中の再呼出結果 |
| 10 | A/X/Y/P, スタック, MPR, IRQ, ワークRAM副作用 | UNKNOWN | 呼出前後の全レジスタ・MPR・スタック・`$2000-$21FF` のスナップショット差分 |
| 11 | System Card 3.0 での記述 | BLOCKED | 公開された公式仕様の入手、または実コア+合法に用意した System Card 3.0 での上記観測 |

「実コア+System Card」の観測は、ローカル `rom/` が利用可能な場合のみの任意テストとし、ROM/BIOSをGit/CIに含めない。
本書の作成時点ではその観測は実施していない。

## 6. 非実行範囲

- CD_PLAY サービスの HuC6280 実装、実機のCD音声互換宣言は対象外。
- 親 #15 の受け入れ条件は置き換えない。
