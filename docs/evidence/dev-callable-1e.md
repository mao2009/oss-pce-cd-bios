# DEV_CALLABLE 承認証拠: slot $1E EX_GETVER

- 対象 Issue: #433（証拠収集）、#324（オーナー判断）
- 基準コミット: 初版は main `c906728609c61945ef72ae020ddeb43db5eb5e4e`。本 PR のブランチ `docs/dev-callable-1e-evidence` は main `0c2fa412995a448052ce7fdedd96fcc1285b5ad1` をマージ済み（マージコミット `cf49557`）。以降の記録はこのブランチでの実測値。
- 承認フィールド: `spec/dev_implementations.json` の slot 30 `approval`（`approved_by: null`, `review_ref: null`）は **本文書では変更していない**。承認はオーナーのみが行う。

## 1. 範囲と前提

- 本文書の「DEV_CALLABLE」は、#324 の標準ルールどおり「DEV ROM の入口を通じて、DEV 専用・自己定義 ABI で呼び出せること」のみを指す。
- System Card 互換、Reference BIOS ABI VERIFIED、実機動作は **主張しない**。
- `spec/api_contracts.json` の EX_GETVER 契約は大半が HYPOTHESIS / UNKNOWN のままである。
- DEV ROM 画像・マニフェストのハッシュは本 PR で **生成済み**（E14 / E15）。初版は Windows ホストに cc65 がなく未生成（NOT_RUN）だった。
- rom/ 配下（独自 BIOS）は本作業で読み取り・公開・実行していない。

## 2. 対象コード

`src/overrides/slot_1E.s`（sha256 `d5d6e9dda8cacca57dd4963cf59f50cc93f16b6dc47386129e627e9a572b0f43`）:

> 初版は sha256 を `8be2efe63c547eb62833cf00e69e3f66860b67363a082daa912159eb7b707176` と記載していたが誤りで、main `c906728` と現在の main のいずれでも実ファイルは `d5d6e9dd…` である（`git show c906728:src/overrides/slot_1E.s` の sha256 と一致する）。ファイルは初版コミット `1de62ad` 以降変更されていない。

```
api_slot_1E:
    ldx #major_version   ; reported System Card major version
    ldy #minor_version   ; reported System Card minor version
    rts
```

本体バイト列: `a2 03 a0 00 60`（LDX #3; LDY #0; RTS）。

旧ヘッダコメントは、固定の I/O 契約を私有の参照 BIOS に対して測定した旨に言及していたが、この記述は取り下げ、現行の `src/overrides/slot_1E.s` のコメントから削除した。この測定の観測値と内部アドレスは、現行の公開文書から取り下げた（オーナー最終裁定、2026-10-28）。これは **測定の由来** の記述であり、DEV ROM 本体は独自に書かれた 5 バイトである。

## 3. 証拠表

