# MA_MUL8S 独自実装（Issue #74）

プロジェクト内部ルーチンとして、符号付き 8-bit 乗算を、本プロジェクト独自仕様で
`src/math/ma_mul8s.s` に実装した。**参照 BIOS の呼出規約は UNVERIFIED** で、下表は本プロジェクトの定義にすぎない。
API ジャンプテーブルには接続していない（`release_eligible=false`）。

| 項目 | 本プロジェクトの定義 |
| --- | --- |
| 入力 | A = 被乗数、X = 乗数。2 の補数の符号付き 8-bit（-128..127） |
| 出力 | X:A = 符号付き 16-bit 積の 2 の補数（X 上位、A 下位） |
| オーバーフロー | 値域は -16256..16384（`-128×-128 = $4000`）で 16-bit に必ず収まるため発生しない |
| 保存 | Y と P（PHP/PLP）。呼出側 D=1 でも内部は CLD して 2 進演算 |
| 副作用 | スタック 5 バイト（JSR 分を除く）。ゼロページ・`$20F8-$20FF` は不使用 |
| 前提 | MPR1 が RAM バンク `$F8` を写像し、スタックが論理 `$2100` にあること |

符号なしシフト加算の後、`a<0` なら上位から b、`b<0` なら上位から a を引く（mod 2^16 の恒等式）。

## 検証

`tests/integration/geargrafx_ma_mul8s.py` はルーチンと独自ドライバ（`tests/fixtures/ma_mul8s_driver.s`）を
pinned cc65 で 8 KiB HuCARD テスト ROM にし、pinned Geargrafx libretro コアで実行する。乗算はゲスト CPU が行い、
ホストは要求・stale 埋め・初期化確認値の書込みと RAM 読出しだけを行う。全 65,536 組を host モデルと照合し、各呼出で
Y（`b^$5A`）、S、P（C=b bit0、D=b bit1 を変化）の保存も確認する。負の対照として `a<0` 補正（`sec; sbc $2102,x`）と `b<0` 補正（`sec; sbc $2103,x`）を
それぞれ NOP にした ROM が実コアでちょうど 32,640 組ずつ不一致になることを確認する。
`tests/host/test_ma_mul8s.py` は境界値モデルと判定器の破損検出のみ（実コア非依存）。実コア実行は上記スクリプトを
Linux 固定環境で明示的に実行する（通常の host テスト・既定 CI には含めない）。

2026-10-19 WSL Ubuntu: PASS（exit 0）、65,536 組不一致 0、負の対照（a<0 補正なし・b<0 補正なし）は各々期待どおり FAIL（32,640 組）。
Geargrafx `b49ae82a012eade566d72e9d61c9d95a5caa4da3`（core sha256 `38c9af41e76ab0df0bdd451d537ba770b2d1bc5e4644e9647f21b76170cad52c`）、
cc65 `71746c829e77f74c2b601a7d16821170c2610df3`、テスト ROM sha256 `13a063583cea3d600a4d2bba970b9b3f842b3ce73f5b3dcd42926aea39efa3a5`。
実コア境界値（16 進）: `-128×-128=4000`、`-128×127=C080`、`-128×-1=0080`、`-1×-1=0001`、`-1×127=FF81`、`127×127=3F01`、`0×n=0000`。

2026-10-20 再検証（Worker 04）: `origin/main` 963f3a3 の clean clone（WSL Ubuntu）で `make routine-real-core` を実行し、
MA_MUL8S は PASS（65,536 組不一致 0、負の対照 a<0・b<0 は各 32,640 組で FAIL、期待どおり）。ROM・core・cc65 の sha は上記 2026-10-19 と同一。

## 参照 BIOS ABI との差分（独自仕様の定義と分けて扱う）

`spec/api_contracts.json` の MA_MUL8S 契約は arguments / returns / clobbered がすべて `UNKNOWN`、
エントリ `0xE0C0` は `HYPOTHESIS`（名前の出所は HuCC ヘッダ等）であり、参照 ABI は未確定。

| 項目 | 独自仕様（本ドキュメント） | 参照 BIOS |
| --- | --- | --- |
| 入力レジスタ | A, X | UNKNOWN |
| 出力 | X:A 符号付き 16-bit | UNKNOWN（16-bit か、片側のみか不明） |
| 出力フラグ | 返さない（P を PLP で完全復元するため N/Z は呼出前の値） | UNKNOWN |
| 保存 | Y, P | UNKNOWN |
| 前提メモリ | MPR1 = `$F8`、スタック `$2100` 領域 | UNKNOWN |
| 実行時間 | 未計測（フレーム単位の確認のみ） | UNKNOWN |

未確定の契約項目（すべて UNKNOWN、参照 BIOS の検証なしには埋められない）:
- 引数・戻り値のレジスタ位置、戻り値が 8-bit か 16-bit か。
- 参照 BIOS が返す N/Z/C/V の内容。
- 呼出時の T フラグ（HuC6280 の T ビット）の扱い。ルーチンは `CLT` を行わず、テストも T=0 のみ。T=1 の呼出は未検証。
- 呼出元 MPR 設定が stack 以外の変更を前提とするか。テストは MPR1 = `$F8` 固定のみ。
- 独立エミュレータと実機は NOT_RUN。

未確定: 参照 BIOS の引数・戻り値位置、フラグ/レジスタ保存、タイミングは UNVERIFIED。独立エミュレータと実機は NOT_RUN。
