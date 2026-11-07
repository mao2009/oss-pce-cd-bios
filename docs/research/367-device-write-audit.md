# Issue #367: デバイス起点 RAM 書込みの源泉監査とブロック転送の停止点（ソース監査）

## 目的と状態

親 #173 の V07（W17 デバイス起点書込みの列挙）と V04（TII/TIN/TDD/TIA/TAI の命令途中停止点）のうち、
**ソースの読み取りだけで確定できる部分**を記録する。ブロック転送の実コア実測は
`docs/367-block-transfer-audit.md` と `tests/integration/geargrafx_block_midrefusal.py` を参照。

- 対象: pinned Geargrafx `b49ae82a012eade566d72e9d61c9d95a5caa4da3`（ツールディレクトリの読み取り専用参照。リポジトリへの複製はしない）。
- 実行なし（本書）。純正 BIOS・ROM・JSR は一切実行しない。
- 状態: **BLOCKED / release_eligible=false**。ゲートは開けない。
- 機械可読な記録: `tests/fixtures/367_device_write_sites.tsv`（検証: `tests/host/test_367_device_write_audit.py`）。

## 結論

1. 検索範囲では、CPU 以外が**実行時に** WRAM（`m_wram`、32 KB）へ書く経路は見つからなかった。`m_wram` への書込みはリセット時の初期化（DEV-04、DEV-05）、ホストのステート復元（HOST-01）、および通常の CPU store（CPU-04）のみである。
2. デバイス起点の書込み先は ADPCM RAM（64 KB の別配列）、VRAM、VDC 内部の SAT である（DEV-01、DEV-02、DEV-03）。いずれも MPR 経由の WRAM には届かない。
3. 旧来の UNKNOWN 行（外部バックアップ RAM、Arcade Card の起動元）はトレースして解決した。どちらも CPU store 起点の別配列への書込みである（CPU-01、CPU-02、CPU-03）。デバイスが自律的に書く経路ではない。
4. CD-ROM RAM、カード RAM、Arcade Card RAM、バックアップ RAM への実行時書込みは、すべて CPU store 起点である（CPU-05、CPU-06、CPU-02、CPU-03）。CD や ADPCM の転送がこれらの RAM に直接書く経路は検索範囲では見つからなかった。
5. ブロック転送は **1 バイト = 1 回の `RunInstruction` 呼出し**の状態機械として実装されている。命令途中で制御が戻る経路はソース上に存在する。per-byte 拒否の継続挙動と転送元読みの副作用は実コアで実測済みである（`geargrafx_block_midrefusal.py`、30 ケース）。全体停止の実測は、全体事前拒否（preflight）については `geargrafx_block_transfer_matrix.py` および #473 の stack-s6 で確認済みであり、本書の範囲はソース構造の記録に限る。
6. **GG_TESTING について:** probe のビルド条件（`tools/setup.py` の debugger ビルドフラグと `geargrafx_block_write_guard.py` の `DEBUGGER_BUILD_FLAGS`）は `GG_TESTING` を定義しない。定義されると `Memory::Write` / `Memory::Read` / `TryPeek` がすべて `m_test_memory` へ迂回し、MPR 検査そのものが素通りする（`memory_inline.h` の `GG_TESTING` 分岐）。実測でも、MPR `$FF` の宛先への書込みが VDC AR に着地し、WRAM 宛先が WRAM 物理位置に着地したことから、このビルドで `GG_TESTING` が無効であることが裏付けられる。定義する別ビルドでは本監査の前提が崩れるため、ビルド定義は使うごとに記録すること。

## 検索方法と限界

- 検索語: `m_wram`、`m_cdrom_ram`、`m_adpcm_ram`、`m_vram`、`m_sat`、`m_card_memory`、`m_card_ram`、`m_backup_ram`、`m_memory->Write`、`Memory::Write`、`DMA`、`GetRAM`、`block_transfer`、`m_transfer_state`、`SendBit`、`WritePortData`、`UpdateBackupRam`、`m_current_mapper`。
- 検索範囲: `src/` 配下の全 `.cpp` / `.h` / `_inline.h`。`m_memory_map[bank][offset]` 経由の間接書込みは、`ReloadMemoryMap` / `UpdateBackupRam` / `UpdateLaserActiveSram` による map の張り替えを列挙して対象を確定した（WRAM、CD-ROM RAM、カード RAM、バックアップ RAM、未使用、ROM）。
- 限界: 全ファイルを精読していない。検索で見つからない間接書込み（関数ポインタ経由、マクロ展開）は除外できていない。したがって「検索範囲では見つからなかった」は「全経路の不存在の証明」ではない。フレームバッファ、`m_line_buffer`、オーディオバッファ等のホスト側描画・音声用配列への書込みは、ゲスト RAM ではないため対象外とした（判定対象の RAM 領域は MPR 経由の CPU アドレス空間と同等の配列に限る）。