| # | 項目 | 方法 / コマンド | 結果 | 判定 |
|---|---|---|---|---|
| E1 | DEV ROM の $1E 入口がテーブルにあり、本体へ到達する | `tests/host/test_api_build.py::test_slot_1e_override_matches_measured_contract`（overrides() == {0x1E,0x3C,0x3D}、テーブル JMP 先が `a2 03 a0 00 60`） | CI `bios-dev` c906728: `Ran 68 tests` / `OK` | PASS |
| E2 | 同一ビルドでの host テスト（DEV ROM 構造・同一性・改ざん検出） | CI `verify`（`make test`）、run 39551289738 | success | PASS |
| E3 | 実コア（Geargrafx）での $1E 呼び出し: X=3, Y=0, A 保持, 戻り | `make dev-integration` → `tests/integration/geargrafx_api.py`（`geargrafx_contracts.py` の IMPLEMENTED_SLOTS: a=0x11, x=3, y=0, sp=255） | CI `bios-dev` c906728 ログ: `PASS real Geargrafx System Card diagnostic: ... slot 1E EX_GETVER returned its measured contract; A/X/Y/P/SP/MPR/RAM observed; NMI NOT_TESTED` | PASS |
| E4 | JSR / RTS と戻り PC | `geargrafx_api.py` / `geargrafx_contracts.py`: 戻り PC = `api_call_return+3`（JSR 3 バイト）、呼び出し中の SP=253、戻り後 SP=255 | 上記 E3 の PASS に含まれる | PASS（PASS 行のみで個別値は未掲載） |
| E5 | SP 不変 | 呼び出し前後の SP=255 | E3 に含まれる | PASS |
| E6 | MPR 不変 | `before['mpr'] == after['mpr'] == MPR`（contracts.py 103 行） | E3 に含まれる | PASS |
| E7 | A/X/Y/P | A: 0x11 保持、X=3、Y=0。P は LDX/LDY により N/Z が変化し、絶対値は固定しない（test docstring） | E3 に含まれる | PASS（P は部分的） |
| E8 | 正常入力 | 上記呼び出し（入力は DEV ROM 入口のみ、引数なし） | E3 | PASS |
| E9 | 境界入力 | 本ルーチンは引数を取らない（引数レジスタを読まない）。境界入力は存在しない | — | NOT_APPLICABLE |
| E10 | 不正入力・改ざん | 本体の 1 バイト改ざん、テーブル JMP の同長再ターゲット、マニフェスト単独改ざんを host で拒否 | `test_identity_accepts_untampered_slot_1e` 系、`test_table_target_change_evades_layout_but_not_identity`、`test_manifest_alone_cannot_make_a_rom_trusted` 等（CI verify/bios-dev で PASS） | PASS |
| E11 | 負例（実コア） | `geargrafx_api.py` の wrong slot target / wrong guest MPR7 負例 | E3 の PASS 行に含まれる | PASS |
| E12 | 実コアの NMI | 契約上 `NOT_TESTED` | — | NOT_TESTED |
| E13 | CI | `CI`（verify, guard-verify）と `BIOS DEV diagnostics`（bios-dev, guard-bios-dev）、`core-harness`、`bcdbin-core` と各 guard が success。CDREAD attestation 系は本 PR の差分に触れないため SKIPPED | 4.1 節の run 番号参照 | PASS |
| E14 | DEV ROM 画像のハッシュ | `make dev-rom`（本 PR のブランチ、Linux、pinned cc65）で生成 | SHA-256 `5c6aaf0a789787f8749843d7b3d11c3bd868e1437a3e1422afce214095480f8e`（262144 バイト）。`build/debug/bios-dev/manifest.json` の `rom_sha256` と一致 | PASS |
| E15 | マニフェストのハッシュ | 同上のビルドで生成 | `build/debug/bios-dev/manifest.json` の sha256 `f24e5115b587c83be751e653dae714666f569e3de24fb907edb1a9ec91c9d935`（ビルド時のブランチ HEAD `cf49557`）。manifest はビルドしたコミットを `source_sha` に埋め込むため、**コミットが変わると manifest 全体の sha256 も変わる**。再現性地比較できる値は manifest 内の `source_sha256`（ファイル別 sha256）である | PASS |
| E16 | Geargrafx 固定 SHA | `tools/dependencies.json`: revision `b49ae82a012eade566d72e9d61c9d95a5caa4da3`、archive sha256 `a80a3eae26a8efcd994350e84bbe73c975e406ced41f8f2404c88b00d4217f68` | 記録のみ | INFO |
| E17 | 承認フィールド不変 | `spec/dev_implementations.json` slot 30 の `approval` は null のまま | 本 PR は当該ファイルを変更しない | PASS |
| E18 | 多入力の実コア確認（独立レビュー指摘 3 への対応） | `tests/integration/dev_getver_multi_input.py`（`make dev-integration` から実行、CI でも再現）。DEV ROM エントリ `$E05A`（slot $1E のテーブル JMP 先は `$E2A0`）を、A 入力 8 値（`$00` `$01` `$11` `$42` `$7F` `$80` `$AA` `$FF`）× 呼出時 C 2 値 × 呼出時 (X,Y) 2 組 × 呼出時 SP 3 値 の 96 条件で呼び、cold と warm の両方で A 保持・X=3・Y=0・P（C/Z/I/D/N）・SP 均衡・MPR1/MPR7 を検査 | `PASS real Geargrafx DEV ROM: EX_GETVER slot $1E entry $E05A 96 cases (8 A values x 2 carry x 2 X/Y x 3 SP; cold and warm); A preserved, X=3, Y=0, P (C/Z/I/D/N), SP, MPR1/MPR7 checked` | PASS |

### 3.1 注記

- E3 の PASS 行は、CI の bios-dev ジョブログ（run 39551289760、job 119060840729）から取得した。個別の A/X/Y/P 値は PASS 行に要約されているのみで、生の観測 JSON（`geargrafx-api-evidence.json`）は CI アーティファクトに含まれていない。
- E14 / E15 は本 PR で Linux 上の実ビルドにより取得した（initially NOT_RUN だった項目）。再ビルドでも DEV ROM の sha256（`5c6aaf0a…`）は一致する（ソース 内容が決定的）。manifest.json の sha256 だけは埋め込まれた `source_sha`（ビルドしたコミット）のためにコミットごとに変わるので、比較には `source_sha256` を使うこと。
- E18 の `PASS` 行は本 PR で実コアを実行して得た（`build/debug/bios-dev/geargrafx-dev-getver-evidence.json` に観測値を記録する）。期待値を X=4 に改変した版では FAIL することも確認済み（テストが空振りしないことの確認）。
- `make dev-integration` は DEV ROM を読み込むが、参照 BIOS（rom/）は読み込まない。

## 4. 独立レビュー

