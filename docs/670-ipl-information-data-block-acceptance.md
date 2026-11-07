# Issue #670: IPL Information Data Block (public spec) — 受入条件と既存実装の対応

Status: DEV-only / synthetic media only / NOT a System Card BIOS / NOT a BIOS ABI / NOT CD_BOOT or CD_READ verified /
`release_eligible=false`. No commercial bytes, sums, addresses, or sector contents are recorded here.
Existing multi-sector loader (PR #681, #683) is reused; no reimplementation.

## 1. 受入条件 → 既存実装 対応表

| # | 受入条件 (Issue #670) | 既存実装 | テスト | 状態 |
|---|---|---|---|---|
| 1 | 情報ブロック位置: データトラック相対セクタ 1、署名オフセット 32 を確認 | `ipl_loader_rom.s` `validate_info` SIG_LEN=23 at BUF+32 | neg_signature_byte (code 20) | PASS (synthetic) |
| 2 | $00-$02 24bit big-endian ブロック番号でコードセクタ決定 | BUF[0] must be 0; BUF[1]/BUF[2] -> bodyl (16-bit LBA) | neg_iplblk_high_byte_nonzero (code 21, 追加), neg_iplblk_zero/one (code 21) | PARTIAL: 24bit field accepted only when high byte is 0 |
| 3 | $03 セクタ数で複数セクタ READ(6) | nsec 1..10; copy_body loops nsec | pos_* / neg_iplbln_zero/11 | PASS (synthetic) |
| 4 | $04-$05 ロード先、$06-$07 実行開始値の範囲・整合性検査 | IPLSTA $3000-$7FFF and fit; entry in [IPLSTA, IPLSTA+N*2048) | neg_iplsta_*, neg_entry_* (code 23/24) | PASS (synthetic) |
| 5 | $08-$0C MPR2-6 設定、MPR0/1 は DEV 初期値維持 | m2..m6 from BUF+8..12; MPR0=$FF, MPR1=$F8 at reset | neg_mpr2_not_ram_bank (code 25), MPR verification in judge | PASS (synthetic) |
| 6 | $0D OPENMODE: DEV では解釈しない（0 以外は拒否） | BUF+13 must be 0 | neg_openmode_nonzero (code 26) | PASS (synthetic) |
| 7 | 実行は単純な制御移行に限る; JSR/JMP・レジスタ条件は未確定 | `jmp` to IPLJMP; no JSR gate; release_eligible=false | payload runs and returns via halt | PASS (DEV scope) |
| 8 | 負例: 情報ブロック範囲外、セクタ数 0、データトラック境界超過、ロード先範囲外、実行開始値範囲外 → コピー/実行前に拒否 | neg_* above | see above | PARTIAL: データトラック境界超過は事前拒否不可 (§3) |
| 9 | 既存統合テスト (dev-cdload-exec, dev-boot-internal, experimental-bios-boot, dev-ipl-boot) の期待値維持 | 未変更 | CI | PASS (not touched) |
| 10 | DEV 専用; CD_BOOT/CD_READ ABI を VERIFIED にしない; release_eligible=false | 不変 | status/docs | PASS |

## 2. 24bit IPLBLK / 21bit READ(6) / 16bit 内部 LBA の分離

3 つの幅を混同してはならない。

1. 公開 IPL 情報ブロックの IPLBLK フィールド幅: **24bit** (big-endian, $00-$02)。
2. SCSI READ(6) の標準 LBA フィールド幅: **21bit** (6 バイト CDB の LBA は 21bit)。
3. 現行ローダーの内部 LBA 実装幅: **16bit** (`bodyl` 2 バイト、`IPL_SEL` 16bit に加算)。

したがって 24bit IPLBLK の全域を READ(6) だけで表現できるとは限らない。READ(6) の
21bit を超える値は別コマンド (READ(10)/READ(12) 等) または媒体境界の再定義が必要であり、
現行 Geargrafx プローブ/ローダーがそれらをサポートするかは **UNKNOWN** (未確認)。
Owner 承認なしに 24bit 対応を実装しない。

現行実装の扱い:
- `BUF[0] (high byte) != 0` を拒否 (code 21)。24bit のうち上位 8bit は 0 でなければならない。
- これは 24bit フィールドを部分的にしか扱えない状態であり、データトラックが 65536 セクタを超える
  媒体では IPLBLK の上位バイトが非零になり拒否される。
- LBA 幅の拡張 (24bit/32bit) と READ(6) CDB の LBA フィールド拡張が必要。Owner 判断の仕様変更であり、
  本 PR では保留。

## 3. データトラック境界超過の事前拒否 (BLOCKED on missing info)

- 受入条件は「データトラック境界超過をコピーと実行の前に拒否」を要求する。
- 現行テスト `partial_read_beyond_disc_after_5_sectors` は 5 セクタコピー後に READ(6) 失敗を検出し、実行のみ阻止する (code 5, copied 5)。先行拒否の証拠ではない。
- ゲスト (HuC6280) は SCSI/CD-ROM2 の TOC やトラック長を読み取る API を持たない (CD_BOOT/CD_READ 未検証)。データトラックのセクタ数はホストのみが知る。
- したがって「コピー前に境界超過を拒否」は現行アーキテクチャでは実装不可能。推測で境界チェックを実装しない。
- 必要な情報と実装案:
  1. ホストがデータトラックのセクタ数を IPL 情報ブロックに追加フィールドとして埋め込む (例: $0E-$0F または予約領域)。ただし公開仕様の $0E-$1F は reserved (0 必須) であり、意味を付与するには Owner 承認が必要。
  2. またはホストが選択レコード (Issue #668) にトラック長を含め、ゲストが READ 前に比較する。これも #668 の契約変更。
  3. または READ(6) の失敗を「コピー前」ではなく「部分コピー後」の安全停止として受入条件を改訂する (Owner 判断)。
- 現状の安全停止 (部分コピー報告、実行阻止) は成果として維持する。

## 4. LBA 上限境界の検証 (混同の整理)

用語を分離する:
- 絶対 LBA = `IPL_SEL (0x0109) + IPLBLK (相対)`。
- 相対 IPLBLK = データトラック相対セクタ番号 (24bit フィールド)。
- 最終読み込みセクタ = 絶対 LBA の末尾 (`base + nsec - 1`)。
- exclusive end = `base + nsec`。

現行検証:
- 絶対 base が 16bit を超えない: `IPL_SEL + IPLBLK` の加算で carry 発生 → 拒否 (code 27)。
  例: `0x0109 + 0xFFFF = 0x10108` → 16bit 範囲超過で拒否。したがって `IPLBLK=0xFFFF` 単体 (n=1) は
  **受理されない** (§2 以前の誤記を訂正)。
- ラップ拒否: `bodyl + nsec <= 0xFFFF` (exclusive end が 16bit を超えない)。最終セクタが `0xFFFF` に
  ちょうど一致する場合 (`base + nsec == 0x10000`) も保守的に拒否される。
- 確認テスト: `neg_body_lba_overflows_16bit` (IPLBLK=0xFFFF), `neg_body_lba_tail_wraps_16bit`
  (IPLBLK=0xFEF5, n=3)。

不足点:
- 上限の基準が 16bit 幅であり、実際のデータトラック長ではない (§3)。
- 最終セクタが 16bit 上限に一致する境界 (`base + nsec - 1 == 0xFFFF`) は tail check により拒否されるが、
  これは保守的であり、受理すべきかは Owner 判断。
- 24bit 高位バイト拒否 (§2) により 0x010000 以上は拒否されるが、0x00FFFF 以下は受理される
  (実データトラック長がそれより短ければ実行時失敗)。

## 5. 本 PR の変更範囲 (最小)

- 受入条件対応表の追加 (本ドキュメント)。
- 24bit 高位バイト拒否の回帰テスト追加 (`neg_iplblk_high_byte_nonzero`)。
- 既存テストの期待値は変更しない。
- ローダー本体のコード変更なし (Owner 判断が必要な仕様変更は保留)。
- `release_eligible=false`, `ABI VERIFIED` 未変更を維持。

## 6. 未解決 / Owner 判断待ち

- データトラック境界の事前拒否 (§3) の実装方式。
- 24bit LBA 幅への拡張 (§2)。
- 受入条件の明示改訂 (Owner)。

## 7. READ(6) エンコードと 24bit IPLBLK の表現可能性

- READ(6) CDB: LBA は 21bit (3 バイト、最上位 3bit はグループ番号)。24bit IPLBLK の値が 2^21 を超える
  場合は READ(6) 単独では表現できない。
- 現行ローダーの内部 LBA は 16bit であり、READ(6) の 21bit 能力も未使用。
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

本 PR で追加したテスト (`neg_iplblk_high_byte_nonzero`) は合成媒体のみ; 実コア検証は NOT_RUN (Geargrafx 未実行)。
既存 35+ ケースは HISTORICAL_PASS (PR #681/#683)。
