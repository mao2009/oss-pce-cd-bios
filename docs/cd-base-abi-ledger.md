# CD_BASE ABI 証拠台帳（Issue #11）

スロット `$02`、候補エントリ `$E006`。**状態: ABI は未確定。ROM 実装なし。**
本台帳は、Issue #11 の六つの未確定項目（(1) 引数の場所、(2) ブロック番号が絶対 LBA か相対か、
(3) 基準値の設定と保持、(4) CD_READ との関係、(5) MPR と作業 RAM の副作用、(6) 戻り値と失敗の規約）
について、出典と確度を記録する。既存の `spec/api_contracts.json` と `docs/abi-contracts.md` は変更しない。

## 1. 規則

状態語の意味（Wave 3 のオーナー裁定、Issue #570）:

- `SOURCE_CONFIRMED`: 公開された固定版ソース（§2 の出典）の記述を確認した事実。確認したのはソースの記述であり、そのソースが記述する振る舞いを実行して確かめたものではない。本台帳では呼出側ソースの記述（「この呼出しがある」「この値を書く」など）にのみ使う。**BIOS（System Card 呼出先）の振る舞いには付けない。**
- `HYPOTHESIS`: ソースや文献に基づく未確認の動作仮説。呼出先を実行して確認していない。
- `MEASURED`: 実行環境・対象・測定範囲を明記した実測事実。MEASURED は VERIFIED を自動的には意味しない。本台帳に CD_BASE 呼出先の実測はない。READ(6) の測定（#118 系）は CD_READ 経路の実測であり、CD_BASE の MEASURED には含めない。
- `UNKNOWN`: 根拠不足。推測で埋めない。成功値も定義しない。
- `BLOCKED`: 安全条件や依存関係が未成立で、確認できない状態。本台帳の項目には付けていない。ただし `docs/cd-read-contract.md` の T1〜T4 は、bundle がない場合に BLOCKED を付ける（実効判定）。
- `VERIFIED`: 対象を限定し、要求される独立検証と信頼条件を満たした事実。本台帳では付けない。CD_BASE の純正 ABI の正式な VERIFIED 判定は既存 Trusted Gate の信頼境界に従い、今回は昇格させない。ホスト解析、DEV ROM の実測、Geargrafx 転送層の実測は、CD_BASE の純正 ABI の VERIFIED に含めない。

呼出側ソースの記述（SOURCE_CONFIRMED）と呼出先（callee）の振る舞い（MEASURED / VERIFIED）は、別の項目として記録する。呼出側の記述は呼出先の振る舞いの証拠にならない。

除外する証拠:

- ホスト側の算術（`tools/cd_base_model.py` 系や、本ブランチの合成テスト）は BIOS の証拠ではない。
- READ(6) の測定（#118 系）は CD_READ 経路の測定であり、CD_BASE の証拠ではない。READ(6) の LBA を CD_READ のブロック番号の基準の根拠にもしない。
- 以下の出典は **ライセンスが不明なため、内容を根拠にしない**（`docs/oss-audit-systemcard.md` の記録に従う）:
  - Hu-Go `bios.c`: ライセンス・帰属が未確定。HLE であり、本台帳の判断に使わない。
  - HuC ライブラリ `include/huc/cdrom.asm`、`include/huc/startup.asm`: ライセンス表示がなく、ルート `LICENSE` は「混在・不確実」と記録されている。コメントの内容は根拠にしない。
  - `examples/asm/elmer/kickc/bios.h`: ライセンス未確認。名前の宣言のみで、内容は使わない。
- PR #120（`feat/11-cd-base-contract`）は 2026-10-16 に main へマージ済み（main の `7bf85b5`）。`tools/cd_base_model.py` と `tests/host/test_cd_base_model.py` は branch 版と同一であることを確認した。その候補式（`lba_to_block` 等）はホスト算術であり、本台帳の根拠に使わない。main の `docs/cd-base-contract.md` §3 にある「#118 の T1 は絶対 LBA で動く」は READ(6) 測定の記述であり、CD_READ の基準の証拠として引用しない。合成テストは独立に定義した。

## 2. 出典

