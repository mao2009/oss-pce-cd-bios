# CD_BASE 契約台帳（Issue #11、`$02 CD_BASE`）

スロット `$02`、候補 CPU エントリ `$E006`。**状態: 契約は未確定。ROM 実装なし。**
本書は CD_READ（#118 の `$E009`）が使うブロック番号の基準を整理する。既存の契約ファイル
（`docs/cd-reset-contract.md`、`spec/api_contracts.json`、`docs/abi-contracts.md`）は変更しない。

状態の意味:

- `VERIFIED`: 実行した呼出先（BIOS 相当）で測定された事実。本書の BIOS 関連項目は 0 件。
  ホスト側の算術だけを検証した行には「ホスト算術のみ」と付記する。これは BIOS の事実ではない。
- `HYPOTHESIS`: 出典から推測した内容。実機・実 BIOS では未確認。
- `UNKNOWN`: 確認できる出典がない。推測で埋めない。

## 1. 出典と採否

| 主張 | 出典 | 種別 | 状態 |
| --- | --- | --- | --- |
| 名前 `CD_BASE` が `$E006`（スロット 2） | HuCC `include/hucc/pcengine.inc`（System Card 関数イコール、L468-552） | SDK イコール（呼出側） | HYPOTHESIS |
| `CD_BASE` のセレクタ名 | Hu-Go `bios.c`（L30-133） | HLE エミュレータ | HYPOTHESIS。HLE は実 BIOS の証明にならない。エントリ番号は UNKNOWN |
| 名前だけの観測 | `spec/api_contracts.json` の slot 2 | 既存の契約台帳 | `mapping_status` = HYPOTHESIS。全項目 UNKNOWN |
| ブロック番号は 24bit（`_cl:_ch:_dl`、`$20FC/$20FD/$20FE`） | `docs/cd-read-contract.md`（#118 ブランチ、`origin/feat/12-cd-read-abi`） | HuCC 呼出側の読み取り | HYPOTHESIS |
| 「ブロック番号が CD_BASE 相対か絶対 LBA か」 | 同上（#11 を参照） | 未解決 | UNKNOWN |
| INDEX 01 と LBA の対応、仮想 PREGAP | `docs/cd-read-diagnostic.md`（「INDEX 00/01, pregap and LBA」）と `tools/cue_analysis.py` | ホスト側の CUE 解析（Geargrafx 読み出しの対象データ） | VERIFIED（CUE 解析の範囲のみ） |
| READ(6) の LBA は 21bit | `docs/cd-read-contract.md` の T1（Geargrafx 測定） | エミュレータ測定 | VERIFIED（Geargrafx のみ） |

HuCC と Hu-Go は同じ呼出規約の観測にすぎない。どちらも `CD_BASE` の呼出先の振る舞いを独立に裏付けない。
Zeograd や Stum の記事は名前の確認にのみ使う。

## 2. 契約項目

| 項目 | 状態 | 内容と根拠 |
| --- | --- | --- |
| エントリ | HYPOTHESIS | `$E000 + 3*2 = $E006`（ジャンプテーブル規約）。ROM のマッピングは #1 の所管。 |
| 引数（A/X/Y、`$20F8-$20FF`） | UNKNOWN | HuCC の `cd_base` ラッパーは引数を渡すと推定できる箇所がない。ブロック基準の値がどのレジスタ・RAM で渡るかは未確認。 |
| 基準の単位（LBA か、MSF か、トラック先頭か） | UNKNOWN | #11 の調査項目。issue の文言は「LBA・MSF・トラック先頭を基準」。どれか一つに決められる出典がない。 |
| 基準の値域 | UNKNOWN | 24bit と推定されるが、基準値そのものの幅は未確認。 |
| 戻り値（A、フラグ） | UNKNOWN | Hu-Go の戻り値は HLE であり根拠にならない。 |
| エラー（無ディスク、範囲外基準） | UNKNOWN | 出典なし。 |
| 副作用（作業 RAM `$20F8-$20FF`、BIOS 作業 RAM） | UNKNOWN | CD_READ 側の HYPOTHESIS（`$20FC-$20FE`）を除き、CD_BASE 自身の書き込み先は不明。 |
| MPR | UNKNOWN | HuCC は呼出側が MPR を戻すと記す。CD_BASE 固有の MPR 変更は未確認。 |
| スタック | HYPOTHESIS | JSR/RTS 想定。深さと釣り合いは UNKNOWN。 |
| 割り込み（I フラグ、IRQ 前提） | UNKNOWN | |
| 完了条件、タイミング | UNKNOWN | 同期・非同期の区別なし。 |
| 基準の永続性（次の CD_READ まで有効か、リセットで消えるか） | UNKNOWN | CD_RESET（#10）との関係も未確認。 |

## 3. LBA 絶対指定とベース相対指定（ホスト算術）

`tools/cd_base_model.py` が計算する候補。どちらが BIOS の呼出形式かは決まっていない。

| 候補 | 式 | 状態 |
| --- | --- | --- |
| 絶対 LBA | `block = lba`（基準 0） | HYPOTHESIS（#118 の T1 は絶対 LBA で動く。CD_BASE を使う経路は別） |
| INDEX 01 相対 | `block = lba - index01_lba` | HYPOTHESIS |
| 任意基準 | `block = lba - base` | UNKNOWN（基準値の出所が決まらないため） |

### INDEX 01 との関係

