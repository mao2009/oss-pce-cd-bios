# CD_READ 参照 ABI 準備（V16）: 合成 SCSI 転送と純正 callee 観測の分離

関連: #12（親）、#188（1モード・1セクターの転送先と count 境界）、#189（無媒体・範囲外・count 0 の失敗結果）。
前提: [188-cd-read-transfer-mode-contract.md](188-cd-read-transfer-mode-contract.md)、[189-cd-read-error-contract.md](189-cd-read-error-contract.md)、[188-cd-read-mode-safety.md](188-cd-read-mode-safety.md)。
対象外: #190（独立 HuC6280 routine）、#149（slot `$03` 接続）、CD_READ OSS ROM、CD_BOOT、他の転送モード。

## 0. 判定

* **参照 ABI の承認済み検証証拠: 未登録。** 承認済み検証証拠として登録された純正 BIOS の実行、`$E009` の JSR、Geargrafx 実行は 0 件（本 PR は重い実コア実行を含まない）。歴史上の実行の有無は述べない。
* JSR gate は閉じたまま（`spec/reference_jsr_gate.json` の slot 3 記録なし、`NO_REVIEWED_GATE_RECORD`）。#188 と #189 の参照観測は **BLOCKED**。
* 本 PR の判定器が返す項目別の PASS は合成入力に対するものだけで、純正 CD_READ の ABI 証拠ではない。全体（overall）の PASS は shipped 範囲では発生しない。CD_READ 転送層の判定は System Card callee の VERIFIED ではない。
* `release_eligible`、`VERIFIED_PATH_ENABLED`、JSR gate、承認欄、`api_contracts.json` の schema と status は変更していない。Reference VERIFIED は 0/81 のまま。

## 1. 証拠の種別を分ける

| 種別 | `kind` | 判定関数 | できること | できないこと |
| --- | --- | --- | --- | --- |
| 合成 SCSI 転送 | `synthetic_scsi` | `judge_synthetic()` | 自作媒体での転送長、宛先バイト、宛先外書込みの判定 | 純正 callee の戻り値・副作用の証拠 |
| 純正 callee | `reference_callee` | `judge_callee()` | 信頼された `reference_jsr_gate.require_allowed('slot-03')` の結果だけで許可する。`obs['gate']` は許可を与えない。provenance（`origin` / `rom_sha256` / `core` / `head` / `evidence`）が必須（形式と存在のみ検査。自己申告の値であり認証はされていない。`rom_sha256` と `head` は長さと小文字 16 進の形式を検査する）。通過後のみ `cd_read_error_verdict.judge` に委譲 | 合成結果の代用、ゲートを開いたモックの結果を承認とみなすこと |

**モックの扱い:** テストでゲートを `ALLOWED` にモックするものは「配線の確認」であり、承認ではない。実ゲートは現在 CLOSED（`NO_REVIEWED_GATE_RECORD`、`NO_INDEPENDENT_APPROVAL`）で、そのため実 callee 記録は BLOCKED になる。

両者は相互に受け付けない。合成観測を callee 判定に渡すと BLOCKED、callee 観測を合成判定に渡すと BLOCKED になる（テストで確認）。

## 2. 転送契約（合成、1モード・1セクター）

| 項目 | 契約（合成判定器の規則） | 状態 |
| --- | --- | --- |
| 転送先モード | `_dh = 3` のみ（188 D1）。他のモードは BLOCKED。`_dh=3` は公開 caller 由来の観測候補であり、ABI として確定したものではない | 合成判定器の範囲のみ。callee 側は UNKNOWN |
| count | 1 のみ IN_SCOPE。0、2、3、4、256 は UNKNOWN（PASS にならない）。count の単位がセクターであることは [HYPOTHESIS A4]（コメント/宣言由来）で、コードの事実ではない | count 境界は callee 未観測のため UNKNOWN |
| 宛先の幾何 | MPR3 窓 8 KiB 内に `count * 2048` バイトが収まるとき IN_WINDOW。窓を跨ぐと UNKNOWN、窓外の offset は BLOCKED | 合成の幾何のみ |
| LBA 範囲 | 21 ビット（[D] `cd-read-contract.md` T1 の記録）。媒体サイズ未満が範囲内。範囲外（LBA = 媒体サイズ を含む）は配送 0、宛先不変を期待。LBA の基点（絶対か CD_BASE 相対か）は #11 で未解決 | 合成媒体のみ |
| 1セクターの内容 | 範囲内は `reference_sector(lba)` の 2048 バイトを**全バイト比較**。サンプリングしない | 合成の内容のみ |
| 宛先外書込み | 0 でなければ FAIL | 合成の監視 |
| A / X / Y / P、MPR、stack、IRQ | 合成観測として記録し、形式が不正なら BLOCKED。判定は UNKNOWN（callee の期待値は未確定） | UNKNOWN |

