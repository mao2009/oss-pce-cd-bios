# #800: 商用IPL起動経路への実験API統合（G5 P0、親 #635）

**状態: DEV-only / 合成媒体のみ / NOT a compatible System Card BIOS。** System Card互換性、BIOS ABI、CD_BOOT/CD_READのVERIFIEDを主張しない。`release_eligible=false`を維持。商用媒体は本PR範囲ではNOT_RUN（`./rom`の存在確認・列挙・読取りなし）。

> 2026-11-18注記: 上記のNOT_RUNはPR #801の範囲の記述であり、後続のIssue #802上でOwner許可の商用観測（標準G5イメージおよび#803 SIMULATED診断の記録）がHISTORICALLY_OBSERVEDとして別途残されている。本書の合成証拠・契約記述は当時の事実として維持し、詳細は[2026-11-18 addendum](g5-evidence-2026-11-18.md)を参照。

基準: PR #799統合後の最新main `43e86002`。子Issue #800、1 Issue = 1 PR。

## 目的

G5 DEVプロファイル（合成PCEX IPLで動作確認済み）を、市販CDのIPL起動経路（PUBLIC multi-sector INFO/BODY）で動作させ、実際のブロッカーを特定できる状態を作る。設計資料で終わらず、合成媒体で実装・実コア検証まで進める。

## A. IPL起動経路の差分（実測・コード根拠付き）

| 項目 | 通常DEV/G5実験ローダー（PCEX） | PUBLIC商用観測ローダー（`ipl_loader_rom.s` + `IPL_PUBLIC`） | 本Issueの扱い |
| --- | --- | --- | --- |
| ヘッダー検証 | PCEX magic `PCEX`、len 1..255、entry、checksum（`encode_sector`/`oracle`） | INFO SIG `PC Engine CD-ROM SYSTEM`、IPLBLK 24-bit（>=2、21-bit範囲）、IPLBLN 1..31、IPLSTA/IPLJMP（`$3000`-`$E000`）、MPR2..6 raw 0..7、bytes 16..31無視 | PUBLIC検証を再利用（`validate_info`/`copy_body`/`verify_body` unchanged）。PCEXとして解釈させない |
| RESET→IPL | `experimental_bios_reset`→XSEL検証→`cd_entry_selected`→READ(6)1セクタ→PCEX検証→`$3000`固定コピー→`EXEC`→MARK復帰 | `reset`→INFO READ（INDEX01+1）→全項目検証（最初のRAM書込み前）→BODY READ×N→IPLSTAへコピー（MPR2..6切替え）→readback検証→MPR設定→IPLJMPへ`jmp` | 両経路のRESETは`sei`先頭、MPR0=`$FF`/MPR1=`$F8`/SP=`$FF`/IRQ maskedで一致。相違は選択・検証・ロード先 |
| INDEX01/LBA選択 | `experimental_track_selection.selection_from_cue`のXSEL記録（track 1..99、21-bit LBA、media_identity束縛） | `commercial_media.data_track_index01_lba`のstat-only LBA（CUEテキスト+lstatのみ、BIN内容不読）+ ビルダー埋込み16-bit `IPL_SEL` | 本イメージは同一合成selから両方を導出（`IPL_SEL`+XSEL）。不一致はビルド時・ゲスト時にfail-closed拒否。商用21-bit>16-bitはBLOCKED（切捨てなし） |
| MPR/RAM初期化 | MPR1=`$F8`固定、BUF `$2800`、EXEC `$3000`固定、RES `$2300`、MARK `$3F00` | BUF `$2200`、RB `$2F00`、payload結果 `$2F80`、load area可変IPLSTA（`$3000`-`$E000`、MPR2..6で`$40`/`$60`/`$80`/`$A0`/`$C0`切替え） | 起動時IPLJMPではMPR0=`$FF`/MPR1=`$F8`/SP=`$FF`/A/X/Y=0/IRQ maskedで一致（proto/linkのpreconditionを満たす）。loaderは`$2502`/`$2503`/`$2400`/`$20F2`-`$20FF`をseedしない（payloadがseed） |
| APIジャンプテーブル | G5 profile: 通常18 + `$02`→proto、`$03`→link、`$33`→dspoff（3窓JMP、BOOT `>=$F000`） | 通常18のみ（`$02`/`$03`/`$33`は`LDX #slot; JMP api_unimplemented`の5-byte fallback） | 本イメージはPUBLICローダーに3窓配線を移植（API-area差分＝3窓のみ、`$4A`はstubのまま）。無条件RTSパッチ禁止 |
| 合成PCEX前提 | `$3000`固定、単一セクタ、PCEX checksum、XSEL記録依存 | なし（INFO/BODY、IPLSTA可変、readback検証、PUBLICはheader sumなし） | 合成PCEX前提を持ち込まない。PUBLIC検証経路をそのまま使う |

