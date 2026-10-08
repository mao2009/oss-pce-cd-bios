# System Card 3.0 API issue inventory (preliminary)

**Source inventory only; no implementations have been verified.** Each row is a *candidate callable service* to be researched, implemented and tested; function naming/behavior may be incomplete or conflict across sources. Intended target: software-only System Card 3.0-compatible BIOS, beginning with JP.

Sources:
- [Zeograd System Card notes](https://www.zeograd.com/download/pce_bios.html) — incomplete and partly informed by reverse engineering; claims are not automatically provenance-approved.
- [Stum 2025 System Card method offsets](https://www.stum.de/2025/pcenginebiosoffsets/) — jump table and revision-specific addresses; four additional named slots are the author's **unofficial labels**, not verified official API names.

Calls are conventionally located at candidate CPU-visible `$E000 + 3*slot`, with MPR requirements; **verify memory mapping** in #1. The entry is not a fixed implementation address. For PSG child services, `48/NN` denotes candidate selector NN in the `PSG_BIOS` dispatcher, **not** a separate jump-table location. Status for all: `HYPOTHESIS`, `provenance_status=PENDING`, `implementation_status=NOT_STARTED`, `verification_status=NOT_RUN`.

### Known conflicts

- `$0D`: Zeograd calls it `CD_SUBRQ`; Stum uses `CD_SUBRD`. Do not merge or assume identical semantics before evidence.
- `$4A`: Stum identifies `KEY_BIOS` / `EX_MEMOPEN` depending on System Card generation. System Card 3.0 target requires independent confirmation.
- `$4C`: `EX_COLORC` vs `EX_COLORCMD` naming difference.
- `$4D` and `$4E`: Stum provides `AD_STREAM_START` / `AD_STREAM_POLL` as provisional names; Zeograd omits these entries.
- `$50`: Zeograd calls `MA_CBASIS` (base conversion); Stum calls `MA_DIV8U` (8-bit division). **Conflicting behavior; block implementation until resolved.**
- `$42/$43`: Stum/Zeograd order signed then unsigned 16-bit division. Compare additional third-party code cautiously; independently verify before treating ABI as authoritative.
- Low-level register labels `al/ah/bl/bh/cl/ch/dl/dh` are *BIOS work-RAM slots*, **not eight physical HuC6280 registers**. See #3 for ABI definitions.

### Main BIOS jump-table candidates (81)

| Slot | Entry candidate | API name (candidate) | Area | Priority | Investigation focus | Issue |
| --- | --- | --- | --- | --- | --- | --- |
| `$00` | `$E000` | `CD_BOOT` | CD | P0 | CDブート画面・起動経路。起動先・中断/復帰・ディスク未挿入を調べる。 | pending |
| `$01` | `$E003` | `CD_RESET` | CD | P0 | CDサブシステムのリセットと待機完了条件を調べる。 | pending |
| `$02` | `$E006` | `CD_BASE` | CD | P0 | LBA・MSF・トラック先頭を基準としたコードトラック基準位置の設定を調べる。 | pending |
| `$03` | `$E009` | `CD_READ` | CD | P0 | CDセクター読み込み。ブロック番号、ローカル/RAMバンク/VRAM転送先、終了・エラーを調べる。 | pending |
| `$04` | `$E00C` | `CD_SEEK` | CD | P0 | CDシーク。指定形式、完了待ち、境界条件を調べる。 | pending |
| `$05` | `$E00F` | `CD_EXEC` | CD | P0 | CDからコードロード→実行。ロード範囲、スタック、バンク、制御移譲を調べる。 | pending |
| `$06` | `$E012` | `CD_PLAY` | CD | P1 | CD-DA再生。開始/終了位置・トラック指定・継続モードを調べる。 | pending |
| `$07` | `$E015` | `CD_SEARCH` | CD | P1 | CD検索・再生位置変更の動作、状態遷移を調べる。 | pending |
| `$08` | `$E018` | `CD_PAUSE` | CD | P1 | CD-DA一時停止と再開可能な状態を調べる。 | pending |
| `$09` | `$E01B` | `CD_STAT` | CD | P1 | ドライブの状態報告、エラー・無ディスク・待機中の返値を調べる。 | pending |
| `$0A` | `$E01E` | `CD_SUBQ` | CD | P1 | サブQ情報のバッファ形式と状態値を調べる。 | pending |
| `$0B` | `$E021` | `CD_DINFO` | CD | P1 | TOC/トラック/ディスク長取得。BCD入力とバッファ4バイトの意味を調べる。 | pending |
| `$0C` | `$E024` | `CD_CONTNTS` | CD | P1 | ディスク内容・トラック属性情報の取得を調べる。 | pending |
| `$0D` | `$E027` | `CD_SUBRQ / CD_SUBRD` | CD | P1 | 文献で CD_SUBRQ と CD_SUBRD の名称不一致。Qサブチャネルの読み取り/リクエスト動作を切り分ける。 | pending |
| `$0E` | `$E02A` | `CD_PCMRD` | CD | P1 | CD音声/PCM取得のレジスタ出力とハードウェアステータスを調べる。 | pending |
| `$0F` | `$E02D` | `CD_FADE` | CD | P1 | CD-DA/ADPCMのフェード開始・解除、時間と音量曲線を調べる。 | pending |
| `$10` | `$E030` | `AD_RESET` | ADPCM | P1 | ADPCMコントローラのリセット・レジスタ/再生中状態を調べる。 | pending |
| `$11` | `$E033` | `AD_TRANS` | ADPCM | P1 | CDからADPCM RAMへの転送、busy時挙動、宛先・ブロック数を調べる。 | pending |
| `$12` | `$E036` | `AD_READ` | ADPCM | P1 | ADPCM RAMからメインメモリ/VRAMへの読み出しを調べる。 | pending |
| `$13` | `$E039` | `AD_WRITE` | ADPCM | P1 | ADPCM RAMへの書き込み。転送方向・アドレス/サイズ/バンク解釈を検証する。 | pending |
| `$14` | `$E03C` | `AD_PLAY` | ADPCM | P1 | ADPCM再生の開始/繰り返し・サンプルレート・停止時の挙動を調べる。 | pending |
| `$15` | `$E03F` | `AD_CPLAY` | ADPCM | P1 | ADPCM継続再生・連続再生の意味を確認する。 | pending |
| `$16` | `$E042` | `AD_STOP` | ADPCM | P1 | ADPCM停止と割り込み・再生位置への副作用を調べる。 | pending |
| `$17` | `$E045` | `AD_STAT` | ADPCM | P1 | ADPCM再生状態・残容量・ビジー返値を調べる。 | pending |
| `$18` | `$E048` | `BM_FORMAT` | BURAM | P1 | バックアップRAMのフォーマット。安全確認文字列、破壊的操作防止、戻り値を調べる。 | pending |
| `$19` | `$E04B` | `BM_FREE` | BURAM | P1 | バックアップRAMの空き容量取得・フォーマット異常を調べる。 | pending |
| `$1A` | `$E04E` | `BM_READ` | BURAM | P1 | バックアップRAMからファイルの読込。名前、容量、バッファ、エラーを調べる。 | pending |
| `$1B` | `$E051` | `BM_WRITE` | BURAM | P1 | バックアップRAMへのファイル書込。上書き・容量不足・電源断後の永続性を調べる。 | pending |
| `$1C` | `$E054` | `BM_DELETE` | BURAM | P1 | バックアップRAMのファイル削除と断片化を調べる。 | pending |
| `$1D` | `$E057` | `BM_FILES` | BURAM | P1 | バックアップRAMファイル一覧・列挙・終了条件を調べる。 | pending |
| `$1E` | `$E05A` | `EX_GETVER` | EX | P0 | System Card バージョン取得。3.0の期待返値と他版互換を調べる。 | pending |
| `$1F` | `$E05D` | `EX_SETVEC` | EX | P2 | 割り込み/コールバックベクタを設定するAPIの引数と割込復帰を調べる。 | pending |
| `$20` | `$E060` | `EX_GETFNT` | EX | P2 | 内蔵フォント取得のエントリ、字体・格納先/返値を調べる。 | pending |
| `$21` | `$E063` | `EX_JOYSNS` | EX | P2 | ジョイスティックのサンプリング。5ポート分の入力・トリガーRAMを調べる。 | pending |
| `$22` | `$E066` | `EX_JOYREP` | EX | P2 | 入力のリピート制御の実態を調べる。文献には単なる RTS と記載。 | pending |
| `$23` | `$E069` | `EX_SCRSIZ` | EX | P2 | 表示サイズ設定のVDCパラメータ・副作用を調べる。 | pending |
| `$24` | `$E06C` | `EX_DOTMOD` | EX | P2 | ドットクロック/表示モード制御と境界を調べる。 | pending |
| `$25` | `$E06F` | `EX_SCRMOD` | EX | P2 | 画面モード設定・VDCタイミング値を調べる。 | pending |
| `$26` | `$E072` | `EX_IMODE` | EX | P2 | VDC VRAM自動インクリメント幅の設定と影響するワークRAMを調べる。 | pending |
| `$27` | `$E075` | `EX_VMODE` | EX | P2 | 垂直方向表示モードの設定を調べる。 | pending |
| `$28` | `$E078` | `EX_HMODE` | EX | P2 | 水平方向表示モードの設定を調べる。 | pending |
| `$29` | `$E07B` | `EX_VSYNC` | EX | P2 | VSync待機の解除条件、タイミング、割り込み停止時の動作を調べる。 | pending |
| `$2A` | `$E07E` | `EX_RCRON` | EX | P2 | ラスタ割り込み許可のVDC制御値・ワークRAMを調べる。 | pending |
| `$2B` | `$E081` | `EX_RCROFF` | EX | P2 | ラスタ割り込み禁止のVDC制御値・ワークRAMを調べる。 | pending |
| `$2C` | `$E084` | `EX_IRQON` | EX | P2 | VDC IRQ許可・フラグの副作用を調べる。 | pending |
| `$2D` | `$E087` | `EX_IRQOFF` | EX | P2 | VDC IRQ禁止・フラグの副作用を調べる。 | pending |
| `$2E` | `$E08A` | `EX_BGON` | EX | P2 | BG表示ON時のワークRAM/VDCレジスタ反映タイミングを調べる。 | pending |
| `$2F` | `$E08D` | `EX_BGOFF` | EX | P2 | BG表示OFF時のワークRAM/VDCレジスタ反映タイミングを調べる。 | pending |
| `$30` | `$E090` | `EX_SPRON` | EX | P2 | スプライト表示ON時のワークRAM/VDC反映を調べる。 | pending |
| `$31` | `$E093` | `EX_SPROFF` | EX | P2 | スプライト表示OFF時のワークRAM/VDC反映を調べる。 | pending |
| `$32` | `$E096` | `EX_DSPON` | EX | P2 | BG+スプライト表示ONの組み合わせと反映を調べる。 | pending |
| `$33` | `$E099` | `EX_DSPOFF` | EX | P2 | BG+スプライト表示OFFの組み合わせと反映を調べる。 | pending |
| `$34` | `$E09C` | `EX_DMAMOD` | EX | P2 | VDC DMAモード制御とミラーRAM更新を調べる。 | pending |
| `$35` | `$E09F` | `EX_SPRDMA` | EX | P2 | Sprite Attribute Table DMAを開始/設定する操作と転送レジスタを調べる。 | pending |
| `$36` | `$E0A2` | `EX_SATCLR` | EX | P2 | SATのクリア範囲・ゼロ埋め・DMA待機を調べる。 | pending |
| `$37` | `$E0A5` | `EX_SPRPUT` | EX | P2 | スプライト配置/転送データの入力・出力を調べる。 | pending |
| `$38` | `$E0A8` | `EX_SETRCR` | EX | P2 | ラスタカウンタレジスタの設定。A/X入力候補を検証する。 | pending |
| `$39` | `$E0AB` | `EX_SETRED` | EX | P2 | VDC VRAM読込アドレス設定。A/X入力候補を検証する。 | pending |
| `$3A` | `$E0AE` | `EX_SETWRT` | EX | P2 | VDC VRAM書込アドレス設定。A/X入力候補を検証する。 | pending |
| `$3B` | `$E0B1` | `EX_SETDMA` | EX | P2 | VDC DMAソース/宛先/長さ設定の入力を調べる。 | pending |
| `$3C` | `$E0B4` | `EX_BINBCD` | EX | P2 | 二進数→BCD変換。範囲とフラグ、オーバーフローを調べる。 | pending |
| `$3D` | `$E0B7` | `EX_BCDBIN` | EX | P2 | BCD→二進数変換。非正規BCD値やフラグを調べる。 | pending |
| `$3E` | `$E0BA` | `EX_RND` | EX | P2 | 乱数生成・seed/更新時点を調べる。 | pending |
| `$3F` | `$E0BD` | `MA_MUL8U` | MATH | P2 | 符号なし8-bit乗算。結果幅・レジスタ配置・フラグを調べる。 | pending |
| `$40` | `$E0C0` | `MA_MUL8S` | MATH | P2 | 符号付き8-bit乗算。負数・オーバーフロー・結果幅を調べる。 | pending |
| `$41` | `$E0C3` | `MA_MUL16U` | MATH | P2 | 符号なし16-bit乗算。32-bit結果の配置を調べる。 | pending |
| `$42` | `$E0C6` | `MA_DIV16S` | MATH | P2 | 符号付き16-bit除算。ゼロ除算・負数・余りを調べる。別OSSとの名称/番号差異に注意。 | pending |
| `$43` | `$E0C9` | `MA_DIV16U` | MATH | P2 | 符号なし16-bit除算。ゼロ除算・余り/商配置を調べる。別OSSとの名称/番号差異に注意。 | pending |
| `$44` | `$E0CC` | `MA_SQRT` | MATH | P2 | 平方根演算。整数/固定小数点形式・丸めを調べる。 | pending |
| `$45` | `$E0CF` | `MA_SIN` | MATH | P2 | 三角関数SIN。角度単位・固定小数点精度・象限を調べる。 | pending |
| `$46` | `$E0D2` | `MA_COS` | MATH | P2 | 三角関数COS。角度単位・固定小数点精度・象限を調べる。 | pending |
| `$47` | `$E0D5` | `MA_ATNI` | MATH | P2 | アークタンジェント系演算。入力/象限/返値の定義を調べる。 | pending |
| `$48` | `$E0D8` | `PSG_BIOS` | EXT | P2 | PSG BIOS共通ディスパッチャ。dhで子関数を選ぶという文献記載を検証する。 | pending |
| `$49` | `$E0DB` | `GRP_BIOS` | EXT | P2 | GRP BIOS共通ディスパッチャ。サブコマンド体系は未確定。 | pending |
| `$4A` | `$E0DE` | `KEY_BIOS / EX_MEMOPEN` | EXT | P2 | System Card 3.0ではEX_MEMOPEN、古い版ではKEY_BIOSという文献の差異。世代別の意味を検証する。 | pending |
| `$4B` | `$E0E1` | `PSG_DRIVE / PSG_DRIVER` | EXT | P2 | PSGドライバー・サウンド機能への入り口、引数を調べる。 | pending |
| `$4C` | `$E0E4` | `EX_COLORC / EX_COLORCMD` | EXT | P2 | 色設定コマンドの名称差異 EX_COLORC/EX_COLORCMD と副作用を調べる。 | pending |
| `$4D` | `$E0E7` | `AD_STREAM_START` | EXT | P2 | System Card 2.x以降のCD→ADPCM非同期転送開始。独自命名、ABI未検証。 | pending |
| `$4E` | `$E0EA` | `AD_STREAM_POLL` | EXT | P2 | System Card 2.x以降のCD→ADPCM非同期転送ポーリング。独自命名、ABI未検証。 | pending |
| `$4F` | `$E0ED` | `MA_MUL16S` | EXT | P2 | 16-bit符号付き乗算。結果32-bitの配置・演算符号/境界を調べる。 | pending |
| `$50` | `$E0F0` | `MA_DIV8U / MA_CBASIS` | EXT | P2 | 文献でMA_DIV8U（8-bit除算）とMA_CBASIS（基数変換）が競合。まず命名/機能/世代差を調査し、実装は検証後。 | pending |

### PSG_BIOS subfunction candidates (9)

These entries must only be exposed as individual PSG dispatcher work after the `$48` ABI has been established.

| Selector | Dispatcher entry | API name (candidate) | Area | Priority | Investigation focus | Issue |
| --- | --- | --- | --- | --- | --- | --- |
| `48/00` | `$E0D8` | `PSG_ON` | PSG-SUB | P2 | PSGドライバー有効化. 呼出セレクタ・レジスタ・状態・エラーは未検証。 | pending |
| `48/01` | `$E0D8` | `PSG_OFF` | PSG-SUB | P2 | PSGドライバー停止. 呼出セレクタ・レジスタ・状態・エラーは未検証。 | pending |
| `48/02` | `$E0D8` | `PSG_INIT` | PSG-SUB | P2 | PSG初期化. 呼出セレクタ・レジスタ・状態・エラーは未検証。 | pending |
| `48/03` | `$E0D8` | `PSG_BANK` | PSG-SUB | P2 | PSG音源のデータバンク選択. 呼出セレクタ・レジスタ・状態・エラーは未検証。 | pending |
| `48/04` | `$E0D8` | `PSG_TRACK` | PSG-SUB | P2 | PSGトラックの選択. 呼出セレクタ・レジスタ・状態・エラーは未検証。 | pending |
| `48/0B` | `$E0D8` | `PSG_PLAY` | PSG-SUB | P2 | PSG再生. 呼出セレクタ・レジスタ・状態・エラーは未検証。 | pending |
| `48/0C` | `$E0D8` | `PSG_MSTAT` | PSG-SUB | P2 | PSG状態取得. 呼出セレクタ・レジスタ・状態・エラーは未検証。 | pending |
| `48/10` | `$E0D8` | `PSG_ASTOP` | PSG-SUB | P2 | PSG全停止. 呼出セレクタ・レジスタ・状態・エラーは未検証。 | pending |
| `48/13` | `$E0D8` | `PSG_FDOUT` | PSG-SUB | P2 | PSGフェードアウト. 呼出セレクタ・レジスタ・状態・エラーは未検証。 | pending |

### Acceptance contract for each issue

1. Verify function name, slot and executable entry behavior for the targeted version; record confidence, source provenance and any cross-document conflict.
2. Specify exact inputs and outputs (A, X, Y, flags, zero-page, MPR, relevant memory ranges), overwritten state, interrupts/events and errors.
3. Implement independently only after source/contract review; provide original fixture and negative/boundary case in real emulator core.
4. Record evidence with ROM hash, emulator version, relevant guest state and PASS/FAIL/SKIP/BLOCKED, avoiding proprietary media in repository.

Tracker: #3 API inventory · #1 ROM map · #6 Geargrafx harness · #8 provenance policy.
