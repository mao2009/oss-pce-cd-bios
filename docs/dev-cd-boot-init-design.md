# DEV専用 CD起動の最小初期化: リセット停止の分析と設計（Issue #551）

**状態: 設計・静的解析のみ。DEV専用・非互換。** 純正 BIOS の実行は行わず、reference JSR gate は CLOSED のまま。
CD ABI と既定 DEV ROM のバイト列は変更しない。本書は cc65 でのアセンブル・Geargrafx での実行結果を主張しない。

## 1. 既定 DEV ROM のリセットが停止ループになる理由

既定の `make dev-rom`（tools/build_rom.py）は `src/boot.s` の `reset_not_implemented` をリセット先にする。
これは意図された診断の停止であり、不具合ではない。

| 位置 | 内容 |
|---|---|
| `src/boot.s:6` | ラベル `reset_not_implemented`（BOOT セグメント先頭、リンク後 `$F000`） |
| `src/boot.s:7` | `sei`（I=1、IRQ マスク） |
| `src/boot.s:8` | `ldx #$FF`（X=$FF は「リセット」の識別子。API 呼び出しと区別する） |
| `src/boot.s:9-10` | `@loop: bra @loop`（**停止ループ**。「成功を報告しない」と明記） |
| `src/boot.s:13-17` | VECTORS: IRQ2/BRK, IRQ1/VDC, Timer, NMI, Reset の全てが同じラベルを指す（Reset は `$FFFE`） |

ビルド検査が同じ 5 バイトを要求する: `tools/build_rom.py:289-296` が `$FFFE` の先頭が `$E000..$FFF5` の本体にあること、
およびリセット先のバイト列が `78 a2 ff 80 fe`（SEI, LDX #$FF, BRA self）であることを確認する。
つまり停止ループは既定 ROM の検証済みの形であり、変えるには `verify_bank` と、ベース ROM を使う各テストの改訂が必要になる。

### 停止時点の CPU・メモリ状態（既定 ROM）

| 項目 | 状態 | 根拠 |
|---|---|---|
| CPU 初期化 | SEI のみ。`CLD` なし、`TXS` なし（SP は未設定）、`CSH` なし | `src/boot.s:7-8` |
| IRQ マスク | I=1（SEI）。全 IRQ ベクタが停止ループへ向く | `src/boot.s:7`, `13-17` |
| MPR | 一切設定しない。リセット時の MPR7=0 のみ仕様で固定（`spec/rom-contract.json` の `reset_mapping`）。MPR0..6 は UNKNOWN | `src/boot.s:2` のコメント |
| 作業 RAM / スタック | 未使用。SP はリセット値のまま（`$2100..$21FF` を前提にしない） | `src/boot.s` 全体 |
| VDC | 初期化しない（`boot.s:14` の IRQ1/VDC は停止先のみ） | `src/boot.s` 全体 |
| 結果の観測 | X=$FF のみ。RAM にも I/O にも書かない。判定は PC の停止位置で行う | `src/boot.s:8` |

## 2. 既存の DEV 専用起動コードの要約

| ファイル | 役割 | 初期化の内容 |
|---|---|---|
| `src/reset_entry.s`（#143） | 最小 reset、SEI/CLD、MPR0=$FF、MPR1=$F8、TXS=$FF。opt-in `CD_MEDIA_STATE` で `cd_media_state` を 1 回呼ぶ | `reset_entry:` 9-18 行。`reset_ready` は BRA 自己ループ（24 行） |
| `src/dev_boot_internal.s`（#501） | DEV 専用の CD 起動経路。`dev_boot_reset`（SEI 1 バイト、リセット先）→ `dev_boot_entry` | `dev_boot_entry:` 27 行。`cld`(28)、MPR0=$FF(29-30)、MPR1=$F8(31-32)、`ldx #$FF; txs`(33-34)、`jsr cd_media_state`(35)、`cmp #$01; bne dev_media_halt`(36-37)、`jmp cd_entry`(38)。`dev_media_halt: bra` 自己ループ（41-42 行） |
| `src/cd_media_state.s`（#144/#501） | READ(6) LBA 0 による媒体判定。`cd_media_state:` 47 行、`ms_done: rts` 234 行 | 結果は `MS_RES`（既定 `$2300`、DEV 経路は `-D MS_RES=$3F40`）。戻り値 A=X=状態 |
| `src/cd_load_exec_probe.s`（#14/#5） | `cd_entry`（46 行）。CSH、MPR0..MPR7 を全て再設定、SP=$FF、READ(6) LBA 5 を `$2800` へ、`$3000` へコピーして実行 | `.ifdef DEV_BOOT_INTERNAL` でリセットベクタを `dev_boot_reset` にする（449-455 行） |
| `src/startup_probe.s`（#4） | 診断用の完全なマッピング（MPR2..7 を 1,2,31,3,4,0）。CD は読まない | 参照用。CD 起動の前提ではない |
| `tools/build_dev_boot_internal.py` | `dev_boot_internal` ROM を組み立てる。`REQUIRED` のラベル、リセット先の SEI、`dev_boot_entry == reset+1` を検査 | `MS_RES = 0x3F40`（24 行） |

### 再利用できる部品と前提条件

