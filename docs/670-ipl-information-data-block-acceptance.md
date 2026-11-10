# Issue #670: IPL Information Data Block (public spec) — 受入条件と既存実装の対応

Status: DEV-only / synthetic media only / NOT a System Card BIOS / NOT a BIOS ABI / NOT CD_BOOT or CD_READ verified /
`release_eligible=false`. No commercial bytes, sums, addresses, or sector contents are recorded here.
Existing multi-sector loader (PR #681, #683) is reused; no reimplementation.

## 1. 受入条件 → 既存実装 対応表

| # | 受入条件 (Issue #670) | 既存実装 | テスト | 状態 |
|---|---|---|---|---|
| 1 | 情報ブロック位置: データトラック相対セクタ 1、署名オフセット 32 を確認 | `ipl_loader_rom.s` `validate_info` SIG_LEN=23 at BUF+32 | neg_signature_byte (code 20) | PASS (synthetic) |
| 2 | $00-$02 24bit big-endian ブロック番号でコードセクタ決定 | BUF[0..2] -> 24bit body base (bodyh/bodyl); IPLBLK<2 は code 21 | neg_iplblk_high_byte_01_in_range_probe_refused (code 5/stage 4)、neg_iplblk_high_byte_80/ff_over_range_refused (code 27)、neg_iplblk_zero/one (code 21) | PASS (synthetic): 21bit 範囲内は受理 (Issue #744)。範囲外は code 27 |
| 3 | $03 セクタ数で複数セクタ READ(6) | nsec 1..10; copy_body loops nsec | pos_* / neg_iplbln_zero/11 | PASS (synthetic) |
| 4 | $04-$05 ロード先、$06-$07 実行開始値の範囲・整合性検査 | IPLSTA $3000-$7FFF and fit; entry in [IPLSTA, IPLSTA+N*2048) | neg_iplsta_*, neg_entry_* (code 23/24) | PASS (synthetic) |
| 5 | $08-$0C MPR2-6 設定、MPR0/1 は DEV 初期値維持 | m2..m6 from BUF+8..12; MPR0=$FF, MPR1=$F8 at reset | neg_mpr2_not_ram_bank (code 25), MPR verification in judge | PASS (synthetic) |
| 6 | $0D OPENMODE: DEV では解釈しない（0 以外は拒否） | BUF+13 must be 0 | neg_openmode_nonzero (code 26) | PASS (synthetic) |
| 7 | 実行は単純な制御移行に限る; JSR/JMP・レジスタ条件は未確定 | `jmp` to IPLJMP; no JSR gate; release_eligible=false | payload runs and returns via halt | PASS (DEV scope) |
| 8 | 負例: 情報ブロック範囲外、セクタ数 0、データトラック境界超過、ロード先範囲外、実行開始値範囲外 → コピー/実行前に拒否 | neg_* above | see above | PARTIAL: 媒体終端超過は最終セクタ先読みでコピー前に拒否 (§3.1、READ(6) 到達性のみ)。データトラック長に基づく拒否は BLOCKED (§3.2) |
| 9 | 既存統合テスト (dev-cdload-exec, dev-boot-internal, experimental-bios-boot, dev-ipl-boot) の期待値維持 | 未変更 | CI | PASS (not touched) |
| 10 | DEV 専用; CD_BOOT/CD_READ ABI を VERIFIED にしない; release_eligible=false | 不変 | status/docs | PASS |

## 2. 24bit IPLBLK / 21bit READ(6) / 24bit 内部計算 (Issue #744 で拡張)

3 つの幅を混同してはならない。

1. 公開 IPL 情報ブロックの IPLBLK フィールド幅: **24bit** (big-endian, $00-$02)。
2. SCSI READ(6) の標準 LBA フィールド幅: **21bit** (6 バイト CDB の LBA は 21bit)。
3. ローダーの内部 LBA 計算幅 (Issue #744, Owner decision B): **24bit** (`bodyh/bodyl` 3 バイト、
   `IPL_SEL` 16bit に加算)。READ(6) 発行時に 21bit 範囲 (`body + nsec <= $200000`) を強制する。

したがって 24bit IPLBLK の全域を READ(6) だけで表現できるとは限らない。READ(6) の
21bit を超える値は別コマンド (READ(10)/READ(12) 等) または媒体境界の再定義が必要であり、
現行 Geargrafx プローブ/ローダーがそれらをサポートするかは **UNKNOWN** (未確認)。
21bit 超は code 27 でコピー前に拒否する (Owner decision B の範囲内)。

Issue #744 以前の扱い (履歴):
- `BUF[0] (high byte) != 0` を拒否 (code 21)。24bit のうち上位 8bit は 0 でなければならなかった。
- これは 24bit フィールドを部分的にしか扱えない状態であり、データトラックが 65536 セクタを超える
  媒体では IPLBLK の上位バイトが非零になり拒否される。
- 24bit 全域への対応は、現行ローダーの 16bit 内部 LBA の拡張に加え、READ(6) の 21bit 限界を超える場合の
  別コマンド等の方式選択を要する。READ(6) の LBA フィールドは標準で 21bit 固定であり、24bit へ任意に
  拡張できるものではない。READ(10)/READ(12) の対応可否は実装・実測で確認できていないため UNKNOWN。
  Owner 判断の仕様変更であり、本 PR では保留 (BLOCKED)。

## 3. データトラック境界超過の事前拒否

### 3.1 実装済み: 最終 body セクタの先読み (media-end reachability probe)

- ローダー (`ipl_loader_rom.s` `probe_last`) は、検証 (`validate_info`) 通過後かつロード領域への最初の書込み前に、
  最終 body セクタ (`bodyl + nsec - 1`) を READ(6) でステージングバッファ (BUF) に読む。失敗すれば `copied=0` で停止し、
  ロード領域は書き換えられない。実行時に得られる情報は READ(6) の成否のみで、ホスト側情報 (CUE のトラック長) は使わない。
- これは **到達性プローブ** であり、データトラック長の検査ではない。ゲストは TOC/トラック長を読めないため、
  (a) 最終セクタがデータトラックの外 (後続オーディオトラック等) でも読めてしまう場合は検出できない、
  (b) 先頭と最終の間の途中セクタの失敗は検出できない (途中失敗は従来どおり部分コピー後の安全停止、copied>0)。
  合成媒体は連続した 1 データトラックなので (b) を再現できず、(b) は実コア未検証である。
- 旧 `partial_read_beyond_disc_after_5_sectors` (コピー 5 セクタ後に検出) は
  `neg_body_tail_beyond_disc_refused_before_copy` (copied=0) に置き換えた。READ(6) 失敗時の guest code は同じ 5。
- 先読みの拒否ケースは guest の停止ステージ (R_STAGE=4) も検査し、先頭セクタ読出し (ステージ 5) による拒否と区別する。PUBLIC 形式にも N=20 の受理/拒否ケースがある。
- 先読みにより起動ごとに READ(6) が 1 回増える (性能への影響は未計測)。

### 3.2 BLOCKED: データトラック長に基づく拒否 (Owner 判断待ち)

- 受入条件の厳密な読み (「データトラック境界超過」) には、データトラックのセクタ数が必要。
- ゲスト (HuC6280) は SCSI/CD-ROM2 の TOC やトラック長を読み取る API を持たない (CD_BOOT/CD_READ 未検証)。
  READ CAPACITY / READ TOC を Geargrafx で使えるかは UNKNOWN。実行時に得られない情報に依存する案は採らない。
- 選択肢:
  1. (A) 現行の先読みを受入条件の実装として受理し、条件を「媒体終端/読取不能な最終セクタをコピー前に拒否」へ改訂する。
  2. (B) ホストがトラック長を情報ブロック予約領域 ($0E-$1F) に埋め込む。公開仕様では reserved (0 必須) のため意味付与に Owner 承認が必要。
  3. (C) #668 の選択レコードにトラック長を含める (#668 契約変更)。
  4. (D) 全セクタを先に読み切ってから commit するステージング方式 (RAM 不足の可能性があり実現性は未検証)。
- 推奨: (A)。実行時情報だけで成立し、公開仕様も #668 契約も変えない。(B)/(C) はホスト情報を信頼する別問題で、
  トラック長の出所 (CUE 解析) の整合性検証が前提になる。

## 4. LBA 上限境界の検証 (現行仕様, Issue #744)

用語を分離する:
- 絶対 LBA = `IPL_SEL (16bit INDEX 01 LBA) + IPLBLK (相対)` を 24bit 加算した body base。
- 相対 IPLBLK = データトラック相対セクタ番号 (24bit ビッグエンディアン)。
- 最終読み込みセクタ = 絶対 LBA の末尾 (`base + nsec - 1`)。
- exclusive end = `base + nsec`。

現行仕様 (Issue #744, Owner decision B):
- IPLBLK は 24bit ビッグエンディアン。
- INDEX01 は 16bit LBA (`0x0000..0xFFFF`)。
- 情報セクタ LBA は INDEX01+1 (24bit 加算、16bit 桁上がり保持)。INDEX01=`$FFFF` なら情報セクタ LBA=`$010000`。
- body base は 24bit 加算。24bit 桁溢れ (`body > 0xFFFFFF`) は拒否 (code 27)。
- READ(6) LBA は 21bit。最大 LBA は `0x1FFFFF`。
- `body + nsec <= 0x200000` (exclusive end)。最終セクタが 21bit 上限に一致する境界
  (`base + nsec - 1 == 0x1FFFFF`, end `== 0x200000`) は受理される。
- end `> 0x200000` および 24bit 桁溢れは code 27 でコピー前に拒否。
- 16bit 境界を跨ぐだけでは拒否しない。16bit を超える絶対 LBA も範囲内なら受理される。
- ただし媒体が実際に存在しなければ最終セクタ先読み (§3.1) で拒否される (code 5 / copied=0 / stage=4)。
- 受理には少なくとも次を満たす必要がある:
    - 相対 IPLBLK が 2 以上
    - IPL_SEL + IPLBLK (24bit 加算、桁溢れなし) の body base が範囲内
    - IPL_SEL + IPLBLK + nsec (exclusive end) が $200000 以下、24bit 桁溢れなし
    - その他のヘッダ・ロード先・実行開始値の検証も通過する
  相対 LBA (IPLBLK) と絶対 LBA (IPL_SEL + IPLBLK) を混同しないこと。

現行確認テスト (いずれも `tests/integration/geargrafx_ipl_loader.py`, `tools/ipl_lba21_model.py` と一致):
- 16bit 境界跨ぎの受理: `neg_abs_010108_crosses_16bit_probe_refused` (IPLBLK=0xFFFF, 絶対 0x10108 → 幅は通り媒体先読み拒否),
  `neg_abs_00fffe_tail_crosses_16bit_probe_refused` (0xFEF5, n=3), `neg_abs_last_ffff_probe_refused` (0xFEF5, n=2),
  `neg_lba_ffff_probe_refused` (0xFEF6, 絶対 0xFFFF), `neg_lba_fffe_n1_passes_width_fails_media_probe` (0xFEF5, 絶対 0xFFFE → code 5)。
- 24bit IPLBLK: `neg_iplblk_high_byte_01_in_range_probe_refused` (高位 `0x01`, 範囲内 → code 5),
  `neg_iplblk_high_byte_80_over_range_refused` / `neg_iplblk_high_byte_ff_over_range_refused` (範囲外 → code 27),
  `neg_iplblk_0100_passes_lba_checks_fails_media_probe` (0x100 → code 5)。
- 21bit 最大境界: `neg_abs_1fffff_n1_passes_width_fails_media_probe` (body `0x1FFFFF`, end `0x200000` → code 5),
  `expect_cdb_last=[0x08, 0x1F, 0xFF, 0xFF, 0x01, 0x00]` で 21bit バス符号を確認。
- `0x200000` 拒否: `neg_abs_1fffff_n2_tail_over_width_refused` (end `0x200001` → code 27),
  `neg_abs_200000_over_width_refused` (body `0x200000` → code 27)。
- 24bit carry out: `neg_iplblk_ffffff_carry_out_width_refused` (code 27)。
- 最終セクタ先読み: `pos_last_body_sector_is_last_disc_frame` (受理),
  `neg_last_body_sector_one_past_disc_end`, `neg_single_sector_at_first_frame_past_end`,
  `neg_body_entirely_beyond_disc`, `neg_body_tail_beyond_disc_refused_before_copy` (いずれも code 5 / copied=0 / stage=4)。
- 媒体終端の境界 (合成ディスク最終フレーム = 絶対 LBA 299) も上記 code 5 ケースで確認。

不足点:
- 上限の基準が 21bit 幅と媒体先読みであり、実際のデータトラック長ではない (§3)。
- 大容量合成媒体による 16bit 跨ぎ受理ロードは別 suite (BIN 約 155 MB、§9.2)。

### 4.1 旧仕様・履歴 (pre-#744, 現行検証ではない)

Issue #744 以前は 16bit 契約だったため、以下は**旧仕様**としてのみ記録する。現行検証の根拠にしないこと:
- 旧: 絶対 base が 16bit を超えると拒否 (`IPL_SEL + IPLBLK` の carry で code 27)。
  例: `0x0109 + 0xFFFF = 0x10108` → 16bit 範囲超過で拒否。`IPLBLK=0xFFFF` 単体は受理されなかった。
- 旧: `bodyl + nsec <= 0xFFFF` (exclusive end が 16bit を超えない)。
  最終セクタが `0xFFFF` に一致する場合 (`base + nsec == 0x10000`) も保守的に拒否された。
- 旧テスト名 (現行名称へ置換済み): `neg_body_lba_overflows_16bit` → `neg_abs_010108_crosses_16bit_probe_refused`,
  `neg_body_lba_tail_wraps_16bit` → `neg_abs_00fffe_tail_crosses_16bit_probe_refused`,
  `neg_body_lba_final_sector_at_ffff` → `neg_abs_last_ffff_probe_refused`,
  `neg_lba_ffff_n1_width_refused` → `neg_lba_ffff_probe_refused`,
  `neg_iplblk_high_byte_nonzero` (code 21) → `neg_iplblk_high_byte_01_in_range_probe_refused` (code 5) /
  `_80`・`_ff_over_range_refused` (code 27)。
- 旧: 24bit IPLBLK の上位バイトが非ゼロなら拒否 (code 21)。
- 旧: 16bit 境界を跨ぐと code 27 で拒否。

旧仕様の記述 (§2 以前の誤記を含む) は訂正済み。現行契約は本 §4 冒頭および `tools/ipl_lba21_model.py`・
`tests/integration/ipl_loader_rom.s` の実装を正とする。

## 5. 本 PR の変更範囲 (最小)

- 受入条件対応表の追加 (本ドキュメント)。
- 24bit 高位バイト拒否の回帰テスト追加 (`neg_iplblk_high_byte_nonzero`)。
  Issue #744 (Owner decision B) により高位バイトは範囲チェック対象となり、同ケースは
  範囲内受理→媒体先読み拒否へ移行 (`neg_iplblk_high_byte_01_in_range_probe_refused`)。
  範囲外 (`0x80`/`0xFF`) は code 27 のまま拒否 (改名のみ)。
- (初回 PR 時点の記述) 既存テストの期待値は変更しない。後続更新で `partial_read_beyond_disc_after_5_sectors` は置き換え済み (§3.1)。
  ただし #744 の幅拡張に伴い、16bit 上限を前提とした 7 ケースの期待値・名称を更新 (契約変更のため。正当化は #744 PR)。
- (後続更新) 最終 body セクタの先読み (§3.1) をテスト用ローダーに追加。公開仕様・24bit 対応・他コマンド方式は変更なし。
  #744 で先読み・コピーの両アドレス計算を 24bit 化、READ(6) CDB byte 1 を `(LBA>>16)&$1F` 化。
  `IPL_SEL` 自体は 16bit host-derived INDEX 01 のまま。
- (Issue #744) ビルダー (`tools/build_dev_ipl_loader_bios.py`) の `IPL_SEL` 受入範囲を
  16bit 非負整数 `0x0000..0xFFFF` (strict int) に整合。旧上限 `0xFFF0` は 16bit body 計算時代の
  wraparound 防止用であり、24bit 計算＋code 27 事前拒否となった現在は不要。
  範囲外の媒体は guest が署名・範囲ゲートで拒否するため、INDEX 01 = 0 の受理は媒体可用性の
  保証ではない (実コア読込の成否とは区別)。商用由来 SEL の manifest 秘匿は不変。
- (初回 PR 時点の記述) ローダー本体の動作変更なし (Owner 判断が必要な仕様変更は保留)。`ipl_loader_rom.s` の
  `IPL_MUTANT_HIGHBYTE` ガードは #744 で削除 (検証専用 mutation の役目を終えたため。デフォルトビルドへの影響なし)。
- `release_eligible=false`, `ABI VERIFIED` 未変更を維持。

## 6. 未解決 / Owner 判断待ち

- データトラック長に基づく事前拒否 (§3.2) の方式選択 (推奨 A)。先読み (§3.1) を受入条件として受理するか。
- 24bit LBA 幅への拡張 (§2): Issue #744 (Owner decision B) で対応済み。内部 LBA は 24bit 計算、
  READ(6) は 21bit 範囲 (`body + nsec <= $200000`) を強制し、超過は code 27 でコピー前に拒否。
  21bit 超の別コマンド方式 (READ(10)/READ(12) 等) は対象外のまま UNKNOWN。
- 受入条件の明示改訂 (Owner)。

## 7. READ(6) エンコードと 24bit IPLBLK の表現可能性

- READ(6) CDB (6 バイト): byte 0 = opcode、byte 1 = 下位 5bit が LBA[20:16]、byte 2 = LBA[15:8]、
  byte 3 = LBA[7:0]、byte 4 = Transfer Length、byte 5 = Control。LBA は 21bit で上限は `0x1FFFFF`。
  byte 1 の上位 3bit は LBA に含まれない。グループ番号ではない (SCSI-2 での LUN フィールドと
  後続仕様での予約ビットの扱いは区別し、グループコードと LUN を混同しない)。
  24bit IPLBLK フィールドを READ(6) の 21bit LBA フィールドにそのまま拡張する表現は誤り。
  16bit 内部 LBA の拡張と、READ(6) の 21bit 限界を超える場合の別方式 (READ(10)/READ(12) 等)
  の選択を区別する。READ(10)/READ(12) の対応可否は未確認のため UNKNOWN。
- 現行ローダーの内部 LBA は 24bit 計算 (Issue #744) であり、READ(6) 発行時に 21bit 範囲
  (`body + nsec <= $200000`) を強制する。旧 16bit 内部 LBA の記述は §4.1 の履歴を参照。
- Geargrafx プローブ/実装が READ(10)/READ(12) をサポートするかは **UNKNOWN** (未確認)。本 PR では調査のみ。
- 対応方式が未確定のため、24bit 全域対応は **BLOCKED (Owner 判断待ち)** とする。

## 8. データトラック境界の取得可能性

- 「現在の実装に取得手段がない」と「原理的に取得不可能」を区別する。
  - 現行ゲスト (HuC6280) は CD-ROM2 の TOC / トラック長を読む API を持たない (CD_BOOT/CD_READ 未検証)。
    → **現在の実装に取得手段がない** (実装上の制約)。
  - SCSI READ CAPACITY 等で媒体容量を取得できる可能性は **UNKNOWN** (Geargrafx プローブ未確認)。
    → 原理的に取得不可能とまでは言えない。
- ホストから渡す場合の契約変更案 (Owner 判断待ち):
  1. IPL 情報ブロックの予約領域 ($0E-$1F) にトラック長を追加。ただし公開仕様の reserved (0 必須) の意味付与は Owner 承認必要。
  2. Issue #668 の選択レコードにトラック長を含める。#668 契約変更。
  3. 全読み込み完了後にコミットするステージング方式 (部分コピーを RAM に残さない) の実現可能性を検証。
- 現状の安全停止 (部分コピー報告、実行阻止) は成果として維持する。

## 9. 証拠状態 (Evidence status)

- 最新 HEAD で実行: PASS/FAIL
- 以前の PR で実行: HISTORICAL_PASS
- 今回未実行: NOT_RUN
- 前提不足: BLOCKED

本 PR で追加したテスト (`neg_iplblk_high_byte_nonzero`) は合成媒体のみ。修正後 HEAD での実コア
(Geargrafx) 検証は、GitHub CI が当該 HEAD で完了するまで NOT_RUN。過去 HEAD の実行を最新 HEAD の
PASS とはみなさない。

- 過去の実行証拠 (HISTORICAL_PASS): HEAD `00a3045c3b92668ca35521ed390508fb6dd90010`、pinned
  Geargrafx core harness workflow、run 42326947105 (2026-11-07T01:59:53Z)、37 ケース cold+warm /
  74 runs、conclusion success。ただし当時は高位バイトケースが偽陽性だったため、この実行は
  修正後ケースの有効性の証拠にはならない。
- 生成エビデンス: CI ワークフローが生成する `ipl-loader-evidence.json` (build output、tracked
  ではない)。本ドキュメントには要約のみを記録し、生成物自体は添付しない。
- 修正後 HEAD: 上記の mutation 付き 37 ケースの再実行は GitHub CI に委ねる (本 PR では
  ローカルで重い実コア実行を繰り返さない)。CI 完了後に PASS/FAIL を最終報告に記録する。

### 9.1 本更新 (最終 body セクタ先読み) の実行証拠

- 実行環境: WSL2 Ubuntu、pinned Geargrafx b49ae82a、cc65 pinned。合成媒体のみ。
- RED: ケース追加後、変更前ローダーで `neg_last_body_sector_one_past_disc_end` が `guest code/copied = 5/4, expected 5/0` で失敗。
- GREEN: 変更後、`geargrafx_ipl_loader.py` 59/59 (8 KiB HuCARD+CUE 経路、cold+warm)、
  `make dev-bios-ipl-loader-integration` 59/59 (256 KiB DEV BIOS バッファ経路)。ローカル実行。CI の結果は PR を参照。
- 商用メディア、実機、System Card BIOS 互換、CD_BOOT/CD_READ ABI は一切検証していない。

### 9.2 Issue #744 (21bit LBA, Owner decision B) の実行証拠

- 実行環境: 上記 9.1 と同一 (pinned Geargrafx b49ae82a、cc65 pinned、合成媒体のみ)。
- Host RED->GREEN: `tests/host/test_744_ipl_21bit_lba.py` 24 件。変更前 ROM では ROM-audit
  (`LDA zp / AND #$1F / STA $2F11` パターン) が失敗=RED、変更後に全件 GREEN。
  境界表 (abs `0x00FFFF/0x010000/0x010001/0x1FFFFE/0x1FFFFF` 受理、`0x200000/0xFFFFFF` 拒否、
  INDEX01 carry、rel/abs 混同、last overflow、per-sector carry、nsec=0、16bit/21bit wrap、
  CDB top-5bit、非零高位バイト受理、範囲外コピー前拒否) は `tools/ipl_lba21_model.py` で判定。
- Real-core RED: 変更前 ROM で新ケース 2 件が失敗=RED (`neg_abs_010108...`: stage 3 vs 期待 4;
  `neg_abs_1fffff_n1...`: `R_CDB 0800010a` vs 期待 `081fffff`)。
- Real-core GREEN (small disc, 300 frames): `geargrafx_ipl_loader.py` **63/63**
  (8 KiB HuCARD+CUE 経路、cold+warm 各 2 run)。`geargrafx_dev_bios_ipl_loader.py` (256 KiB DEV BIOS、
  LoadBiosFromBuffer 経路) も **63/63**。R_CDB ピン (最終 READ(6) の 6 バイト) 付き。
- Real-core GREEN (big disc, 66000 frames / 約 155 MB BIN、SEL=0x109 のまま):
  `big_pos_base_10000_n5` (abs base 0x10000、payload 実行・MPR/entry 検証済み、約 5.2M steps)、
  `big_pos_cross_ffff_n2` (0xFFFF->0x10000 per-sector carry、約 2.7M steps)、
  `big_neg_past_end_66005` (code 5/stage 4/copied 0、payload 不実行)。いずれも cold+warm。
  当初 BUDGET 60M の範囲内 (実測最大約 5.2M steps のため BIG_BUDGET 120M は未使用に等しい)。
- Mutation (実コア、各 1-2 focused case): drop-high-byte / 16bit-truncation /
  carry-removal / byte1-OR-$E0 / boundary-weakened / body-only-revert / pre-read-only-revert /
  carry-gate-bypass の 8 件すべて検出 (期待不一致で FAIL)。byte1 マスク除去 (`and #$1F` 削除) は
  範囲ゲート下で等価なため実コアでは区別不能 (pinned core が `buf[1]&0x1F` でマスクするため):
  host ROM-audit が静的に強制する。詳細は #744 PR 報告を参照。
- 最大 abs LBA: 受理確認は 0x1FFFFF (単一セクタ、媒体先読み拒否まで。小ディスク上)。
  実ロード成功の最大は big-disc の 0x10004。0x200000 以上は code 27 でコピー前に拒否。
- 残存 UNKNOWN/BLOCKED: READ(10)/READ(12) 対応 (pinned core は opcode 0x08 のみ。対象外のまま)、
  byte1 上位 3bit の実機挙動 (core はマスク。実機未測定)、データトラック長ベースの拒否 (§3.2、
  従来どおり BLOCKED)、商用メディア・実機・System Card 互換・CD_BOOT/CD_READ ABI (一切検証せず)。
- 安全ゲート不変性: 全拒否 (code 21/22/27) は最初の load-area 書込み前に発生 (R_COPIED=0 検証済み);
  post-copy/partial-copy ケースは従来どおり copied>0 で実行阻止 (code 30/31)。payload 不実行は
  judge が全 run で payload 結果領域ゼロにより確認 (big-disc の拒否ケース含む)。
