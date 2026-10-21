# Issue #305: BM_FREE の呼出 ABI 契約台帳（Contract 段階）

本書は Issue #305（親 #35 の Contract 段階）の契約台帳である。空き容量・未フォーマット時の BM_FREE 呼出規約を、公開された呼出側（caller）の根拠だけから整理し、判定を分けて記録する。

- 判定ラベル: `CONFIRMED-by-source`（出典に直接ある事実。呼出側の使い方に限る）/ `HYPOTHESIS`（出典はあるが呼出先の事実として裏付けがない）/ `UNKNOWN`（出典なし）。
- 呼出側の事実と呼出先（BIOS）の事実は別行に分ける。呼出側のコメントは呼出先の仕様の証拠にならない。
- 合成データ（PR #396 / Issue #306 `tools/buram_free_judge.py`）の 2 KiB・128 byte ブロック・配置は**プロジェクトの創作**であり、System Card の形式ではない。本書の判定に使わない。
- 実行は行っていない。実 BIOS・実バックアップ RAM・実ユーザーのセーブへの読み書きはない。
- `release_eligible=false`。BM_FREE の API は `BLOCKED_ABI` を維持する。

## 1. 出典

| ID | 出典 | 位置 | 備考 |
| --- | --- | --- | --- |
| S1 | pce-devel/huc `include/hucc/pcengine.inc`、revision `54f5c73606c1e7142e8095c727cab2767e2b3cb3` | L491–499（`bm_format` ～ `bm_files` の等号、`bm_free = $00:E04B, FUNC` は L495）、L337–339（`_cx = $F8:20FC, 2`、`_cl`、`_ch`） | sha256 `b6a25d33bb787ea1fe550c6e04aff9f8ccaaa91b649a05a963be24b440b728a9`。Boost-1.0（ファイル先頭 L8–10 に明記）。呼出側の等号のみ |
| S2 | pce-devel/huc `include/hucc/hucc-systemcard.asm`、同 revision | L54–61（BM_ 定数）、L65（`__bm_error`）、L667–694（`_bm_format`、`bm_free` の戻り値を分岐）、L707–722（`_bm_free` マクロ） | sha256 `4258b7c3e8751127c0615af3178e431d81043c25fa6cb2b6b1d51b566aec7ea9`。Boost-1.0（`Copyright John Brandwood 2024` L8、ライセンス L10–12）。呼出側ラッパー（CD 系だけでなく BM 系も含む） |
| S3 | 同 `include/hucc/hucc-systemcard.h` | L112（`unsigned int __fastcall __macro bm_free( void )` の宣言） | sha256 `f2477642f867c3daeff00938534f3f8ef970d1a9403f44bd09e90894220b8a3b`。Boost-1.0（ファイル先頭 L13–15 に明記） |
| S4 | リポジトリ `docs/system-card-rom.md` | L102（バックアップ RAM 窓 `$F7` / `$1EE000`、コアは 2 KiB を初期化） | プロジェクトの注記。コア側の出典は本 Issue で再確認していない |
| S5 | リポジトリ `docs/api-inventory.md` | L50（`$19` / `$E04B` / `BM_FREE` / #35） | 一覧のみ |
| S6 | リポジトリ `spec/api_contracts.json` slot 25、`spec/api_slots.json` id 25 | `mapping_status: HYPOTHESIS`、`arguments/returns/clobbered/preserved/stack/mpr/ram/flags/irq/errors` 全て `UNKNOWN` | 台帳の現状。本 PR は変更しない |
| S7 | リポジトリ `docs/173-safety-monitor-limits.md` L63、L74–76（「ゲートと不変条件」節。L75 は slot `$01` の BLOCKED 維持）；`spec/reference_jsr_gate.json` | JSR 実行強制ゲートは BLOCKED 維持。`reference_jsr_gate.json` の slots に `$E04B` の項目はなく、slot 1 のみ `execution: BLOCKED` | `$E04B` を呼ぶ許可はどこにもない |
| S8 | リポジトリ `tools/buram_free_judge.py`（PR #396 / Issue #306） | `SIZE=2048`、`BLOCK=128`、`free_blocks` を返す | プロジェクトの創作。`PROJECT-INVENTED` |

