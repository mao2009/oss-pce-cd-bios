# ADPCM リセット実コア診断の証拠台帳（Issue #294）

親 #25。後続は #295（ROM-Primitive）。**状態: 固定版 Geargrafx の device 層（ADPCM 実装）の観測のみ。BIOS の AD_RESET は実行していない。`BLOCKED_ABI` は解消していない。`release_eligible=false`。System Card 互換の VERIFIED とは主張しない。**

対象: `tests/integration/geargrafx_ad_reset.py`（判定と mutation）と `tests/integration/geargrafx_ad_reset.cpp`（プローブ）。固定版 commit `b49ae82a012eade566d72e9d61c9d95a5caa4da3`。BIOS と CD は合成（ゼロ埋め）、検査用ゲストは `BRA *` のみ。

## 1. 観測の経路

- `Adpcm::Write` / `Adpcm::Read` は `cdrom.cpp` が `$1808`〜`$180E` で使うのと同じ入口。`Read($0A)` は副作用があるため呼ばない。
- 内部状態（ADDRESS、READ/WRITE_ADDRESS、LENGTH、END/HALF IRQ、PLAYING）は `Adpcm::GetState()` で読む。バス上の読出しではない。
- 時間は `GeargrafxCore::RunToVBlank` で 1 フレームずつ進める。

## 2. 固定版ソースから読んだ事実（`src/adpcm_inline.h`、`src/adpcm.cpp`）

| 項目 | 内容 | 根拠 |
| --- | --- | --- |
| リセット経路 | CONTROL（`$180D`）bit7 が 1 の間、`Clock` ごとに `SoftReset` が走る | `CheckReset` |
| SoftReset の対象 | ADDRESS、READ/WRITE_ADDRESS、LENGTH、read/write cycles（busy）、END/HALF IRQ、play pending | `SoftReset` |
| 再生の扱い | `RunAdpcm` は bit7 のとき `PLAYING = (CONTROL bit5)`。bit5 が残れば再生は止まらない | `RunAdpcm` |
| 状態の保持 | CONTROL 自体は書かれた値のまま（`$80` なら読み戻し `$80`） | `WriteControl` |

## 3. 観測結果（実コア、2 症例）

| 症例 | 操作 | 事前（pre） | 事後（post） | 判定 |
| --- | --- | --- | --- | --- |
| `pos-reset`（正） | アイドル、ADDRESS=`$1234`、CONTROL=`$80` | playing 0、ADDRESS `$1234` | CONTROL `$80`、playing 0、status `$00`、ADDRESS/READ/WRITE/LENGTH 0、END/HALF IRQ 0、SR `$0F` と DMA 不変 | `PASS_IDLE_AFTER_RESET_FROM_IDLE` |
| `neg-reset-playing`（負） | CONTROL=`$20` で再生開始（1 フレームで PLAYING=1）、その後 CONTROL=`$A0` | control `$20`、playing 1、status `$08`、LENGTH 130805 | CONTROL `$A0`、playing **1**、status `$08`、ADDRESS 0、LENGTH 0 | `NOT_IDLE_RESET_WITH_PLAY_BIT_SET` |

- 再実行の記録（コミット `99e93e0` を WSL の clean clone で 2 回実行。`git status` は clean、`HEAD` は一致、`mkdir -p /home/loach/locks && flock` で直列化）。両回とも終了コード 0、`PASS issue-294 ADPCM reset device-layer diagnostic`。
  - run1 ログ: `C:\Users\Hobart\AppData\Local\Temp\claude\C--Users-Hobart-Documents-Projects-oss-pce-cd-bios\88427502-ac7d-49df-83df-ba3558684b0b\scratchpad\w06-294-real-core-rerun-1.log`（SHA-256 `80b5527af6e3a6077f4b5f2267b33fc91456014849ac2c7be0736ecea676d3de`）
  - run2 ログ: 同ディレクトリの `w06-294-real-core-rerun-2.log`（SHA-256 `80b5527af6e3a6077f4b5f2267b33fc91456014849ac2c7be0736ecea676d3de`、run1 と同一）
  - 以前の単発ログ `w06-294-real-core.log` は、この再実行の根拠としては使わない。
- mutation: 偽造した観測（負例で playing=0、正例で playing=1、正例で ADDRESS 残存）はすべて拒否された。アイドル判定は正例を受理し、負例を拒否した。

## 4. #295 への実装条件（ROM-Primitive）

1. 停止判定は「リセット書込み後にアイドルを確認する」形にする。書込み 1 回の完了を成功としない。
2. リセット値は CONTROL `$80`（bit5 を 0）で書く。bit5 が残ると再生が続き、アイドルにならない。再生中に呼ぶ場合は先に bit5 を 0 にして止めることを条件にする（この PR では未検証）。
3. アイドル判定は `status & $8D == 0` かつ PLAYING が 0。status の busy（`$04`、`$80`）と END IRQ（`$01`）を含める。
4. 本 PR で未検証（UNKNOWN）: 転送中の busy からのリセット、HALF IRQ の発生、DMA 経路、CPU 側のレジスタ保存。#295 の対象外か、別 Issue で測る。
5. BIOS の AD_RESET の入出力・副作用は未確定のまま（`docs/research/ad-reset-contract.md` の 3b〜19b）。#295 は BIOS を呼ばず、device 層の条件だけを使う。

## 5. 証拠の区分

| 区分 | 状態 | 内容 |
| --- | --- | --- |
| 実コア device 層の実行 | PASS（合成 BIOS/CD、固定版） | 上記 2 症例と mutation。判定は `geargrafx_ad_reset.py` の終了コード 0。 |
| BIOS AD_RESET の実行 | NOT_RUN | 実行していない（ABI 未確定、BLOCKED_ABI）。 |
| 実機・第 2 エミュレータ | NOT_TESTED | 本観測はモデルの振る舞いであり、実機の確認ではない。 |
| 独立レビュー | NOT_RUN | 別エージェントによるレビューは未実施。 |
| CI | 本 PR では CI 対象外 | 手動実行のみ（Makefile 未接続）。 |

## 6. 限界

- 観測は固定版 Geargrafx のソースに従う device 層の振る舞いである。実 PC Engine の ADPCM チップとの一致は示さない。
- 合成 BIOS と検査用ゲストは BIOS の振る舞いを含まない。
- 本診断の PASS は「固定版コアの観測が期待と一致し、判定器が偽造を拒否した」ことだけを示す。BIOS 互換、スロット `$10` の接続、リリース許可は示さない。
