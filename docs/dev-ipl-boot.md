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
4. 長さ 255 の最大 PCEX も同じ ROM で cold/warm 成功する。255 バイトが `$3000-$30FE` に入り、ガード `$30FF` とセクタ境界 `8+255=263 <= 2048` を満たすことを実コアで確認する。
5. 負例は、コピー前と実行前で独立に拒否される（stage 11 のヘッダー失敗は EXEC `$3000-$30FF` の `$5A` センティネルが無傷＝部分コピーなし、かつ payload ラベル未到達＝実行阻止）: マジック不正、チェックサム不正、長さ 0、エントリが長さと等しい、ヘッダーなし、読み取り中断（注入）。コピー前拒否（stage 11）と実行後拒否（IPL B の stage 15）を区別する。ステップ予算超過はゲストの拒否ではなく、予算による打ち切りである（シード完了前に止まるため full-copy チェックのみ）。音声のみ媒体はホストで拒否する。
6. ホスト契約（`python3 -m unittest tests.host.test_pcex_boundary`、cc65/emulator 不要）が境界を固定する: 長さ 1/255 受理、0/256 host 拒否、エントリ 254 受理 / 255 拒否（長さ 255 のとき）、チェックサムは宣言されたコード範囲のみ。judges の部分コピー/overshoot mutation も synthetic record で拒否する。
7. 全実行の後も、ROM の SHA-256 が変わっていないことを確認する。

### PCEX 境界の表現可能性（#668 残存境界）

| 条件 | 状態 | 根拠 |
| --- | --- | --- |
| 長さ超過（256 以上） | host PASS / real-core NOT_RUN（符号化不可） | 長さは 1 バイト。`encode_sector` が 0/256 を拒否し、oracle が長さ 0 を `BAD_LENGTH` とする。255 より大きい値を guest セクタに書く手段がないため、実コアの負例は作れない。 |
| セクタ境界超過（`8+len > 2048`） | host PASS / real-core NOT_RUN（符号化不可、255 で最大値実測） | 最大でも `8+255=263 <= 2048`。実コア 255 バイト成功で最大値を実測済み。 |
| ロード先範囲外 | host PASS / real-core NOT_RUN（フィールドなし、255 で最大値実測） | ロード先は `$3000` 固定でアドレス欄がない。最大コピーは `$30FE` 終端で `$3100` ページ・MARK `$3F00`・work RAM 上限 `$4000` に届かない。 |
| 部分コピー | real-core PASS | ゲストが READ 前に `$3000-$30FF` を `$5A` でシードする（1 ページ）。`$3100-$3FFF` はシードしない（MARK `$3F00` と dev-boot 経路の MS_RES `$3F40` の衝突回避）。stage 11 失敗で 1 バイトでもセンティネルが変われば拒否する。 |
| コピー前拒否と実行阻止の区別 | real-core PASS | `judge_failure` は EXEC 無傷（copy blocked）と payload ラベル未到達（execution blocked）を別条件で検査する。IPL B（stage 15）はコピー後にペイロードが動き、戻り検査で拒否される対照例である。 |

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
