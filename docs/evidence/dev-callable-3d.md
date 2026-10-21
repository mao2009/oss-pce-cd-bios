# DEV_CALLABLE 承認証拠: EX_BCDBIN（スロット $3D）

- 状態: **オーナー判断待ち**。本文書は証拠の提示であり、承認ではない。
- 承認欄（`spec/dev_implementations.json` の id 61 `approval`）は**変更していない**（`approved_by: null`、`review_ref: null`）。
- 対象の契約は本プロジェクト独自の DEV 専用 ABI（`docs/ex-bcdbin.md`）であり、System Card 互換、Reference BIOS ABI VERIFIED は含意しない。
- 関連: Issue #442（本証拠の作成）、#324（ABI 証拠の 3 軸分離、オーナー判断）、#428 / PR #432（$3D の DEV 接続）。

## 1. 対象と固定版

| 項目 | 値 |
| --- | --- |
| main | `c906728609c61945ef72ae020ddeb43db5eb5e4e`（PR #432 マージ） |
| DEV ROM | `build/debug/bios-dev/dev-only-not-compatible-syscard3.pce`、SHA-256 `d669c03f46b5e73b913776c50e2620a7d73584e42a5115febcc3fc5fb65f8e65`（262144 バイト） |
| manifest | `build/debug/bios-dev/manifest.json`、SHA-256 `791beb1b1275a54ae40c8ffe17ee7cf9ab07eb468c3c6c8ff14fdfa034fb88c3` |
| Geargrafx（pinned） | `b49ae82a012eade566d72e9d61c9d95a5caa4da3`（`tools/dependencies.json`） |
| cc65（pinned） | `71746c829e77f74c2b601a7d16821170c2610df3` |
| 実行環境 | Ubuntu（WSL2）、clean clone of main `c906728`、ビルドは `make dev-rom`、`TOOLS_DIR=/home/loach/oss-pce-cd-bios-tools` |
| `src/ex_bcdbin.s` | SHA-256 `b38a14aa873a8552288062cb5dd1a23f71e95211cadc4c5b048c0d1b3005f858` |
| `src/overrides/slot_3D.s` | SHA-256 `94c79485ae2f6b9b3d393241a7df095fdc15ecc3dc2f8deb2a9e9eff893446da` |
| `spec/dev_implementations.json` | SHA-256 `6f6b3a4c7d5b47123c2b764c5af53bf807bb513c2acd6f30806a96a901c9362e` |
| `spec/api_contracts.json` | SHA-256 `b2fb8da1da1cc3d5827a2025bb34591966a324bc57117162f0d7596309b12f28` |

DEV ROM の manifest は `source_sha256` で `src/overrides/slot_3D.s` と `include:src/ex_bcdbin.s` の値を持つ。上の 2 ファイルの SHA-256 と一致している。

### 1.1 再確認（本 PR のブランチ、2026-10-21）

初版の 1 節は main `c906728` の記録だが、main は `0c2fa41` に進んでいる。本 PR のブランチ
（`docs/evidence/dev-callable-3d`、HEAD `5281fc96dec0ad86d3b53c04451508f3f6e017d8`、main `0c2fa41` を
マージ済み）で再ビルドし、3.2 節に再実行の結果を記録した。

| 項目 | 初版（main `c906728`） | 再確認（本 PR のブランチ） |
| --- | --- | --- |
| DEV ROM SHA-256 | `d669c03f46b5e73b913776c50e2620a7d73584e42a5115febcc3fc5fb65f8e65` | `5c6aaf0a789787f8749843d7b3d11c3bd868e1437a3e1422afce214095480f8e` |
| manifest SHA-256 | `791beb1b1275a54ae40c8ffe17ee7cf9ab07eb468c3c6c8ff14fdfa034fb88c3` | `83d40fc948d8124268d23bfe9f55dd8e96269e7e03400406fe8e17363ce8124e` |
| `src/ex_bcdbin.s` | `b38a14aa873a8552288062cb5dd1a23f71e95211cadc4c5b048c0d1b3005f858` | 同じ（main で変更されていない） |
| `src/overrides/slot_3D.s` | `94c79485ae2f6b9b3d393241a7df095fdc15ecc3dc2f8deb2a9e9eff893446da` | 同じ（main で変更されていない） |
| `spec/dev_implementations.json` | `6f6b3a4c7d5b47123c2b764c5af53bf807bb513c2acd6f30806a96a901c9362e` | `81c6a02f4742462020198260d8d46fd767c1bc068bcaba0023dd775d04ec64cb`（main の更新分。承認欄は未変更） |
| `spec/api_contracts.json` | `b2fb8da1da1cc3d5827a2025bb34591966a324bc57117162f0d7596309b12f28` | 同じ |