探索の範囲: 上記以外の公開 SDK・実機資料・他エミュレータの BIOS 相当実装は、本 Issue では調べていない。第 2 の独立した呼出先の根拠は未取得である。

## 2. 契約台帳（呼出側 S1–S3 と呼出先の区別）

| ID | 項目 | 事実（出典） | 判定 |
| --- | --- | --- | --- |
| R01 | 入口アドレス | 呼出側は `$00:E04B` を `FUNC` として等号定義（S1 L495） | CONFIRMED-by-source |
| R02 | 名前と入口の対応 | `bm_free` の名前は S1・S2・S3 で一致するが、呼出先の同一性は未検証（S6 と同じ） | HYPOTHESIS |
| R03 | 入力（引数） | `_bm_free` マクロは `system bm_free` の前に入力レジスタを設定しない（S2 L713–715）。C 宣言も `(void)`（S3 L112） | CONFIRMED-by-source（呼出側は入力なし） |
| R04 | 呼出先が入力を読むか | 出典なし | UNKNOWN |
| R05 | 戻り値（空き容量）の置き場所 | マクロは呼出し後に `_cx`（`$F8:20FC`、下位 `_cl`、上位 `_ch`）を読む（S2 L720–721。`_cx` の定義は S1 L337–339） | CONFIRMED-by-source（呼出側の読み先） |
| R06 | 呼出先が `_cx` に書くか | 出典なし（R05 は呼出側の期待のみ） | UNKNOWN |
| R07 | 空き容量の単位 | 呼出側コメント「number of user bytes available in BRAM」（S2 L709） | HYPOTHESIS（呼出先の単位の根拠は呼出側コメントのみ） |
| R08 | エラー時の容量 | 呼出側マクロがエラー時に `_cx` を 0 にする（S2 L718–719） | CONFIRMED-by-source（呼出側が強制する値） |
| R09 | 呼出先がエラー時に容量 0 を返すか | 出典なし | UNKNOWN |
| R10 | 戻り値（状態コード）の置き場所 | 呼出し後の A を `__bm_error` に格納し、`tay` / `beq` で 0 を成功と判定（S2 L715–717） | CONFIRMED-by-source（呼出側の読み先） |
| R11 | 成功コード | `BM_OK = 0`（S2 L54） | CONFIRMED-by-source（呼出側の定数） |
| R12 | 呼出先が成功時に A=0 を返すか | 出典なし（R11 は呼出側の定数） | HYPOTHESIS |
| R13 | 未フォーマットのコード | `BM_NOT_FORMATTED = $FF`（S2 L60–61）。`bm_free` の事後条件は「BM_OK or BM_NOT_FORMATTED」（S2 L711） | CONFIRMED-by-source（呼出側の定数と宣言） |
| R14 | 呼出先が未フォーマット時に `$FF` を返すか | 出典なし（R13 は呼出側の期待） | HYPOTHESIS |
| R15 | 他のエラーコードが返るか | `bm_free` の宣言には他のコードが書かれていない（S2 L707–711）。ほかのラッパー（`bm_read` 等）のコードは別呼出し | UNKNOWN |
| R16 | 未フォーマット時の呼出側の動作 | `_bm_format` は `bm_free` の A が非 0 なら `bm_format`（`$E048`、S1 L494）を `!BM FORMAT!` の文字列つきで呼ぶ（S2 L675–694）。書込み経路であり、本 Issue では実行しない | CONFIRMED-by-source（呼出側の分岐） |
| R17 | `bm_free` が副作用（書込み）を持たないか | 出典なし。呼出側が書込み前の確認に使う、という使われ方のみ | UNKNOWN |
| R18 | 保存・破壊されるレジスタ、スタック、MPR、フラグ | 出典なし | UNKNOWN |
| R19 | `system` 疑似命令の展開（JSR、バンク、MPR 退避・復帰） | 定義は取得した 3 ファイルに無い | UNKNOWN |
| R20 | バックアップ RAM 窓と初期化量 | `$F7` / `$1EE000`、コアは 2 KiB を初期化（S4 L102） | HYPOTHESIS（プロジェクト注記。コア側の根拠は未再確認） |
| R21 | 実 System Card の BRAM 形式（ヘッダ、ブロック長、割当表、ディレクトリ） | 公開の呼出側ソースに無い。S8 の配置は創作で、根拠にならない | UNKNOWN |

