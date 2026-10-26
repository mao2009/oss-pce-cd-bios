# MA_MUL16S DEV 契約（設計 Issue #479、実装 Issue #494）

DEV API 候補 MA_MUL16S（slot `$4F`、候補エントリ `0xE0ED` = HYPOTHESIS）の**プロジェクト独自契約**。
設計は Issue #479、実装は Issue #494（`src/overrides/slot_4F.s`、実コア試験 `tests/integration/geargrafx_dev_mul16s.py`）。
実装の結果と設計との差は文末「実装結果（Issue #494）」に記録する。
参照 System Card の MA_MUL16S 呼出規約は **UNKNOWN**（`spec/api_contracts.json` の arguments / returns / clobbered 全て UNKNOWN）。
下表は DEV ROM 専用の定義にすぎず、VERIFIED / DEV_CALLABLE を主張しない。兄弟ルーチン MA_MUL16U（slot `$41`、`src/overrides/slot_41.s`）の
スタック渡し規約を踏襲する。

## 入出力（スタック渡し）

| 項目 | 定義 |
| --- | --- |
| 呼出側 | `PHA` 被乗数 lo、`PHA` 被乗数 hi、`PHA` 乗数 lo、`PHA` 乗数 hi、`JSR`。各 16-bit は 2 の補数の符号付き（-32768..32767）。全 2^32 組が有効 |
| 出力 | 同じ 4 バイトに 32-bit 符号付き積の 2 の補数を上書き: 先に push した順に bits 0-7、8-15、16-23、24-31。呼出側が 4 回 `PLA` する |
| 値域 | -1,073,709,056（`-32768×32767 = $C0008000`）..1,073,741,824（`-32768×-32768 = $40000000`）。32-bit 符号付きに必ず収まりオーバーフローは存在しない（エラー戻りなし） |
| A, X | 破壊（値は契約外） |
| Y | 保存 |
| P | 呼出前の値を `PLP` で復元。C/Z/N/V は**結果を反映せず**呼出前の値のまま（結果フラグは返さない）。I は変更しない。D は内部で `CLD` し 2 進演算、復帰時は呼出前の D。B は格納されないため比較対象外。T は復帰時に 0（固定コア上。実機の PHP が T を push するかは UNKNOWN）|
| S | `RTS` 後の S は **JSR 直前の S** と同一（呼出側が 4 バイトを pop する前の値）。「入口 S」は JSR が戻り番地 2 バイトを push した直後の S を指す。`check_case` の `s_in`/`s_out` は JSR 直前・RTS 直後の値。S 基準の対応（slot_41 試験の caller SP と同じ基準）: caller SP（4 回の PHA の前）= `s_in`（JSR 直前）+ 4 = 入口 S + 6。入口 S `$08..$F9` は caller SP `$0E..$FF`、`s_in` `$0A..$FB` に当たる |
| メモリ | スタックページ `$2100-$21FF` のみ。ゼロページ・BSS・I/O・MPR 書込みなし。MPR1 が RAM `$F8` を写像し `$2100` がスタックであること（JSR が成立する通常前提）。再入可 |
| スタック予算 | ルーチン内の push は P・Y・作業バイトを含め **8 バイト以内**（JSR 戻り 2 バイトは別）と定める。最小構成は P・Y・上位ワード 2・乗数退避 2 の 6 バイトで、8 は余裕を含む上限（実装は 8 以下であること）。前提: 入口 S（JSR push 後）が `$08..$F9`。`$21xx,X` の添字が `$FF` を超えず、スタックページ内で wrap しない |

符号なし 16×16 のシフト加算（MUL16U と同一）の後、mod 2^16 の恒等式で上位ワードを補正する:
被乗数 < 0 なら上位から乗数を引き、乗数 < 0 なら上位から被乗数を引く（両方負なら両方引く）。
補正に被乗数 hi・乗数 hi の元の値が要るため、乗数はシフトで破壊される前に退避する（これがスタック予算 8 バイトの根拠）。