- 固定データ（`INDEX 01 00:00:00`、仮想 PREGAP なし）では、INDEX 01 は LBA 0 である。
  絶対 LBA と INDEX 01 相対は**一致する**。このフィクスチャでは両者を区別できない。
- `PREGAP 00:02:00` と `INDEX 01 00:00:00` の仮想 PREGAP では、INDEX 01 は LBA 150、
  仮想 PREGAP の開始は LBA 0 である。LBA 0..149 は BIN に格納されない。
  INDEX 01 相対では LBA 150 が block 0、LBA 149 は負になり拒否される。
- `INDEX 00 00:00:00` と `INDEX 01 00:02:00`（BIN にプリギャップを格納）でも、INDEX 01 は LBA 150 である。

境界（`tests/host/test_cd_base_model.py` で確認）:

| 入力 | 結果 |
| --- | --- |
| 仮想 PREGAP 開始 LBA 0 | `pregap` |
| LBA 149（INDEX 01 は 150） | `pregap` |
| LBA 150 | `data` |
| LBA -1 | 例外（拒否） |
| INDEX 01 の前で PREGAP が無い LBA | `before_track` |

**実験で区別するための条件**: 絶対 LBA と INDEX 01 相対を分けるには、INDEX 01 が LBA 0 でないディスク
（仮想 PREGAP、または INDEX 00 のプリギャップ）が必要である。現在のフィクスチャでは区別できない。

## 4. 24bit アドレスの単位

| 項目 | 内容 | 状態 |
| --- | --- | --- |
| 幅 | ブロック番号は 24bit（`0..0xFFFFFF`）と推定 | HYPOTHESIS（#118 の HuCC 呼出側から） |
| 1 ブロックの単位 | セクタ（2048 バイトのユーザデータか、2352 バイトの生フレームか）は未確認 | UNKNOWN |
| 上限の扱い（ラップか、エラーか） | 未確認。モデルは**ラップせず拒否する**（推測した挙動を事実にしない） | UNKNOWN |
| READ(6) との関係 | READ(6) の LBA は 21bit（`0..0x1FFFFF`）。ブロック番号がこれを超えると READ(6) へ直接渡せない | VERIFIED（Geargrafx 測定。ホスト算術の `fits_read6_lba` で確認） |

`fits_read6_lba` が偽になる範囲では、BIOS が内部で別形式に変換するかどうか未確認である。

## 5. モデルの検証

| 種類 | 結果 |
| --- | --- |
| `tests/host/test_cd_base_model.py` | 21 件。MSF（境界・不正入力）、仮想 PREGAP、INDEX 00/01 の境界、INDEX 01 相対、ベースのラウンドトリップ、24bit 範囲、READ(6) 21bit |
| 失敗確認 | モデル実装前に `ModuleNotFoundError` で失敗することを確認済み |
| 実エミュレータ | **実施していない**（本 PR は Geargrafx を使わない） |

これは host の算術テストであり、CD_BASE の BIOS 動作の証拠ではない。

## 6. 実装可否の判断

**判断: ROM 実装は行わない（未実装・理由: 必要な ABI が確認できないため）。**

理由（UNKNOWN が残るもの）:

1. 引数の渡し方（レジスタ、RAM、単位）が UNKNOWN。
2. 基準の意味（絶対 LBA、INDEX 01、トラック先頭、MSF）が UNKNOWN。
3. 戻り値、エラー（無ディスク、範囲外）、副作用が UNKNOWN。
4. 実エミュレータで `$E006` を実行する手段がない（#1 のマッピング、#8 の出典採否が未決）。

ROM に `CD_BASE` を入れると、ABI を推測した成功値を返すことになる。これは禁止事項に当たるため行わない。
未実装 API は成功値を返さない（ホストモデルは ROM に組み込まれない）。

## 7. 次に必要な確認（UNKNOWN の解消条件）

- System Card 3.0 の `CD_BASE` 呼出側と呼出先の観測（#8 の採否基準を満たす出典、名前だけでは不可）。
- 基準値の出所（どの RAM または引数で渡るか）と単位（LBA か、セクタか）。
- 実エミュレータで、INDEX 01 が LBA 0 でないディスクを使い、絶対 LBA と INDEX 01 相対を区別する測定。
- 無ディスクと範囲外基準の戻り値。
- 上記が揃うまで、`spec/api_contracts.json` の slot 2 は更新しない（別の Issue で扱う）。

## 8. UNKNOWN 一覧

1. CD_BASE の引数（レジスタ、RAM、値域）
2. 基準の単位（LBA、MSF、トラック先頭）
3. 基準値の出所と永続性
4. 戻り値、フラグ
5. エラー（無ディスク、範囲外）
6. 副作用（作業 RAM `$20F8-$20FF` を含む）、MPR、割り込み、完了条件、タイミング
7. ブロック番号の 1 単位（セクタ種別）
8. 24bit の上限超過時の挙動（ラップか、エラーか）
9. 絶対 LBA と INDEX 01 相対のどちらが呼出形式か（現フィクスチャでは区別不能）
10. エントリ `$E006` の実機マッピング（#1 の所管）

## 関連

- ホスト算術: `tools/cd_base_model.py`、`tests/host/test_cd_base_model.py`
- 前提: `docs/cd-read-diagnostic.md`（アドレス契約）、`docs/cd-reset-contract.md`（#10 の形式）
- 参照のみ: `docs/cd-read-contract.md`（#118 ブランチ）。`docs/oss-audit-systemcard.md` には CD_BASE の記述がなかった。
