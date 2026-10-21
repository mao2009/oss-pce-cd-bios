# DEV_CALLABLE 承認証拠: slot $1E EX_GETVER

- 対象 Issue: #433（証拠収集）、#324（オーナー判断）
- 基準コミット: `c906728609c61945ef72ae020ddeb43db5eb5e4e`（main）
- 承認フィールド: `spec/dev_implementations.json` の slot 30 `approval`（`approved_by: null`, `review_ref: null`）は **本文書では変更していない**。承認はオーナーのみが行う。

## 1. 範囲と前提

- 本文書の「DEV_CALLABLE」は、#324 の標準ルールどおり「DEV ROM の入口を通じて、DEV 専用・自己定義 ABI で呼び出せること」のみを指す。
- System Card 互換、Reference BIOS ABI VERIFIED、実機動作は **主張しない**。
- `spec/api_contracts.json` の EX_GETVER 契約は大半が HYPOTHESIS / UNKNOWN のままである。
- DEV ROM 画像・マニフェストのハッシュは **ローカルでは未生成**（後述）。
- rom/ 配下（独自 BIOS）は本作業で読み取り・公開・実行していない。

## 2. 対象コード

`src/overrides/slot_1E.s`（sha256 `8be2efe63c547eb62833cf00e69e3f66860b67363a082daa912159eb7b707176`）:

```
api_slot_1E:
    ldx #major_version   ; 3
    ldy #minor_version   ; 0
    rts
```

本体バイト列: `a2 03 a0 00 60`（LDX #3; LDY #0; RTS）。

ヘッダコメントには「固定の I/O 契約は、固定済み Geargrafx コアで、私有の参照 BIOS に対して測定した（slot $1E -> $F02D が $FEC2/$FEC3 を読み、X=3, Y=0, A 保持）」と記載されている。これは **測定の由来** の記述であり、DEV ROM 本体は独自に書かれた 5 バイトである。

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
| E13 | CI（main, c906728） | `CI`（verify, guard-verify）success。`BIOS DEV diagnostics`（bios-dev）success。CDREAD attestation は本文書作成時点で queued | 上記 | PASS（一部 queued） |
| E14 | DEV ROM 画像のハッシュ | `make dev-rom` でのローカル生成は、Windows ホストに cc65 がないため未実施 | — | NOT_RUN |
| E15 | マニフェストのハッシュ | E14 と同じ理由 | — | NOT_RUN |
| E16 | Geargrafx 固定 SHA | `tools/dependencies.json`: revision `b49ae82a012eade566d72e9d61c9d95a5caa4da3`、archive sha256 `a80a3eae26a8efcd994350e84bbe73c975e406ced41f8f2404c88b00d4217f68` | 記録のみ | INFO |
| E17 | 承認フィールド不変 | `spec/dev_implementations.json` slot 30 の `approval` は null のまま | 本 PR は当該ファイルを変更しない | PASS |

### 3.1 注記

- E3 の PASS 行は、CI の bios-dev ジョブログ（run 39551289760、job 119060840729）から取得した。個別の A/X/Y/P 値は PASS 行に要約されているのみで、生の観測 JSON（`geargrafx-api-evidence.json`）は CI アーティファクトに含まれていない。
- E14 / E15 は本作業では生成していない。DEV ROM のハッシュは、オーナーが必要とする場合に CI の再現ビルドで取得する必要がある。
- `make dev-integration` は DEV ROM を読み込むが、参照 BIOS（rom/）は読み込まない。

## 4. 独立レビュー

- 方式: 別の general-purpose エージェントによる review-only 検証（コードと証拠のみを渡し、結論は渡さず。rom/ 不可、書き込み不可）。
- 判定: **INSUFFICIENT（現状のままでは不十分）**。
- 主な指摘:
  1. 来歴の記述が不正確。EX_GETVER の契約値は **私有参照 BIOS** を実コアで観測して得たものであり（`docs/reference-bios-test-plan.md` の実測表）、`slot_1E.s` の「proprietary BIOS code からではない」はコードについてのみ正しい。`spec/dev_implementations.json` の「project-own measured contract」は測定対象を省いている。
  2. 実コアの検証は循環している。`tests/integration/geargrafx_contracts.py` の `IMPLEMENTED_SLOTS = {0x1E: {'a': 0x11, 'x': 3, 'y': 0, 'sp': 255}}` はその実測値をハードコードしたものであり、参照挙動の独立確認ではない。DEV ROM を BIOS として走らせている。
  3. 入力は A=0x11 の 1 ベクタのみ。「A 保持」は他の入力で未確認。X/Y は固定出力。
  4. P レジスタの扱いが文書間で不一致。参照試験計画は `P = $06` を記録する一方、`geargrafx_contracts.py` は P 絶対値を固定しない（PRNG シード）としている。
  5. 証拠 E3 の CI 実行は当初 8b3bf4d のものだった。c906728 でも下記のとおり再実行され成功した。
  6. 私有 BIOS の測定記録はリポジトリに生ログ・ハッシュとして存在しない。独立 ABI レビューは未了（`docs/reference-bios-test-plan.md`）。クリーンルーム判定は Issue #40 が未クローズのため未了。
- 承認フィールドは null のまま（`approved_by`, `review_ref`、`release_eligible: false`、状態 NOT_READY）。System Card 互換や VERIFIED は主張していないことを確認。

### 4.1 c906728 の再確認（レビュー後）

- CI `BIOS DEV diagnostics`（run 39551289760、bios-dev job 119060840729）: 全ジョブ success。`Ran 68 tests` / `OK`、`PASS real Geargrafx System Card diagnostic: ... slot 1E EX_GETVER returned its measured contract; ...` を確認。

## 5. オーナーが判断する前に不足しているもの

1. DEV ROM 画像とマニフェストの SHA-256（CI 再現ビルドから取得）。本作業では未取得（NOT_RUN）。
2. 私有 BIOS 測定の記録（生ログと測定条件の所在）。ヘッダコメントの来歴記述を、自己定義 ABI の主張とどう整合させるか。
3. 観測値の多入力テスト（A 以外の入力、X/Y の入力依存性の確認）。現状は単一ベクタ。
4. P レジスタの扱いの統一（参照試験計画 `P = $06` と、契約ハーネスの「絶対値を固定しない」の整理）。
5. 個別の観測値を示す生 JSON（CI アーティファクトには含まれていない）。
6. クリーンルーム・ライセンス判断（Issue #40 のクローズ）。
7. 実機での検証は未実施（実機動作と ABI 承認は PENDING）。

注: 上記は独立レビューの指摘を反映したものであり、本文書は承認を行わず、承認判断はオーナーに委ねる。

## 6. 結論（証拠の範囲）

- 証拠は「DEV ROM の $1E 入口が、DEV 専用・自己定義 ABI で、固定コア上で X=3, Y=0, A 保持, SP/MPR 不変で戻る」ことを示す。
- System Card 互換、Reference BIOS ABI VERIFIED、実機動作は示さない。
- 承認可否はオーナーが決定する。本文書は承認を行わない。
