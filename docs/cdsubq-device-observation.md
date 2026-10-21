# CD_SUBQ デバイス層観測診断 (#261)

状態: **実コア結果は PASS（トラック 2・LBA 66 の PLAYING / PAUSED / STOPPED の 3 状態）。** 範囲は後述。System Card の CD_SUBQ ABI（引数・戻り値・Q バッファ配置）は UNKNOWN。
本診断の PASS は、固定版 Geargrafx の SCSI/CD 層が既知の媒体位置で返した `$DD`（READ SUBCODE-Q）応答の観測であり、
BIOS 互換性・callee ABI の証明ではない。親 Issue #19、呼出側の根拠は [cd-subq-contract.md](research/cd-subq-contract.md)。

## 何を観測するか

| 項目 | 内容 |
|---|---|
| ROM | #15 の CD-DA 観測 ROM（`src/cdda_probe.s`、`make dev-cdda`）。新しいゲストコードは追加しない |
| 媒体 | `tools/make_cd_image.py` の自作混合モード CUE/BIN。トラック 1 データ LBA 0–15、トラック 2 音声 LBA 16–79 |
| 応答 | `$DD` の 10 バイト応答。CPU `$2380` / `$2390` / `$23A0` に格納（再生中・一時停止後・停止後の 3 点） |
| 判定 | `tools/cdsubq_observation.py`（純粋な判定器） |
| 実コア | `tests/integration/geargrafx_cdsubq.py`（フロントエンドは #15 の `geargrafx_cdda_probe.cpp` を再利用） |

応答のレイアウトは #15 の `tools/cdda_verify.py::check_subq` が前提とするもの（先頭 1 バイトが状態、続いて
ADR/コントロール、トラック、インデックス、相対 MSF、絶対 MSF。MSF は BCD）。上流 `scsi_controller.cpp`（b49ae82a）の
CommandReadSubcodeQ との照合は要約取得による確認のみで、バイト単位の機械照合は未実施。実コアの 3 応答は、このレイアウトで復号され一致した。

## 判定規則

- 各 `$DD` 応答は、直前のチェックポイントと直後のチェックポイントの `CURRENT_LBA` の範囲（窓）に対応づける。
- 窓内で応答が**ちょうど 1 つ**の LBA に完全一致しなければ FAIL（未説明の応答）。
- 窓の両端の外側の LBA（`lo-1` と `hi+1`）は NEGATIVE でなければならない（境界負例）。
- 状態が PLAYING / PAUSED / STOPPED 以外（IDLE など）は現在位置を持たないため NEGATIVE。
- トラック外の LBA、長さ違い、非 BCD、フレーム 75 以上は NEGATIVE（理由つき）。

## 正例と境界負例（ホストテストで検証）

正例（手計算のベクトル。`tests/host/test_cdsubq_observation.py`）:

| 名前 | 状態 | トラック | LBA | 応答（16 進） |
|---|---|---|---|---|
| playing_track2_start | PLAYING | 2 | 16 | `00 01 02 01 00 00 00 00 02 16` |
| playing_track2_last | PLAYING | 2 | 79 | `00 01 02 01 00 00 63 00 03 04` |
| paused_track2_mid | PAUSED | 2 | 50 | `02 01 02 01 00 00 34 00 02 50` |
| stopped_track2_start | STOPPED | 2 | 16 | `03 01 02 01 00 00 00 00 02 16` |

境界負例（同じ判定器で拒否されることを確認）: LBA 16 の応答を LBA 17 とみなす（1 フレームずれ）、
トラック 2 の応答をトラック 1 の末尾 LBA 15 とみなす、LBA 79 の応答をリードアウト LBA 80 とみなす、
LBA 15（音声の手前）、負の LBA、存在しないトラック 3、IDLE 状態、状態不一致（STOPPED を PLAYING とみなす）、
非 BCD のトラックバイト、フレーム 0x75、9 バイトの応答。