| 部品 | 再利用できる内容 | 前提条件 |
|---|---|---|
| `dev_boot_entry` の 7 命令（`dev_boot_internal.s:28-34`） | CLD、MPR0=$FF（CD I/O `$1800`）、MPR1=$F8（作業 RAM、スタックページ `$2100..$21FF`）、SP=$FF | リセット先が SEI 1 バイトで、直後がこの labelled 位置であること（`build_dev_boot_internal.py:83`） |
| `cd_media_state` | READ(6) LBA 0、`$01` が MEDIA_SIGNATURE_OK、他は失敗状態 | MPR0/MPR1 のみ使用。MPR2..7 は不要。I フラグは変更しない（ポーリングのみ）。JSR 用の有効な SP。結果ブロックを DEV 経路の `$3F40` に置く |
| `dev_media_halt` | 媒体判定が `$01` 以外のとき、READ(6) LBA 5 を発行せず停止する | 判定の観測は PC と `MS_RES` で行う |
| `cd_entry`（`cd_load_exec_probe.s`） | MPR2..7、CSH、SP、結果ブロック `$2300` のクリア、LBA 5 の転送と実行 | `jmp` で入る。自分で MPR を全て設定するため、呼び出し前の MPR2..7 は不要 |
| `tools/build_dev_boot_internal.py` のリンク検査 | ラベル存在、リセット先バイト、API 表と未使用バンクの同一性 | `make dev-rom` の成果物（`verify_dev_rom` が要求） |

### 既存経路で足りない点

1. `dev_media_halt` は裸の自己ループで、停止理由を区別する値を残さない。判定値は `MS_RES`（`$3F40`）と A=X にしか無い。
2. `dev_boot_entry` は `csh` を設定しない。CD 転送は `cd_entry` の CSH 設定後に行うため、媒体判定は既定の速度のまま動く（Geargrafx 統合試験で検証済み）。
3. `reset_entry.s`（#143）の経路は `$2300` を使い、`cd_entry` と同じ結果ブロックを共有する。DEV の CD 起動では `dev_boot_internal` の `MS_RES=$3F40` を使うこと。

## 3. 提案: 最小の DEV 専用初期化経路

新しいアセンブリは追加しない。根拠は次のとおり。

- 既定 ROM の停止ループは検証済みの形であり、変更すると `verify_bank` とベース ROM の全テストに波及する。
- 既存の `dev_boot_internal` は、同じ 7 命令の初期化、媒体判定の分岐、停止位置を Geargrafx（固定版）の cold/warm 試験で確認している（#501）。
- 新規アセンブリは cc65 と実コアで検証しなければ GREEN にできない。本作業ではそれを実行していない。

### 初期化の順序（既存 `dev_boot_internal` と同一）

```
dev_boot_reset:  sei                 ; リセット先（1 バイト）。I=1 で IRQ をマスク
dev_boot_entry:  cld
                 lda #$FF : tam #$01 ; MPR0 = IO（$1800 CD-ROM2）
                 lda #$F8 : tam #$02 ; MPR1 = 作業 RAM（$2000-$3FFF、スタックは $2100-$21FF）
                 ldx #$FF : txs      ; SP = $FF
                 jsr cd_media_state  ; A=X=状態、結果は MS_RES（$3F40）
                 cmp #$01 : bne dev_media_halt   ; $01 以外は READ(6) LBA 5 を発行しない
                 jmp cd_entry        ; 転送と実行（MPR2..7 は cd_entry が設定）
dev_media_halt:  bra dev_media_halt  ; 失敗の停止位置（PC で判定、A=X=状態）
```

### 失敗時の診断状態

- 停止位置は `dev_media_halt` の 1 点だけであり、判定は PC と `MS_RES+0`（状態）で行う。
- 状態値は `$01`（成功）、`$02`（媒体不明）、`$03`（データなし）、`$81..$8A`（バス・タイムアウト・ステータス）。
- 媒体判定の結果ブロック `$3F40..$3F46` は `cd_load_exec` の結果（`$2300..$231F`）と重ならない（`tests/host/test_dev_reset_init_static.py` で検査）。
- 停止時に X と A は状態値を保持する。ただし host は X を観測できないため、判定の根拠は `MS_RES` である。

**本設計で追加しないもの**: 新しい停止コード、新しい結果ブロック、MPR2..7 の独自割当、VDC 初期化、IRQ 処理、API 応答。
どれも純正側の契約（UNKNOWN）に依存するため、DEV 経路の最小初期化には含めない。

## 4. テスト

- `tests/host/test_dev_reset_init_static.py`（cc65 不要）:
  - 既定 ROM の停止ループ（`src/boot.s`）と、ビルド検査のバイト列（`tools/build_rom.py`）を固定する。
  - `dev_boot_entry` の初期化 7 命令が媒体判定より前にあること、媒体判定の分岐順（`jsr` → `cmp #$01` → `bne dev_media_halt` → `jmp cd_entry`）を固定する。
  - 改変された入力（判定順の入れ替え、MPR1 書き込みの欠落）が拒否されることを、テスト内で確認する。
  - `MS_RES`（`$3F40..$3F46`）が他の結果ブロックと重ならないことを検査する。
  - 本書が `src/boot.s` の停止ループの行番号を引用していることを検査する。
- 実行: `python3 -m unittest tests.host.test_dev_reset_init_static -v`。
- RED の記録: 本書作成前は、設計書の存在検査のみが失敗した（コミット `49d256a`）。他の検査は現行ソースで最初から成立する。これは既存の振る舞いを固定する検査であり、ソースを変えていないため、RED は設計書の不在でのみ観測される。

## 5. 非目標

- 純正 BIOS の起動順序、System Card のメモリ配置、割り込み ABI、VDC 初期化は UNKNOWN のまま。
- CD_READ（`$03`）、CD_BOOT、API 応答の実装はしない。
- 既定 DEV ROM、`dev_boot_internal` のバイト列、`cd_entry` の契約は変更しない。
- reference JSR gate は CLOSED のまま。
- 実機、第二エミュレータでの検証は行っていない。