## デバイス起点書込みの列挙（W17）

| ID | 場所 | 起点 | 書込み先 | 役割 | WRAM 到達 | 判定 | 実行条件 |
|----|------|------|----------|------|-----------|------|----------|
| DEV-01 | `adpcm_inline.h:173`（`UpdateDMA`:191-224 が SCSI データを取得、`Adpcm::Write` のデータレジスタ書込みも同じ経路） | device / CPU | ADPCM RAM（`adpcm.h:95`） | device_store | なし（別配列） | NOT_MAIN_RAM | ADPCM DMA 有効化 (`m_dma`) 時。SCSI 信号成立時 |
| DEV-02 | `huc6270.cpp:630`（VRAM DMA） | device | VRAM（`dest < 0x8000` のみ） | device_store | なし | NOT_MAIN_RAM | VDC LENR の MSB 書込みで転送開始 |
| DEV-03 | `huc6270.cpp:607`（SATB 転送、VRAM から内部 `m_sat` へ） | device | VDC 内部 SAT | device_store | なし | NOT_MAIN_RAM | VDC DVSSR 書込みで SAT 転送開始 |
| DEV-04 | `memory.cpp:123` | reset | WRAM | reset_store | — | RESET_ONLY | `Memory::Reset()` |
| DEV-05 | `memory.cpp:125` | reset | WRAM | reset_store | — | RESET_ONLY | `Memory::Reset()` |
| DEV-06 | `memory.cpp:151` | reset | CDROM RAM | reset_store | — | RESET_ONLY | `Memory::Reset()`（CDROM ハードウェア有効時） |
| DEV-07 | `memory.cpp:153` | reset | CDROM RAM | reset_store | — | RESET_ONLY | 同上 |
| DEV-08 | `memory.cpp:177` | reset | CARD_RAM | reset_store | — | RESET_ONLY | `Memory::Reset()`（カード RAM サイズ > 0 時） |
| DEV-09 | `memory.cpp:187` | reset | Arcade Card RAM | reset_store | — | RESET_ONLY | `Memory::Reset()` |
| DEV-10 | `memory.cpp:192` | reset | BACKUP_RAM | reset_store | — | RESET_ONLY | `Memory::Reset()` |
| DEV-11 | `huc6270_inline.h:244`（MAWR/VWR） | CPU | VRAM | cpu_store | なし | CPU_ORIGINATED | CPU が VDC データレジスタへ書いた時（`$FF` ページまたは ST 経由） |
| CPU-01 | `mb128.cpp:143`（`SendBit` の MODE_WRITE） | CPU | MB128 RAM（128 KiB 別配列） | cpu_store | なし（別配列） | CPU_ORIGINATED | MB128 接続時。`Memory::Write` のハードウェアページ `$1000` 帯（I/O）→ `Input::WriteO` → `MB128::Write` の CLR 立上りで 1 ビットずつ書込む。接続はメディア AUTO / `EnableMB128` 依存 |
| CPU-02 | `arcade_card_mapper_inline.h:42`（`WritePortData`） | CPU | Arcade Card RAM（2 MiB 別配列） | cpu_store | なし（別配列） | CPU_ORIGINATED | Arcade Card マッパー有効時（`m_current_mapper` が Arcade Card）。`Memory::Write` の bank `$40-$43`、またはハードウェアページ `$1800` 帯 offset `$1A00-$1A7F` の port register 0/1（`WriteHardware` → `WritePortRegister`）。offset `$1AE0-$1B00` は mapper レジスタであり RAM ではない |
| CPU-03 | `memory_inline.h:288` | CPU | BACKUP_RAM（bank `$F7`、2 KiB） | cpu_store | なし（別配列） | CPU_ORIGINATED | CDROM の BRAM 有効ビット（リセット時 true、レジスタ書込みで変更）→ `UpdateBackupRam` で map 有効時。MPR が `$F7` を指す時 |
| CPU-04 | `memory_inline.h:293` | CPU | WRAM | cpu_store | —（WRAM そのもの） | CPU_ORIGINATED | MPR の bank が `$F8-$FB`（WRAM）で `m_memory_map_write` が true の時。通常の CPU store およびブロック転送の着地先 |
| CPU-05 | `memory_inline.h:293` | CPU | CDROM RAM（bank `$80-$87`） | cpu_store | なし（別配列） | CPU_ORIGINATED | CDROM ハードウェア有効時。MPR が `$80-$87` を指す時 |
| CPU-06 | `memory_inline.h:293` | CPU | CARD_RAM | cpu_store | なし（別配列） | CPU_ORIGINATED | カード RAM 範囲の bank を MPR が指す時（LaserActive の SRAM 有効化を含む） |
| HOST-01 | `memory.cpp:498`（`LoadState`） | host | WRAM | host_store | — | HOST_ONLY | ホストがステートを復元した時（ゲスト実行中ではない） |
| HOST-02 | `memory.cpp:506`（`LoadState`） | host | CDROM RAM | host_store | — | HOST_ONLY | 同上 |
| HOST-03 | `geargrafx_core.cpp:537`（`LoadMB128`） | host | MB128 RAM | host_store | — | HOST_ONLY | ホストが MB128 ファイルをロードした時 |

