# Issue #635: API入口の直接観測（合成対照の計測器）

**状態: DEV-only計測器 / 合成媒体のみ / 観測専用ゲスト非変更。商用互換・BIOS ABI・`release_eligible=false` の主張なし。**

## 目的

停止時Xだけから最初の未実装APIを断定しないために、IPL payload（WRAM `$3000-$7FFF`）からBIOS領域（`$E000-$FFFF`）への最初の制御移行を直接記録する。

- 呼出し元PC、入口PC、ジャンプテーブル位置、SP差分によるJSR/JMP区分、入口直前A/X/Y/P/S・MPR・SP、戻り先推定（`caller+2`一致でJSRを裏付け）、IDR/IRR
- 商用ゲストのバイト（オペコード・ペイロード）をAPICALLに出力しない。APICALLはアドレス・レジスタ・差分・戻り先構造値のみ。RUN/HITのRAM dump（`buffer_hex`等）はローダー判定用の既存ダンプであり、ハーネスは公開JSONに写さない（ローカル限定）。スタック2バイトはMPR1=`$F8`（WRAMスタック）前提。MPR1が異なる場合はUNKNOWNとして扱う

## 合成対照（`make api-entry-integration`）

6ケース cold+warm： `jsr_slot02`、`jsr_slot03`、`jmp_slot02`、`direct_stub`（テーブルを経ずX=`$02`でスタブへ）、`nocall`、`spin`（予算負例）。

- `jsr_slot02`: table `$E006`、slot2、delta2=JSR、戻り=`caller+2`、entry regs preset、halt X=2
- `jsr_slot03`: 同様にslot3
- `jmp_slot02`: table `$E006`、slot2、delta0=JMP
- `direct_stub`: tableでない（`$E24F`直達）、X=2でもDIRECTとして識別（停止時Xのみの判定との差を実証）
- `nocall`: APICALL 0件、`cd_halt_ok` halt
- `spin`: APICALL 0件、`budget`（haltでない。harnessは予算をPASSしない）

これらで「X=`2`ならslot2」と読むだけの分類と区別できることを証明する。合成で証明されない場合、商用のslot判定を確定扱いしない。

## 観測専用性

- ゲストRAM書込・レジスタ書込・割り込み投入・MMIO読取なし。状態はpinnedコアのstate pointer読取のみ（`memory->Read(first_pc)` は既存probeと同一のROM `SEI`先頭確認。副作用なし）。スタック2バイトは `GetWorkingRAM()` の読取（書込なし、MPR1前提）
- 入口観測はwatch比較のみで実行順序を変更しない。観測あり/なしの自動対照は本ハーネスには含まれない。二観測一致（本debugger vs Frame Tracer normal-frame）は手動で外部比較する（自動証明ではない）
- APICALLは上限8件（cycle毎）。超過時は `apicall_truncated=true` をRUNに記録する。先頭8件を超える呼出しは未観測として扱う

## 商用再測定（opt-in、手動）

`geargrafx_api_entry.py --commercial-rom <DEV BIOS> --commercial-cue <ローカルCUE> --commercial-labels <labels>`
（入力は読取専用。出力は構造要約のみ。生トレース・媒体情報はローカル限定）

## 既知の制限

- JSR/JMP区分はSP差分と戻り先一致による分類であり、オペコード復元ではない。JSRの戻り先一致はMPR1=`$F8`前提。非JSRのスタック2バイトは任意内容のためharnessは比較しない
- IRQ区別は入口PC（vector `$FFFx`かtableか）とSP差分による分類。IDR/IRRは記録するが、割り込み注入実験（統合）は未実施（＝NOT_RUN。安全条件未構成のため断定しない）
- ジャンプテーブル範囲は `$E000-$E0F2`（81*3）。境界外のBIOS到達はtableでない
- B11表示・B12タイトルは本計測器の対象外