- 方式: 別の general-purpose エージェントによる review-only 検証（コードと証拠のみを渡し、結論は渡さず。rom/ 不可、書き込み不可）。
- 判定: **INSUFFICIENT（現状のままでは不十分）**。
- 主な指摘:
  1. 来歴の記述が不正確。EX_GETVER の契約値の来歴は、私有参照 BIOS の観測（観測値は取り下げ済み。本書は扱わない）に依らず、DEV ROM が自己定義する契約である。`slot_1E.s` の「proprietary BIOS code からではない」はコードについて正しい。`spec/dev_implementations.json` の provenance は、DEV 専用の自己定義契約であることを明記するように改めた。
  2. 実コアの検証は循環している。`tests/integration/geargrafx_contracts.py` の `IMPLEMENTED_SLOTS = {0x1E: {'a': 0x11, 'x': 3, 'y': 0, 'sp': 255}}` はその実測値をハードコードしたものであり、参照挙動の独立確認ではない。DEV ROM を BIOS として走らせている。
  3. 入力は A=0x11 の 1 ベクタのみ。「A 保持」は他の入力で未確認。X/Y は固定出力。
  4. P レジスタの扱いが文書間で不一致。参照試験計画の観測値は取り下げ済みであり、`geargrafx_contracts.py` は P 絶対値を固定しない（PRNG シード）としている。
  5. 証拠 E3 の CI 実行は当初 8b3bf4d のものだった。c906728 でも下記のとおり再実行され成功した。
  6. 私有 BIOS の測定については、リポジトリに記録を置かない（取り下げ、2026-10-28）。独立 ABI レビューは未了（`docs/reference-bios-test-plan.md`）。クリーンルーム判定は Issue #40 が未クローズのため未了。
- 承認フィールドは null のまま（`approved_by`, `review_ref`、`release_eligible: false`、状態 NOT_READY）。System Card 互換や VERIFIED は主張していないことを確認。

### 4.1 c906728 の再確認（レビュー後）

- CI `BIOS DEV diagnostics`（run 39551289760、bios-dev job 119060840729）: 全ジョブ success。`Ran 68 tests` / `OK`、`PASS real Geargrafx System Card diagnostic: ... slot 1E EX_GETVER returned its measured contract; ...` を確認。

### 4.2 本 PR のブランチでの再確認（2026-10-21）

- ブランチ: `docs/dev-callable-1e-evidence`、main `0c2fa41` をマージ済み。
- ローカル実測: `make dev-rom` で DEV ROM を生成し、E14 / E15 のハッシュを取得（3 節）。
- ローカル実測: `python3 tests/integration/dev_getver_multi_input.py --rom build/debug/bios-dev/dev-only-not-compatible-syscard3.pce` で 96 ケース PASS（E18）。`make dev-integration` にも組み込んだため CI の `bios-dev` ジョブが同じ検査を実行する。
- CI の run 番号は各コミット時点の記録である。最新 head の結果は PR のチェック一覧で確認すること。

## 5. オーナーが判断する前に不足しているもの

1. DEV ROM 画像とマニフェストの SHA-256 → **本 PR で取得済み**（E14 / E15）。manifest の sha256 はビルドしたコミットに依存するため、比較は `source_sha256` で行う。
2. 取り下げた私有 BIOS 測定については記録を置かない。残る論点は、自己定義 ABI の主張と DEV テストの整合のみ。
3. 観測値の多入力テスト → **A 保持については本 PR で対応済み**（E18: A 入力 8 値 × C 2 値 × (X,Y) 2 組 × SP 3 値 = 96 条件、cold と warm）。未了なのは「X/Y の入力依存性」の観点で、$1E は X=3・Y=0 を固定出力する契約のため入力依存性は存在しない（呼出側の X/Y は出力に影響しないことを E18 で確認している）。
4. P レジスタの扱いの統一（参照試験計画の観測値（取り下げ済み）と、契約ハーネスの「絶対値を固定しない」の整理）。
5. 個別の観測値を示す生 JSON → E18 の分（`geargrafx-dev-getver-evidence.json`）は本 PR のドライバがローカルに書き出す（ビルド成果物のため git には含めない）。CI アーティファクトには含まれないという点は変わらない。
6. クリーンルーム・ライセンス判断（Issue #40 のクローズ）。
7. 実機での検証は未実施（実機動作と ABI 承認は PENDING）。

注: 上記は独立レビューの指摘を反映したものであり、本文書は承認を行わず、承認判断はオーナーに委ねる。

## 6. 結論（証拠の範囲）

- 証拠は「DEV ROM の $1E 入口が、DEV 専用・自己定義 ABI で、固定コア上で X=3, Y=0, A 保持, SP/MPR 不変で戻る」ことを示す。
- System Card 互換、Reference BIOS ABI VERIFIED、実機動作は示さない。
- 承認可否はオーナーが決定する。本文書は承認を行わない。
