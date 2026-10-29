# BM_FREE 呼出 ABI の根拠整理（Issue #305 / Contract 段階）

- 対応 Issue: #305（親 #35 の Contract 段階）
- 状態: `release_eligible=false`。BM_FREE の API は `BLOCKED_ABI` を維持する。
- 関連する既存台帳: `docs/305-bm-free-contract.md`（R01–R21）。本書はその根拠を出典ごとに再確認し、独立文献の取得範囲を明示するための文書である。両者の判定が食い違う場合は本書の出典行を優先し、差分を §6 に記録する。

## 0. 判定ラベルと原則

| ラベル | 意味 |
| --- | --- |
| 確認済み | 指定した出典の指定行で直接確認できる事実。呼出側の使い方に限る |
| 推定 | 出典はあるが、呼出先（BIOS）の事実としては裏付けがない |
| UNKNOWN | 取得した出典に記述がない |

- 呼出側（huc のラッパー）のコメントや宣言は、呼出先（BIOS）の仕様の証拠にならない。
- 純正 BIOS のバイト列・逆アセンブル・コードは本書に転記しない。引用は出典の行番号と SHA-256 に限る。
- 実 BIOS・実バックアップ RAM・実ユーザーのセーブは読まず、実行もしていない（NOT_RUN）。

## 1. 出典

### 1.1 独立文献・既存 OSS の公開情報（今回取得できた範囲）

| ID | 出典 | 取得 URL（revision 固定） | SHA-256 | 確認した行 | ライセンス |
| --- | --- | --- | --- | --- | --- |
| E1 | pce-devel/huc `include/hucc/pcengine.inc` | `https://raw.githubusercontent.com/pce-devel/huc/54f5c73606c1e7142e8095c727cab2767e2b3cb3/include/hucc/pcengine.inc` | `b6a25d33bb787ea1fe550c6e04aff9f8ccaaa91b649a05a963be24b440b728a9` | L8（Boost 明記）、L337–339（`_cx` / `_cl` / `_ch`）、L494（`bm_format`）、L495（`bm_free`） | Boost Software License 1.0 |
| E2 | pce-devel/huc `include/hucc/hucc-systemcard.asm` | 同 revision の `hucc-systemcard.asm` | `4258b7c3e8751127c0615af3178e431d81043c25fa6cb2b6b1d51b566aec7ea9` | L8（Copyright John Brandwood 2024）、L10（Boost）、L54・L56・L57・L61（`BM_OK` / `BM_BAD_CHECKSUM` / `BM_DIR_CORRUPTED` / `BM_NOT_FORMATTED`）、L65（`__bm_error`）、L671–673（`_bm_format` 前のコメント）、L675–700（`_bm_format`）、L707–711（`bm_free` のコメント）、L713–722（`_bm_free` マクロ） | Boost Software License 1.0 |
| E3 | pce-devel/huc `include/hucc/hucc-systemcard.h` | 同 revision の `hucc-systemcard.h` | `f2477642f867c3daeff00938534f3f8ef970d1a9403f44bd09e90894220b8a3b` | L112（`bm_free` の C 宣言、`(void)`） | Boost Software License 1.0 |

- revision: `54f5c73606c1e7142e8095c727cab2767e2b3cb3`。
- 行番号は 2026-10-29 に上記 URL から取得したファイルを `grep -n` / `Read` で直接確認した値である。WebFetch の要約は行番号を推測で数えるため採用していない。
- 3 ファイルとも、ハッシュは本書の値と一致した（取得時刻 2026-10-29）。
- 上記 3 ファイルは **呼出側（caller）** のソースである。呼出先の BIOS コード（System Card 本体）は取得していない。

### 1.2 既存 spec/・docs/ 内の既知情報（参照のみ・未編集）

| ID | 出典 | 位置 | 扱い |
| --- | --- | --- | --- |
| P1 | `docs/api-inventory.md` | L50（`$19` / `$E04B` / `BM_FREE` / #35） | 一覧のみ |
| P2 | `docs/system-card-rom.md` | L102（バックアップ RAM 窓 `$F7` / `$1EE000`、コアは 2 KiB を初期化） | プロジェクトの注記。コア側の根拠は未再確認 |
| P3 | `docs/173-safety-monitor-limits.md` | L63、L74–76（「ゲートと不変条件」。L75 は slot `$01` の BLOCKED 維持） | 安全ゲート。`$E04B` の実行許可はない |
| P4 | `spec/reference_jsr_gate.json` | `$E04B` の項目は無い（検索で一致なし） | 実行許可の記録なし |
| P5 | `spec/api_slots.json` | L108（id 25 の `candidate_name`: `BM_FREE`） | 台帳の現状 |
| P6 | `spec/api_contracts.json` | slot 25（`mapping_status: HYPOTHESIS`、引数・戻り値・副作用は UNKNOWN） | 本 PR は変更しない |
| P7 | `spec/dev_implementations.json` | BM_FREE 項目（`state: NOT_READY`、`abi_contract.status: UNKNOWN`） | 本 PR は変更しない |
| P8 | `docs/research/302-bm-format-contract.md` | §2 C4a / C4b（呼出側の 0 / 非 0 分岐） | 呼出側の分析（BM_FORMAT, #302） |
| P9 | `docs/research/306-bm-free-fixture-evidence.md` | §2、§3 | 合成フィクスチャの記録。System Card の形式根拠にならない |
| P10 | `tools/buram_free_judge.py`、`tools/bm_free_fixture.py`、`tests/host/test_bm_free_fixture.py` | 存在を確認 | PROJECT-INVENTED の合成モデル。根拠にしない |