再確認時の DEV ROM の SHA-256 は `build/debug/bios-dev/manifest.json` の `rom_sha256` と一致し、
manifest の `source_sha256` の 2 ファイル値も上の SHA-256 と一致する。

## 2. 契約（DEV 専用、docs/ex-bcdbin.md）

- 入力: A = packed BCD 1 バイト（上位ニブル = 十の位、下位 = 一の位、`$00`〜`$99`）。
- 前提: D = 0。
- 正常（両ニブル ≤ 9）: C = 0、A = 二進値 0〜99。
- 不正（どちらかのニブル > 9）: C = 1、A = 入力値のまま（156 入力）。
- 保存: X、Y は保存。スタックは均衡し、スタック以外のメモリは書かない（PHX/PHY の 2 バイトのみ）。
- 未定義: N / Z / V。

## 3. 実コア全件実行

**駆動スクリプトは本 PR でリポジトリに登録した**（`tests/integration/dev_bcdbin_full_sweep.py`、
`make dev-bcdbin-sweep`）。初版は本リポジトリに含めず WSL のローカルに置いていた（第 8 節）。
登録済みなので、第三者は任意のチェックアウトから同じ全件実行を再現できる。

| 項目 | 結果 |
| --- | --- |
| 入力 | 全 256 値（有効 100、不正 156） |
| 条件 | 18 通り = 入力 C（0/1）× (X,Y)（`$5A/$A5`、`$00/$FF`、`$FF/$00`）× 呼出時 SP（`$FF`、`$80`、`$C0`） |
| 実行数 | 4608（= 256 × 18）。有効 1800、不正 2808 |
| 結果 | **PASS 4608 / FAIL 0** |
| 所要 | 初版 約 200 秒（WSL、pinned core、逐次）／再確認 約 66 秒（8 並列、本 PR のブランチ） |

各実行で次を確認した（cold と warm の両方）。

- A と出力 C（P のビット 0）が契約値と一致する。
- X と Y が入力値のまま保たれている。
- 復帰 PC が呼出後の停止ループの番地と一致する。
- 停止時の SP が呼出時の SP と一致する（スタック均衡）。
- MPR1 = `$F8`、MPR7 = 0 のまま変化していない。

### 3.1 駆動スクリプト（本 PR で登録）

| 項目 | 値 |
| --- | --- |
| スクリプト | `tests/integration/dev_bcdbin_full_sweep.py`（SHA-256 `1e578f2861a4c34ad017a12d2d9ace6a16a4d6f5645056e0aace2571bf2c406c`） |
| 起動方法 | `make dev-bcdbin-sweep`（または `python3 tests/integration/dev_bcdbin_full_sweep.py [ROM パス]`） |
| CI | `make dev-integration` から呼ばれるため、CI の `bios-dev` ジョブでも実行される（CI の実測: `bios-dev` ジョブ success、7 分 35 秒） |
| 移植性 | リポジトリルートはスクリプト自身の位置から求める。特定の作業ディレクトリ（初版の `/home/loach/dev3d-evidence/`）への依存はない |
| 並列 | 既定で `min(8, CPU 数)` 個のプロセスで実行する（`--jobs` で変更可）。プローブは 1 実行 1 プロセスなので並列化の影響は互いに独立。各ワーカースレッドは自分専用の ROM ファイルを使う（スレッド ID でファイル名を分ける。案件番号でファイルを選ぶと、2 つのスレッドが同じファイルを書き換え、プローブが書き込み途中の画像を読むことがある）|
| 出力 | 集計 JSON は `build/debug/bios-dev/dev-bcdbin-full-sweep.json`（ビルド成果物のため git には含めない） |

### 3.2 再確認（本 PR で実行、2026-10-21）

- 実行場所: 本 PR の作業ツリー（ブランチ `docs/evidence/dev-callable-3d`、main `0c2fa41` マージ済み）
- 手順: `make dev-bcdbin-sweep`（`make dev-rom` でビルド → `tests/integration/dev_bcdbin_full_sweep.py`）
- 実行の再現: 初版の記録（マージコミット `5281fc9` のツリー）と、スレッドごとの ROM ファイル割り当てに修正した後の `7e94fc6` のツリーで**それぞれ 4608 実行が失敗 0**。修正は並列実行のファイル競合の排除だけで、判定項目・判定基準・条件は同一である。
- 結果行: `PASS DEV ROM EX_BCDBIN $3D full domain: 4608 runs (18 conditions x 256 inputs), 0 failures`
- 集計: 入力 256、条件 18、実行 4608、有効 1800、不正 2808、失敗 0、失敗ラベルなし
- 各実行で cold と warm の両方を観測した（A/C/X/Y、復帰 PC、SP 均衡、MPR1=$F8、MPR7=0）
- DEV ROM SHA-256: `5c6aaf0a789787f8749843d7b3d11c3bd868e1437a3e1422afce214095480f8e`（manifest の `rom_sha256` と一致）
- 所要時間: 約 66 秒（8 並列）

