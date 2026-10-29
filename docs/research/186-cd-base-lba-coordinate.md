# Issue #186: CD_BASE（$02）の LBA / INDEX 01 基準候補の観測計画（非実行）

対象: スロット `$02`、候補エントリ `$E006`（`CD_BASE`）。親は #11、関連は #12。
本書は**調査と観測計画であり、実測結果は含まない**。純正 BIOS の実行・観測は、安全な参照実行境界が検証されるまで行わない。`docs/173-safety-monitor-limits.md` の裁定では、RAM 系（MPR 変更と物理バンク別名）は事前拒否可能と記録され、一般の MMIO と IRQ の書込みは BLOCKED（事前阻止不能）と記録されている。既知 guest PC の `$1804` のみ事前遮断が PASS（`tests/integration/geargrafx_mmio1804_preblock.py`、既知コードに限る）。RAM は一部 PASS、一般の MMIO・IRQ は BLOCKED のため、境界は全体として未確立とする。

- 実コア観測: **0 件**。BIOS 関連の VERIFIED は **0 件**。§1 の K9〜K11 の VERIFIED は両定義の文言には当てはまらず、既存文書内の先例に従った。owner 判断（下記）。
- 既存の調査との関係: 台帳本体は `docs/cd-base-abi-ledger.md`、六項目の独立根拠は `docs/research/146-cd-base-abi.md`、CD_READ 側は `docs/research/148-cd-read-abi.md`、合成媒体の定義は `docs/cd-boot-loader-spec.md` §5 と `docs/cd-base-contract.md` §3。本書はそれらを再掲せず、**観測の条件と候補の区別**に絞る。
- 状態語: VERIFIED / HYPOTHESIS / UNKNOWN / BLOCKED（`docs/cd-read-contract.md` の語彙に従う）。

## 1. 既知の情報（出典付き）