HuCC（`pce-devel/huc`）の固定版を使う。

- 固定版: commit `54f5c73606c1e7142e8095c727cab2767e2b3cb3`
- 取得元: `https://codeload.github.com/pce-devel/huc/tar.gz/54f5c73606c1e7142e8095c727cab2767e2b3cb3`
- アーカイブ SHA-256: `9fe91254051381077bd4ea217b718ec033a52da185283ae9479c1abac365311b`
- 再確認: アーカイブを新しい空ディレクトリに展開し、次を実行する（展開物は実行しない）。

  ```sh
  grep -rIn -E 'cd_base' include examples
  grep -rIn -E 'cd_read' include/hucc
  ```

| ID | 出典（固定版内のパス） | 行 | ライセンス表示 | 状態 |
| --- | --- | --- | --- | --- |
| S1 | `include/hucc/core-startup.asm` | 293–301（`.if SUPPORT_2ISO` 内で `jsr cd_base`、300 行目） | BSL-1.0（John Brandwood 2021–2024） | 出自は PENDING（`docs/oss-audit-systemcard.md`） |
| S2 | `include/hucc/pcengine.inc` | 330–343（`_al`〜`_dh` のラベル）、399（`tnomax = $F8:226B`）、470–471（`cd_base`、`cd_read` の等号） | BSL-1.0（著作権者名なし） | 出自は PENDING |
| S3 | `include/hucc/core-config.inc` | 83–84（`SUPPORT_2ISO = 0` の既定値） | 同上 | 同上 |
| S4 | `include/hucc/hucc-systemcard.asm` | 285–300（`_cd_fastvram`）、313–330（`_cd_loadvram`）、344–408（`_cd_loaddata`、MPR3 の保存と復元、`cd_read` は 388 行）、427–452（`_cd_loadbank`、MPR3 のコメントは 450–452 行） | BSL-1.0（John Brandwood 2024） | 同上 |
| S5 | `include/hucc/core-kernel.asm` | 531–552（`get_file_info` / `get_file_lba`）、580–612（オーバーレイ読込み。`cd_read` は 596 行、再試行は 600–601 行） | BSL-1.0（John Brandwood 2021–2024） | 同上 |
| S6 | `include/huc/system.inc` 67 行、`examples/asm/elmer/kickc/bios.h` | 名前の宣言のみ | 不明（除外） | 根拠に使わない |
| S7 | `include/hucc/hucc-systemcard.asm` 594–600 行（`_add_sectors`: `_dl` / `_ch` にセクタオフセットを加算） | BSL-1.0（S4 と同じ） | 同上 | 同上 |

`cd_base` の呼出しは、固定版全体の `grep` で S1 の 1 箇所のみである。`examples/asm/elmer/ted2-bios-romcd/syscard3-ted2.inc` の
`$E006` への書込みは、TED2 カードのレジスタ操作であり CD_BASE の呼出しではないため、除外する。

「SOURCE_CONFIRMED」の事実は、S1 から S5 の本文を上記の方法で再確認できる。`PENDING` の出自は採否を変えない。
各出典の使用は、ライセンス表示のある呼出側コードの記述に限る。

## 3. 六つの問いへの回答