## T フラグ（T=1 は契約外）

T=1 での呼出は **契約外**（挙動を定義しない）。MA_MUL16U / MA_MUL8S の DEV 契約と同じ扱い。評価:
- T モードの対象は ADC/AND/EOR/ORA（SBC は対象外）。乗算は `ADC` を使い、T=1 ではメモリ間モードになり A に計算されずゼロページを壊すため、T=1 を許すには全 ADC を保護する必要がある。
- 固定 Geargrafx コア（`b49ae82`）では `SET` の効果は直後の 1 命令で消費され `PHP` は常に T=0 を push するため、`SET; JSR` では JSR が T を消費し、ルーチンは T=0 で入り先頭命令が `PHP`。よって実コアで T=1 のまま ADC に到達するケースを構成できない。
- 先頭 `PHP` の直後に `CLT` を置く防御は 1 命令で実装可能で、復帰 P は PHP が積んだ T=0 の値で決まるため契約を変えない。ただし実機で T=1 のまま入る経路（および PLP が T を設定するか）は **UNKNOWN / NOT_RUN** のため、`CLT` を入れても「T=1 対応」とは主張しない。実装 Issue では `CLT` の採否を任意とし、T=1 テストは追加しない。

## 境界値（host oracle `tools/ma_mul16s_model.py` が定義）

| a × b | 積（32-bit 16 進） | 狙い |
| --- | --- | --- |
| -32768 × -32768 | `40000000` | 最大正値、両補正 |
| -32768 × 32767 | `C0008000` | 最小値 |
| 32767 × 32767 | `3FFF0001` | 補正なし最大 |
| -1 × -1 | `00000001` | 両負 |
| -1 × 1 / -1 × 32767 | `FFFFFFFF` / `FFFF8001` | 符号拡張 |
| -32768 × -1 / × 1 | `00008000` / `FFFF8000` | 下位ワード境界 |
| 0 × n, n × 0, 1 × 1 | `0`, `0`, `1` | 零・単位元 |

その他: `$00FF/$0100/$FF00/$FF01`（バイト境界）、`$5555/$AAAA`（交互ビット）、`$1234/$EDCC`。a と b の入替（交換則）も確認する。

## host oracle と負例

`tools/ma_mul16s_model.py`: `expected()`（Python 整数乗算）と `shift_add()`（実装予定の符号なしシフト加算＋補正）の 2 つを独立に持ち、
`check_case()` が積フレーム 4 バイト・Y・S・P（B 除く、T=0）を判定する。`tests/host/test_ma_mul16s_model.py` は境界値、
ランダム 20,000 組＋エッジ 16×16 組での 2 モデル一致、判定器の破損検出（各フレームバイト・Y・S・P・caller T=1）を確認する。
負の対照: 被乗数補正なし・乗数補正なし・補正両方なし（符号なし乗算）の 3 変異が不一致を出すことを縮小領域で確認する。
全空間での不一致数は解析的に、被乗数補正なしが 32768×65535 = 2,147,450,880 組（被乗数 < 0 かつ乗数 ≠ 0）、乗数補正なしも同数。
host の結果は実装の正しさを示さない（アセンブルされたバイトは未検証）。

## 実コア確認ケース（次 Wave の実装 Issue 用、現状 NOT_RUN）