### 1.3 未取得の出典

- 呼出先（System Card BIOS）の公開仕様、NEC / Hudson の公開資料、他エミュレータの BIOS 相当実装は、本 Issue では取得していない。
- `spec/api_contracts.json` が参照する `hugo:bios.c`（function selector、HLE、実行入口の証拠ではないと記録済み）も未確認。
- 第 2 の独立した呼出先の根拠は現時点で無い。

## 2. 呼出側から確認できた事実（確認済み）

| ID | 項目 | 事実 | 出典 | 判定 |
| --- | --- | --- | --- | --- |
| C01 | 入口アドレス | `bm_free` は `$00:E04B` を `FUNC` として定義 | E1 L495 | 確認済み（呼出側の等号） |
| C02 | 入力レジスタ | `_bm_free` マクロは `system bm_free` の前に入力を設定しない。C 宣言も `(void)` | E2 L713–714、E3 L112 | 確認済み（呼出側は入力なし） |
| C03 | 戻り値（容量）の読み先 | 呼出し後に `_cx`（`$F8:20FC`、下位 `_cl`、上位 `_ch`）を読む | E2 L720–721、E1 L337–339 | 確認済み（読み先） |
| C04 | 状態コードの読み先 | 呼出し後の A を `__bm_error` に格納し、`tay` / `beq` で 0 を判定 | E2 L715–717 | 確認済み（読み先） |
| C05 | 成功コード | `BM_OK = 0` | E2 L54 | 確認済み（呼出側の定数） |
| C06 | 未フォーマットコード | `BM_NOT_FORMATTED = $FF` | E2 L61 | 確認済み（呼出側の定数） |
| C07 | エラー時の容量 | 呼出側マクロは A ≠ 0 のとき `_cx` を 0 にする | E2 L718–719 | 確認済み（呼出側が強制する値） |
| C08 | 非 0 の扱い | `_bm_format` は `bm_free` の A が 0 なら `.done` へ進み、非 0 のときだけ `bm_format` を呼ぶ | E2 L677–680 | 確認済み（呼出側の分岐） |
| C09 | `bm_format` の入力 | 非 0 時のみ `_ax` に文字列ポインタを置いて `bm_format`（`$00:E048`）を呼ぶ | E2 L682–687、E1 L494 | 確認済み（呼出側。実行しない） |
| C10 | 容量の単位（呼出側コメント） | 「number of user bytes available in BRAM」 | E2 L709 | 推定（呼出先の単位の根拠は呼出側コメントのみ） |
| C11 | 事後条件（呼出側コメント） | `__bm_error` は `BM_OK` または `BM_NOT_FORMATTED` | E2 L711 | 推定（呼出先の事後条件の根拠は呼出側コメントのみ） |
| C12 | 呼出側コメントの不整合 | `bm_format` の宣言コメント（E2 L667–673）は「`BM_OK, BM_NOT_FOUND`」と書くが、`BM_NOT_FOUND` は E2 の定数定義（L54–61）に無い | E2 L667–673、L54–61 | 確認済み（記述の不整合。挙動の根拠にしない） |

## 3. 呼出先（BIOS）について未確定の項目

| ID | 項目 | 状態 | 理由 |
| --- | --- | --- | --- |
| U01 | 呼出先が `_cx` に容量を書くか | UNKNOWN | 呼出側の期待（C03）のみ |
| U02 | 呼出先が入力レジスタを読むか | UNKNOWN | 呼出側は入力を設定しないが、BIOS 側の要件は未確認 |
| U03 | 容量の単位（user bytes か、ブロック数か） | 推定（C10）のみ | 呼出先の裏付けなし |
| U04 | エラー時に呼出先が容量を 0 にするか | UNKNOWN | 呼出側が 0 を強制（C07）するのみ |
| U05 | 成功時に呼出先が A = 0 を返すか | 推定 | C05 は呼出側の定数 |
| U06 | 未フォーマット時に呼出先が `$FF` を返すか | 推定 | C06 は呼出側の期待 |
| U07 | 他のエラーコード（`BM_BAD_CHECKSUM`、`BM_DIR_CORRUPTED` 等）を `bm_free` が返すか | UNKNOWN | 呼出側の宣言に記述なし（E2 L54–61 は定数のみ） |
| U08 | `bm_free` が副作用（書込み）を持つか | UNKNOWN | 呼出側は書込み前の確認に使うという使われ方のみ |
| U09 | 保存・破壊されるレジスタ、スタック、MPR、フラグ | UNKNOWN | 出典なし |
| U10 | `system` 疑似命令の展開（JSR、バンク切替、MPR の退避・復帰） | UNKNOWN | 定義は取得した 3 ファイルに無い |
| U11 | 実 System Card の BRAM 形式（ヘッダ、ブロック長、割当表、ディレクトリ） | UNKNOWN | 公開の呼出側ソースに無い。P9 の合成モデルは PROJECT-INVENTED で根拠にならない |
| U12 | 入口 `$00:E04B` に本当に `bm_free` が存在するか（名前と入口の対応） | 推定 | 名前の一致は呼出側 3 ファイルと `spec` の記録のみ |

