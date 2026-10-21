# AD_TRANS 実コア診断（Issue #299）の非実行計画と現状

**状態: BLOCKED（スコープ・PR 規模）。実コアでの観測は未実施（NOT_RUN）。** release_eligible=false。
本書は pinned Geargrafx（b49ae82a）のソース読取りによる事実と、実測に必要な残作業の分割案だけを記録する。
ROM 実装（#300）と slot `$11` 接続（#301）は含まない。BIOS の AD_TRANS（`$E033`）の ABI は未確定のまま（[ad-trans-contract.md](ad-trans-contract.md)）。

## 1. 実施していないこと

- 固定版 Geargrafx 上での実行（PASS / FAIL のいずれも付けない）。
- 正常系・負例のゲスト ROM、判定器、ドライバの実装。

## 2. 作業を止めた理由

- 必要な部品は次の三つで、1 PR（3 ファイル・200〜250 行）に収まらない。
  1. ゲスト asm: CD 制御（RESET、SELECT、COMMAND、DATA、STATUS、MSG）は既存の `src/cd_read_probe.s`（356 行）と同じ手順が必要。データ相だけを ADPCM DMA に置き換える。
  2. 観測用の C++ プローブ: ADPCM RAM の読出しは既存の libretro メモリ面で取れるか未確認。`Adpcm::GetRAM()`（`adpcm_inline.h`）を使う専用プローブが必要と見込む。
  3. Python ドライバ（判定器と負例を含む）。
- 起動と Makefile のターゲットは Coordinator 管理。Makefile は変更していない。
- 上記で合計見込みは数百行・複数ファイルであり、1 Issue = 1 Draft PR の規則と 200〜250 行の目安を超える。

## 3. Geargrafx b49ae82a のソース読取り（実行ではない）

出典: `src/cdrom.cpp`（レジスタ振分け）、`src/adpcm_inline.h`（ADPCM 本体）。

| 項目 | 内容（ソース上の動作） |
| --- | --- |
| 配置 | CD 側の `reg` 0x08〜0x0E（`$1808`〜`$180E`）を `Adpcm::Write` / `Read` へ振り分ける |
| 書込み先アドレス | `$1808`（下位）、`$1809`（上位）で `m_address` を設定する |
| 書込み方向の確定 | `$180D` の bit1 が 0 から 1 に変わるとき `m_write_address = m_address - (bit0 ? 0 : 1)` |
| データ書込みポート | `$180A` への書込みで `m_write_value` を設定し、次の書込みスロットで RAM へ入る |
| DMA 制御 | `$180B` の bit0〜1 が非 0 のとき `UpdateDMA` が有効。bit0 は SCSI のデータ待ちで自動的に下がる |
| DMA の取得条件 | ACK=0、CD=0、IO=1、REQ=1 のとき 36 サイクル後に `ReadData()` と `AutoAck()`。書込みスロットで RAM へ格納 |
| 読出しポート | `$180A` の読出しは直前の値を返す（1 スロット遅延）。アドレスは `$180D` bit3 で `m_read_address` に確定 |
| 観測手段 | `Adpcm::GetRAM()` が RAM 配列を返す。トレース列挙に `TRACE_ADPCM_DMA_STATE`、`TRACE_ADPCM_WRITE_COMPLETE` がある |

注: 上表は読取りによる。実機の挙動、ゲストから見た副作用の完全性、BIOS の使い方は観測していない。

## 4. 観測項目（提案、未実測）

- ADPCM RAM の宛先範囲のバイト列（正常系で期待値と一致、負例では事前の番兵値のまま）
- `$180B` の DMA 状態と、転送完了後の残り（bit0 が下がっているか）
- 書込みアドレスの前後（宛先 + 転送バイト数の一致）
- SCSI の STATUS / MESSAGE と、ゲストの戻り値
- 実行予算（命令数）、コア版（b49ae82a）、ROM と CUE の SHA-256

## 5. 負例の候補（一つだけ実装する場合）

- DMA を有効化しないまま READ(6) を実行する。ADPCM RAM は番兵のままでなければならず、判定器は「転送済み」を PASS にしてはならない。
- 媒体なしと busy の境界は既存の CD 読出し診断（Issue #5）の負例と同じ判定器で扱うのが望ましい。

## 6. 残る UNKNOWN（台帳 ad-trans-contract.md との対応）

- C5 / C6（宛先と `_dh`）、C16（宛先範囲）、C8 / C9（成功値と busy）は BIOS 側の問いであり、本診断の対象外。
- 本診断で答えられるのは、ハードウェア DMA 経路（`$1808`〜`$180E`）が自作ゲストから正しく使えるかだけ。BIOS の ABI の代替にはならない。

## 7. 次の作業（分割案）

1. PR-A: 観測用プローブの追加（ADPCM RAM と DMA 状態のダンプ）。判定器のホスト試験を含む。
2. PR-B: ゲスト asm（DMA 版の READ(6)）と正常系・負例のドライバ。
3. PR-C: Makefile のターゲット登録（Coordinator 側で実施）と、実コアでの実測結果の記録。

いずれも #300（ROM プリミティブ）の前提にはならない。#300 は本診断が main に統合された後にのみ着手する。