固定 cc65 + Geargrafx コアで、乗算はゲスト CPU が行いホストは入力書込みと RAM 読出しのみ。2^32 は網羅不能のため:
1. 上記境界値 × 交換、`EDGES` 16×16 全組。乗数の全 65,536 値 × 被乗数 {`$8000`,`$7FFF`,`$FFFF`,`$0001`,`$0000`,`$1234`}、および被乗数の全 65,536 値 × 乗数 {同 6 値}（合計約 79 万呼出、所要時間は実装 Issue で見積る。符号付きの両補正経路を網羅）。シード固定ランダム数万組。
2. 各呼出でゼロページ・BSS・フレーム外スタック余白が不変であること（「メモリはスタックのみ」の検証。再入可は主張するが専用テストは設けず、共有メモリを持たない構造で担保）。Y（変化させた値）、S、P（C/Z/N/V/D/I を変化させ、B 除き保存・T=0）、フレームより上位のスタックと、入口 S から 8 バイトより下（S-8 以下）が不変であること（入口 S-7..S はルーチンの作業域として変化を許容）。
3. 入口 S の境界: 最小 `$08`、最大 `$F9`（予算 8 バイトの検証）。
4. 負の対照: 被乗数補正の命令列、乗数補正の命令列をそれぞれ NOP にした ROM が期待どおり FAIL すること。
5. T=1 ケースは追加しない（契約外、NOT_APPLICABLE）。独立エミュレータ・実機は NOT_RUN。

## 結論と残件

- 参照 BIOS の引数/戻り値/フラグ/タイミングは UNKNOWN のまま。独自契約を参照 ABI と同一視しない。
- slot `$4F` は DEV ROM にだけ接続した。`state` は NOT_READY、`abi_contract` は PROJECT_SPEC_ONLY のまま。VERIFIED / DEV_CALLABLE は付与していない。

## 実装結果（Issue #494）

- 設計どおり: スタック渡し、被乗数 hi / 乗数 hi の符号で上位ワードを補正（mod 2^16 恒等式）、Y・S・P（B 除く、T=0 前提）保存、ゼロページ・BSS・I/O・MPR に触れない。
- 設計との差（矛盾ではなく詳細化）: スタック消費は P・Y・乗数コピー 2・上位ワード 2 の **6 バイト**（予算 8 以内）。入口 S の下限は設計どおり `$08`（実際の最低書込は S-5 = `$03`）、上限は `$F9`（`$210C,X` が `$21FF`）。
  フレーム各バイトの位置は X を 6 push 後の S として X+9 乗数 hi / X+10 乗数 lo / X+11 被乗数 hi / X+12 被乗数 lo、乗数コピーは X+3(lo)・X+4(hi)。
- `CLT` は追加していない（設計の任意項目。T=1 は契約外のまま、実機の T 挙動は UNKNOWN / NOT_RUN）。
- 実コア試験の「SP」は呼出側 SP（4 回の PHA の前）。入口 S = 呼出側 SP - 6 なので、入口 S `$08` / `$F9` は呼出側 SP `$0E` / `$FF`。
- 副作用の検証は最終 RAM 差分に加え、コアの CPU アドレスブレークポイントでルーチン自身の命令（PC 窓）による書込み・読出しを命令単位で監視する（同値書込み・書いて戻す操作も検出）。許可: 自身の push 6 バイト（呼出側 SP-11..SP-6）とフレーム 4 バイト（SP-3..SP）のみ書込可。JSR 戻り番地 2 バイトを含むそれ以外のスタック・ZP・BSS・I/O・ROM への書込み、ZP・BSS・I/O・自フレーム外スタックの読出しは違反。MPR0〜7 は全命令後に比較。
- 手動の大規模試験: `make dev-mul16s-sweep`（乗数 65,536 値 × 被乗数 6 値、被乗数 65,536 値 × 乗数 6 値、呼出側 SP `$FF` と `$0E`、計 1,572,864 呼出）。CI の `make dev-integration` には境界値・EDGES・符号組合せ・P フラグ全 64 組・固定シード乱数・変異試験を組み込む。

## ABI-VERIFIED 準備（Issue #89、準備のみ）

- 基準: main `ec496e9`。本節は準備の記録であり、参照 BIOS の callee は実行していない（JSR なし）。VERIFIED は 0/81 のまま。`spec/api_contracts.json`、`spec/dev_implementations.json`、`release_eligible`、`VERIFIED_PATH_ENABLED`、JSR ゲート、承認欄は変更しない。
- DEV ROM の出力は参照契約ではない。

