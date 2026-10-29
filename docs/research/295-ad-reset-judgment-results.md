# Issue #295: AD_RESET busy 契約の測定結果に対する R0〜R6 判定

親 #25。対象契約: [295-ad-reset-busy-contract.md](295-ad-reset-busy-contract.md)（PR #577、HEAD 97de277）。本書は契約本文を変更せず、契約 §9 の判定基準を測定ログへ適用した結果を記録する。

**状態: 判定文書（測定結果の判定）。`MEASURED` の範囲に限る（契約 §11）。VERIFIED とは主張しない。`release_eligible=false`、`DEV_CALLABLE` 不可を維持する。期待値は本書でも決め打ちしない。**

- 測定区分: PLAY_ACTIVE（CONTROL `$80` での reset）のみ。TRANSFER_BUSY は BLOCKED（契約 §3.4）のため 0 試行。
- 試行数: 25（cold 5、new-process 20）。warm 0 本、arm-B（`$A0`）0、pos-reset 0。
- 生データ: 5 ファイル（§2 の対応表を参照）。

---

## 1. 判定の要約

| ID | 項目 | 判定 | 根拠（件数・理由） |
| --- | --- | --- | --- |
| R0 | 終了状態と hash | PASS | `COMPLETED` 25/25、終了コード 0 が 25/25、入力 hash と pinned commit が 25/25 で同一。注記: 合成入力の参照 hash はリポジトリ内に記録が無いため、外部参照との絶対一致は未確認（§7 の 2） |
| R1 | busy（区分）成立 | PASS | PLAY_ACTIVE 25/25。P0 で `playing=1`、`status_0c=0x08`。区分成立は frame 2（上限 60 以内）。UNOBSERVED_BUSY 0 件。T_b の独立記録は無い（§7 の 3） |
| R2a | CONTROL 読戻し | PASS | P1 の `control_0d=128`（`$80`）が 25/25。`reset.r2a_readback=128` も 25/25 |
| R2b | reset 後の Clock 機会 | NOT_EVALUATED | `RunToVBlank` の完了（P1 後、frame 2→3）は 25/25 で記録。Clock 呼出し回数は NOT_CAPTURED のため判定しない |
| R2c | reset 効果の生値 | 記録のみ（判定なし） | P2・P3 の生値を §4 に列挙。`read_cycles`、`write_cycles`、`play_pending` は NOT_CAPTURED |
| R3 | 予算内での終了 | PASS | `end_reason=budget_end` 25/25、`frames=300`（予算 300 以内）、壁時計 0.459〜0.669 s（上限 120 s 以内） |
| R4 | P0〜P3 の採取完了 | FAIL | 4 点は 25/25 で存在する。ただし §5 が要求する項目が NOT_CAPTURED（§5 の欠落表）。全項目採取完了とは扱わない |
| R5 | 初期化済み領域の再現性 | PASS | WRAM 8 KiB（4 点）、MPR、PC、ADPCM 状態（ADDRESS、CONTROL、LENGTH）が 25 試行で一致。ただし初期化手順は契約 §1 どおりではない（§7 の 1） |
| R6 | 未確定領域の差異 | 差分を記録（不合格ではない） | A・X・Y が 25 試行で変動（15 種類の組）。WRAM の残り、IRQ 保持値、MPR、PC、P、S には差異なし。原因は未確定（§6） |
| — | TRANSFER_BUSY | BLOCKED | 0 試行（契約 §3.4、§10） |
| — | warm（§8） | 未判定 | 0 本（最小 5 本、§9.3）。再現性は判定しない |

件数: PASS 5（R0、R1、R2a、R3、R5）、FAIL 1（R4）、NOT_EVALUATED 1（R2b）、記録のみ 1（R2c）、差分記録 1（R6）。NOT_CAPTURED の観測項目は 1 試行あたり 14 項目（ADPCM の 3 項目 × 4 点 + Clock 系 2 項目）、25 試行で計 350 セル。

---

## 2. 生データと実行条件の対応表（§12 の要件）

### 2.1 生データと試行の対応

