# Issue #635: API入口の直接観測（合成対照の計測器）

**状態: DEV-only計測器 / 合成媒体のみ（商用はopt-inローカルのみ） / 観測専用ゲスト非変更。商用互換・BIOS ABI・`release_eligible=false` の主張なし。**

## 目的

停止時Xだけから最初の未実装APIを断定しないために、IPL payload（WRAM `$3000-$7FFF`）からBIOS領域（`$E000-$FFFF`）への最初の制御移行を直接記録する。

- 呼出し元PC、入口PC、ジャンプテーブル位置、SP差分によるJSR/JMP区分、入口直前A/X/Y/P/S・MPR・SP、戻り先推定（`caller+2`一致でJSRを裏付け）、IDR/IRR
- 商用ゲストのバイトをAPICALLに出力しない。APICALLはアドレス・レジスタ・差分・戻り真偽値（`stack_check`/`return_matches_caller`）のみ。戻り先の数値・スタックバイトはC++内部で判定する（MPR1=`$F8`前提。想定外はUNKNOWN）
- RUN/HITのRAM dump（`buffer_hex`等）：合成modeのみ生成（ローダー判定用）。商用modeではC++が生成しない（fail-closed境界）。商用出力にはRAM dumpが一切現れない

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

## 商用再測定（opt-in、手動・ローカル限定）

`geargrafx_api_entry.py --commercial-rom <DEV BIOS> --commercial-cue <ローカルCUE> --commercial-labels <labels> --commercial-output /tmp/opencode/issue635-private/<file>.json`（直接子のみ。専用親0700を事前に作成。上書き不可）
（入力は読取専用。ログにパスや媒体識別を出さない。既存ファイルは上書きしない。保存失敗はFAIL。`git worktree list` 登録worktreeと祖先`.git`を確認しfail-closed。別クローン全網羅を主張しない）

書込みは親fd固定（`O_DIRECTORY|O_NOFOLLOW` + fstat + `dir_fd` で `O_EXCL|0600` 作成）。親置換後もfd固定先へ書く（symlinkターゲットへ逃げない）。部分書込失敗時は部分ファイルを残しFAIL（削除しない。未完成として扱う）。親dir自体の移動（rename）に対する完全防御はしていない（残存リスクとしてはfd固定のinodeへ書く）

判定は次を区別する：`API_ENTRY_CONFIRMED`（入口観測+停止確認）、`PRE_API_BLOCKER_OBSERVED`（入口0件+IPL拒否halt）、`UNKNOWN`（入口なし理由不明）、`INCOMPLETE`（切断）、`BUDGET`（予算）、`FAIL`（器異常）。「正常終了」と「最初のAPI特定」を混同しない

## 既知の制限

- JSR/JMP区分はSP差分と戻り先一致による分類であり、オペコード復元ではない。JSRの戻り先一致はMPR1=`$F8`前提。非JSRのスタック2バイトは任意内容のためharnessは比較しない
- IRQ区別は入口PC（vector `$FFFx`かtableか）とSP差分による分類。IDR/IRRは記録するが、割り込み注入実験（統合）は未実施（＝NOT_RUN。安全条件未構成のため断定しない）
- ジャンプテーブル範囲は `$E000-$E0F2`（81*3）。境界外のBIOS到達はtableでない
- B11表示・B12タイトルは本計測器の対象外