| # | 問い | 回答 | 確度 | 出典 | 決着条件 |
| --- | --- | --- | --- | --- | --- |
| (1) | 引数の場所 | 呼出側（S1）は、`_al`（`$F8:20F8` の下位バイト、`tnomax` の値）に最終トラック番号と推定される値を入れ、`_bh` に `$80`、`_cl` に `2` を入れてから `jsr cd_base` を行う。呼出先がどのバイトを読むかは未確認。呼出側のコメントは「最後のトラックをバックアップ領域として設定する」と記す（S1 の 294–295 行。コメントは呼出側の記述であり、呼出先の仕様ではない）。 | UNKNOWN（呼出側の書き込みは SOURCE_CONFIRMED、呼出先の読取りは UNKNOWN） | S1、S2（L330–343、L399） | 作業 RAM を監視して、`_al`、`_bh`、`_bl`、`_cl` の値を変えた入力感度試験を、実行可能な呼出先で行う |
| (2) | ブロック番号は絶対 LBA か相対か | 未確定。比べる候補は三つ。(i) ディスク LBA（MSF 00:02:00 が LBA 0）、(ii) INDEX 01 起点（トラック先頭が 0）、(iii) MSF フレーム起点（00:00:00 が 0、リードイン 150 を含む）。仮説は「(ii) のような基準値からの相対」。根拠は次の三点。(a) CD_BASE の唯一の呼出しはトラック番号と推定される値を渡す（S1）。(b) オーバーレイ読込みの呼出側は ISO ディレクトリ由来の値を加算せずに渡す（S5 の 531–552、596 行）。ただし VRAM / データ読込みは `_add_sectors` でオフセットを加える（S4、S7）。(c) ISO 9660 のエクステント位置はボリューム先頭からの論理ブロック番号である（ECMA-119。本リポジトリ外で、未確認）。 | UNKNOWN（仮説は HYPOTHESIS） | S1、S5 | INDEX 01 が LBA 0 でないディスクで、同じブロック番号に対する CD_READ の結果を、基準が 0 の場合と既知の基準の場合で比較する。合成テストのプローブ表が判定の形を与える（§4） |
| (3) | 基準値の設定と保持 | 未確定。設定の唯一の呼出しは S1 にあり、ただし `SUPPORT_2ISO` が 1 のときだけ（S3。既定は 0）。基準値そのものの格納場所は、ライセンスの確認できる出典にない。`tnomax` は入力に使われる変数の位置を示すのみ（S2）。保持の期間（次の CD_READ まで、CD_RESET で消えるか）も未確認。 | UNKNOWN | S1、S2、S3 | CD_BASE の前後で作業 RAM と MPR を全比較し、CD_RESET 後と連続する CD_READ の間の残存を確認する |
| (4) | CD_READ との関係 | 未確定。HuCC の CD_READ 呼出しは 5 箇所（S4 の 297、325、388、446 行と、S5 の 596 行）あり、それぞれの関数内に CD_BASE の呼出しはない。CD_BASE の呼出しは固定版全体で S1 の 1 箇所だけであり、その後に CD_READ が同じ基準で動くかは、ソースに記述がない。 | UNKNOWN（仮説は HYPOTHESIS: CD_BASE は CD_READ のブロック基準を変える） | S1、S4、S5 | 2ISO 経路と同じ順序（CD_BASE の直後に CD_READ）を実行し、結果を比較する |
| (5) | MPR と作業 RAM の副作用 | CD_BASE については未確認。CD_READ については、HuCC の呼出側が MPR3 を CD_READ の前後で保存・復元し、エラー時に値が壊れると記す（S4 の 443–452 行）。これは呼出側の報告であり、呼出先の計測ではない。 | UNKNOWN（CD_BASE）、HYPOTHESIS（CD_READ の呼出側の報告） | S4、S5（604–610 行の MPR 復元コメント） | CD_BASE の前後で MPR0–7 と作業 RAM の全差分を取り、成功経路とエラー経路の両方で確認する |
| (6) | 戻り値と失敗の規約 | CD_BASE: 未確認。S1 の呼出側は `jsr cd_base` の後に戻り値を読まない（呼出側の事実）。戻り値がないとは言えない。CD_READ: 呼出側は戻り値（HuCC では X にコピー）が 0 なら成功、非 0 をエラーとして扱う（S4 の 388–390 行、S5 の 600–601 行）。これは呼出側の期待であり、BIOS の戻り値の証拠ではない。 | UNKNOWN（CD_BASE）、HYPOTHESIS（CD_READ の呼出側の期待） | S1、S4、S5 | 無ディスク、基準の範囲外、INDEX 01 より前の LBA で戻り値とフラグを実測する |

### 補足: 2ISO 経路の矛盾（決着が必要）

S1 の呼出しは、基準をトラック単位に変えるように読める（ただし、呼出先の仕様は未確認）。基準が次の CD_READ まで残るなら、
起動後に行われるオーバーレイ読込み（S5）は最終トラック基準になり、ISO ディレクトリの値と矛盾する。
矛盾を解く条件（基準の復帰、CD_READ の絶対値、または 2ISO 経路の呼出しが後続に影響しないこと）は
ソースに記述がない。この点は (2) と (4) の決着の対象である。