| 生ログ（作業者の作業ディレクトリ） | SHA-256 | 含む試行 | repeat | §8 区分（probe の付与） | probe の OUTPUT_DIR |
| --- | --- | --- | --- | --- | --- |
| `series577-raw/cold-1.out` | `ff89cdd9f1015fdd04e7bc6ab0b7299302dd1b66147f9b63ae084e12a687025c` | 試行 1 | 0 | cold | `/tmp/pce-ad-reset-full-0l6bklfj` |
| `series577-raw/cold-2.out` | `027df673622f73cc7709e6e9f98a8ba6029540d9f6bb0a3d93f0e773bc6cd0e4` | 試行 2 | 0 | cold | `/tmp/pce-ad-reset-full-8meigcex` |
| `series577-raw/cold-3.out` | `bbbe157f4cf6ad7722046b828e610f126978d5040d44c34e0dd1dc4175895abe` | 試行 3 | 0 | cold | `/tmp/pce-ad-reset-full-4if6mdi1` |
| `series577-raw/cold-4.out` | `e8d44fc5987e457411693d6e04320984ee7c84d53d8fa17a51321b31be6ae066` | 試行 4 | 0 | cold | `/tmp/pce-ad-reset-full-nl4qdptk` |
| `series577-raw/newproc-21.out` | `3443510bdf2c6378d6d5f9264956c020e813e1b7f271f86d3e39b09ea30887f9` | 試行 5（repeat 0、cold）、試行 6〜25（repeat 1〜20、new-process） | 0〜20 | cold 1、new-process 20 | `/tmp/pce-ad-reset-full-ka_3oj6s` |

- 各ファイルの TRIAL 行は 1 行 1 試行で、TRIAL 行の JSON を読んだ。`newproc-21.out` は 21 行の TRIAL を含む。
- 各ファイルに `OUTPUT_DIR` と `PASS` の行が 1 つずつある。runner（`tests/integration/geargrafx_ad_reset_full.py` の `run_trial`）は 1 試行 = 1 プロセスで起動するため、`newproc-21.out` の 21 試行は 21 プロセスである（runner の 1 回の呼出しで `--repeat 21`）。
- 試行番号は本書の表示用で、生データのファイル順（cold-1〜4、newproc-21 の行順）による。

### 2.2 実行条件

| 項目 | 値 | 出典・確認 |
| --- | --- | --- |
| 固定版 Geargrafx commit | `b49ae82a012eade566d72e9d61c9d95a5caa4da3` | 全 25 試行の `record.pinned_commit`。probe ソースの定数（`kPinnedCommit`）で、実行時に `git rev-parse` で確認した記録は無い（§7 の 2） |
| probe revision | `73ef6ac`（コミット件名「test: AD_RESET の busy 測定用 probe に全項目採取と区分引数を追加（#295）」） | 作業者の指定。生データに revision の記録は無い |
| probe の名称 | `geargrafx_ad_reset_full` | 全 25 試行の `record.probe` |
| ビルド・実行場所（TOOLS_DIR） | `/home/loach/w3-103/tools/geargrafx-b49ae82a012eade566d72e9d61c9d95a5caa4da3-debugger/`（WSL 側のパス） | 生データ先頭のコンパイル警告の行より。最適化なしでビルド（警告 "Compiling without optimizations"） |
| probe の実行元 | `/home/loach/w3-103/repo/tests/integration/geargrafx_ad_reset_full.cpp` | 生データ先頭のコンパイル行より |
| 実行日時 | 記録なし | 生データに時刻の記録は無い。ファイルの更新時刻は 2026-10-29 16:13:26 で、コピー時刻の可能性が高い（§7 の 8） |
| 合成 BIOS の SHA-256 | `8a39d2abd3999ab73c34db2476849cddf303ce389b35826850f9a700589b4a90` | 全 25 試行の `hashes.bios_sha256`、`record.input_sha256.bios` が一致 |
| 合成 CD（cue）の SHA-256 | `8afaf35bf2705504288e0ad63d0e38a250e5bdf3f984234a33706004bed0d0b7` | 全 25 試行の `hashes.cue_sha256`、`record.input_sha256.cue` が一致 |
| ゲストバイト列の SHA-256 | `44490c763e9b974968ab48ca3e4f4ac67c0fd9b5d2691bbf77260f46e0c1deec` | 全 25 試行の `hashes.guest_bytes_sha256` が一致。`record.guest` は `base=0x3E00`、`bytes_hex=80fe`。`80 FE` の SHA-256 を本書の作成時に再計算して一致を確認 |
| 予算 | `frame_budget=300`、`observe_wait=60`、`wall_seconds=120` | 全 25 試行の `record.budget`（契約 §6 と一致） |
| 実行結果 | `end_state=COMPLETED`、`exit_code=0` | 全 25 試行 |