`reference_sector(lba)` は決定的な合成パターンで、ディスクイメージではない。純正 BIOS の出力を複写していない。

## 3. 2048 バイト全比較の方式

* `compare_sector(expected, observed)`: 両方が厳密に 2048 バイトの bytes / bytearray であることを確かめ、全 2048 バイトを 1 バイトずつ比較する。
* 長さ・型が違う観測は FAIL ではなく **BLOCKED**（証拠として不正）。短い、長い、文字列、`None` のいずれも PASS にならない。
* 結果は `verdict`、`mismatches`（不一致バイト数）、`first`（最初の不一致位置）を返す。

## 4. エラーと戻り値（#189 と接続）

* count 0、無媒体、範囲外の callee の戻り値・副作用は引き続き UNKNOWN。合成の範囲外は配送 0 と宛先不変だけを判定する（転送層の計測 T3 に由来し、callee の値ではない）。
* 無媒体（T7）は実行不能のため本 PR でも判定しない（`cd_read_error_verdict.CASES['C3_NO_MEDIA']` は `executable: False`）。

## 5. テスト（RED → GREEN）

* RED: `tests/host/test_cd_read_v16_contract.py` を先に作成し、`tools/cd_read_v16_contract.py` が無い状態で実行 → `ModuleNotFoundError`。
* RED（ゲート修正の前）: 新規テストは 44 件中 1 件の失敗と 6 件のエラー（`_gate` / `CALLEE_API_ID` 未定義を含む）。
* GREEN（ゲート修正の後）: 45 件すべて OK。実ゲートは CLOSED のまま読まれる。`tests/host/test_cd_read_error_verdict.py` も OK（既存の判定器に変更なし）。
* 実行コマンド: `python -I tests/host/test_cd_read_v16_contract.py`（`-I` で環境の PYTHONPATH を無視）。

### 5.1 変異テスト（mutation）

18 個の変異（M1-M15 と、ゲート関連の M16-M18）を `tempfile.TemporaryDirectory()` 内の複製に適用し、各変異が**正確に 1 箇所に適用されたこと**を確認してから、テストを実行した。

| 変異 | 結果 |
| --- | --- |
| M1 先頭 16 バイトのみ比較（サンプリング） | 検出 |
| M2 長い観測を許容 | 検出 |
| M3 合成観測を callee 判定に通す | 初回は未検出（テスト不足）→ テスト追加後に検出 |
| M4 count 0 を IN_SCOPE | 検出 |
| M5 窓跨ぎを 1 セクター許容 | 検出 |
| M6 LBA の off-by-one | 検出 |
| M7 宛先外書込みを無視 | 検出 |
| M8 範囲外の宛先変化を無視 | 検出 |
| M9 monitor_ok を見ない | 検出 |
| M10 宛先モードを見ない | 検出 |
| M11 範囲内の配送 count を見ない | 初回は未検出（テスト不足）→ テスト追加後に検出 |
| M12 合成の効果項目が PASS を出す | 検出 |
| M13 bool を count として許容 | **未検出（等価変異）。** `dest_span_ok` の型検査が同じ BLOCKED を返すため、判定結果は変わらない |
| M14 callee 判定の委譲をバイパス | 検出 |
| M15 同長なら常に PASS | 検出 |

最終結果: 18 件中 17 件検出（M16 信頼ゲートを呼ばない、M17 provenance 未検査、M18 ゲート例外の握り潰し はいずれも検出）。M13 は判定結果を変えない等価変異として扱い、感度の欠落とはみなさない。

## 6. 未実施と UNKNOWN

* 純正 BIOS の観測（`$E009` の入口、戻り A/X/Y/P、MPR、stack、IRQ、宛先）: UNKNOWN、未実施。
* count 0、2、3、4、256 の callee 挙動: UNKNOWN。
* 無媒体、範囲外、末尾跨ぎの callee 挙動: UNKNOWN（#189）。
* 合成 SCSI の実コア（Geargrafx）実行: **未実施**（本 PR は判定器とテストのみ。スロットロック `geargrafx-slots/` は取得していない）。
* 第二エミュレータによる比較: 未実施。

## 7. BLOCKED 条件

次のいずれかが成立するまで、参照 ABI の観測は BLOCKED のまま。

1. JSR gate の slot 3 記録と独立承認（`spec/reference_jsr_gate.json` に slot 3 が無い）。
2. #173 の子 #365、#366、#367 が閉じること（書込み・MMIO の事前遮断）。
3. callee ABI の入口、宛先基点、LBA 基点（#11）の出典付き確定。

## 8. 次の作業

* 合成 SCSI の実コア実行は、スロットロックを取得してから別 PR で行う（本 PR の範囲外）。
* 参照観測は上記 BLOCKED 条件の解消後に、#188 と #189 の子 Issue で行う。