## B. 再利用できたAPIと未解決契約

再利用（コード・テスト共に再利用、重複実装なし）：

- 通常DEV 18件（`src/overrides/slot_*.s`、`assemble_api_objects`経由、byte-identical）。`EX_JOYSNS $21`・`EX_SCRSIZ $23`含む。PUBLIC IPL経路でも実復帰を確認（coexist）。
- 実験CD_BASE `$02`（`src/cd_base_proto.s` unchanged、BOOTリンク）。
- 実験CD_READ `$03`（`src/cd_read_link.s` unchanged、BOOTリンク、EXPERIMENTAL契約のみ）。
- 実験EX_DSPOFF `$33`（`src/ex_dspoff.s`を`src/ipl_loader_g5_glue.s`から`.include`、single source）。
- パッチ共通部品（`_patch_slot02_to_proto`、`_redirect_slot33_to_body`、`_redirect_slot03_to_link`、`unified_api_windows`）を`tools/build_experimental_bios_boot.py`からimport再利用。
- ローダー共通部品（`LOADER_SOURCE`、`REQUIRED`、`parse_labels`、reset/SEI+vector検査、16-bit `IPL_SEL`契約）を`tools/build_dev_ipl_loader_bios.py`から再利用。
- 選択・媒体検証（`verify_dev_rom`、およびsel/track束縛による選択整合性）を再利用・緩和なし。CUE/BIN媒体identity検証（`verify_media_identity`）は再利用せず、主張しない。
- `$4A`はstubのまま（`build_slot02_return_loader`の即時RTSは独立SIMULATEDコントロールとして区別、配線しない）。

未解決契約（合成成功を真正互換としない、UNKNOWN/BLOCKED維持）：

- `cd_base_proto`のXSEL依存 vs IPLローダーの`IPL_SEL`埋込み（本イメージは同一selで束縛、商用21-bit>16-bitはBLOCKED）。
- `cd_read_link`のwindow/state seeding前提（`$2400` proto state + `$20F2`-`$20F7` window/block、MPR1=`$F8`、work-RAM stack、IRQ masked）。loaderはseedしない。
- `ex_dspoff`のshadow seeding前提（`$2502`/`$2503`=`$A5` marker、no-direct-CR-write discipline）。loaderはseedしない。
- MPR2..6のIPLヘッダー由来値（PUBLIC raw 0..7→`$80`+index）。proto/linkはMPR2..6を触らない（preserved）。
- 商用呼出し規約（genuine ABI）は全てUNKNOWN/BLOCKED。合成復帰は実サービス復帰の証明であって互換証明ではない。

契約不一致が判明した場合はバイパスせず、合成テストで再現して停止原因を報告する（本PRのbadsel/badinfo/unimpl/refuse相当）。

## C. 合成IPLの実行・復帰・停止点（pinned real-core実測）

固定版: cc65 `71746c829e77f74c2b601a7d16821170c2610df3` + Geargrafx `b49ae82a012eade566d72e9d61c9d95a5caa4da3`、SEL 265（`0x0109`）、track 1、N=5、IPLBLK=2、IPLSTA/ENTRY `$3000`、MPR raw `[0,1,2,3,4]`→`[$80..$84]`、budget 20M、cold/warm両方。

- RESET到達: `reset`（`sei`先頭）→ INFO 266 + BODY 267..271のREAD(6)をPHASEで確認（chain/coexist共に6 BODY READs）。
- IPL制御移行: INFO検証→BODYコピー→readback検証→MPR設定→IPLJMP `$3000`へ`jmp`。payload先頭コード（`cld; lda #$00...`）がRAM `$3000`に一致。
- API呼出順序: chain `$02`→`$03`→`$33`、coexist `$02`→`$21`→`$23`→`$4F`（order markersで証明、cold/warm一致）。
- API入口・実処理・復帰: 5-gate相当（arrival + post-return position + SP restored + MPR consistent + order matches）を測定。proto state `[0,1,9,1,0,0,0,0]`、LINK_RES `[0,8,0,8,0,0,0,0,1,11]`、shadow `[0x32,0xA5]`、MPR01 `[0xFF,0xF8]`、SP `$FF`。
- RAMへの実データ転送: `$2800`がBODY sector 0（LBA 267、payload code + pattern）と2048B一致。INFO 266と異なるsectorを読むことでstale boot dataとの一致を排除。loaderの`$2200` BUFとは別領域。
- SP/MPR維持: SP `$FF`（`$21FF`）、MPR1 `$F8`、MPR0 `$FF`（VDC window前後でrestore）。MPR2..6はIPLヘッダー由来値を維持。
- cold/warm差: なし（`calls_match` + `halts_match`相当、records[0]==records[1]、stepsは比較しない）。
- 最初の停止原因: chain/coexistは`cd_halt_ok`（payload_done）で正常halt（ブロッカーなし、次は商用IPL実測）。unimplは`api_unimplemented`（X=`$04`、SP=`$FD`=JSR push、never return）。badinfoは`cd_halt_fail(20)`（signature mismatch、copied 0、payload未実行、load area無傷）。
- fake RTSとの区別: 全配線がJMP（`0x4C`）、callee先頭が実処理（PHP/PHA prologue）、副作用（state/LINK_RES/shadow/transfer）あり。bare RTSでは再現できないことをnegativeで固定（`$4A`はstubのまま）。