## 4. 未解決の論点

1. 呼出先 BIOS の公開仕様（独立文献）を取得する。U01、U04、U06、U07 が解消の対象。
2. 容量の単位（U03）。呼出側コメントと合成モデルの「ブロック数」が一致しない点（P9 と `docs/305-bm-free-contract.md` §2.1）。
3. 未フォーマット時に書込みが起きないかどうか（U08）。書込みを伴う経路（`bm_format`）は本 Issue の対象外。
4. レジスタ・スタック・MPR の扱い（U09、U10）。
5. C12 の不整合（`BM_NOT_FOUND` の記述）が古いコメントか、別の定数を指すのかは未確認。

## 5. 実機・実コア検証の記録

| 項目 | 状態 | 理由 |
| --- | --- | --- |
| 純正 BIOS の `$00:E04B` 呼出し | NOT_RUN | 安全な実行境界が未検証。JSR 安全ゲートは BLOCKED（P3） |
| 実バックアップ RAM の読取り | NOT_RUN | 実機・実 ROM を使わない範囲 |
| 実ユーザーのセーブ | NOT_RUN | 対象外 |
| BIOS の FORMAT（`bm_format`） | NOT_RUN | 書込み経路。本 Issue では実行しない |
| Geargrafx 等の実コア検証 | NOT_RUN | 本 Issue の範囲外 |

## 6. 既存台帳（docs/305-bm-free-contract.md）との対応

- R01（入口）→ C01、R03（入力なし）→ C02、R05（容量の読み先）→ C03、R08（エラー時 0）→ C07、R10（状態の読み先）→ C04、R11（OK = 0）→ C05、R13（`$FF`）→ C06、R16（非 0 で `bm_format`）→ C08 / C09 に対応する。
- 本書では C10、C11 を「推定」、C12 を新規の記録とした。`docs/305-bm-free-contract.md` は R07 を「HYPOTHESIS」、R13 を「CONFIRMED-by-source」としている。C10 は R07 と同じ判定、C06 は R13 と同じ判定である。
- 既存台帳の行番号との照合: E1 L337–339、L494–495、E2 L54、L61、L65、L677–680、L713–722 は一致した。既存台帳の E2「L667–694」は本書の確認で `bm_format` の宣言コメント（L667）から `_bm_format` の終端付近までを指す範囲として整合する（開始行の指定が広いだけで誤りではない）。

## 7. 集計（表の判定列から数えた値）

| 区分 | 件数 | 内訳 |
| --- | --- | --- |
| 確認済み | 10 | C01–C09、C12 |
| 推定 | 6 | §2 の C10、C11。§3 の U03、U05、U06、U12 |
| UNKNOWN | 8 | U01、U02、U04、U07、U08、U09、U10、U11 |

§3 の 12 行は推定 4 件と UNKNOWN 8 件に分かれる。

## 8. 検証記録（2026-10-29）

- 出典ハッシュ: E1–E3 の SHA-256 は §1.1 の値と一致（取得時の `sha256sum` 出力）。
- 行番号: E1–E3 の行番号は取得ファイルで `grep -n` / `Read` により確認。
- 本書の参照先（ファイル・節）: `docs/api-inventory.md` L50、`docs/system-card-rom.md` L102、`docs/173-safety-monitor-limits.md` L63、L74–76、`spec/api_slots.json` L108、`spec/dev_implementations.json` の BM_FREE 項目、`docs/research/302-bm-format-contract.md` §2、`docs/research/306-bm-free-fixture-evidence.md` §2–3、`tools/buram_free_judge.py`、`tools/bm_free_fixture.py`、`tests/host/test_bm_free_fixture.py` の存在を確認。
- Markdown のリンク: 本書には相対リンクを置いていない（参照は文字列の出典 ID で示す）。
- テスト: 本 PR はドキュメントのみの変更。ホストのテストは実行していない（既存の `tests/host` は変更していない）。
- 実機・実コア検証は NOT_RUN（§5）。
