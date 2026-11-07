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

## 2. 24bit IPLBLK と 16bit LBA 制限の差異

- 公開仕様の $00-$02 は 24bit big-endian。
- 現行ローダーの LBA は 16bit (`bodyl` 2 バイト) で、`IPL_SEL` (16bit) に加算する。
- 現行実装は `BUF[0] (high byte) != 0` を拒否 (code 21)。したがって 24bit のうち上位 8bit は 0 でなければならない。
- これは「24bit フィールドを部分的にしか扱えない」状態であり、データトラックが 65536 セクタを超える媒体 (約 128 MiB 以上) では IPLBLK の上位バイトが非零になり拒否される。
- ゲスト側でデータトラック長を取得する手段がないため、24bit 全域を有効にするには LBA 幅の拡張 (24bit/32bit) と READ(6) CDB の LBA フィールド拡張が必要。これは Owner 判断の仕様変更であり、本 PR では保留。

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

## 4. LBA 上限境界の検証不足

- 現行検証: `bodyl + nsec <= 0xFFFF` (ラップ拒否, code 27)。`neg_body_lba_overflows_16bit`, `neg_body_lba_tail_wraps_16bit` で確認。
- 不足点: 上限の基準が 16bit 幅であり、実際のデータトラック長ではない (§3)。また IPLBLK=0xFFFF 単体 (n=1) は 16bit 範囲内として受理されるが、データトラック長がそれより短ければ実行時失敗になる。
- 24bit 高位バイト拒否 (§2) により 0x010000 以上は拒否されるが、0x00FFFF 以下は受理される。

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