## 実コアの結果

**PASS。** 実行コマンドと終了コードは次のとおり。

```sh
make dev-cdsubq-integration MODE=debug   # 終了コード 0
```

- 実行環境: WSL の clean clone（HEAD を `8ee22407da1459fbee7276fa62bab04c81d9fcd3` に固定）。
- ツール版: Geargrafx `b49ae82a012eade566d72e9d61c9d95a5caa4da3`（pinned）。
- 証拠: `build/debug/cdda-probe/cdsubq-evidence.json`（status PASS、sha256 `fc1bf82d5450e2aed77b5668d5eaa1716002095427433446a9c4e1015bf429e8`）。`build/` は追跡しない。

観測した 3 つのチェックポイント（いずれもトラック 2、LBA 66、窓の外の LBA 64 と 67 は NEGATIVE）:

| チェックポイント | 状態 | トラック | LBA | `$DD` 応答（16 進） |
|---|---|---|---|---|
| スロット 2 | 0（PLAYING） | 2 | 66 | `00 01 02 01 00 00 50 00 02 66` |
| スロット 4 | 2（PAUSED） | 2 | 66 | `02 01 02 01 00 00 50 00 02 66` |
| スロット 6 | 3（STOPPED） | 2 | 66 | `03 01 02 01 00 00 50 00 02 66` |

実コアで**未確認**（NOT_RUN）の項目: LBA 16・50・79 の正例、トラック 1 の境界、LBA 80 の終端。
これらはホストの合成ベクタでのみ確認している（後述）。

## 観測の範囲と限界

- 対象は固定版 Geargrafx の SCSI/CD 層。実機・第 2 エミュレータの結果ではない。
- 媒体は自作（作者定義）。LBA 0–15 のデータトラックでの `$DD` 応答は本診断の正例にしていない（再生対象にしていないため、仕様未決）。
- 「未再生」の応答（`$D8` の前）は観測点がない。本診断の負例には含めない。
- 境界負例の確認経路: 実コアでは窓の外の LBA 64 と 67 を NEGATIVE として確認した。
  ホストでは、開始端（窓の `lo` = LBA 16、`lo-1` = LBA 15）は `observe_run` 経由で確認（`test_edge_window_boundary_is_compared_by_fields`）。
  終端（窓の `hi+1` = LBA 80）は判定器の単体テスト（`leadout_lba80`）のみで確認しており、`observe_run` 経由の終端テストはない。
- 判定の期待値は `canonical_fields`（トラック開始からの相対 LBA と LBA+150 の絶対フレーム）から計算する。この式そのものは判定器と同じコードであり、独立の確認は手計算のベクトル（ホストテスト）による。

## #262 の依存判定

#262（ROM-Primitive: 検証済み 1 種別の Q 応答取得を独立 HuC6280 ルーチンとして実装）は、
「同親の Contract・Fixture・Diagnostic に必要な実証が揃い、先行 PR が main に統合済み」の場合のみ着手可能。

判定: **BLOCKED**。理由:

1. 実コアの観測は取得済み（トラック 2・LBA 66 で PLAYING・PAUSED・STOPPED）。ただし観測は LBA 66 の 1 位置に限られ、
   LBA 16・50・79 の正例、トラック 1 の境界、LBA 80 の終端は実コアで未確認。この範囲だけでは、#262 が前提とする
   Q 応答の実証は満たさないと判断する。
2. 同親の CD_SUBQ callee ABI は BLOCKED_ABI（#259 / #260）。引数・戻り値・Q バッファ配置が未確定。
3. 本 PR は未マージ（Draft）で、main に統合されていない。

解除条件: (a) 本 PR が main に統合される、(b) 検証済みの 1 種別について callee の引数・戻り値の根拠が別途確定する、
(c) 必要と判断した範囲の実コア観測が追加で取得される。(b) は本 Issue の範囲外であり、#262 の実装は今回のスコープ外。
