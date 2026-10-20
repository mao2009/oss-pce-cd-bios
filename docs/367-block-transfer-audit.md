# Issue #367: ブロック転送（TII/TIN/TDD/TIA/TAI）とデバイス起点書込みの事前遮断監査

## 目的と状態

親 #173（V04: ブロック転送、V07: デバイス起点書込み）の監査。合成 ROM・合成入力のみを対象とする。
**状態: BLOCKED / release_eligible=false。** slot `$01` と全スロットのゲートは開けない。

## 結論（要約）

- 5 命令はいずれも **1 命令内で length 回のバイト書込みを行う**（複数書込み）。静的な範囲検査では、書込み先が許可域外に届くかを命令実行前に判定できる（`tools/block_xfer_audit.py`）。
- **命令途中で停止できるかは未実測（UNMEASURED）。** 実コア（pinned Geargrafx）での実測は本ワーカーでは未実施。WSL は worktree 隔離エージェントから拒否された（NOT_RUN）。
- 静的検査の結果は PASS ではない。`gate` は常に BLOCKED。
- デバイス起点 RAM 書込みの監査は未実施（W17 は UNKNOWN のまま）。

## 実装したもの（スタンドアロン）

| ファイル | 内容 |
|----------|------|
| `tools/block_xfer_audit.py` | 5 命令の書込み先集合（過大近似）、MPR から許可述語を作る関数、静的判定。出力は REFUSED_STATIC / PERMITTED_STATIC_ONLY と gate=BLOCKED のみ |
| `tests/host/test_block_xfer_audit.py` | 正例・負例（窓外、ハードウェアページ、ROM バンク、TDD 下方向、16bit ラップ）・変異例（ハードウェア判定の欠落、窓の欠落、TDD 方向）。16 件 |
| `docs/367-block-transfer-audit.md` | 本書 |

既存の実コアプローブ（`tests/integration/`）、`spec/`、共有フック（#365）、DEV ROM ジャンプテーブルには一切触れていない。どのルーチンも DEV ROM に配線していない（配線数 0）。DEV_CALLABLE も主張しない。

## ブロック転送の書込み先モデル

| 命令 | 書込み先の動き | モデル | 未確認事項 |
|------|----------------|--------|------------|
| TII | dst を +1（src も +1） | 厳密（16bit ラップ） | 実コアでの確認なし |
| TDD | dst を -1（src も -1） | 厳密（16bit ラップ） | 同上 |
| TIN | dst 固定（src は +1） | 厳密 | 同上 |
| TIA | dst が交互（src は +1） | dst..dst+length の過大近似 | 交互の順序・基準の進み方は未確認 |
| TAI | src が交互（dst は +1） | dst の集合は TII と同じ | 同上 |

- length 0 は最悪ケース（0x10000 バイト）として扱う。長さ 0 の規則は未確認。
- 過大近似のため、拒否は保守的（過剰拒否はありうる）。許可が楽観的になることはない。

## 受入条件ごとの状況

| 受入条件 | 状況 | 根拠 |
|----------|------|------|
| 5 命令の命令途中停止の実測 | **NOT_RUN / BLOCKED**（未測定。probe に TII 等の実行モードがない） | `tests/integration/geargrafx_preside_probe.py` の V04 は NOT_RUN |
| 各バイト書込みが許可外（ハードウェアページ・ROM バンク）に届かない（負例） | **静的のみ**（host モデル。実コアでは未検証） | `tests/host/test_block_xfer_audit.py` |
| デバイス起点 RAM 書込みの監査列挙 | **未実施**（W17 は UNKNOWN） | 下記 |
| 変異テストで偽 PASS が出ないこと | **host 変異テスト 3 件が成功（静的のみ、実コア証拠ではない）** | `MutationTest` 3 件 |

## デバイス起点 RAM 書込み（W17）

本 Issue では実コアソースを読まず、列挙も行っていない。既知の参照は `docs/cd-reset-contract.md` のみ。
CD/ADPCM 転送の書込み経路は **未監査（UNKNOWN）**。遮断可否は判定できない。

## 残課題と BLOCKED 理由

BLOCKED 理由:
1. 命令途中で停止するかが未実測（実コア実測は WSL 拒否のため NOT_RUN）。
2. 実行時オペランドの復号（src/dst/length）が未配線。静的検査は入力が既知のときのみ使える。
3. デバイス起点書込みが未監査。

解除条件:
- 実コアで 5 命令それぞれに対し、許可外書込みが各バイトで着地しないこと、および命令途中の停止有無を WSL 実行で記録する（`tools/jsr_observation.py` のスキーマ）。
- TIA の交互順序・長さ 0 の規則を実コアで確認し、過大近似を正確な集合に置き換える。
- デバイス起点の RAM 書込み経路を列挙し、各経路の遮断可否を判定する（別 Issue）。

次の候補: 命令途中停止の実測（V04 専用の probe モード追加。共有フックは #365 の担当範囲のため、新規ファイルで実装する）。