## 4. 合成テスト（候補モデルのみ）

`tests/host/test_cd_base_abi_candidates.py` は、INDEX 01 が LBA 150 の合成ディスク（仮想 PREGAP 150 フレーム、
16 セクタ、各セクタの内容は自分の LBA）で四つの候補モデルを比較する。**これは候補モデルのテストであり、
System Card の CD_BASE / CD_READ の動作を検証しない。**

| 候補モデル | 式（block、base、ディスク → LBA） | 基準の定義 |
| --- | --- | --- |
| `ABS_LBA` | `lba = block` | ディスク LBA（MSF 00:02:00 が 0） |
| `BASE_PLUS_BLOCK` | `lba = base + block` | 呼出しで設定された基準（ディスク LBA） |
| `TRACK_START_PLUS_BLOCK` | `lba = INDEX01 + block` | INDEX 01 起点（単一データトラックでは ISO ボリューム LBA と同じ意味） |
| `MSF_FRAMES_FROM_00_00_00` | `lba = block - 150` | MSF 00:00:00 起点（リードイン 150 フレームを含む） |

ホスト算術 `tools/cd_base_model.py`（PR #120）の `lba_to_block(lba, base)` と `absolute_msf_to_lba` は、
`BASE_PLUS_BLOCK` の逆、`MSF_FRAMES_FROM_00_00_00` と同じ換算である。

プローブは `(block, base)` の四組: `(5, 0)`、`(5, 150)`、`(155, 0)`、`(5, 16)`。結果（REJECT は読めない、または範囲外）:

| 候補モデル | (5, 0) | (5, 150) | (155, 0) | (5, 16) |
| --- | --- | --- | --- | --- |
| `ABS_LBA` | REJECT | REJECT | 155 | REJECT |
| `BASE_PLUS_BLOCK` | REJECT | 155 | 155 | REJECT |
| `TRACK_START_PLUS_BLOCK` | 155 | 155 | REJECT | 155 |
| `MSF_FRAMES_FROM_00_00_00` | REJECT | REJECT | REJECT | REJECT |

四つの署名はすべて異なる。INDEX 01 が 0 の対照ディスクでは `ABS_LBA` と `TRACK_START_PLUS_BLOCK` が一致するため、
INDEX 01 != 0 の条件が必要であることも確認している。実行可能な呼出先を得た後、同じプローブを実機またはエミュレータで
実行し、どの署名に一致するかを比べるのが決着の方法である。

## 4a. 実行可能な呼出先の有無と実測ハーネス

### 呼出先の有無（実測）

| 候補 | 結果 | 根拠 |
| --- | --- | --- |
| 本リポジトリの DEV ROM の `$E006` | 実行可能な呼出先なし（MEASURED）。`$E006` から fail-closed の fallback（`$E295`、`X=$02`）へ入り、cold、さらに 32 命令、warm の各時点で同じ PC に留まる。RTS がないため、呼出後の観測はできない | `make dev-cd-base-integration`（固定版 Geargrafx `b49ae82a`、debug と release の両方で PASS）。証跡は `geargrafx-cd-base-evidence.json`（ローカル生成。コミットしない） |
| 固定版 Geargrafx の BIOS 呼出し HLE | なし（ソース確認のみ）。`src/` の検索で見つかったのは BIOS イメージの読込み（`LoadBios` / `LoadBiosFromBuffer`）だけで、`$E006` を横取りする経路は見つからなかった。網羅の証明ではない | 固定版ソースの `grep`（`hle`、`0xE006`、`syscard`） |
| System Card（実 BIOS） | BLOCKED。リポジトリに含めず、利用者所有の BIOS を使う経路は本作業の範囲外 | §5 |

この結果、(1)〜(6) の呼出先の振る舞いは、測定対象そのものがないため **BLOCKED**（実測不能）である。
状態は UNKNOWN のまま変えない。DEV ROM の fallback の振る舞いは本 ROM の性質であり、BIOS の証拠ではない。