---

## 3. 採取値の一致（R5 の根拠）

以下は 25 試行のすべてで同一だった値。PC、MPR、WRAM の一致は、固定 Geargrafx・今回の初期化条件（§7 の 1）における測定事実として記録する。

| 項目 | P0（発行直前、frame 2） | P1（Write 直後、frame 2） | P2（RunToVBlank 1 回後、frame 3） | P3（予算終了、frame 300） |
| --- | --- | --- | --- | --- |
| PC | `0x3E00` | `0x3E00` | `0x3E00` | `0x3E00` |
| MPR0〜MPR7 | `00 F8 00 00 00 00 00 00` | 同左 | 同左 | 同左 |
| S、P | `0xFF`、`0x04` | 同左 | 同左 | 同左 |
| WRAM 8 KiB（SHA-256） | 同一（4 点、25 試行で 1 種類） | 同左 | 同左 | 同左 |
| WRAM の非ゼロ位置 | オフセット `0x1E00`〜`0x1E01`（値 `80`、`FE`） | 同左 | 同左 | 同左 |
| IRQ（idr、irr） | 0、0（semantics: UNKNOWN） | 同左 | 同左 | 同左 |

- WRAM 8 KiB（`m_wram[0x0000–0x1FFF]`）は 4 点・25 試行で同一。非ゼロの 2 バイトは `0x1E00`（CPU アドレス `$3E00`、MPR1 = `$F8` による WRAM 写像）の `80 FE` で、ゲストバイト列と一致する。残りの 8190 バイトは全試行でゼロ。
- 初期化済みの ADPCM 状態（R5 の対象）は次のとおり。
  - P0: ADDRESS 4660（`$1234`）、CONTROL `$20`、LENGTH 4393、READ_ADDRESS 267、`$0C` 8（`$08` 立つ）、`playing=1`。
  - P1: CONTROL `$80`（他は P0 と同じ）。

---

## 4. R2c: reset 効果の生値（記録のみ、判定しない）

本節は値の記録である。期待値との比較はしない（契約 §9.2 R2c）。

| 項目（S2 / §5） | P0 | P1 | P2 | P3 |
| --- | --- | --- | --- | --- |
| frame | 2 | 2 | 3 | 300 |
| `playing`（m_playing） | 1 | 1 | 0 | 0 |
| `$0C` 状態（status） | `0x08` | `0x08` | `0x00` | `0x00` |
| `control_0d`（m_control、`$0D` 読戻し） | `0x20` | `0x80` | `0x80` | `0x80` |
| `address` | 4660 | 4660 | 0 | 0 |
| `length`（m_length） | 4393 | 4393 | 0 | 0 |
| `read_address` | 267 | 267 | 0 | 0 |
| `write_address` | 0 | 0 | 0 | 0 |
| `half_irq` | 1 | 1 | 0 | 0 |
| `end_irq` | 0 | 0 | 0 | 0 |
| `dma_0b` | 0 | 0 | 0 | 0 |
| `sample_rate_0e` | 15 | 15 | 15 | 15 |
| `read_cycles`（m_read_cycles） | NOT_CAPTURED | NOT_CAPTURED | NOT_CAPTURED | NOT_CAPTURED |
| `write_cycles`（m_write_cycles） | NOT_CAPTURED | NOT_CAPTURED | NOT_CAPTURED | NOT_CAPTURED |
| `play_pending` | NOT_CAPTURED | NOT_CAPTURED | NOT_CAPTURED | NOT_CAPTURED |
| `reset.r2a_readback` | — | 128 | — | — |

- 上の値は 25 試行すべて同一（§3 と同じ）。
- 取得済み: `m_control`、`m_playing`、`m_length`、ADDRESS、READ_ADDRESS、WRITE_ADDRESS、`half_irq`、`end_irq`、`dma_0b`、`sample_rate_0e`、`$0C` の状態。
- 未取得: `m_read_cycles`、`m_write_cycles`、`play_pending`、Clock 呼出し回数（S3）、マスタークロック累計（S3）。
- 効果の判定（P0 から P2 への変化が reset の効果か等）は、契約 §13 の 6 に従い、別の結果文書で行う。

