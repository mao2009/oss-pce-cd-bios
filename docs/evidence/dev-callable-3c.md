# DEV_CALLABLE 承認証拠: $3C EX_BINBCD

- 対象: スロット $3C（ledger id 60）EX_BINBCD、DEV 入口 `$E0B4`
- 関連: #324（DEV_CALLABLE 承認の親 Issue）、#434（本証拠の Issue）、#427（$3C DEV 接続）、PR #431（main に取り込み済み）
- 基準: main = `c906728609c61945ef72ae020ddeb43db5eb5e4e`（2026-10-21 時点）
- 本書は証拠の整理であり、承認ではない。承認フィールド（`spec/dev_implementations.json`、slot id 60 の `approval`）は未変更で、`approved_by: null`、`review_ref: null` のまま。承認はオーナーの判断が必要。

## 1. 結論（現時点）

| 項目 | 状態 |
|---|---|
| DEV ROM 入口経由で全入力（0..255）を実コアで実行 | **未実施**（下記 3 参照） |
| 既存の DEV 入口テストの実行 | **未実施**（本作業では実行できなかった） |
| 負例（範囲外、誤入口）の実コア確認 | **未実施** |
| 独立レビュー | 第 6 節に記載（結果待ち時点の記録） |

現状の証拠は「ソース・契約・既存テストの定義」の確認に限られる。実コア全入力の PASS は、以下の手順を実行するまで主張しない。

## 2. 契約（DEV 専用、独自仕様）

出典: `docs/ex-binbcd-internal.md`、`src/ex_binbcd.s` ヘッダ。参照 ABI（System Card `$3C/$E0B4`）は `spec/api_contracts.json` で全項目 UNKNOWN であり、本契約はその根拠にならない。

| 項目 | DEV 契約 |
|---|---|
| 入力 | A = 符号なし 2 進数。定義域は 0..255（8 ビット）。有効は 0..99 |
| 出力（0..99） | A = packed BCD（上位ニブル＝十の位）、C=0 |
| 範囲外（100..255） | A は入力のまま、C=1 |
| 保存 | X, Y 保存。D=0 を前提（D=1 は対象外） |
| スタック | 3 バイト（JSR 2 + PHX 1）。範囲外経路は PHX を実行しない |
| 未規定 | 戻り時の N/Z/V |

## 3. 実コア検証の計画と現状

### 3.1 既存テスト（main 上の定義）

- `tests/integration/geargrafx_dev_bcd.py`: DEV ROM の `$E0B4` を実行。**9 入力のみ**（0, 1, 9, 10, 42, 99, 100, 128, 255）。検査は A、C（P のビット 0 のみ）、X、Y、戻り PC、SP=$FF、MPR1/MPR7。P の D/I ビットは検査しない。
- `tests/integration/geargrafx_ex_binbcd.py`: **0..255 の 256 入力**を検査するが、対象は独自 HuCARD プローブ（`ex_binbcd_probe`）であり、DEV ROM の `$E0B4` 入口ではない。検査は A、C、D、X。SP、MPR、戻り PC は検査しない。ミューテーション（`ADC #$10`→`ADC #$20`）による負例を含む。

よって「DEV ROM 入口で 0..255 全入力」は既存テストでは網羅されていない。

### 3.2 全入力スイープ（未実行）

全入力スイープ用のスクリプトを作成済み（リポジトリ外、作業ディレクトリ内）。main の `tests/integration/geargrafx_dev_bcd.py` の関数を再利用し、256 入力それぞれについて以下を検査する:

- A = 期待 packed BCD（範囲外は入力のまま）、C = 期待値
- X = $5A、Y = $A5（呼び出し前の値）
- P.D = 0、P.I = 1（ガードの SEI による）
- 戻り PC = 停止番地、SP = $FF、MPR1 = $F8、MPR7 = 0
- N/Z/V は記録のみで検査しない

範囲内 100 件（0..99）、範囲外 156 件（100..255、拒否動作）。誤入口の負例（`$E0B4` に 0x42 を入れて 42 を期待すると失敗すること）も含む。

**実行結果: 未実施。** 理由は第 5 節。

### 3.3 必要な実行コマンド（オーナーまたは WSL 環境で実行）

```
# 清浄なクローンを WSL のホーム配下に作る（Windows worktree 上では実行しない）
git clone <main のパス> /home/loach/<作業名>
cd /home/loach/<作業名> && git checkout --detach c906728
TOOLS_DIR=/home/loach/oss-pce-cd-bios-tools make dev-rom MODE=debug
python3 <スクリプト> /home/loach/<作業名> build/debug/bios-dev/dev-only-not-compatible-syscard3.pce
```

スクリプトの出力は `build/debug/bios-dev/dev-bcd-full-sweep.json` に書かれ、`PASS`/`FAIL` の 1 行と入力件数が出る。

## 4. ハッシュと版の記録

