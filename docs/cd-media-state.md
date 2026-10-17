# Issue #144: Firmware側CDメディア状態判定の最小経路（開発用イメージ）

`src/cd_media_state.s` は #143 の reset エントリ (`src/reset_entry.s`) から1回だけ呼ばれる観測点。
完成BIOS、`CD_READ`、`CD_BOOT`、System Card ABI ではない。`ca65 -D CD_MEDIA_STATE` の opt-in ビルドだけが
`JSR` を含み、既定DEV ROMと #143 の reset-entry ROM はバイト単位で不変。

```sh
make dev-cd-media-state                 # build/<mode>/cd-media-state/cd-media-state-not-bios.pce
python3 -m unittest tests.host.test_cd_media_state -v
make dev-cd-media-state-integration     # 固定版Geargrafx、媒体あり/なしの対照実験
```

## 判定の根拠

- 使うSCSI手順は #5/#133 で固定版Geargrafx上で実行済みの READ(6) の select/command/data/status 手順のみ
  (`docs/cd-read-diagnostic.md`)。送るコマンドは READ(6) LBA 0、1セクタだけ。
- TEST UNIT READY は使わない。Geargrafx `b49ae82a` では媒体の有無に関係なく GOOD を返す（ソース読解のみ）。
- READ TOC (`$DE`) は未検証なので使わない。
- MPR0=`$FF`(CD I/O)、MPR1=`$F8`(work RAM) は #143 の SOURCE_FACT。それ以外のMPRは触らない。I フラグはセットのまま（ポーリングのみ）。

## 結果（`A`=`X`=状態、`$2300` 結果ブロック）

| 値 | 状態 | 条件 |
| --- | --- | --- |
| `$01` | `MEDIA_SIGNATURE_OK` | LBA 0 を2048バイト受信、GOOD/COMMAND COMPLETE、先頭が本プロジェクト合成ディスクの magic |
| `$02` | `MEDIA_UNRECOGNISED` | 同上だが magic なし（実ディスクの形式についての主張ではない） |
| `$03` | `NO_DATA` | DATA_IN なしで STATUS、GOOD、message 0 |
| `$81`..`$8A` | エラー | バス/タイムアウト/ステータス異常。媒体状態ではなく、判定器は常に拒否する |

`NO_DATA` は「媒体なし」と「LBA 0 を返さない媒体」を区別しない（Geargrafx では先頭LBAがディスク末尾以上の
場合も同じ応答になる）。

## 対照実験（実コア、Geargrafx `b49ae82a` のみ）

同じROM・同じローダ (`SetCDROMType(STANDARD)+LoadHuCardFromBuffer+ResetMedia(false)`) で、変数は
`CdRomMedia::LoadCueFromFile` でディスクを挿入するかどうかの1つだけ。cold/warm の両方で観測する。
判定器 (`tools/build_cd_media_state.py` の `judge` / `check_pair`) は、ROM SHA-256・ローダ文字列の一致と、
`disc_given`/`drive_ready` が片側だけ真であることを要求する。

| 条件 | 結果 |
| --- | --- |
| 合成ディスクあり | `MEDIA_SIGNATURE_OK`、2048バイト、status 0、message 0 (cold/warm) |
| ディスクなし | `NO_DATA`、0バイト、ゲストが読んだ status 0 / message 0、DATA_IN なし、バッファはセンチネルのまま (cold/warm) |
| 空データディスク（ヘッダ有効、ユーザデータ全ゼロ） | `MEDIA_UNRECOGNISED` |
| ゼロバイトBIN | `LoadCueFromFile` が拒否（probe exit 4）。ゲストは実行されない（エミュレータの挙動） |
| selection を `$1805` へ変異 | `SELECT_TIMEOUT` を返し、判定器が拒否 |
| ディスクあり/なしを入れ替えた組 | `check_pair` が拒否 |

どの結果も reset_ready へ戻り、SP=`$FF`、MPR0/MPR1、I=1/D=0 が維持されることを判定器が確認する。

## UNKNOWN / 対象外

- 実機・第2エミュレータ: NOT RUN。実ドライブの媒体なし応答、TEST UNIT READY/REQUEST SENSE の意味は UNKNOWN。
- 実ディスクの起動セクタ形式、`CD_BOOT`、`CD_READ` の ABI、エラー後のバス回復、リトライ方針は未実装。
- 判定結果は API として公開しない。ABI は VERIFIED にしない。