| ID | 内容 | 出典 | 確度 |
| --- | --- | --- | --- |
| K1 | 名前 `CD_BASE` がスロット `$02`、候補エントリ `$E006` に対応する | `docs/cd-base-contract.md` §1、HuCC `include/hucc/pcengine.inc` L470–471（ラベルの等号のみ） | HYPOTHESIS（マッピングは #1 の所管） |
| K2 | HuCC の呼出側は、`SUPPORT_2ISO` が 1 のときだけ `_al`（最終トラック番号と推定）、`_bh=$80`、`_cl=2` を設定して `jsr cd_base` を行う | HuCC 固定版 `54f5c736` の `include/hucc/core-startup.asm` L293–301、`core-config.inc` L83–84（既定 0） | HYPOTHESIS（呼出側ソースの記述。caller wrapper は VERIFIED に昇格させない） |
| K3 | 固定版全体で `cd_base` の呼出しは K2 の 1 箇所だけ | `docs/cd-base-abi-ledger.md` §2（同 commit の `grep`） | HYPOTHESIS（同上、grep の範囲。呼出側の記述） |
| K4 | HuCC の `cd_read` 呼出し（5 箇所）の関数内には `cd_base` の呼出しがない | `docs/cd-base-abi-ledger.md` §3 (4)、`hucc-systemcard.asm` L285–452、`core-kernel.asm` L580–612 | HYPOTHESIS（呼出側ソースの記述。caller wrapper は VERIFIED に昇格させない） |
| K5 | オーバーレイ読込みの呼出側は ISO ディレクトリ由来の値を加算せずに `cd_read` へ渡す。VRAM / データ読込みは `_add_sectors` でオフセットを加える | `core-kernel.asm` L531–552、L596。`hucc-systemcard.asm` L594–600 | HYPOTHESIS（呼出側ソースの記述。caller wrapper は VERIFIED に昇格させない） |
| K6 | 「通常の CD_READ は `recbase0` を加算し、16 回の読込み失敗後に `recbase1` を使う」「LBA はデータトラック先頭からの相対」「`_cl`=`$01` が recbase0、`$02` が recbase1」 | stum.de「Why do PC Engine CD-ROM² games have a second data track?」<https://www.stum.de/2026/pc-engine-second-data-track/>（Michael Stum、2026-05-01、本文 CC BY-NC-SA 4.0）の二次資料。取得日: 本 PR では未再取得。`docs/research/146-cd-base-abi.md` W1 の記録日（2026-10-16）を引く。`$80` を「CD-TNO モード」とする記述は、この記事の例コードのコメント（`_BH = CD-TNO mode`）に由来する（HuCC K2 にはこの語はない）。HuCC K2 と値が整合する | HYPOTHESIS（著者の一次資料が未確認。HuCC を参照した可能性を排除できないため、独立した二つの証拠とは数えない） |
| K7 | 「最初のデータトラックの LBA」で `recbase0/1` を初期化する | 同 W1（`docs/research/146-cd-base-abi.md` 項目 3） | HYPOTHESIS。「LBA」が INDEX 01 か PREGAP 先頭か未記載 |
| K8 | 2ISO 経路（K2）は `recbase1` だけを設定し、通常のオーバーレイ読込み（K5）は `recbase0` を使う可能性がある。両者の矛盾は解けていない | `docs/cd-base-abi-ledger.md` §3 補足、`docs/research/146-cd-base-abi.md` §1 | UNKNOWN（矛盾は OPEN） |
| K9 | 合成媒体（CUE `PREGAP 00:02:00` + `INDEX 01 00:00:00`）では INDEX 01 は LBA 150、LBA 0..149 は BIN に格納されない。INDEX 01 が 0 の対照媒体では絶対 LBA と INDEX 01 相対が一致する | `docs/cd-base-contract.md` §3、`tools/cue_analysis.py` | VERIFIED（owner 判断。ホスト側 CUE 解析の出力に限る。ツールの自己検査であり、CD_BASE の事実ではない） |
| K10 | 本リポジトリの DEV ROM の `$E006` は、fail-closed の fallback（`$E295`、`X=$02`）へ入って同じ PC に留まる。RTS がないため呼出し後の観測はできない | `docs/cd-base-abi-ledger.md` §4a（固定版 Geargrafx `b49ae82a`、`make dev-cd-base-integration`） | VERIFIED（owner 判断。定義外の可能性あり（§1 の注）。DEV ROM の測定で、BIOS の証拠ではない。再現には `make dev-cd-base-integration` が必要で、証跡 JSON（`geargrafx-cd-base-evidence.json`）はローカル生成でコミットされていない） |
| K11 | READ(6) の LBA は 21 bit。Geargrafx で T1（LBA n が BIN のセクタ n を返す）を確認 | `docs/cd-read-contract.md` T1 | VERIFIED（owner 判断。Geargrafx の転送層のみ。CD_READ 経路の測定で、CD_BASE の証拠ではない） |

**VERIFIED の範囲（owner 判断が必要な論点）**: `docs/cd-base-contract.md` の定義は「実行した呼出先（BIOS 相当）で測定された事実」であり、`docs/research/148-cd-read-abi.md`（と `docs/cd-read-contract.md`）は caller wrapper・HLE・第三者名を VERIFIED にしない。K9〜K11 は両定義の文言には当てはまらず、既存文書内の先例に従った。
- K9（CUE のホスト算術）: `docs/cd-base-contract.md` §1 の表が、ホスト側 CUE 解析を「VERIFIED（CUE 解析の範囲のみ）」とした先例。
- K11（Geargrafx の READ(6) 転送層）: `docs/cd-read-contract.md` の T1 が、Geargrafx 測定を「VERIFIED（Geargrafx のみ）」とした先例。
- K10（DEV ROM の非復帰）: 両定義のどちらにも当てはまらず、VERIFIED とした先例は無い。根拠は `docs/cd-base-abi-ledger.md` §4a の測定（固定版 Geargrafx `b49ae82a`、`make dev-cd-base-integration`、debug と release の両方で PASS）。ただし同 §4a はこの結果を「MEASURED」と記録しており、VERIFIED と書く本書の扱いは既存台帳と合わない。
- 一方、`docs/cd-base-abi-ledger.md` §1 は呼出側ソースの記述を VERIFIED として扱う。これは両定義と衝突する。本書は K2〜K5 を HYPOTHESIS にして定義側に従った。
どの扱いが正しいかは本書では断定しない。K9〜K11 を VERIFIED の件数に数えるかは owner が決める。