---

## 5. R3、R4 の詳細

### 5.1 R3（予算内での終了）

- `end_reason=budget_end`、`frames=300` が 25/25。予算 300 フレームに達して正常に終了した（`frames` は予算以内）。
- 区分成立までのフレーム数は 2（上限 60、§6）。
- 壁時計は 0.459〜0.669 秒（上限 120 秒）。
- 中断（`BUDGET_EXCEEDED`、`TIMEOUT`、`ABORTED`）は 0 件。

### 5.2 R4（採取完了の欠落）

P0〜P3 の 4 点は 25/25 で存在する。次の項目が NOT_CAPTURED であり、契約 §5 の採取要件を満たさない。

| 欠落項目 | 契約での位置づけ | 影響する点 |
| --- | --- | --- |
| `read_cycles`（m_read_cycles） | §3.1 S2、§5（P2 で記録）、R2c | P0、P1、P2、P3 の全点 |
| `write_cycles`（m_write_cycles） | §3.1 S2、§5（P2 で記録）、R2c | P0、P1、P2、P3 の全点 |
| `play_pending`（m_play_pending） | §3.1 S2 | P0、P1、P2、P3 の全点 |
| Clock 呼出し回数（`clock_calls`） | §3.1 S3、§5（P2 で記録）、R2b | 試行全体 |
| マスタークロック累計（`master_clock_cycles`） | §3.1 S3 | 試行全体 |

- IRQ マスクと保留フラグの保持場所は未確認のため、`irq` の値（idr、irr）は記録されているが `semantics` は UNKNOWN のまま（§5 の定め）。

判定: §5 の P0〜P3 の要求項目が揃っていないため、R4 は FAIL とする。点の存在のみでは PASS としない（指示どおり、全項目採取完了とは扱わない）。

---

## 6. R6: A・X・Y の変動（原因未確定）

A、X、Y は probe が設定していない（§1 の初期化一覧に無い）ため、初期値は測定条件で決まらない。25 試行で次のとおり変動した。値は P0、P1、P2、P3 の全点で同一（試行内では変化しない）。

| 試行 | 生データ | repeat | §8 区分 | A | X | Y | S | P | PC |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | cold-1.out | 0 | cold | 233 | 28 | 143 | 255 | 4 | 0x3E00 |
| 2 | cold-2.out | 0 | cold | 92 | 94 | 95 | 255 | 4 | 0x3E00 |
| 3 | cold-3.out | 0 | cold | 85 | 65 | 137 | 255 | 4 | 0x3E00 |
| 4 | cold-4.out | 0 | cold | 106 | 95 | 22 | 255 | 4 | 0x3E00 |
| 5 | newproc-21.out | 0 | cold | 65 | 209 | 1 | 255 | 4 | 0x3E00 |
| 6 | newproc-21.out | 1 | new-process | 65 | 209 | 1 | 255 | 4 | 0x3E00 |
| 7 | newproc-21.out | 2 | new-process | 99 | 64 | 192 | 255 | 4 | 0x3E00 |
| 8 | newproc-21.out | 3 | new-process | 80 | 138 | 233 | 255 | 4 | 0x3E00 |
| 9 | newproc-21.out | 4 | new-process | 80 | 138 | 233 | 255 | 4 | 0x3E00 |
| 10 | newproc-21.out | 5 | new-process | 114 | 27 | 40 | 255 | 4 | 0x3E00 |
| 11 | newproc-21.out | 6 | new-process | 114 | 27 | 40 | 255 | 4 | 0x3E00 |
| 12 | newproc-21.out | 7 | new-process | 250 | 100 | 34 | 255 | 4 | 0x3E00 |
| 13 | newproc-21.out | 8 | new-process | 250 | 100 | 34 | 255 | 4 | 0x3E00 |
| 14 | newproc-21.out | 9 | new-process | 216 | 245 | 227 | 255 | 4 | 0x3E00 |
| 15 | newproc-21.out | 10 | new-process | 216 | 245 | 227 | 255 | 4 | 0x3E00 |
| 16 | newproc-21.out | 11 | new-process | 240 | 38 | 208 | 255 | 4 | 0x3E00 |
| 17 | newproc-21.out | 12 | new-process | 240 | 38 | 208 | 255 | 4 | 0x3E00 |
| 18 | newproc-21.out | 13 | new-process | 201 | 174 | 11 | 255 | 4 | 0x3E00 |
| 19 | newproc-21.out | 14 | new-process | 201 | 174 | 11 | 255 | 4 | 0x3E00 |
| 20 | newproc-21.out | 15 | new-process | 243 | 123 | 244 | 255 | 4 | 0x3E00 |
| 21 | newproc-21.out | 16 | new-process | 243 | 123 | 244 | 255 | 4 | 0x3E00 |
| 22 | newproc-21.out | 17 | new-process | 209 | 234 | 53 | 255 | 4 | 0x3E00 |
| 23 | newproc-21.out | 18 | new-process | 209 | 234 | 53 | 255 | 4 | 0x3E00 |
| 24 | newproc-21.out | 19 | new-process | 209 | 234 | 53 | 255 | 4 | 0x3E00 |
| 25 | newproc-21.out | 20 | new-process | 226 | 32 | 28 | 255 | 4 | 0x3E00 |