除外（CPU 起点、列挙対象外）: `cdrom.cpp:274`（SCSI データレジスタへの CPU 書込み）、`Memory::Write` 経由の全 CPU 書込み、`huc6280_opcodes_inline.h` の命令ごとの `m_memory->Write`、`SF2Mapper::Write`（ROM bank 切替のみで RAM 書込みなし）。

旧来の UNKNOWN 行は上記の CPU-01、CPU-02 に解決した。MB128（`mb128.cpp:141,143` の起動元）は `Input::WriteO`（`input_inline.h:94`）→ `MB128::Write`（`mb128_inline.h`）→ `SendBit`（`mb128.cpp:52`）の呼出し鎖で確定した。Arcade Card の書込み起動経路は `Memory::Write` → `Mapper::Write`（`arcade_card_mapper.cpp:49`）→ `WritePortData` で確定した。どちらもデバイス自律書込みではない。

## CD / ADPCM / VDC 経路の判定

| 経路 | 書込み先 | 遮断可否（本監査の範囲） |
|------|----------|--------------------------|
| CD-ROM SCSI データ → ADPCM RAM（DEV-01 の DMA 分岐） | ADPCM RAM | CPU の書込みゲートでは遮断できない（SCSI 応答に駆動される）。ただし WRAM には届かないため、WRAM の許可域外書込みという脅威ではない。ADPCM RAM 自体を許可域外とみなすポリシーでは、ゲートの観測対象に含める必要がある |
| CPU → ADPCM データレジスタ → ADPCM RAM（DEV-01 の CPU 分岐） | ADPCM RAM | CPU store として `Memory::Write` のハードウェアページ分岐（`$1800` 帯 → `CdRom::WriteRegister` → `Adpcm::Write`）で遮断可能。書込み副作用前に止める経路は per-byte / preflight のいずれでも、ADPCM レジスタ書込み自体は通常の CPU store である |
| VDC VRAM DMA（DEV-02）、SATB（DEV-03） | VRAM / VDC 内部 SAT | CPU store では遮断できない（VDC 内部転送）。WRAM には届かない。VRAM / SAT を許可域外とみなすポリシーでは、VDC レジスタ書込み（起点）側で遮断する必要がある |
| CPU → VDC データレジスタ → VRAM（DEV-11） | VRAM | CPU store として遮断可能（`$FF` ページまたは ST 経由）。ST 経由は `WriteFromCPU` を経て `Memory::Write` を迂回する（W05-W07、別 Issue の範囲） |
| CD-ROM RAM / カード RAM / バックアップ RAM / Arcade Card RAM / MB128 RAM | 各別配列 | いずれも CPU store 起点。MPR の bank 検査で遮断可能（CPU-02、CPU-03、CPU-05、CPU-06）。デバイス自律書込みではない |
| タイマ / IRQ / PSG / HuC6260（VCE） | 各デバイスレジスタ | RAM 書込み経路なし。IRQ 要求の立上りはあるが、RAM への DMA はない（NOT_VERIFIED ではなく、検索範囲では経路なし） |
| ステート復元・ファイルロード（HOST-01、HOST-02、HOST-03） | WRAM / CDROM RAM / MB128 RAM | ホスト起点。ゲスト実行中のゲート対象外。ホスト API を呼ぶ主体がゲストでないため、ゲスト書込みの脅威ではない |