### 1.1 不採用の出典（根拠に使わない）

| 出典 | 不採用の理由 |
| --- | --- |
| Hu-Go! `bios.c`（revision `mckayemu/hugo@64ed226d`、HLE、L388–392 で `$2273` を加算。revision は `docs/research/146-cd-base-abi.md` の記録どおりで、本 PR では未再確認） | ライセンス・帰属が未確定。HLE であり実 BIOS ではない（`docs/cd-base-abi-ledger.md` §1 の規則）。**注: `docs/research/146-cd-base-abi.md` §1 と `docs/research/148-cd-read-abi.md` は「補強」として参照しているが、台帳 §1 の規則と矛盾するため、本書は採用しない** |
| HuC `include/huc/cdrom.asm`、`startup.asm`、`examples/asm/elmer/kickc/bios.h` | ライセンス未確認（`docs/cd-base-abi-ledger.md` §1） |
| TED2 カードの `$E006` への書込み | CD_BASE の呼出しではなくレジスタ操作（同 §2） |
| `tools/cd_base_model.py` のホスト算術 | BIOS の証拠ではない（同 §1） |
| READ(6) の測定（#118 系） | CD_READ 経路の測定。CD_BASE の基準の根拠にしない（同 §1） |
| 純正 BIOS（`rom/`）のコード・バイト列・逆アセンブル | 私有データ。本書は読まず、引用もしない |

## 2. 観測候補の一覧

### 2.1 座標系の候補

基準の定義は、`docs/cd-base-abi-ledger.md` §4 の四候補を使う。本書は候補を増やさない。

| 候補 | 式（block、base → LBA） | 区別する条件 |
| --- | --- | --- |
| `ABS_LBA` | `lba = block` | INDEX 01 が 0 の媒体と、INDEX 01 が 150 の媒体で、READ LBA が変わらない |
| `BASE_PLUS_BLOCK` | `lba = base + block` | `base` を変えると READ LBA が `base` 分だけ変わる（`base` の既定値が未知） |
| `TRACK_START_PLUS_BLOCK` | `lba = INDEX01 + block` | INDEX 01 が 150 の媒体で、ABS_LBA と READ LBA が 150 ずれる |
| `MSF_FRAMES_FROM_00_00_00` | `lba = block - 150` | 拒否の理由は PREGAP 領域（LBA 0..149、BIN に格納されない）。`(155, 0)` も REJECT（`docs/cd-base-abi-ledger.md` §4 の表）。呼出元の値の単位が MSF とは限らない |

### 2.2 条件ごとの観測候補

1 回の観測で変える変数は 1 つだけとする。確信度は、その条件の結果について根拠のある予測の強さを示す。