- 25 試行の (A, X, Y) の組は 15 種類。A、X、Y それぞれの取り得る値も 15 種類。
- 連続する試行で同一の組になった箇所は 10 組（試行 5〜6、8〜9、10〜11、12〜13、14〜15、16〜17、18〜19、20〜21、22〜23、23〜24）。cold 1〜4 はそれぞれ異なる値だった。
- S、P、PC は 25 試行で同一（`0xFF`、`0x04`、`0x3E00`）。
- 原因は未確定。電源投入時の未初期化が原因という仮説は記録可能だが、本書では確定事項としない。

R6 の他の領域: WRAM の残りはゼロで差異なし。IRQ の値（idr、irr）は 0 で差異なし。`dma`、`end_irq`、`half_irq` などの ADPCM 値も差異なし。

---

## 7. 手順上の確認事項（判定基準は変更していない）

以下は測定の前提との差異、または生データだけでは確認できない事項である。判定基準は変えていない。判定の解釈に影響する項目として明記する。

1. **§1 の初期化経路と一致しない。** 契約 §1 は `Memory::SetResetValues` を電源投入またはリセットの前に呼ぶことを求める。probe（`73ef6ac`）は `SetResetValues` を呼ばず、`core.Init` の後に `SetMpr` で MPR を直接設定し、WRAM の `$1E00`〜`$1E01` に `80 FE` を直接書き、PC、S（`0xFF`）、P（`0x04`）を直接設定する。P の設定は契約 §1 の初期化一覧に無い。このため、WRAM のゼロ初期化が §1 の経路で得られたものかは確認できない。R5 の PASS は、契約 §9.2 R5 の基準（25 試行の一致）どおりの判定である。
2. **固定版 commit の実行時確認が無い。** `pinned_commit` は probe ソースの定数で、実行時の `git rev-parse` の結果は記録されていない。ビルドのディレクトリ名（`geargrafx-b49ae82a…-debugger`）に commit が含まれるが、`-debugger` 版であり、§1 の clean clone の手順は確認できない。
3. **T_b の独立記録が無い。** 契約 §3.2 は T_b と T_r の両方で S1、S2 を読むことを求める。probe のコメントによると、区分の成立確認（T_b）と reset 直前の採取（T_r）は同じフレーム境界で行われ、生データには P0 のみが記録されている。R1 の PASS は、同一フレーム境界での成立確認（P0 の `playing=1`、`status=0x08`）に基づく。
4. **§8 の系列構成が一致しない。** 契約 §8 の系列 S は 5 プロセスを連続で起動する。実際は cold-1〜4 がそれぞれ 1 プロセスの系列、`newproc-21.out` が 21 プロセス（repeat 0〜20）の系列である。区分ごとの件数（cold 5、new-process 20）は §9.3 の最小数を満たすが、系列の構成は §8 の定義どおりではない。
5. **warm、arm-B、pos-reset は未実施。** 系列 W（warm）は 0 本で、最小 5 本に達していない（§9.3）。`arm-B`（`neg-reset-playing`、`$A0`）と `pos-reset` は含まれていない。
6. **probe revision は生データに記録されていない。** `73ef6ac` は作業者の指定による。
7. **Clock 系とマスタークロックの採取が無い。** `run_to_vblank_calls=300` は記録されているが、Clock 呼出し回数、マスタークロック累計は NOT_CAPTURED（§5）。
8. **実行日時が記録されていない。** 生データに時刻は無い。ファイルの更新時刻 2026-10-29 16:13:26 はコピー時刻の可能性があり、実行日時とは扱わない。
9. **合成入力の参照 hash が無い。** リポジトリ内（`main`、`97de277`、`73ef6ac`）に合成 BIOS、合成 CD、ゲストバイト列の参照値は見つからなかった。R0 は、契約 §9.2 R0 の基準（比較する全試行で hash が同一）で判定した。外部の参照値との一致は確認していない。
10. **契約本文の参照先。** 契約 295-ad-reset-busy-contract.md は PR #577 のブランチにあり、`main` には未マージである。本書の相対リンクは #577 のマージ後に有効になる。