### System Card 版の対応（明確ではない）

| 根拠 | 観測 | 扱い |
| --- | --- | --- |
| HuCC `include/hucc/pcengine.inc`（`54f5c736`） | equates は `$4C` まで。`$4D–$50` の equate なし | `$4F` 名の裏付けなし |
| Hu-Go `bios.c` selector 定義 | `$4F` に MA_MUL16S | HLE モデル。実行アドレスの証明ではない |
| `spec/api_contracts.json` slot 79 | arguments / returns / clobbered / flags などすべて UNKNOWN、entry `$E0ED` は HYPOTHESIS | 参照契約は未確定 |
| Issue #89 | 「Candidate System Card 3.0 ABI」、provenance PENDING | 版の特定は未完了。`$50` の名称衝突も未解決 |

結論: 版対応は明確ではない。VERIFIED 昇格の BLOCKED 条件に含める。

### host 側の準備（この PR）

- 符号付き 16×16 → 32-bit の境界値は `tests/host/test_ma_mul16s_model.py` の既存表を使う。
- `tests/host/test_ma_mul16s_dev.py` の `DevMul16sSensitivityTests`:
  - 変異感度: `dev_mul16s(a, b, mut=...)` が 1 か所だけ誤らせる 8 変異を、EDGES 組合せ 256 組と seed 89 の乱数 2000 組で評価する。
  - 適用確認: 各変異は原型と異なる出力を少なくとも 1 組で出すことを検査する。
  - 等価変異の除外: 当初の `ror_first_no_carry_in`（ループ先頭の上位 ROR の C 入力を 0 にする）は、先頭反復の C が常に 0 のため適用不能（等価）。除外し、低位ワード ROR の C 入力 0 化（`ror_lo_no_carry_in`）に置き換えた。
  - 静的ガード: `src/overrides/slot_4F.s` の全メモリオペランド（`#imm` と `A` を除く）が `$2101..$210C,X` の内側にあり、ZP・BSS・I/O・MPR・フレーム外スタックを触れないことを検査する。
- 結果（2256 組）: 原型の不一致 0。変異 7 種はすべて適用され、差が出た組はすべて検出された（`no_a_fix` 1091、`no_b_fix` 1133、`hi_sbc_ignores_borrow` 1019、`lo_sbc_no_carry` 1683、`adc_hi_no_carry` 1815、`ror_lo_no_carry_in` 2183、`fix_uses_final_multiplier` 1096、`rounds_15` 2232）。未検出の感度ギャップはなし。
- 手順: 先に新テストを書き、変異フックのない状態で RED（`TypeError: unexpected keyword 'mut'`）を確認した。その後フックを入れて GREEN（`tests.host.test_ma_mul16s_dev` 8 件 OK）。原型一致と静的ガードは既存コードへの回帰ガードのため、RED を経ずに最初から GREEN になった。

### UNKNOWN（未確定、本 PR では検証しない）

- 参照 BIOS の引数、戻り値、フラグ、RAM 副作用、MPR、IRQ、エラー: UNKNOWN（参照 callee を実行していないため）。
- T=1 での挙動、PHP の T push、PLP による T の設定: UNKNOWN / NOT_RUN（契約外）。
- 実コアでの変異試験（`tests/integration/geargrafx_dev_mul16s.py` の変異群）: NOT_RUN（この PR では Geargrafx を実行していない）。
- 版対応（上表）: UNKNOWN。

### BLOCKED 条件

- System Card の版と `$4F` 名について、承認済みの一次根拠（HuCC、Zeograd、Stum などの証拠と人間の承認）が得られない。
- 参照 BIOS の実行に必要な承認、ROM の SHA-256 固定、独立エミュレータ検証のいずれかが欠ける。
- 上記のいずれかが満たされるまで、slot `$4F` を VERIFIED または DEV_CALLABLE にしない。