## ブロック転送の 1 命令内構造（W15 / V04 の前提）

| 事実 | 根拠（pinned ソースの行） |
|------|--------------------------|
| 開始時に state=2、A/X/Y を push、src/dst/length を取得し、PC を -7 して同じオペコードを再実行させる | `huc6280_opcodes_inline.h:664-683`（`OPCodes_TransferStart`） |
| state 2 の 1 ステップで 1 バイトを `Memory::Write(dst, Read(src,true), true)` し、length を 1 減らす。length=0 で state=1 | `huc6280_opcodes_inline.h:525-535`（TAI を代表例）。他の 4 命令は :559, :589, :620, :650 の同型ブロック |
| state 1 で `OPCodes_TransferEnd` が state=0 にし、A/X/Y を復元 | `huc6280_opcodes_inline.h:687` 付近 |
| `RunInstruction` は 1 呼出し 1 ステップ。`instruction_completed = (m_transfer_state == 0)` | `huc6280_inline.h:56-85` |
| IRQ 受付は `m_transfer_state == 0` のときのみ。転送中の IRQ は保留される | `huc6280_inline.h:90` |
| `block_transfer` フラグは `InjectCycles` の増分（1 または 2）にだけ効く。MPR・mapper・memory_map_write・ハードウェアページの分岐は通常の CPU 書込みと同一 | `memory_inline.h:265-303`（:303 が増分） |
| `CheckMemoryHooks` は store の前に呼ばれる（フック登録時のみ） | `memory_inline.h:276-278` |
| length 0 は u16 のラップで 65536 バイトになる（state 2 の最初のステップで 1 バイト書いたあと length が `0xFFFF` になる） | `huc6280.h:206`（`u16 m_transfer_length`）、`huc6280_opcodes_inline.h` の各 state 2 ブロック |

対応 ID: BLK-01、BLK-02、BLK-03、BLK-04、BLK-05（fixture 上の store 行。BLK-01 が TAI 代表、残りは同型）。

要点: 5 命令はいずれも、1 命令内で length 回の store を行い、**store の間に制御が戻る**。したがって「命令途中で停止できるか」は、駆動ループが `RunInstruction` 単位で止まるかに依存する。ソースは止められる構造を示す。

## 防御方式 A / B / C の成立範囲（実測との対応）

実測は `docs/367-block-transfer-audit.md` と各 driver の LIMIT 行を参照。本書は方式ごとの結論だけを記録する。

- **A. TransferStart 前に危険な命令を拒否する（preflight）:** オペランドを `TryPeek` で副作用なく取得し、全バイトの src/dst を MPR 経由で検査してから TransferStart に入らない。実コアで、許可外宛先・境界跨ぎ・length 0・TAM 後の MPR 変更を含む 51 ケース（matrix 5 命令 × 長さ 1/2/3/4/8 等）について、拒否時に CPU / RAM / VDC / MPR / スタックが不変でゲストが次命令へ進むことが確認済みである。#473 の stack-s6（policy T）でも、5 命令の pre-start 拒否と mid-transfer MPR 変更の検出が確認済みである。成立条件: オペランドページが WRAM で `TryPeek` できること（できない場合は fail-closed で拒否する実装だが、その分岐自体の実測は未実施）、全バイトの列挙が正確であること（TIA/TAI の交互順序を含む）、length 0 を最悪の 65536 として扱うこと。
- **B. 各バイト書込みを拒否する（per-byte）:** 拒否されたバイトの Read と Write は副作用前に止まるが、転送は継続し、TransferEnd まで進む。実コアで 5 命令 × 境界跨ぎ（cross、30 ケース中の 10 ケース）により、拒否バイトの前後の許可バイトが着地し、A/X/Y が復元され S が釣り合うことが確認済みである。したがって B は「許可外バイトだけを届かせない」方式であり、**命令全体の停止ではない**。原子性・完全停止を要求するポリシーでは不十分である。
- **C. 命令全体を副作用前に安全に中止する:** A の preflight がこの性質を持つ（TransferStart に入る前に PC+6 で skip し、push / pop / 書込み / 読出し副作用が一切ない）。ただし「中止後にゲストを再開できるか」は再開点の定義に依存する。#473 の stack-s6 では mid-transfer の MPR 変更に対する停止を BLOCKED（再開不可）と判定している。本番方式の最終選定は #473 の範囲であり、本 Issue では比較のための検証結果を引き渡す。
- **転送元読みの副作用:** per-byte 拒否は拒否バイトの転送元読みもスキップする（実測: `srcreadff`）。しかし許可宛先の転送では転送元読みの副作用が着地する（実測: `srcread`、VDC ステータスレジスタのクリアと値の着地）。したがって B のみでは「デバイスレジスタの read side effect を発生させない」条件を満たさない。A では src 全バイトを事前に検査できる。
- **TransferStart の push / TransferEnd の pop:** スタックページが `$FF` の場合、push 拒否だけでは TransferEnd の pop が未書込みスロットを読んで A/X/Y を破壊し VDC ステータスレジスタをクリアする（#459、実測済み）。A が有効なのは、preflight が TransferStart 自体に入らない場合に限る。push だけを拒否する方式は安全停止ではない。

