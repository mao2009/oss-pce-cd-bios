# EX_RND 内部乱数ルーチン（本プロジェクト独自仕様）

Issue #72。`src/ex_rnd.s` の `ex_rnd_next` / `ex_rnd_seed` は本プロジェクトが独自に定めた契約で動く HuC6280 ルーチンである。**System Card の slot `$3E` / 候補 `$E0BA` の ABI ではない**。純正 EX_RND の契約（seed、状態保持、更新規則、戻り値）は未確認（Spec=HYPOTHESIS）のままで、本ルーチンの検証結果はその根拠にならない。src/ex_rnd.s は ROM ジャンプテーブルには接続していない。なお DEV ROM の slot `$3E`（`src/overrides/slot_3E.s`、候補 `$E0BA`）には、Y=1 で seed、それ以外の Y で next を行う別の DEV 契約（状態 `$2500/$2501`）を接続している。こちらも独自仕様で、純正 EX_RND の ABI ではない。

## 二つの実装の関係

- `src/ex_rnd.s`：独立した HuCARD プローブ用ルーチン。BSS の `ex_rnd_state`（2 バイト）に状態を保持し、`ex_rnd_seed` / `ex_rnd_next` をエクスポートする。ROM ジャンプテーブルには接続しない。`tests/fixtures/ex_rnd_probe.s` と組み合わせてプローブ ROM を構成し、`tests/integration/geargrafx_ex_rnd.py` で実コア検証する。
- `src/overrides/slot_3E.s`：DEV ROM の slot `$3E` に接続するオーバーライド実装。Y=1 で seed、それ以外の Y で next を行う DEV 契約で、状態は work RAM `$2500/$2501` に保持する。`make dev-integration` の `geargrafx_dev_ex_rnd.py` で実コア検証する。

両者は同じ LFSR（taps `$B400`、8 ステップ/呼び出し、零状態を 1 に置換）を使うが、状態の格納場所と呼び出し規約が異なるため、一方の検証結果を他方の ABI 証拠としては扱わない。

## 契約（独自）

| 項目 | 内容 |
|---|---|
| 状態 | 16 ビット Galois LFSR。BSS の `ex_rnd_state`（2 バイト、下位バイトが先） |
| 更新規則 | 1 ステップ = 右シフト、ビット 0 が 1 だったら XOR `$B400`。1 呼び出しで 8 ステップ |
| `ex_rnd_seed` | 入力 A = seed 下位、X = seed 上位。状態をそのまま格納。戻り値なし |
| `ex_rnd_next` | 出力 A = 8 ステップ後の状態の下位バイト。X, Y を保存。D は触らない |
| 零状態 | 状態が `$0000`（明示的な seed 0 やゼロクリア済み RAM）の場合に限り、更新前に 1 とみなす。非ゼロの未初期化 RAM はその値から進む |
| リセット時 | ルーチンはリセットで状態を初期化しない。使う前に `ex_rnd_seed` を呼ぶこと。未シードでの出力は未定義。RAM に非ゼロの値が残っていると出力列はその値に依存するため、必ず使用前に `ex_rnd_seed` を呼ぶこと |
| 周期 | 状態の周期は 65535（最大）。`$0000` には到達しない |
| 再現性 | 同じ seed からは毎回同じ出力列。seed 0 と seed 1 は同じ列 |
| 未規定 | 戻り時の N/Z/V |
| 副作用 | RAM は `ex_rnd_state` の 2 バイトのみ。IO アクセスなし。スタック使用 2 バイト（PHX + JSR） |

## 独自仕様として未確認の項目

- 純正 EX_RND の seed 入力方法、状態の格納場所、更新規則、戻り値の形式（すべて未確認）。
- 純正の呼び出し規約（A/X/Y/P の入出力、`$20F8-$20FF` の作業値の使用、ページ・MPR 前提）。
- 本ルーチンはこれらのいずれにも従っていない可能性がある。

## 検証

- ホストテスト（`make test` の対象）: `tests/host/test_ex_rnd.py`。Python モデル `tools/ex_rnd.py` の周期 65535、零 seed の扱い、同 seed 再現性を確認する。プローブ ROM のバイト固定（`EOR #$B4` の位置、リセットベクタ）も確認するが、アセンブルしたルーチンは実行しない。
- 振る舞いの検証（実コア、opt-in）: `tests/integration/geargrafx_ex_rnd.py`。HuCARD プローブ `tests/fixtures/ex_rnd_probe.s` が 3 フェーズ（零状態、seed `$1234`、seed 0）で各 256 回の出力を RAM に記録し、ホストモデルと照合する。X の保存も各呼び出しで確認する。`EOR #$B4` を `EOR #$B5` に変えた変異 ROM が不一致になることも確認する。Geargrafx の結果は実機の挙動を証明しない。
- `src/ex_rnd.s` は独立したプローブ用ルーチンであり、ROM ジャンプテーブルには接続しない。`src/overrides/slot_3E.s` は同じ LFSR 契約を DEV ROM の slot `$3E`（候補 `$E0BA`）へ接続するオーバーライド実装である。両者はアルゴリズムを共有するが、配置・呼び出し規約・状態アドレス（`src/ex_rnd.s` は BSS `ex_rnd_state`、slot `$3E` は work RAM `$2500/$2501`）が異なる。
- slot `$3E` の実行証拠は `tests/integration/geargrafx_dev_ex_rnd.py`（pinned Geargrafx、DEV ROM）で取得する。`make dev-integration` に登録済み。
- ABI は UNVERIFIED のままであり、ROM は DEV ONLY（`release_eligible: false`）。