| # | 条件（変えるもの） | 期待される区別（候補ごと） | 確信度 | 未観測の項目 |
| --- | --- | --- | --- | --- |
| C1 | リセット直後、CD_BASE なしで CD_READ、ブロック番号を二つ以上（既存計画の 5 と 155 相当） | READ LBA − ブロック番号 が一定なら既定の基準が推定できる。`ABS_LBA` は差 0 | UNKNOWN（既定の基準値が未知） | 既定の基準値、INDEX 01 が 150 の媒体での差 |
| C2 | CD_BASE の `_al` だけを変える（他は固定） | READ LBA が変わるなら `_al` は基準の入力。変わらなければ `_al` は基準に効かない | UNKNOWN（読取り範囲が未確認） | `_al` が基準の入力か |
| C3 | `_bh` だけを変える（`$80` と他の値） | 同上 | UNKNOWN（`$80` を「CD-TNO モード」とする記述は K6 の記事の例コードにのみ現れ、HuCC K2 には無い。一次資料なし） | モード値の意味、`$80` 以外の値 |
| C4 | `_cl` を `1` と `2` で変える | HYPOTHESIS: `_cl=1` は recbase0、`_cl=2` は recbase1 を変える。通常の CD_READ の LBA が `_cl=2` で変わらなければ、K8 の矛盾の候補解の一つを支持する | HYPOTHESIS（K6 のみ） | `_cl` の意味、2ISO 矛盾 |
| C5 | `_bl`、`_dl`、`_ch` を変える（他は固定） | 変化があれば基準の入力に含まれる | UNKNOWN（HuCC の記述に該当なし） | 役割 |
| C6 | INDEX 01 が 0 の媒体（対照）と 150 の媒体（PREGAP 付き）で、同じ（block、base）の CD_BASE → CD_READ | `ABS_LBA` と `TRACK_START_PLUS_BLOCK` は、対照で一致し、PREGAP 付きで 150 ずれる。`BASE_PLUS_BLOCK` は媒体に依らない | 中（候補式からの算術。BIOS の挙動は未知のため、区別できる前提は置けない） | 実 BIOS の挙動、SCSI 層の LBA 対応（PREGAP 付き媒体は NOT RUN、`docs/cd-base-abi-ledger.md` §4a） |
| C7 | CD_BASE のあと、CD_RESET のみ | 基準が CD_RESET で消えるかどうか。消えなければ C1 と同じ差が残る | UNKNOWN | 保持期間、格納位置 |
| C8 | CD_BASE のあとの CD_READ を二回実行（間に他の呼出しなし） | 基準が次の CD_READ まで残るなら、二回目も同じ差。K8 の矛盾の検証に必要 | UNKNOWN | 基準の復帰 |
| C9 | 読込み失敗を注入し、再試行の READ LBA を観測（`_cl=2` の後） | HYPOTHESIS（K6）: 失敗後に recbase1 を使う。既存の観測計画（`tools/cd_base_probe.py`）には無い項目 | HYPOTHESIS（K6）。注入の手段は未確立 | 注入方法、K6 の真偽、#173 の境界 |
| C10 | 無ディスク、範囲外のトラック番号、INDEX 01 より前の LBA で A / X / Y / P と C フラグを記録 | 戻り値の規約。`docs/research/146-cd-base-abi.md` 項目 6 | UNKNOWN | 戻り値の有無、失敗の定義 |
| C11 | CD_BASE 前後の MPR0–7、作業 RAM の全差分（成功経路・失敗経路の両方） | 副作用の有無 | UNKNOWN（CD_READ の MPR3 の扱いは HuCC の呼出側の報告のみ、K5 の範囲外） | 副作用の全体 |

### 2.3 観測の判定規則（既存の判定器に従う）

- 実行記録は OBSERVED（値そのまま、または整合する候補の一覧）。未記録は UNKNOWN。呼出先が無い、または非復帰は BLOCKED（`tools/cd_base_probe.py` の `judge`）。
- 候補の一つにだけ一致する記録は、その候補を「残る候補」として返すだけで、確定にはしない。
- INDEX 01 が 0 の記録だけでは C6 の区別ができないため、C6 は UNKNOWN のまま残す（本 PR の独自判断。`docs/cd-base-abi-ledger.md` §4a の判定器規則は、(2) について INDEX 01 が 0 の記録を UNKNOWN にすると定めるのみで、C6 全体への適用は本 PR の判断）。
- 観測の値が `ABS_LBA` と `BASE_PLUS_BLOCK`（既定の base = 0）の両方に一致する場合、両者は区別できないと記す。

## 3. 確定できない論点（UNKNOWN）

U1〜U12 は論点の ID。§1 の K8 は UNKNOWN（矛盾 OPEN）で、U5 に対応する。K と U は別の番号体系である。