### ハーネス（`tools/cd_base_probe.py`）

- 合成ディスク: 独自の MODE1/2352 16 セクタ、CUE `PREGAP 00:02:00` + `INDEX 01 00:00:00`。`tools/cue_analysis.py` で
  INDEX 01 がディスク LBA 150、PREGAP 150 フレームが BIN に格納されないことを毎回確認する（INDEX 01 != 0 の条件を維持）。
  固定版 Geargrafx がこの CUE を読み込むこと（`cdrom_media`）は実測した。PREGAP を含む CUE の SCSI LBA 対応は未測定（NOT RUN）。
- 観測項目（`OBSERVATION_PLAN`）: 問いごとに、実行された呼出先で記録すべき値を定める。READ の LBA はエミュレータの SCSI 層が見た値を使う。

  | 問い | 記録する値 |
  | --- | --- |
  | (1) | 同じブロック番号で、入力バイト（`_al`〜`_dh`）を一つだけ変えた CD_BASE → CD_READ の READ LBA |
  | (2) | リセット後、CD_BASE なしの CD_READ を二つ以上のブロック番号で実行した READ LBA |
  | (3) | CD_RESET 後と、二回目の CD_READ 後のオフセット |
  | (4) | (1) と (2) のオフセットの比較 |
  | (5) | 一回の CD_BASE の前後の MPR0–7 と作業 RAM |
  | (6) | 無ディスク、範囲外、INDEX 01 より前、通常の各場合の A/X/Y/P |

- 判定器（`judge`）: VERIFIED を出さない。実行記録は OBSERVED（値そのまま、または整合する候補モデルの一覧）、
  未記録は UNKNOWN、呼出先なし・非復帰は全問 BLOCKED。(2) はオフセット（READ LBA − ブロック番号）が一定のときだけ
  候補を返し、`BASE_PLUS_BLOCK` は常に候補に残す（既定の基準値が未知のため）。INDEX 01 が 0 の記録では (2) を UNKNOWN にする。
  ブロック数は READ が実際に出た行（READ LBA が null でない行）だけで数え、二種類以上を要する。(4) は (2) が OBSERVED で、
  かつ CD_BASE の入力の組ごとに、READ が出たブロックが二種類以上あり、オフセットが一定のときだけ OBSERVED にする
  （入力の組ごとのオフセットを返す）。一つでも条件を満たさない組があれば UNKNOWN。(1) も null の READ 行を証拠に使わない。
  ブロック番号、READ LBA、INDEX 01 の LBA が整数でない記録（bool、小数、文字列）は ValueError で拒否する。
- 試験: `tests/host/test_cd_base_probe.py` は合成記録で判定規則だけを試す（BIOS の証拠ではない）。

## 5. 残る UNKNOWN と理由

- (1)、(3)、(5)、(6) の呼出先の振る舞い: 実行可能な System Card CD_BASE がリポジトリにない（§4a で実測。DEV ROM の `$E006` は非復帰）。BIOS 本体はリポジトリに含めず、ユーザ所有のものを使う経路は本作業の範囲外である。
- (2)、(4) の絶対・相対の決定: 上記の実行結果がない。ISO 9660 の基準は外部標準の確認が必要（本台帳では未検証）。
- 2ISO 経路の矛盾: §3 補足を参照。
- Hu-Go と HuC ライブラリの内容: ライセンスが確定しないため根拠にできない。maintainer によるライセンス判断が必要である。
- 成功値: 定義しない。

## 6. 次の作業

1. ライセンス判断（maintainer）: Hu-Go `bios.c` と HuC ライブラリの採否。判断が出るまで、これらの内容は根拠にしない。
2. 実行可能な呼出先で、§3 の決着条件を測定する（#1、#12 の所管と重複しないよう調整する）。記録は §4a の観測項目に従い、
   `tools/cd_base_probe.judge` に渡す。呼出先を得るまで、(1)〜(6) は BLOCKED / UNKNOWN のままである。
3. ISO 9660 のエクステント基準（ECMA-119）の本文を確認する。