| 項目 | 値 | 取得状況 |
|---|---|---|
| main | `c906728609c61945ef72ae020ddeb43db5eb5e4e` | 確認済み（origin/main と一致） |
| 対象ソース `src/ex_binbcd.s` | `6446e629f9465f7d7fa34341f1390f63809d6771f7b4ee85432a8acd6e2aa72b` | sha256 確認済み |
| 対象ソース `src/overrides/slot_3C.s` | `15f35162574abdac75584b1c75493a679bde528015aa842a7c1e9bd79d89803a` | sha256 確認済み |
| `tests/integration/geargrafx_dev_bcd.py` | `9ed631c8210aea7b69cfabbf0046aaf616a6b0ec7bd832a2341e50afc179ef31` | sha256 確認済み |
| `spec/dev_implementations.json` | `d94ac5ff5ffb3fd0cbd487425b82bdad780548bb79b4455ae47ba580b4361638` | sha256 確認済み |
| Geargrafx 固定 revision | `b49ae82a012eade566d72e9d61c9d95a5caa4da3` | `tools/dependencies.json` より |
| cc65 固定 revision | `71746c829e77f74c2b601a7d16821170c2610df3` | `tools/dependencies.json` より |
| DEV ROM sha256 | 未計測 | DEV ROM 未ビルド（第 5 節） |
| manifest.json の rom_sha256 | 未計測 | 同上 |

## 5. 実行できなかった理由

本作業は worktree 隔離のエージェントとして動作しており、`wsl.exe` の実行が環境のガードにより拒否された（「worktree 隔離のエージェントの git 操作は自 worktree に限る」）。Geargrafx コアと cc65 は WSL 上でしか動かないため、DEV ROM のビルドも実コア実行もできなかった。ガードを回避する手段は取っていない。

DEV ROM のビルドは `src/` と生成テーブルのみを入力とし、`rom/` は読まない（`tools/build_rom.py`）。`rom/` の内容は本作業で一切開いていない。

## 6. CI 状況（main、2026-10-21 時点のスナップショット）

`gh run list --branch main` の結果:

| PR | CI (original fixture) | BIOS DEV diagnostics |
|---|---|---|
| #432（c906728） | success | in_progress |
| #431（49cc084） | success | in_progress |
| #426 | success | in_progress |
| #411 | success | success |

c906728 の BIOS DEV diagnostics は完了していない。完了後の結果を確認してから承認判断に使うこと。

## 7. 独立レビュー

レビュー担当: 新規の general-purpose エージェント（レビュー専用、コード・契約・既存証拠のみを渡し、本書の結論は渡さない）。

判定: **INSUFFICIENT_NEEDS_FULL_RUN**（全入力の DEV 入口実行が必要）

主な指摘（レビュー担当の所見、要約）:

1. ソースは契約どおり。0..99 は有効、100..255 は A 不変・C=1 で、拒否経路は PHX を実行しない。ロジックの誤りは見つからなかった。
2. 既存の DEV 入口テストは P の C ビットのみ検査し、D（bit 3）と I（bit 2）を検査しない。
3. 9 入力は十の位 2, 3, 5, 6, 7, 8 を直接通らない。十の位に依存する誤りは既存テストで検出されない。
4. DEV 入口経路の証拠は既存の 9 入力のみ。256 入力の実行は HuCARD プローブ上であり、DEV 入口（`$E0B4`）ではない。
5. ホストテストは Python モデルとバイト検査のみで、アセンブル後のコードは実行しない。
6. `docs/ex-binbcd-internal.md` の 33-34 行は 0..255 の実コア照合を「PASS」と記すが、同文書 24 行の「本 PR 自体は実コア PASS を主張しない」と矛盾する。DEV 入口の全入力実行は存在しない。
7. `docs/ex-binbcd-internal.md` 3 行の「ROM ジャンプテーブルには接続していない」は、`src/overrides/slot_3C.s` の JMP と矛盾する古い記述。
8. `spec/dev_implementations.json` の注記「DEV ROM implemented and real-core tested」は、DEV 入口の 9 入力実行に比べて強すぎる。
9. 参照 ABI の項目（UNKNOWN / HYPOTHESIS）、`approval` の null、System Card 互換の表現なし、はいずれも正しい。

本 PR は文書のみのため、上記 6〜8 の修正は行わない（台帳・契約文書の変更は別 PR でオーナー判断）。

## 8. 未解決の論点（オーナーへの質問）

1. 全入力スイープ（256 件）の実行をもって DEV_CALLABLE の実コア根拠とみなしてよいか。既存の 9 入力テストを正式な根拠に含めるか。
2. D=1 で呼ばれた場合は対象外とする契約で足りるか。呼び出し側の責任とする場合、ledger か API 文書にその旨を明記するか。
3. N/Z/V を未規定のままにしてよいか。
4. 範囲外（100..255）の動作（A 不変、C=1）を DEV 契約の一部として承認するか。
5. 既存の `docs/ex-binbcd-internal.md` の「PASS」記述、3 行目の古い接続記述、台帳の「real-core tested」注記を、承認前に修正するか（別 PR）。

## 9. 承認の状態

- `spec/dev_implementations.json` slot id 60: `approval.approved_by = null`、`approval.review_ref = null`、`state = NOT_READY`。本書はこれを変更していない。
- 承認はオーナーのみが行う。本書の作成者は承認を主張しない。