## RED→GREEN

- Host負例を先に作成（`tests/host/test_ipl_loader_g5_host_800.py` Part B）。`tools/build_ipl_loader_g5.py`不存在ではSKIP=RED、実装後に15 tests GREEN。
- 実コアは合成IPLでcold/warm両方GREEN（`tests/integration/test_ipl_loader_g5_800.py` 6 tests）。
- 不正selection（track範囲外、sel範囲外・bool・str）、媒体不一致（tampered base）、未実装API（`$04` stub halt）、ROM改変（API-area外差分、`$4A`変更、reset/vector不一致）をfail-closed拒否。

## Host / Geargrafx real-core / CI

- Host: `make test` 5569 tests OK（c3分類追加後、3 failures解消）。
- Real-core: `test_ipl_loader_g5_800` 6/6 PASS（pinned Linux、TOOLS_DIR `/tmp/oss-pce-cd-bios-tools-1000`）。
- `make check`: `contracts.py` PASS、`check.py`はshallow historyでBLOCKED（worktree環境既知制限、CI full historyではPASS見込み）、`c3_static_check`は分類追加でPASS、`verify_build_diff` PASS。
- 既存G5 ROM SHA不変: `build_dev_g5`のtriple-linkビルダー・正常DEV ROMは本PRで変更なし（`verify_dev_rom` + byte-identical + API-area 3窓検査で保証）。既存`test_dev_g5_profile`/`test_unified_*`は本PRで変更なし（`make test`に含めてGREEN）。

## 商用媒体

NOT_RUN。`./rom`の存在確認・列挙・読取りなし（タスク厳守）。（2026-11-18注記: 本PR範囲の記録。後続のIssue #802上のOwner許可観測は[2026-11-18 addendum](g5-evidence-2026-11-18.md)参照。）`commercial_media`をimportしない。opt-in機構（`--commercial`、safe CUE選択、read-only、合成PASS receipt gate、出力サニタイズ）は再利用可能だが本PRでは実行しない。公開物に実名・パス・ハッシュ・CUE/BIN内容・生トレース・ダンプ・IPL命令列・ゲーム資産なし。公開はAPI番号・停止分類・最小状態要約のみ。

## 次に実装すべきAPIまたは起動処理

1. 商用IPL実測（Owner明示許可後のみ、既存opt-in経路で structural-only観測、API番号・停止分類のみ公開）。
2. `$02`の21-bit LBA対応（現16-bit `IPL_SEL`では商用LBA>16-bitがBLOCKED。24-bit化は#744のloader側とproto側の両契約変更が必要）。
3. `$03`の商用呼出し規約（block/windowのgenuine layout UNKNOWN。合成window=16は暫定）。
4. `$33`の商用shadowアドレス（Zeograd `$20F3` vs 本実装`$2502`の不一致未解決）。
5. `$4A`のgenuine契約（現SIMULATEDのみ。実装は別Issue #793参照）。

## MERGE_READY判定

- 本PRはDraftのままOwner承認なしにマージしない。
- Host全GREEN、実コア合成GREEN、c3分類追加済み、安全ゲート維持、商用NOT_RUN、公開サニタイズ済み。
- 残作業: 独立コードレビュー0件 + CodeRabbit再レビューの指摘解消（本PR作成後に実施）。

## 安全ゲート

- reference BIOS JSR: 0/81
- genuine ABI VERIFIED: 0/81
- DEV_CALLABLE: false
- release_eligible: false
- DEV_EXPERIMENTALを真正System Card互換扱いしない（全manifest・docにNO_CLAIM明記）

G5_OBSERVEDなし（タイトル画面未観測）。