初版（3 節の表）と再確認（3.2 節）は判定項目・判定基準が同じである。並列化したのは実行時間のためだけで、
判定ロジックは変更していない。

### 3.3 入力クラスの扱い

- 実行していない入力クラスは**なし**（契約上の 256 入力は全件）。
- 契約上の未定義・対象外として実行していないもの:
  - D = 1 での動作（契約で未定義）。
  - 割込み（IRQ / NMI）が処理中に入る場合（DEV ROM の呼出し経路では試験していない）。
  - 条件の全組み合わせ（X/Y は 65536 × 65536 の組に対し 3 組、SP は 3 値のみ）。X/Y と SP は代表値のみである。
- 入力 C の全値（0/1）、X/Y と SP の代表値は実行している。

## 4. 負例

| 負例 | 結果 | 根拠 |
| --- | --- | --- |
| 不正 BCD 入力 156 件（`$0A`〜`$0F`、`$1A`〜`$1F`、…、`$A0`〜`$FF`） | 契約どおり拒否（C = 1、A 不変）。**2808 実行すべて PASS** | 第 3 節 |
| 3C エントリ（EX_BINBCD）に 3D の復号期待値（`$42` → 42、C = 0）を当てた誤判定 | 検出された（期待と一致せず拒否） | `geargrafx_dev_bcd.py`（既存、PASS） |
| 3D エントリ（EX_BCDBIN）に 3C の符号化期待値（`$42` → `$66`）を当てた誤判定 | 検出された | 同上 |
| 条件マトリクスの負例 6 件（誤った期待値 2、ROM 変異 4） | すべて拒否（6/6） | `geargrafx_bcdbin_conditions.py`（既存、PASS） |
| 実コア変異 3 種（`CMP #10` → 11、`CMP #$A0` → B0、`ADC #10` → 11） | すべて検出 | `geargrafx_bcdbin.py`（既存、PASS） |

注: 変異試験と条件マトリクスは `tests/fixtures/` のハーネスで走り、DEV ROM イメージ自体ではない。DEV ROM の 3D エントリを通す試験は `geargrafx_dev_bcd.py`（12 入力、C = 0 固定）と第 3 節の全件実行である。

## 5. PASS 行（ログからの抜粋）

- `PASS real Geargrafx DEV ROM: EX_BINBCD slot $3C 9 inputs; EX_BCDBIN slot $3D 12 inputs; A/C/X/Y, SP, MPR checked; 2 per-API wrong-entry negatives detected`（`geargrafx_dev_bcd.py`、終了コード 0）
- `PASS: 256/256 records passed; negative controls refused 6/6; core b49ae82a…`（`geargrafx_bcdbin_conditions.py`、終了コード 0）
- `Ran 2 tests`、`OK`（`geargrafx_bcdbin.py`、終了コード 0）
- `Ran 68 tests`、`OK`（`tests/host` の `test_api*`、終了コード 0）
- `Ran 3 tests`、`OK`（`tests.host.test_ex_bcdbin`、終了コード 0）
- `PASS real Geargrafx System Card diagnostic: …`（`geargrafx_api.py`、DEV ROM に対する既存の診断。3D 契約とは別。終了コード 0）
- `PASS DEV ROM EX_BCDBIN $3D full domain: 4608 runs (18 conditions x 256 inputs), 0 failures`（`dev_bcdbin_full_sweep.py`、本 PR で登録した全件スイープ。終了コード 0）

ログの SHA-256（先頭 16 桁）: `dev_bcd` `fd1dedccead34867`、`conditions` `dd5155e6d57e0712`、`bcdbin_core` `0b17b9f61203c0e6`、`host_api` `3e58f2d7decf96d1`、`host_bcdbin` `0db32b05a314db79`、`api_integration` `c82ec768c21d2171`。

## 6. CI 状況

- 次の run 番号は初版（2026-10-21、main `c906728` 時点）の記録であり、main はその後 `0c2fa41` に進んでいる。最新の状態は PR 側の run を確認すること。
- main `c906728` の push 実行: `CI (original fixture, not BIOS compatibility)` = success（run 39551289738）、`BIOS DEV diagnostics (not compatible firmware)` = success（run 39551289760）。
- PR #432 の検査（マージ前の head）: `bcdbin-core` pass（run 39550711937）、`bios-dev` pass（run 39550711920）、`core-harness` pass（run 39550711915）、`verify` pass（run 39550711924）、`guard-*` pass。CodeRabbit は手動レビュー要求のためスキップ。
- `core-bcdbin.yml` は `pull_request` と `workflow_dispatch` のみで動くため、main への push では実行されない。main 上の実コア ex_bcdbin 実行は PR #432 の検査で確認した。
- 本 PR で 3.1 節の全件スイープを `make dev-integration` に組み込んだため、CI の `bios-dev` ジョブ（`api-source.yml`）が PR ごとに 4608 実行を再現する。

