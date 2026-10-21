# CD_SUBQ デバイス層観測診断 (#261)

状態: **診断のみ。実コア結果は NOT_RUN。** System Card の CD_SUBQ ABI（引数・戻り値・Q バッファ配置）は UNKNOWN。
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
ADR/コントロール、トラック、インデックス、相対 MSF、絶対 MSF。MSF は BCD）。固定版ソースでのこのレイアウトの再確認は
**未実施**で、実コアの実行で確かめる必要がある。

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

**NOT_RUN.** 本セッションでは実コアを実行していない。理由:

- 実コアには WSL 上の cc65 と固定版 Geargrafx（`make setup`）が必要。Windows 側には cc65 も Geargrafx もない。
- WSL の呼出しは worktree ガードにより拒否された（`wsl.exe` は git を実行しうるため、隔離 worktree からは実行できない）。
- 規約どおり、実装と静的検証までを行い、実コア検証を NOT_RUN として記録する。

実行手順（Linux/WSL、`make setup` 済みの環境）:

```sh
make dev-cdsubq-integration MODE=debug
```

成功時は `build/debug/cdda-probe/cdsubq-evidence.json` を出力する。実行結果のコマンドと終了コードは、
実行者が本節を更新して記録すること（未実行の結果を PASS と書かない）。

## 観測の範囲と限界

- 対象は固定版 Geargrafx の SCSI/CD 層。実機・第 2 エミュレータの結果ではない。
- 媒体は自作（作者定義）。LBA 0–15 のデータトラックでの `$DD` 応答は本診断の正例にしていない（再生対象にしていないため、仕様未決）。
- 「未再生」の応答（`$D8` の前）は観測点がない。本診断は NOT_RUN とし、負例として扱わない。
- 判定の期待値は `canonical_fields`（トラック開始からの相対 LBA と LBA+150 の絶対フレーム）から計算する。この式そのものは判定器と同じコードであり、独立の確認は手計算のベクトル（ホストテスト）による。

## #262 の依存判定

#262（ROM-Primitive: 検証済み 1 種別の Q 応答取得を独立 HuC6280 ルーチンとして実装）は、
「同親の Contract・Fixture・Diagnostic に必要な実証が揃い、先行 PR が main に統合済み」の場合のみ着手可能。

判定: **BLOCKED**。理由:

1. 本 Issue（#261）の実コア観測が NOT_RUN で、Q 応答の実証がない。
2. 同親の CD_SUBQ callee ABI は BLOCKED_ABI（#259 / #260）。引数・戻り値・Q バッファ配置が未確定。
3. 本 PR は未マージ（Draft）。

解除条件: (a) Linux/WSL で `make dev-cdsubq-integration` が exit 0 となり、evidence が保存される、
(b) 本 PR が main に統合される、(c) 検証済みの 1 種別について callee の引数・戻り値の根拠が別途確定する。
(c) は本 Issue の範囲外であり、#262 の実装は今回のスコープ外。