| # | 論点 | 現状 | 必要な情報 |
| --- | --- | --- | --- |
| U1 | 引数の読取り範囲（`_al`、`_bl`、`_bh`、`_cl`、`_dl`、`_ch` のどれが入力か） | UNKNOWN（呼出側の書込みのみ K2） | 実行可能な呼出先の入力感度試験（C2〜C5） |
| U2 | 絶対 LBA か、基準（INDEX 01 / 既定値）からの相対か | UNKNOWN（HYPOTHESIS は相対、K6。HLE・二次資料のみ） | C1、C6 |
| U3 | 基準値の格納位置、初期値（PREGAP 付き媒体で 150 か 0 か） | UNKNOWN（K7 は未記載） | リセット後の作業 RAM 比較、初期値の測定 |
| U4 | 基準の保持期間（CD_RESET、連続 CD_READ で消えるか） | UNKNOWN | C7、C8 |
| U5 | 2ISO 経路（`recbase1` のみ設定）と通常読込み（`recbase0`）の矛盾 | OPEN（K8） | C4、C8、C9 |
| U6 | 戻り値と失敗の規約、C フラグ | UNKNOWN | C10 |
| U7 | MPR と作業 RAM の副作用 | UNKNOWN | C11 |
| U8 | K6（stum.de）の一次資料による確認。記事の資料（System Card 1.0 開発資料）の本文 | UNKNOWN（本文未確認） | 公式資料の入手と版・ライセンスの記録 |
| U9 | K6 と K2 の独立性（記事が HuCC を参照したか） | UNKNOWN | 著者への確認（maintainer 判断） |
| U10 | ISO 9660 のエクステント基準（ボリューム先頭からの論理ブロック番号）の本文 | UNKNOWN（ECMA-119 は未確認。リポジトリ外） | 規格本文の確認 |
| U11 | `$E006` の実機マッピング | UNKNOWN（#1 の所管） | #1 の結論 |
| U12 | 合成 PREGAP 媒体の SCSI LBA 対応（エミュレータ上） | NOT RUN（`docs/cd-base-abi-ledger.md` §4a） | 非 BIOS の媒体読込みの測定（後述 N5） |

## 4. 次に安全に行える検証（非実行）

純正 BIOS を実行しないことを前提とする。本書の作成は文書のみで、実行は行っていない。

| # | 作業 | 種別 | 前提・境界 | 結果の扱い |
| --- | --- | --- | --- | --- |
| N1 | 公式 System Card 資料（archive.org 等）の入手と、K2・K6 の記述との照合 | 文献 | 版・ライセンスを記録する。本文の転記はしない | U8 を解く。入手できなければ UNKNOWN のまま |
| N2 | K6 と K2 の独立性の確認（記事の著者への問い合わせ） | 文献 | maintainer の判断が必要 | U9 |
| N3 | ECMA-119 のエクステント記述の確認 | 文献 | リポジトリ外の規格。ライセンスを確認 | U10 |
| N4 | `tools/cd_base_probe.py` の `OBSERVATION_PLAN` と本書 §2.2 の条件（C1〜C11）の対応を、既存の試験 `tests/host/test_cd_base_probe.py` で静的に照合する | 非実行（ホスト） | 合成記録のみ。BIOS の証拠ではない | 計画の欠落（C9 など）を見つける |
| N5 | PREGAP 付き合成媒体の SCSI LBA 対応を、BIOS を使わずに読込みだけで測定する | 非 BIOS の媒体測定 | 純正 BIOS を実行しないこと。Geargrafx の媒体読込み経路のみ | U12。CD_BASE の証拠にはならない |
| N6 | C2〜C11 の実行 | **BLOCKED** | #173 の MMIO・IRQ 書込みの BLOCKED の解消（RAM 系の事前拒否だけでは足りない）、呼出先の実行可能性、maintainer の許可が必要 | 実施しない |

## 5. 境界

- 本書は、CD_BASE の ABI、基準の式、戻り値、副作用を確定しない。成功値や既定値は定義しない。
- 推測の実装、成功スタブ、未承認 slot の callable 化は行わない。
- 純正 BIOS のコード、逆アセンブル、バイト列は本書に含めない。私有 ROM（`rom/`）は読んでいない。
- 出典の stum.de、HuCC（BSL-1.0）は、本文の言い換えによる引用に限る。
