# DEV専用IPLブート（Issue #668）

**状態: EXPERIMENTAL / DEV-only / 合成媒体のみ / 互換ではない。ここに記すのは #668 の単一セクタ PCEX 契約。** System Card 互換性、BIOS ABI、CD_BOOT・CD_READ の VERIFIED を主張しない。`release_eligible=false` を維持する。

## 経路

```
RESET -> 初期化（MPR0/1、スタック） -> ホストが選んだ選択レコード（トラック、INDEX 01 の LBA、種別）
      -> READ(6)（1セクタ） -> ゲストが PCEX ヘッダーを検証（マジック、長さ 1..255、エントリ、チェックサム）
      -> $3000 へ固定コピー -> RAM 上のコードを実行 -> 戻り検査（PAYLOAD_MARKER_MISMATCH は 30）
```

- ホストは、媒体のレイアウトから選択レコードを作るだけである。ペイロードのバイトは ROM に埋め込まず、ROM ビルド時にも要求しない。
- ゲストは、読み込んだセクタ自身を検証してから実行する。
- 既存の `experimental-bios-boot` は、ROM ビルド時にセクタがペイロードと一致することを要求していた。DEV IPL 経路はこの要求を取り除いた別ビルダー（`tools/build_ipl_boot.py`）を使う。既存の経路は変更していない。

## 検証（`make dev-ipl-boot-integration`、pinned Geargrafx）

1. ROM は 1 つだけビルドする。その後、合成 IPL A（正常）と、同じ ROM で動く合成 IPL B（マーカーのみ変更、PR #658 と同じ変種）を実行する。
2. IPL A は cold/warm で成功する（RAM 上のコードが、ディスクのペイロードと一致する）。
3. IPL B は同じ ROM で別の結果になる（`PAYLOAD_MARKER_MISMATCH`、stage 15）。成功判定は IPL B を拒否する。
4. 負例は、コピーと実行の前に拒否される（`$3000` に IPL のバイトが無いことを確認する）: マジック不正、チェックサム不正、長さ 0、エントリが長さと等しい、ヘッダーなし、読み取り中断（注入）。ステップ予算超過はゲストの拒否ではなく、予算による打ち切りである（コピーされていないことのみ確認する）。音声のみ媒体はホストで拒否する。
5. 全実行の後も、ROM の SHA-256 が変わっていないことを確認する。

## DEV契約と商用互換契約

| 項目 | DEV契約（本 Issue） | 商用互換契約（未確定、#635 で扱う） |
| --- | --- | --- |
| 選択 LBA | ホストが合成媒体の INDEX 01 から決める（暫定） | 商用 IPL の配置を観測してから決める。推測で固定しない |
| ロードアドレス | `$3000` 固定（暫定） | 商用 IPL の要求から決める |
| ヘッダー | 既存 PCEX（マジック、長さ、エントリ、チェックサム） | 商用 IPL の形式は未測定 |
| 複数セクタ | 未対応（1 セクタのみ） | 未確定 |
| バージョン欄 | なし（必要性が立証されていない） | 未確定 |
| 制御移行後の戻り | 固定マーカー検査（DEV 契約） | 商用の要求は未確定 |

**合成 IPL が動いたことは、商用 IPL 互換の証明ではない。**


## 別経路: 複数セクタ IPL（2026-11-06）

本書の単一セクタ PCEX ルートを置換するものではない。[PR #681](https://github.com/mao2009/oss-pce-cd-bios/pull/681) は `make ipl-loader-integration` で 8 KiB HuCARD+CUE 経路を、[PR #683](https://github.com/mao2009/oss-pce-cd-bios/pull/683) は `make dev-bios-ipl-loader-integration` で **256 KiB DEV イメージ + Geargrafx LoadBiosFromBuffer** 経路を追加し、両方 2026-11-06 に main へ統合された。合成媒体22ケースの cold/warm 検証が報告されている。別ローダーの 16bit LBA、MPR2–6 と DEV 独自チェックサムを単一セクタ PCEX 仕様へ混同しないこと。

main `5a8dc5a`: DEV implementation count 14/81、67 stubs、owner-approved DEV_CALLABLE 0、reference ABI VERIFIED 0/81、reference JSR permitted 0/81。歴史的 main `f90c16b` では9 DEV実装・72スタブ。どの経路も商用互換性を主張しない。詳細は [status](status.md)。