集計（R01–R21）: `CONFIRMED-by-source` 8、`HYPOTHESIS` 5、`UNKNOWN` 8。

### 2.1 合成モデル（#396）との差分（記録のみ、根拠にしない）

| 項目 | 呼出側（S1–S3） | #396 合成モデル（S8） | 扱い |
| --- | --- | --- | --- |
| 戻り値の単位 | user bytes（R07、HYPOTHESIS） | `free_blocks`（ブロック数） | 単位が一致しない。モデルの戻り値をそのまま BM_FREE の結果と呼ばない |
| 未フォーマット | `$FF`（R13、R14） | `UNFORMATTED` という判定名（コード値なし） | 判定名は PROJECT-INVENTED。BIOS のコード値は不明 |
| 大きさ | 未定義（R21） | 2048 byte、ブロック 128 byte | PROJECT-INVENTED。2 KiB は S4 の注記と偶然に一致するが、根拠ではない |

## 3. 呼出側の見える効果（未フォーマット時）

- 呼出側の判断: `bm_free` の状態コードが 0 以外なら未フォーマットとみなし、`bm_format` を呼ぶ（R16、CONFIRMED-by-source）。
- 呼出先で何が起きるか（書込みの有無、レジスタの状態、返す値）は R14、R17、R18 が UNKNOWN または HYPOTHESIS で、確定しない。
- したがって「未フォーマット時に何も書かれない」は未確認であり、本台帳では主張しない。

## 4. #307（デバイス層の読取り専用観測）の判定

**判定: BLOCKED_ABI。プローブは作成・実行しない（NOT_RUN）。**

理由（不足している事実）:

1. 呼出先の事実（R04、R06、R09、R12、R14、R15、R17、R18）に独立な出典がない。台帳は呼出側の使い方だけで、「空き容量を `_cx` に返す」「未フォーマットで `$FF` を返す」は呼出先で確かめられていない。
2. 呼出先の観測には `$00:E04B` へ JSR する必要がある。その安全ゲートは S7 のとおり BLOCKED で、許可の記録がない。ゲートを有効化しない。
3. 呼出先コードを含む System Card の実イメージは、リポジトリに許可された形で存在しない。`$E04B` の入口に到達する手段がない。
4. 書込みを伴わない生メモリ読取りだけでは、BIOS の判定結果を観測できない。R21 の形式が不明なので、読んだバイトに意味を付けられない。

## 5. 次の一手（BLOCKED の解除条件）

- R06、R09、R14 について、呼出先の仕様を示す第 2 の独立した公開根拠を集める（呼出側のラッパーではないもの）。
- `$E04B` の JSR に対する安全ゲートの判断を、人間が記録する（現状は BLOCKED 維持）。
- 解除後に限り、#307 は読取り専用の観測として再評価する。書込み、フォーマット（`bm_format`）、実ユーザーのセーブは対象外。

## 6. 禁止事項の遵守

- 純正 BIOS のバイト、逆アセンブル、市販データは載せていない。引用は呼出側ソースの行番号とハッシュに限る。
- `spec/api_contracts.json`、`spec/api_slots.json`、Makefile、ジャンプテーブル、共有ビルド設定は変更していない。
- 本 Issue は合成データとしてだけ扱い、System Card の ABI を確定したとは記録しない。
