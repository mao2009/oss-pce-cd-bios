# Issue #367: ブロック転送（TII/TIN/TDD/TIA/TAI）とデバイス起点書込みの事前遮断監査

## 目的と状態

親 #173（V04: ブロック転送、V07: デバイス起点書込み）の監査。合成 ROM・合成入力のみを対象とする。
**状態: BLOCKED / release_eligible=false。** slot `$01` と全スロットのゲートは開けない。

## 結論（要約）

- 5 命令はいずれも **1 命令内で length 回のバイト書込みを行う**（複数書込み）。静的な範囲検査では、書込み先が許可域外に届くかを命令実行前に判定できる（`tools/block_xfer_audit.py`）。
- **実コアでの書込み前遮断は実測対象になった（旧記述の NOT_RUN は置き換え）。** `tests/integration/geargrafx_block_write_guard.py`（`make block-write-guard`、`make integration` に含まれる）が pinned Geargrafx（b49ae82）と検証専用パッチ付きの実コアで、TII/TIN/TIA/TDD/TAI の 1 バイト転送を合成メディアで実行する。期待 PASS は 15 ケース（各命令 3 種: 拒否・フックなし対照・許可）。
- **命令途中で停止できるかは未実測（UNMEASURED）のまま。** driver は書込み前の拒否のみを測る。
- 試験の拒否方針（書込み先が $FF ページの場合に拒否）は probe の中に書かれており、本番ゲートの証拠ではない。driver は「コアのフック点が書込み前に拒否できる」ことだけを示す。
- 静的検査の結果は PASS ではない。`gate` は常に BLOCKED。
- デバイス起点 RAM 書込み（W17）はソース列挙済み（#419、`docs/research/367-device-write-audit.md`、`tests/fixtures/367_device_write_sites.tsv`）。検索範囲では CPU 以外が実行時に WRAM へ書く経路は見つからなかった。UNK-01/02 の起動元は未確定、判定は未了、実コアでのデバイス起点書込みの実測はない（UNMEASURED）。

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
| 5 命令の命令途中停止の実測 | **未実施（UNMEASURED）**。driver は命令内の書込み前拒否のみを測り、途中停止は測らない | `geargrafx_block_write_guard.py` の LIMIT 行 |
| 書込み先が $FF ページの 1 バイト転送が書込み前に拒否される（負例） | **実コアの driver で検証対象**（`*-forbidden-armed` 5 件 + 対照 `*-forbidden-unguarded` 5 件）。判定条件は VDC AR 不変（結果はこの PR では記録していない） | `tests/integration/geargrafx_block_write_guard.py` |
| 許可域（WRAM）への 1 バイト転送が届く（正例） | **実コアの driver で検証対象**（`*-allowed-armed` 5 件） | 同上 |
| 複数バイト・他バンク・TIA/TAI の交互順序 | **静的のみ**（host モデル。実コアでは未検証） | `tests/host/test_block_xfer_audit.py` |
| デバイス起点 RAM 書込みの監査列挙 | **ソース列挙済み（#419、検索範囲内）。判定は未了**。UNK-01/02 は起動元未確定、実コアでのプローブ未実施 | 下記、`docs/research/367-device-write-audit.md` |
| 変異テストで偽 PASS が出ないこと | host 変異テスト 3 件（静的のみ）に加え、実コア driver が armed 判定に変異検査を適用する | `MutationTest` 3 件、driver の `mutation_test` |

## デバイス起点 RAM 書込み（W17）

#419 で pinned Geargrafx（b49ae82）のソースを読み取り、検索語による範囲で列挙した（実行なし。詳細は `docs/research/367-device-write-audit.md`）。記録は 9 行:

- デバイス起点（DEV-01〜03）: ADPCM RAM、VRAM、VDC 内部 SAT。いずれも WRAM には届かない（NOT_MAIN_RAM）。
- リセット時の初期化のみ（DEV-04〜07）: WRAM と CDROM RAM（RESET_ONLY）。
- 起動元未確定（UNK-01/02）: 外部バックアップ RAM（`mb128.cpp`）。Arcade Card の書込み起動経路も未トレース（UNKNOWN）。

限界: 検索語で見つけた箇所に限られ、全ファイルは精読していない。間接書込みは除外できていない。
判定の状態は「列挙済み・判定未了」であり、遮断可否は判定していない。デバイス起点書込みを実コアでプローブした記録はない（`geargrafx_block_write_guard.py` の LIMIT も「device-originated write probe なし」と記す）。

## 残課題と BLOCKED 理由

BLOCKED 理由:
1. 命令途中で停止するかが未実測。書込み前拒否は実コア driver で測定対象になった（上記）。
2. 実行時オペランドの復号（src/dst/length）が未配線。静的検査は入力が既知のときのみ使える。
3. デバイス起点書込みの判定が未了（列挙は #419 で済み、UNK-01/02 の起動元は未確定、実コアでのプローブは未実施）。

解除条件:
- 実コアで 5 命令それぞれについて、複数バイト・他バンクを含めて許可外書込みが着地しないことを記録する。命令途中の停止有無も WSL 実行で記録する（`tools/jsr_observation.py` のスキーマ）。
- TIA の交互順序・長さ 0 の規則を実コアで確認し、過大近似を正確な集合に置き換える。
- UNK-01/02 の起動元を確定し、デバイス起点の各経路の遮断可否を判定する（別 Issue）。

次の候補: 命令途中停止の実測（V04 専用の probe モード追加。共有フックは #365 の担当範囲のため、新規ファイルで実装する）。