## 受入条件との対応

| 受入条件 | 状況 | 根拠 |
|----------|------|------|
| 5 命令の命令途中停止の実測 | 実測済み（方式別）。B は停止ではないことが実測済み。A は全体拒否として実測済み（matrix 51 ケース、#473 stack-s6）。mid-transfer の MPR 変更に対する停止は #473 stack-s6 で BLOCKED として実測済み | `geargrafx_block_midrefusal.py`、`geargrafx_block_transfer_matrix.py`、`geargrafx_stack_s6.py` |
| 許可外バイトが届かない負のテスト（MPR・memory_map_write） | 実測済み。per-byte（cross / srcreadff）および preflight（deny-hw/rom/backup、nonmono）の両方 | 同上。静的検査は `tools/block_xfer_audit.py` |
| デバイス起点 RAM 書込みの源泉列挙 | 完了（検索範囲内）。旧来の UNKNOWN 行は CPU 起点に解決。実コアでのデバイス起点書込みのプローブは未実施（UNMEASURED） | 本書・fixture |
| 変異テストで偽 PASS が出ないこと | 実コア driver の armed 判定に対する mutation（midrefusal の `mutation_test`、matrix の mutation suite、block-write-guard の `mutation_test`）、host の合成行に対する mutation（`test_367_device_write_audit.py`、`test_367_midrefusal.py`） | 各 driver / host test |

## BLOCKED 理由と解除条件

BLOCKED 理由:
1. 本番ゲートが未接続。実測したのは検証専用パッチと probe 内のテスト方針であり、本番用の allowlist / 遮断機構ではない。`spec/write_path_inventory.json` の W15、W17 は slot-1e 用の in-core ガードの範囲であり、検証専用パッチの測定で更新することはできないため UNKNOWN / NOT_MEASURED のままである。
2. デバイス起点書込みの実コアプローブがない。ADPCM DMA、VRAM DMA、SATB は WRAM に届かないことがソースで確認できるが、実コアで書込み時点を捕捉した記録はない。
3. `#459` の拒否経路が安全な停止ではない（実コアで実測）。本番用安全機構（スタックページを `$FF` にマップしない、または TransferEnd の pop まで覆うゲート）は #473 の範囲である。
4. オペランドページが WRAM でない場合の preflight fail-closed 分岐はソースで確認できるが実測していない（UNKNOWN）。

解除条件:
- 実コアで 5 命令それぞれについて、複数バイト・他バンクを含めて許可外書込みが着地しないことを記録する（preflight については matrix で記録済み。per-byte については midrefusal で記録済み）。
- デバイス起点の各経路（ADPCM DMA、VRAM DMA、SATB）の実コアプローブで、WRAM への不着を記録する。別 Issue で扱う。
- #459 について本番用安全機構を実装し、実コアで「拒否後に A/X/Y・S・PC・P・MPR・VDC 状態が保存される」ことを記録する。検証専用パッチの測定結果だけでは解除できない。

次の候補: デバイス起点書込みの実コアプローブ（ADPCM / VRAM / SAT の書込み時点を捕捉する新規 probe）。共有ループは変更しない。
