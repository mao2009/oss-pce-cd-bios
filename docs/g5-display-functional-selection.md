# G5 P0 表示初期化debt解消：6 API実装可能性比較と $2C EX_IRQON D2限定機能実験の選定

Status: **選定文書（Phase D/E/F）。NOT a compatible System Card BIOS. NOT genuine ABI.
NOT DEV_CALLABLE. `release_eligible=false`. G5_OBSERVED=false.**
Reference JSR 0/81, genuine ABI VERIFIED 0/81（本書変更なし）。
No System Card image was executed, disassembled, or copied.
No game-disc bytes, titles, paths, hashes, sector contents, audio, or saves appear here.
`./rom/` was not accessed in any form for this document.

Branch basis: `54cad102` (origin/main at task start; PR #837 + #839 merged).
Task: G5 P0 #802（親 #635）。第一候補 $25（Issue #47）を含む6 APIを再調査し、
原則1 APIの最小機能実装を選定する。

## 1. 結論（先に）

- **選定API: `$2C` EX_IRQON（Issue #54）。区分 D2（限定機能experimental）。**
- **実装範囲: VDC CR R05 bit3 の set（OR `$08`、他bit保持）のみ。**
  `$2502` 共有shadow経由（port RMW禁止）、AR=5（ok path）／entry AR保持（reject）、MPR0 window + SEI、
  C=0 ok / C=1 reject（shadow未初期化時、VDC/RAM無書込み）。
  CPU I・IDR/IRR・SR・VCE・VRAM/SATB・`$20F3`/`$20F7`・ゼロページ等は不変
  （`$20F3`/`$20F7` は未実装として明示）。
- **第一候補 `$25` は D1/D2-機能ともに BLOCKED**（公開機能記述が input/output
  値・レジスタ特定に足りない）。`$24`・`$36`・`$35` の機能実装も BLOCKED。
  既存 B1 preservation scaffold（`$25`・`$24`・`$36`・`$35`）は維持し、
  本タスクで置換・再定義しない。
- `$2D` は `$2C` と対称hintを持つが商用経路で到達不能（OBSERVEDなし）のため
  本タスクでは実装しない（次候補として記録）。

## 2. Phase C 再調査の差分（既存契約文書からの新規確定事項）

既存文書（docs/47, 46, 64, 63, 54, 55）の S1–S8 を再取得・再検証した。
HEAD `54cad102` 時点の再取得結果（local `/tmp` scratch、非公開・未commit）：

| ID | 再取得 | 結果 | 旧文書との差分 |
|----|--------|------|----------------|
| S1 Zeograd | 2026-11-27再fetch、19170 bytes、SHA-256 `6966984b…`（先頭一致、全文非公開） | `$25` bare heading（IN/OUTなし）。`$24` bare。`$36` bare。`$35` N/A +「`$2214/5` word を VDC #19へコピー」。`$2C` N/A +「VDC reg #5 の bit3 をsetし `$20F3`/`$20F7` を更新」。`$2D` N/A +「bit3 をclearし `$20F3`/`$20F7` を更新」。`$2A`/`$2B` は bit2 set/clear＋同mirrors | `$2C`/`$2D` の具体的bit操作記述を再確認（docs/54・55と一致）。`$25`・`$24`・`$36` には機能記述なし（一致） |
| S2 Stum | 2026-11-27再fetch、24169 bytes、SHA-256 `81a4c1c1…` | `ex_scrmod $E06F / 1.0=$E333 / 2.x=$E29D / TG=$E2B6`、`ex_dotmod $E06C…`、`ex_satclr $E0A2…`、`ex_sprdma $E09F…`、`ex_irqon $E084 / 1.0=$E466 / 2.x=$E3CF / TG=$E3E8`、`ex_irqoff $E087 / 1.0=$E46A / 2.x=$E3D3 / TG=$E3EC` | 世代別body差異を再確認（単一挙動仮定は禁止、既存通り） |
| S3a HuCC pcengine.inc | pin `54f5c736…` 再fetch、18844 bytes、SHA-256 `b6a25d33…` 一致 | L507 `ex_dotmod = $00:E06C`、L508 `ex_scrmod = $00:E06F`、L515 `ex_irqon = $00:E084`、L516 `ex_irqoff = $00:E087`、L524 `ex_sprdma`、L525 `ex_satclr`。**新規：L384-386 `vce_cr = $F8:2244` / `vdc_hdw = $F8:2245` / `vdc_vdw = $F8:2246`（各 `*UNDOCUMENTED* set by ex_scrmod (not 10MHz!)`）**。hucc-systemcard.asm（同pin、20230 bytes、SHA `4258b7c3…`）にdisplay/IRQ family wrapperなし | **L384-386 は既存 docs/47 の S3 に未記録の新規所見。** ただし caller-side RAM割付コメントであり、callee の値encoding・VDC/VCE操作・flags・timing を定義しない（§4参照）。BSL-1.0、REFERENCE_ONLY/PENDING。コード流用なし |
| S3b 旧HuC | clone内 `startup.asm` L89-92: `$2244 scr_mode / $2245 scr_w / $2246 scr_h`（`set by <ex_scrmod>`）。`library.asm` L1143-1151 にコメントアウト呼出 `ldx #xres/8 / ldy #30 / jsr ex_scrmod` | S3a と同アドレス帯（`$2244-2246`）を ex_scrmod 関連とする2世代一致。ただし旧HuCはライセンス混在・不明確のため REFERENCE_ONLY、実装根拠にしない。コメントアウト呼出は無効コードであり ABI 証拠にならない | 新規所見だが、**入力値域・出力値・VDC/VCE対応が不明**のため機能実装をgroundしない（§4） |
| S4 Hugo bios.c | master 29130 bytes、SHA `db3f227e…`（docs/55記録と一致） | `$2C`/`$2D` とも selector＋debug名のみ、`handle_bios()` 本体なし | 既存通り（動作なし） |
| S5a pinned Geargrafx `b49ae82a` | `tools/dependencies.json` pin確認（§6） | `huc6270_defines.h:37-40` CR IE bits `COLLISION 0x01 / OVERFLOW 0x02 / SCANLINE 0x04 / VBLANK 0x08`、`REG_CR 0x05`、R[5] mask `0x1FFF`。`huc6270.cpp:736` VBLANK IRQ path（CR bit3→STATUS_VBLANK）。`huc6270_inline.h:82` `GetState()` は `&m_state` 返却のみ（非侵襲）。`huc6280_inline.h` I flag、`CheckIRQs: pending = I ? 0 : IRR & ~IDR` | `$2C` bit3 = VBLANK IE `0x08` のHW対応付けは pinned-core 事実。BIOS呼出契約ではない（混同禁止） |
| S5b in-tree | `src/overrides/slot_2E.s`、`src/ex_dspoff.s`、`src/ex_memopen.s` | CR family shadow `$2502`/`$2503`/`$2504`、window規律（MPR0=`$FF`＋SEI＋absolute＋restore、AR残置、max-push）、C=0/C=1約定（PROJECT_SPEC_ONLY） | `$2C` は同shadow共有が整合的（§5）。`$2502` 新規shadowを作らず共有する（desync回避） |
| S5c/S5d manual・practice | 未pinのまま | HYPOTHESISに据置き。実装根拠にしない | 既存通り |
| S6 DEV先例 | `$25`/`$24`/`$36`/`$35` B1 scaffold統合済み（PR #828/#829/#830/#834） | NO-I/O preservation、T=1域外、MPR1事前検証、RAM-diff、mutant体系 | 規律のみ再利用、意味論は流用しない |
| S7 観測 | Issue #802 ExpE（`$2C` stub halt、9 calls、cap-32、VDC snapshot） | CUE[0] `$02→$33→$4A→$23→$25→$24→$36→$35→$2C`（X=`$2C`、entry `$E084`、A=7/X=0/Y=0/P=`$06`/I=1、IDR=0/IRR=0、AR=2/CR=0/RCR=0/SR=0、cold/warm一致）。CUE[1] `$1E→$4A→$02→$02→$21→$1C` | `$2C` 到達可能性・商用比較可能性の根拠。本書は新規測定を追加しない（Phase J/Kで再観測） |
| S8 台帳 | `spec/api_slots.json`、`api_contracts.json`、`dev_implementations.json` | `$2C`=id44、`$E084`=HYPOTHESIS、全11 UNKNOWN（変更なし） | 本書は台帳を変更しない（実装PRで `NOT_READY`→experimentalのみ、VERIFIED不変） |

## 3. Phase D：6 API 実装可能性比較表

評価軸（タスク§D）：1.公開機能記述の具体性 / 2.独立根拠の充実度 / 3.caller-side情報 /
4.入力値の確度 / 5.対象HW操作の確度 / 6.shadow/mirror契約の確度 /
7.実コア検証可能性 / 8.実装規模 / 9.回帰リスク / 10.G5への効果・診断価値。

凡例：◎=実装をgroundできる水準 / ○=部分的 / △=hintのみ / ×=なし。
D1=根拠ある機能実装 / D2=限定機能experimental / D2-B1=B1型preservation scaffold（機能なし）。

| API | 1 公開記述 | 2 独立根拠 | 3 caller | 4 入力確度 | 5 HW操作確度 | 6 shadow確度 | 7 検証可能性 | 8 規模 | 9 回帰リスク | 10 G5診断価値 | 判定 |
|-----|-----------|-----------|---------|-----------|-------------|-------------|-------------|-------|-------------|--------------|------|
| `$25` EX_SCRMOD (#47) | ×（bare heading、IN/OUTなし）／△（HuC L384-386 `$2244-2246`＋旧HuC同帯域。値encodingなし） | △（HW位置はpinnedだが `$25`→HW対応付けなし） | △（X/Y呼出はコメントアウト無効コード。HuC startupはsetupなし） | ×（X=`xres/8`・Y=30は無効コード。商用entryはExpEで観測済み A=0/X=32/Y=32/P=`$05` だが単一callsiteであり真正入力契約の証拠にならない） | ×（VCE `$0400`？VDC HSR/HDR/MWR？不明。どれもHYPOTHESIS） | ×（`$2244-2246`がshadowかcaller変数か不明。値・順序不明） | ○（B1済み。機能oracleなし） | 小（RAM書込みのみなら）だが値不明で不可 | 中（VDC誤設定は表示破壊） | ○（NEXT特定の価値はあるがB1済み。機能 debt残存） | **D1 BLOCKED・D2-機能 BLOCKED**（B1維持） |
| `$24` EX_DOTMOD (#46) | ×（bare heading） | △（VCE `$0400` clock位置はpinnedだが `$24`→VCE対応付けはHYPOTHESIS） | ×（wrapperなし。商用entry A=`$00`,X=Y=`$20`は単一callsite） | × | ×（VCE `$0400`？VDC HSR/HDR/MWR？不明） | × | ○（B1済み） | 小だが対象不明で不可 | 中 | ○（B1済み） | **D1 BLOCKED・D2-機能 BLOCKED**（B1維持） |
| `$36` EX_SATCLR (#64) | ×（bare heading。`$35` one-linerは`$36`証拠にならない） | △（SATB DMA位置はpinnedだが対象不明） | × | × | ×（WRAM SAT？VRAM SAT？SATB？不明。「zero-fill」も推測禁止） | × | ○（B1済み） | 対象不明で不可 | 大（VRAM破壊リスク） | ○（B1済み） | **D1 BLOCKED・D2-機能 BLOCKED**（B1維持） |
| `$35` EX_SPRDMA (#63) | △（N/A＋「`$2214/5` word→VDC #19」one-liner。値域・timing・完了条件なし） | △（DVSSR R13位置はpinnedだがtrigger/wait不明） | ×（wrapperなし） | ×（`$2214/5`の方向・所有・値域不明） | △（#19=DVSSRは対応付け可。書込み値・handshake不明） | × | ○（B1済み。DMA完了oracleなし） | 中 | 大（DMA誤動作リスク） | ○（B1済み） | **D1 BLOCKED・D2-機能 BLOCKED**（B1維持） |
| `$2C` EX_IRQON (#54) | ◎（IN N/A＋「VDC reg #5 の bit3 をsetし `$20F3`/`$20F7` 更新」の記述具体性。単一reverse-engineering所見であり真正契約の証明ではない。`$2A`/`$2B` bit2対称、`$2D` bit3対称の3点一致） | ◎（pinned: CR=`$05`、bit3=`0x08` VBLANK IE、mask `0x1FFF`、stale-RMW hazard→shadow正当化、VBLANK path） | ○（HuC startupが `$2C`/`$2D` 前にreg setupなし＝caller側観測。真正入力要件の証明ではない） | ○（D2範囲では入力なし：Zeograd IN N/A（公開記述）＋HuC no-setup（caller観測）。真正入力要件はUNKNOWN、A=7はresidual HYPOTHESIS、bitmask/enumはUNGROUNDED。D2がA/X/Yを使用しないことはPROJECT_SPEC_ONLY） | ◎（OR `$08`、他bit保持。mask・AR・windowはfamily実績） | ○（`$2502`共有shadowで保持を保証。`$20F3`/`$20F7`は未解決→未実装として明示） | ◎（同期済みfixtureでOR保持・未seed reject・mask mutant・window・RAM-diff・chain・冪等性、全て合成oracle可） | 小（family雛形流用、44 bytes確定） | 小（window＋shadow＋rejectでfail-closed。I/IDR不変） | ◎（商用ブロッカ到達済み。ExpF比較で復帰・NEXT・VDC変化を観測済み） | **D2-機能 SELECTED**（ExpF） |
| `$2D` EX_IRQOFF (#55) | ◎（`$2C`対称：bit3 clear＋同mirrors） | ◎（`$2C`対称） | ◎（`$2C`対称） | ◎（入力なし） | ◎（AND `~$08`、他bit保持） | ○（同上） | ◎（同上） | 小 | 小 | ×（商用経路で到達不能。ExpF比較不可） | **D2-機能 可能だが本タスクでは見送り**（次候補筆頭。対称実装の誘惑を排し1 API原則を守る） |

### 選定理由（`$2C`、第一候補 `$25` からの変更理由）

1. `$25` の新規 HuC 所見（L384-386＋旧HuC `$2244-2246`）は「3 RAM bytesへの書込み」
   を示唆するが、**入力値域・出力値・VDC/VCE対応・flags・timing のいずれも定義
   しない**。Zeograd に `$25` 機能記述は皆無。D2-機能の受入条件
   （公開機能記述＋独立HW根拠＋明確な機能範囲＋反証可能テスト）を満たさない。
   コメントアウト無効コード（`ldx #xres/8`）を ABI に昇格させることは禁止である。
2. `$2C` は **IN N/A（入力なし）の公開記述＋単一bit操作＋対称pair＋no-setup呼出
   慣行＋pinned HW bit割付** の5点が整合し、機能範囲を「CR bit3 set・他bit保持」
   に限定できる唯一の候補である。商用ブロッカ到達済みで ExpF 比較が可能。
3. `$2D` は `$2C` と同等の根拠を持つが、商用比較不能のため本タスクでは選ばない
   （複数API同時実装は契約上不可分の場合のみ許可。本件は可分）。
4. 既存 B1 scaffold（`$25`・`$24`・`$36`・`$35`）は debt として残存するが、
   本タスクで機能置換しない（既存テストの意味変更禁止）。

## 4. D1/D2/BLOCKED判定（`$2C`）

- **D1（真正機能契約の実装）：BLOCKED。** 以下が UNKNOWN のため真正を主張できない：
  `$20F3`/`$20F7` の値・順序・既存mirror関係、CPU I 作用、IDR mask、SR規律、
  handler/vector、`$2D` との対称性の厳密範囲、世代差、timing、error。
- **D2（限定機能experimental）：SELECTED。** 受入条件の充足：
  1. 具体的公開機能記述あり（S1 bit3＋mirrors、IN/OUT N/A）。
  2. 独立HW根拠あり（pinned CR bit/mask/hazard/VBLANK path＋family window規律）。
  3. 機能範囲が明確（bit3 setのみ。他は保持。mirrors・I・IDRは対象外として明示）。
  4. 合成反証可能（seed済みOR保持、未seed reject、mask mutant、window/MPR/AR/SP、
     RAM-diff、chain cold/warm）。
  5. 対象外副作用を検証（I/IDR/IRR/SR/VCE/VRAM/`$20F3`/`$20F7`/zp不変）。
  6. 明示opt-in隔離（`cd_2c="experimental"`、既定stub byte-identical、通常DEV不変）。
  7. 既存 stub/B1/DEV動作維持（`$2C` stub既定不変、他API無変更）。
  8. release非適格維持（`release_eligible=false`、VERIFIED不変、DEV_CALLABLE不変）。
  9. G5診断価値（ExpF：復帰証拠・NEXT・VDC CR変化・halt理由）。

## 5. 実装する機能・実装しない機能（Phase F）

### 5.1 実装する機能（`src/ex_irqon.s`、D2-機能 experimental）

- Entry: `JSR $E084` → table JMP → body → RTS（caller+3）。table entry は JMP のまま。
- 前提（caller義務）: MPR1=`$F8`、stackはwork RAM、caller側でIRQ masked想定
  （sibling bodiesと同一）。MPR1!=`$F8` は OUT OF CONTRACT（harnessがJSR前に検証、
  body内gateなし＝family/B1と同一理由：JSR自体がreturn bytesを書込むため）。
- 正常系（shadow初期化済み `$2503==$A5`）:
  `SEI` → MPR0=`$FF` window → `ST0=$0000` に `$05`（AR=5、ok path）→ `$2502 |= $08`
  → shadow publish → `ST1=$0002` に shadow（CR low書込み、high不触）→
  MPR0復元 → A復元 → PLP → `CLC`（C=0）。AR=5残置（ok path。reject pathは
  AR=entry ARのまま）。CR high・R[5]上位bit不触
  （mask `0x1FFF` でHW保証）。
- 拒否系（shadow未初期化）: VDC書込みなし・shadow/sentinel以外のRAM書込みなし、
  `SEC`（C=1）。A/X/Y/MPR/他flags保持。callerは初回前に `$2502`/`$2503` をseedする
  （harnessが行う。商用ExpFではgameがseedしないため reject となる見込み＝§7）。
- 保存: A/X/Y exact、P latch-exact（Cは上記約定。T=1は検証域外＝pinned-core
  T-consumption、sibling B1と同一ground）、MPR0-7不変、SP balanced
  （JSR 2＋最大3 transient、caller SP最小 `$05`、未満は域外・未実行）、
  transient stack bytes以外のRAM書込みは shadow `$2502` のみ（ok時のみ）。
- 安全性: window外でVDC触らず、MPR1 remapなし、NMI不在想定、MPR0復元、SR読取りなし
  （read-to-clear副作用回避）、VCE/PSG/timer/CD/VRAM/SATB/BURAM/joypad/zp不触。

### 5.2 実装しない機能（明示的未実装。推測追加の禁止）

- `$20F3`/`$20F7` 更新：**しない**。理由：Zeograd register table（`$20F3`=current
  VDC reg）と one-liner（updates `$20F3`/`$20F7`）の対応が未解決（docs/61参照）、
  値（CR low全値か？順序は？）・atomicity・既存mirror（gameが読むか？）不明、
  in-tree CR family（`$2E`/`$33`）も触らない。推測値を書込む方がdesync riskが高い。
  合成テストで `$20F3`/`$20F7` 不変をassertし、未実装を証明する。
- CPU I 操作（CLI/SEIのnet効果）：**しない**。window内 `SEI` は PLP で復元され
  exit I = entry I。CR-bit根拠からのCLI導入は禁止（タスクF2）。
- IDR/IRR mask・pending clear：**しない**。
- A=7 の入力要件・bitmask扱い：**しない**（A/X/Yは保存のみ。residual仮説）。
- `$2D` 同時実装：**しない**（1 API原則。可分契約）。
- handler/vector・VBlank wait・error code：なし（OUT N/A。C約定はPROJECT_SPEC_ONLY）。

### 5.3 契約表（`$2C` D2-機能。各行の証拠分類付き）

| # | 項目 | D2-機能契約 | 分類 |
|---|------|------------|------|
| F-1 | slot/entry `$2C`/`$E084` | table JMP→body→RTS caller+3 | SOURCE_CONFIRMED（台帳機構）／mapping HYPOTHESIS（S1+S2+S3一致） |
| F-2 | 入力 A/X/Y | 読まない（保存のみ）。A=7分岐なし | PROJECT_SPEC_ONLY（D2契約。根拠：Zeograd IN N/A＝公開資料上の記述 REFERENCE_ONLY、HuC no-setup＝caller側観測 REFERENCE_ONLY。真正入力要件はUNKNOWN、A=7残存値はHYPOTHESIS、bitmask/enumはUNGROUNDED。D2不使用と真正不要を混同しない） |
| F-3 | VDC操作 | CR R05 low `\|= $08`、high不触、AR=5（ok path。reject pathはAR=entry ARのまま） | PROJECT_SPEC_ONLY（D2契約。根拠：S1 bit3記述 REFERENCE_ONLY＋S5a bit/mask等のpinned HW事実＋S5b window規律。真正VDC操作契約はUNKNOWN） |
| F-4 | shadow | `$2502` 共有（CR-low image）、`$2503==$A5` marker、`$2504` reserved。single-writer family（`$2E`/`$33`＋`$2C`） | PROJECT_SPEC_ONLY（S5b/S6規律。genuine mirror `$20F3`/`$20F7`はUNKNOWN→不触） |
| F-5 | `$20F3`/`$20F7` | 不変（未実装） | UNKNOWN（genuine）→ D2範囲外として明示。合成で不変assert |
| F-6 | 出力・flags | C=0 ok / C=1 reject（PROJECT_SPEC_ONLY約定）。他flags latch-exact（T=1域外） | PROJECT_SPEC_ONLY（OUT N/Aのためgenuine C約定なし） |
| F-7 | MPR | MPR0 window＋復元、MPR1不変（`$F8`前提） | PROJECT_SPEC_ONLY（family実績） |
| F-8 | SP | balanced（max 3 transient、caller最小 `$05`） | PROJECT_SPEC_ONLY |
| F-9 | IRQ影響 | CPU I net不変、IDR/IRR不変、SR不読、VDC IE bit3のみ変化（ok時） | OBSERVED HW＋PROJECT規律。genuine handler/timingはUNKNOWN |
| F-10 | work RAM | ok時 `$2502` のみ＋transient stack。reject時 transientのみ | PROJECT_SPEC_ONLY（RAM-diff証明） |
| F-11 | 異常値 | 未seed→reject（C=1、無書込み）。SP最小未満・MPR1不正は域外（harness拒否） | PROJECT_SPEC_ONLY fail-closed |
| F-12 | timing | VBlank waitなし（即時反映。次horizontal period有効はHW事実として注記） | HYPOTHESIS→UNKNOWNとして注記のみ |

### 5.4 商用観測時の期待値・反証条件（Phase K先行定義）

- 期待（ExpF、CUE[0]、同一媒体・budget・初期条件）:
  `$02→$33→$4A→$23→$25→$24→$36→$35→$2C` までは ExpE と同一順序。
  `$2C` は functional bodyへ到達（entry `$E084`、in_table、MPR1=`$F8`）。
  shadow未seed（gameは `$2503=$A5` を書かない）のため **reject（C=1、CR不変）** が
  高確率。復帰（RTS）するので NEXT API到達または新規halt理由が観測対象。
  VDC snapshot：CR=0維持（期待、reject経路）。ARはreject pathではentry ARの
  まま（ok pathのみAR=5）。ExpEの `$2C` entry AR=2のため、ExpFでもAR=2維持が
  reject整合の期待値。CUE[1]は `$1E→…→$1C` 不変（回帰）。
- 観測記録（ExpF実施済み、OBSERVED／INFERRED／UNKNOWNの分離）:
  OBSERVED：CUE[0] 10 calls `$02→…→$2C→$2B` stub halt（cold/warm一致、
  truncなし）。`$2C` entry P=`$06`/I=1・AR=2/CR=0/RCR=0/SR=0（ExpEと同一）、
  `$2B` entry P=`$07`。CR=0維持・AR=2維持。CUE[1] 6 calls `$1C` halt不変。
  タイトル未到達（G5_OBSERVED=false）。
  INFERRED：reject経路と整合する挙動（未seed推論。商用WRAM `$2503` は
  観測していないため、shadow値の直接確認ではなく `$2C` body分岐条件と
  観測結果の整合による推論として扱う）。
  UNKNOWN：実際の商用shadow値、caller Carry分岐（`$2B` entry P=`$07`）の意味。
- 反証条件（仮説の棄却）:
  (a) 合成で seed済み `$2502=$00` に対して CR=`$08` にならない→実装誤り。
  (b) 合成で seed済み `$2502=$F0` に対して CR低位が `$F8` 以外→他bit破壊。
  (c) 合成で未seed時にVDC/RAM書込み→fail-closed違反。
  (d) 合成で I/IDR/IRR/SR/`$20F3`/`$20F7` 変化→対象外副作用。
  (e) 商用で APICALL順序が `$2C` 以前で分岐→ExpF imageの混入・配線誤り。
  (f) 商用で CR≠0（ok path）→gameが `$2503=$A5` を書いたか別writer混在（要調査）。
- 成功扱いしない条件：NEXT到達のみでは機能成功としない（reject経路の可能性）。
  機能成功は合成GREENで主張し、商用は「復帰＋NEXT＋状態」の構造観測とする。

## 6. 配線・variant計画（Phase H先行定義）

- 新規flag `cd_2c="stub"|"experimental"`（既定stub）。通常DEV ROM不変、
  既定stub byte-identical（既存32 variant生成結果維持＋旧variant byte検証）。
- 新規window関数2件のみ（無制限増殖を避ける）:
  `unified_api_windows_2c`（base＋`$2C`）と
  `unfinished`→ `unified_api_windows_4a_25_24_36_35_2c`（ExpF full）。
  全64 power setは作らない。既存32組合せの回帰は維持（既存関数・テスト不変）。
- 新規組合せ：合成unit（`$2C`のみexp）＋合成chain/商用ExpF（full exp）。
  影響範囲は `$E084` table 3 bytes＋BOOT bodyのみ。ROM 256KiB・`$FFF6`境界・
  BOOT-span（entry+len ≤ `$FFF6`）・unallowed diff拒否を維持。
- `spec/c3_static_contracts.json` に `src/ex_irqon.s` をexperimental-only fixture
  として分類（他台帳不変。VERIFIED・DEV_CALLABLE不変）。

## 7. 観測機構（Phase I。PR #839優先利用。新規probeなし）

- `run_probe(..., max_apicalls=32, vdc_snapshot=True)` を合成・商用に再利用。
  AR/CR/RCR/SR・CPU I・IDR/IRR・MPR・SP・APICALL順序を取得。
- 不足観測なし（DCR・SATB DMAは `$2C` 範囲外。VCE clockは範囲外）。
- 商用WRAM/VRAM/SATB/framebuffer取得は行わない（禁止遵守）。

## 8. 残存リスク

- `$20F3`/`$20F7` 不更新は genuine との差分になり得る（公開記述との乖離）。
  明示未実装＋合成不変証明で管理し、将来の根拠で追加する。
- C約定（C=0/C=1）は genuine 類似の可能性があるが PROJECT_SPEC_ONLY と明示。
  商用gameがCarry分岐する場合のNEXT解釈は構造観測に留める。
- 世代差（1.0 vs 2.x vs TG）は単一挙動仮定を置かず、商用媒体世代は非公開のまま。
- `$25`・`$24`・`$36`・`$35` の表示初期化debtは残存（B1のまま）。`$2C` は
  debtを解消しない。G5タイトル到達には後続の表示init真正実装が必要。
- CR=0のまま `$2C` reject→NEXT到達でも表示は初期化されない（VDC IEのみの問題）。

---

*Provenance: original research + original code (MIT). No proprietary firmware/game/reference content.
Pinned: Geargrafx `b49ae82a`, cc65 `71746c8`（`tools/dependencies.json`）。
Zeograd/Stum/HuC/HugoはREFERENCE_ONLY（再取得日・size・SHAを§2に固定、本文非転載）。*