## 7. 独立レビュー

- レビュー担当: 新規の general-purpose エージェント（コードと証拠のみを渡し、本文書の結論は渡していない）。
- 判定（ルーチン）: **CORRECT_FOR_SPEC**。境界（`CMP #10`、`CMP #$A0`）、ADC による十の位の加算、C の扱い、PHX/PHY と PLY/PLX の均衡、メモリ書込みなしを確認した。欠陥は見つからなかった。
- 判定（証拠パッケージ）: **NEEDS_WORK**。理由は文書の不正確さ 2 点。
  1. `docs/ex-bcdbin.md` の 21 行目「実コア負例 5 件を含む」は `tests/host/test_ex_bcdbin.py` の内容と合わない。33 行目の `RealCoreFailurePathTests` は `tests/integration/test_ex_bcdbin_real_core.py` にある（host ではない）。
  2. `docs/ex-bcdbin.md` の 43 行目で「メモリ非書込」を「確定（実コア検証済み）」としているが、実コアで確認したのは SP と結果領域のみで、メモリ非書込はコード読みによる。
- 本 PR で上記 2 点を修正した（`docs/ex-bcdbin.md`）。修正内容は 8 節に記載する。
- 本改訂（スクリプトの登録・再実行・基準更新）では、独立レビューは実施していない。上の判定は初版作成時点の記録であり、再レビューが必要ならオーナーの判断で行う。

## 8. 未了・不足

- 駆動スクリプトは**本 PR でリポジトリに登録した**（`tests/integration/dev_bcdbin_full_sweep.py`、3.1 節）。初版のスクリプトは WSL の `/home/loach/dev3d-evidence/` に置かれており（SHA-256 `97074FB617B02CDB95051F9CEC3E347AD9EB1CA2BAE042C0E86D50299DBD2E79`）、その Regeneration ではなく登録版を正とする。判定項目・条件・期待値は初版と同じで、並列化と移植性（リポジトリルートの自動解決）のみが変わっている。
- 全件の結果 JSON は登録スクリプトが `build/debug/bios-dev/dev-bcdbin-full-sweep.json` に出力する（ビルド成果物のため git には含めない）。初版のローカル JSON の SHA-256 は `4ce7b3e0bff30a4ef15177555b03bc6d29080e7e0553954c569d30e4d3f1fd45`。
- 3.2 節の再実行は CI 上でも再現される（`make dev-integration` 経由）。初版の実行は CI ではなくローカル WSL のみであった。
- Windows では DEV ROM の実コア試験は動かない（Linux 限定）。
- 純正 BIOS の EX_BCDBIN の呼出し・戻り値・破壊レジスタは未確定（api_contracts.json の slot 61 は UNKNOWN）。本証拠はこれを補わない。
- 初版が指摘していた文書の不正確さ 2 点は**本 PR で修正済み**（`docs/ex-bcdbin.md`）：①実コア負例 5 件の所在（`tests/integration/test_ex_bcdbin_real_core.py`、`make integration-test` で実行）、②「メモリ非書込」を実コア検証済みとしない記述。いずれも本 PR の差分に含まれる。

## 9. オーナーへの未決事項

1. $3D を DEV_CALLABLE とするか（DEV 専用 ABI、互換は含意しない、の条件で承認するか）。承認は承認欄の更新を含め、オーナーのみが行う。
2. 第 3 節の全件駆動スクリプトを CI 再現可能な試験として登録するか → **本 PR で完了**（`tests/integration/dev_bcdbin_full_sweep.py` ＋ `make dev-bcdbin-sweep` ＋ `make dev-integration` 経由の CI 実行）。別 PR は不要。
3. 文書の不正確さ 2 点を修正してから承認判断に進めるか → **本 PR で修正済み**（`docs/ex-bcdbin.md`）。
4. 条件の代表値（X/Y 3 組、SP 3 値）で十分とするか。割込み中の呼出しを要件にするか。

## 10. 承認状態

- `spec/dev_implementations.json` id 61: `state: NOT_READY`、`approval.approved_by: null`、`approval.review_ref: null`。**本証拠の作成では変更していない**。
- 承認が必要な判断はオーナーのみが行う。