---

## 8. 未確定事項

| 項目 | 状態 | 次に必要なこと |
| --- | --- | --- |
| R2b（reset 後の Clock 呼出し回数） | NOT_EVALUATED | Clock 呼出し回数の採取 |
| R2c（reset の効果） | 記録のみ | 期待値の判定を別の結果文書で行う（契約 §13 の 6） |
| R4（採取の欠落） | FAIL | `read_cycles`、`write_cycles`、`play_pending`、Clock 系、マスタークロックの採取 |
| read/write cycles、play pending | NOT_CAPTURED | 上と同じ |
| 合成入力の参照 hash | 未確認 | 測定時の hash と独立に記録された参照値の照合 |
| 固定版 commit の実行時確認 | 未確認 | 実行時の `git rev-parse` の記録、clean clone での再現 |
| T_b の独立記録 | 未確認 | 区分成立の時点（T_b）で S1、S2 を別に記録 |
| IRQ マスクと保留フラグの保持場所 | UNKNOWN | 固定版ソースでの確認（契約 §5、§13 の 4） |
| A・X・Y の変動の原因 | 未確定 | 原因の調査（仮説は本書では確定しない） |
| warm（系列 W）、arm-B | 未実施 | 契約 §8 の系列 W（5 本）、arm-B を #295 の範囲に含めるかの判定（§13 の 3） |
| 初期化の経路（§1） | 不一致 | probe の初期化を §1 どおりに修正して再測定するか、差異を承認するかの判定 |
| TRANSFER_BUSY | BLOCKED | §3.4 の 3 条件（契約 §13 の 2） |
| 実行日時 | 未記録 | 次回の測定で記録する |

---

## 9. 参照した契約の節

- §1（固定条件、初期化の順序、禁止入力）: §7 の 1、2 の根拠。
- §2.1（PLAY_ACTIVE の手順）: §2.1 の手順 1〜6 に対応する probe の処理を確認。
- §3.0（ステータスの意味）、§3.1（観測する状態 S1〜S3）: §3、§4、§5.2 の項目。
- §3.2（観測の時点 T_b、T_r）: §7 の 3。
- §3.3（区分）、§3.4（TRANSFER_BUSY の BLOCKED）: §1、§8。
- §4（reset 要求の発行方法）: P1 の CONTROL `$80`、R2a の根拠。
- §5（採取範囲 P0〜P3）: §3、§4、§5.2 の根拠。
- §6（予算と timeout）: R3 の根拠。
- §7（停止条件）: 中断 0 件の根拠。
- §8（系列、区分の判定規則）: §2.1、§7 の 4、§7 の 5。
- §9.2（R0〜R6 の判定基準）、§9.3（最小試行数）、§9.4（不安定な値の記録欄）: §1、§5、§6。
- §10（BLOCKED の項目 B1〜B11）: TRANSFER_BUSY の扱い（§1）。
- §11（結果の区分 MEASURED、VERIFIED への昇格不可）: 本書の状態。
- §12（idle reset と busy reset の分離）: 本書の結果は idle reset の結果として数えない。
- §13（残件と Issue #295 の範囲）: §4、§8 の項目。
