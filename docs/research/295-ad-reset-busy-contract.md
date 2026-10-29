# Issue #295: AD_RESET の busy 境界の観測契約（測定手順の固定）

親 #25。前提 PR: #403（`ad_reset_idle` guest 実装）、#404（idle 実コア観測ドライバ）は main に統合済み。関連: [ad-reset-diagnostic.md](ad-reset-diagnostic.md)（#294、idle と play の device 層診断）、[ad-reset-contract.md](ad-reset-contract.md)（#292、ABI 台帳）、[ad-reset-fixture.md](ad-reset-fixture.md)（#293、合成 fixture）。

**状態: 契約のみ。実コア測定は未実施。`BLOCKED_ABI`、`release_eligible=false`、`DEV_CALLABLE` 不可。System Card 互換の VERIFIED とは主張しない。契約の承認は独立再レビューの後。**

安全規則（Issue #295 の「安全と運用」を継承）: 1 Issue = 1 Draft PR。main への直 push、force push、エージェントによるマージと Draft 解除は禁止。非公開 syscard3.pce、市販媒体のバイト列、逆アセンブルは公開しない。出典が不十分な場合は `BLOCKED_ABI`。

本書の目的は、実測の前に測定手順と判定基準を固定することである。reset 後のフラグ、RAM、IRQ、転送の中断結果などの期待値は、本書では決め打ちしない。期待値を含む判定は、測定ログを取得した後に別文書で行う。

ソースの参照は、固定版 Geargrafx（`src/`、commit `b49ae82a`）の読取りのみである。行番号はこの commit に対するものである。

---

## 0. 状態語

本書は次の軸を分けて使う。各語は、下表のいずれか 1 つの軸にのみ属する。

**出典の軸（事実の状態）**

| 語 | 意味 |
| --- | --- |
| CONFIRMED | リポジトリ内の `file:line`、または merged PR 本文・Issue 本文の記述で確認できる。PR 本文の記述は、ログ本体を読んだことを意味しない |
| UNCONFIRMED | 出典はあるが本書で確認していない、または出典が一部しかない |
| HYPOTHESIS | もっともらしいが出典がない。期待値の根拠にしない |
| UNKNOWN | 出典がないハードウェア事実。推測で埋めない |

**実行と前提の軸（試行・手順の状態）**

| 語 | 意味 |
| --- | --- |
| PASS | 判定基準（§9.2）を満たして終了した |
| FAIL | 判定基準を満たさなかった |
| NOT_RUN | 手順を実行していない（理由を問わない。未実施の確認にもこれを使う） |
| BLOCKED | 前提項目が未解消のため、試行または手順を実行しない（承認時 97de277 の意味のまま使う）。前提の判定は §3.5 の表による |

前提の判定（§3.5）は「充足」「一部充足」「未充足」で表す。

**区分と終了状態（§3.3、§8）**

- 測定区分: PLAY_ACTIVE、TRANSFER_BUSY、UNOBSERVED_BUSY。
- 試行の起動区分: cold、new-process、warm。
- 終了状態: `COMPLETED`、`BUDGET_EXCEEDED`、`TIMEOUT`、`ABORTED`。

**規則:** 用語は各軸で一度だけ定義し、本文ではこの表に定義された語のみを使う。`BLOCKED` は前提未解消による実行不可にだけ使う。`BLOCKER` は本書では使わない（改訂 R6 で承認時の `BLOCKED` に戻した。変更の記録は PR 本文の表）。`NOT_TESTED` は使わず、`NOT_RUN` に統一する。`MEASURED`（§11）は、測定ログが得られた後にのみ使う。`BLOCKED_ABI`、`DEV_CALLABLE`、`VERIFIED`、`release_eligible` は既存の契約のゲート語であり、上の軸には属さない。本書の対象区分（PLAY_ACTIVE、TRANSFER_BUSY）の試行は、まだ行っていない。

---

## 1. 固定条件（測定の前提）

| 項目 | 値・方法 |
| --- | --- |
| エミュレータ | Geargrafx、固定 commit `b49ae82a012eade566d72e9d61c9d95a5caa4da3`（#294、#404 と同じ） |
| 取得方法 | 専用の新しいクリーン clone を作り、`git rev-parse HEAD` が上記と一致することを確認する。既存の clone・作業ツリーは使わない |
| 合成 ROM の生成 | 合成 BIOS と合成 CD は、ゼロ埋めの生成器で作る（#294 と同じ方式）。検査用ゲストは `ad_reset_idle` と同じく WRAM `$3E00` に置くバイト列とする（#404 方式）。ゲストのビルドには cc65 を使い、出力は専用の一時ディレクトリに置く |
| 生成物の置き場所 | 新しく作った専用の一時ディレクトリだけ。リポジトリにはコミットしない |
| hash の記録 | 測定時に、合成 BIOS、合成 CD、ゲストバイト列、固定版 commit の SHA-256 を計算し、測定ログ（専用の一時ディレクトリの中）に記録する。hash の値は本書に書かない |
| WRAM の初期化値 | `memory.cpp:53` で WRAM の reset 値を 0 にする（`m_wram_reset_value = 0;`）。引数の要約（`mpr=0x00, wram=0, card_ram=0, arcade_card=0`）は本書の表記。負の値は乱数化になるため使わない（`:120-126`、`:280-286`）。MPR の初期値も `mpr=0x00` で固定する（`memory.cpp:106-118`） |
| 初期化の順序 | `SetResetValues`（`memory.cpp:280-286`）は値を保持するだけである。値を消費するのは `Memory::Reset()`（`memory.cpp:100-126`）である。コンストラクタ内で既定値による `Reset()` が一度走る（`memory.cpp:97`）ため、`SetResetValues` は電源投入またはリセットの前に呼ぶ必要がある。測定ではコア全体の電源投入（または `Memory::Reset()` を通るリセット経路）の直前に `SetResetValues` を呼ぶ。その経路は測定前に固定版ソースで確認し、ログに記録する。反映の確認として、P0 で WRAM と MPR の値を記録する |
| 禁止する入力 | 純正 BIOS、市販ゲーム ROM、私有 ROM（`rom/`）。いずれも使う前提にしない |
| 実行環境 | WSL の clean clone で実行する場合も、`GIT_DIR` や `GIT_WORK_TREE` を環境変数で渡さない（MEMORY の規則に従う） |

## 2. 区分別の成立確認手順

本節は「busy が立つと想定する操作」ではなく、各測定区分の試行を成立させ、確認するための手順である。成立の判定は §3 の観測で行う。ADPCM への書込みは、すべて `Adpcm::Write`（device 層、ハーネス側）で行う。

### 2.1 PLAY_ACTIVE（再生中）の手順

ソースの根拠: `Write` が扱うレジスタは `$08`、`$09`、`$0A`、`$0B`、`$0D`、`$0E` だけで、LENGTH（内部カウンタ `m_length`）に直接書くレジスタはない（`adpcm_inline.h:91-122`）。LENGTH は CONTROL の bit4 が 1 の間、毎 Clock で ADDRESS からコピーされる（`adpcm_inline.h:346-353`、`CheckLength`）。したがって LENGTH は次の手順で間接的に設定する。

1. ADDRESS を設定する: `Adpcm::Write($08, $34)`、`Adpcm::Write($09, $12)`（ADDRESS = `$1234`）。
2. LENGTH のコピーを有効にする: `Adpcm::Write($0D, $10)`（CONTROL bit4 = 1、bit5 = 0、bit7 = 0）。
3. `RunToVBlank` を 1 回実行する。この間に `CheckLength` が LENGTH を ADDRESS からコピーする。LENGTH の値は S2 で記録する（期待値として固定しない）。
4. 再生を要求する: `Adpcm::Write($0D, $20)`（CONTROL bit4 = 0、bit5 = 1、bit7 = 0）。bit4 を 0 に戻すため、以後は LENGTH がコピーされず、再生で減算される（`adpcm_inline.h:275`）。bit5 の立ち上がりで再生要求が保留になる（`adpcm_inline.h:300-304`）。#294 の `neg-reset-playing` で再生を開始した経路と同じ CONTROL の値である。
5. `RunToVBlank` を 1 フレームずつ進め、§3.2 の T_b で再生中を観測する。
6. 観測できるまで最大 60 フレームまで待つ（§6 の PLAY_ACTIVE 専用の測定上限）。観測できない試行は、区分 `UNOBSERVED_BUSY` とする。

### 2.2 TRANSFER_BUSY（転送 busy）の手順

本区分は現時点で **BLOCKED** である（前提項目が未解消）。条件は §3.4 に記す。BLOCKED の間は、この区分の試行を実行しない。

## 3. 観測点と測定区分

### 3.0 ステータスレジスタ（`$180C`）のビットの意味（固定版ソースの読取り）

`src/adpcm_inline.h`、`src/adpcm.cpp`、いずれも固定版 `b49ae82a` の読取り。

| ビット | 固定版ソースの定義 | 根拠（行） | 本契約での扱い |
| --- | --- | --- | --- |
| `$08` | `m_playing` | `adpcm_inline.h:84` | 再生中。ソースの意味として確定 |
| `$01` | `m_end_irq` | `adpcm_inline.h:85` | END IRQ 要求。ソースの意味として確定 |
| `$80` | `m_read_cycles > 0` | `adpcm_inline.h:86` | 「データポート読出しの待ち時間カウンタが 0 でない」。ソースでは busy という名前は使われていない。転送 busy を表すかどうかは **UNKNOWN** |
| `$04` | `m_write_cycles > 0` | `adpcm_inline.h:87` | 同上（書込み側）。DMA 経路でも設定される（`adpcm_inline.h:204`）。転送 busy を表すかどうかは **UNKNOWN** |

補足（ソースの事実）:

- `$0A` の読出しは `m_read_cycles` を設定し（`adpcm_inline.h:65`）、`$0A` の書込みは `m_write_cycles` を設定する（`adpcm_inline.h:102`）。
- 待ち時間は `ComputeLatency` により最大 36 の単位で、毎クロック減算される（`adpcm.cpp:215-234`、`adpcm_inline.h:140-172`）。つまり、`$0A` 経由の `$04` / `$80` は、フレームより短い時間幅で消える。
- `$0C` の読出し（`GetStatusRegisterSnapshot`）は副作用がない。`$0A` の読出しは副作用があるため、観測では呼ばない（#294 と同じ）。
- 履歴注記: 初版（#577 の初回コミット）で `$04` / `$80` を busy ビットと記していたのは、#294 の文書の表現を引き継いだものである。ソースの名称ではないため、本版で訂正する。

### 3.1 観測する状態

| 観測点 | 読む対象 | 経路 | 副作用 |
| --- | --- | --- | --- |
| S1: ステータス | ADPCM_STAT（`$180C`） | device 層の `Adpcm::Read`（ハーネス側） | なし（`adpcm_inline.h:70`） |
| S2: 内部状態 | `m_control`（`$0D` の読戻し）、PLAYING、ADDRESS、READ/WRITE_ADDRESS、LENGTH、`m_read_cycles`、`m_write_cycles`、`m_play_pending` | 読取りのみ（`Adpcm::GetState()` の経路） | なし |
| S3: 時刻 | 経過フレーム数、Clock 呼出し回数、マスタークロックの累計（`GetMasterClockCycles`） | ハーネスのカウンタと core の読取り | なし |

既存の device 層の観測（本書の測定ではない）: `tests/integration/geargrafx_ad_reset.cpp` の `snap()` は、`Adpcm::Read` で `$0D`、`$0C`、`$0B`、`$0E` を読み（44–47 行）、`GetState()` の内部状態を読む（48–54 行）。この読取りは、S1 と S2 の経路と同じ種類である。

### 3.2 観測の時点

- T_b（区分の成立確認時）: S1 と S2 を同時に読む。`RunToVBlank` の完了直後（フレーム境界）で読む。
- T_r（reset 発行直前）: S1 と S2 を再び読む。`RunToVBlank` の完了直後（フレーム境界）で読む。
- 正例の条件は、区分ごとに §3.3 で定める。T_b と T_r の両方で条件を満たしたときだけ、その試行を正例の候補とする。

### 3.3 測定区分名

測定区分は次の 3 つだけである。各試行は必ずこの 3 つのいずれか 1 つに分類する。

| 区分名 | 定義 | 正例の候補となる条件 |
| --- | --- | --- |
| PLAY_ACTIVE | 再生中であることを観測した試行 | T_b と T_r の両方で、S1 の `$08` が 1 かつ S2 の PLAYING が 1 |
| TRANSFER_BUSY | 転送処理が busy であることを観測した試行 | **BLOCKED**（§3.4）。現時点でこの区分の試行は存在しない |
| UNOBSERVED_BUSY | 対象の busy 成立を確認できなかった試行 | 正例にしない。証拠として使わない。件数だけを報告する |

- 区分とは別に、試行の終了状態（§6、§7.2）を持つ。終了状態は `COMPLETED`、`BUDGET_EXCEEDED`、`TIMEOUT`、`ABORTED` のいずれかである。試行は「区分」と「終了状態」の 2 軸で必ず分類する。
- 終了状態が `BUDGET_EXCEEDED`、`TIMEOUT`、`ABORTED` の試行は、区分にかかわらず正例にも負例にも含めない。区分が `UNOBSERVED_BUSY` になるのは、成立を確認できないまま終了した場合である。
- **PLAY_ACTIVE の PASS を TRANSFER_BUSY の PASS へ流用しない。** 再生中の reset の結果は、転送 busy 中の reset の結果としては扱わない。

### 3.4 TRANSFER_BUSY が BLOCKED である条件

以下のいずれかが満たされない限り、TRANSFER_BUSY は BLOCKED とする。

1. **`$0A` 書込み経路（device 層）**: `$04` の待ち時間は 1 回の書込みにつき最大 36 単位で消える（§3.0）。本ハーネスの観測点はフレーム境界（`RunToVBlank` の完了時）だけであり、T_b と T_r の両方で値が立っていることを保証できない。フレーム内の観測点を設けるには CPU 実行の強制停止が必要であり、これは §10 の B4 で BLOCKED である。
2. **`$0B` の DMA 経路**: `$04` は DMA 経路でも立つ（`adpcm_inline.h:204`）。DMA 経路は SCSI コントローラの信号（`adpcm_inline.h:219-225`、`DataReady` など）に依存する（`adpcm_inline.h:191-226`）。その設定は CD 側の操作であり、§10 の B11 で BLOCKED である。
3. **`$80` の意味**: `m_read_cycles` は `$0A` の読出しで立つ。転送の意味を持つかは UNKNOWN であり、どの経路でも確定していない。

解除の条件（3 つすべて）:

- フレーム内の観測点を、CPU 実行の強制停止を伴わずに定義できること（例: device 層の読取りだけで足りる観測点の設計）。強制停止を伴う場合は、#365、#173 の条件に照らして安全性を確認するまで解除しない。
- `$04` / `$80` が転送 busy であることを、ソースまたは実測で確定する（UNKNOWN を解消する）。
- 独立レビューで、TRANSFER_BUSY の観測点と判定基準の固定を承認する。

### 3.5 開始条件（三つの独立した判定）

本節には三つの判定がある。どれか一つの充足を、他の判定の充足として扱わない。

**判定 I: 測定の開始条件（区分ごと）**

| 条件 | PLAY_ACTIVE（§2.1） | warm（§14） | TRANSFER_BUSY（§2.2） |
| --- | --- | --- | --- |
| (a) 前提 PR（#403、#404）が main に統合 | 充足（CONFIRMED。#403 は 2026-10-21T00:57:46Z、#404 は 2026-10-21T01:42:48Z に merge） | 充足（同左） | 充足（同左） |
| (b) 本契約の独立レビューによる承認 | 未充足（NOT_RUN） | 未充足（NOT_RUN） | 未充足（NOT_RUN） |
| (c) 判定に使う観測点の出典が確定 | 充足（`$08` と PLAYING は `adpcm_inline.h:84` の `m_playing`。CONFIRMED） | 充足（同左。W0〜W2 は既存の状態読取り） | 未充足（`$04` / `$80` の意味は UNKNOWN） |
| (e) 安全条件: 手順が §10 の BLOCKED 項目に触れない | 独立レビューで確認する（本書の手順は device 層の読取りと、ハーネスからの `Adpcm::Write` だけを使い、CPU からの MMIO 書込み、IRQ の操作、CD 側の操作を含まない） | 独立レビューで確認する（PLAY と同じ扱い。ただし `ResetMedia` は CD 側の状態に作用するため、次を確認対象とする。`ResetMedia` は `GeargrafxCore::Reset()`（`geargrafx_core.cpp:1205`）を呼び、その中で `m_cdrom->Reset()`、`m_scsi_controller->Reset()`、`m_cdrom_audio->Reset()` を呼ぶ（`:1237-1239`）。(a) 三つの Reset の直接の書込みは各装置の内部変数であり、Memory::Write の経路を通らない（`cdrom.cpp:113-129`、`scsi_controller.cpp:176-204`、`cdrom_audio.cpp:115-131`）。(b) `CdRom::Reset` は 128 行目で `m_memory->UpdateBackupRam(m_bram_enabled)` を呼び（`memory_inline.h:544-558`）、CPU から見える `$F7` の写像（`m_memory_map` と `m_memory_map_write`）を変える。(c) `ScsiController::Reset` は 200 行目で `UpdateIRQs()` を呼び（`scsi_controller_inline.h:92-106`）、CD 側の IRQ 状態を変える。201 行目で `m_cdrom_audio->StopAudio()` を呼ぶ。これは CD 再生の状態を STOPPED にし（`cdrom_audio_inline.h:188-195`）、LaserActive が有効なら `m_laseractive->NotifyAudioStop` を呼ぶことがある。202 行目で `m_cdrom_media->SetCurrentSector(0)` を呼ぶ。これは媒体側のセクタ位置を変える（`cdrom_image.cpp:129-135`）。(d) `CdRomAudio::Reset` は 130 行目で `InvalidateSectorCache()` を呼ぶ（キャッシュフラグのみ）。(b) と (c) は CPU 可視の写像と IRQ 状態に関わるため、B2 と B6 の範囲に当たるかは断定せず、独立レビューで確認すべき対象とする。当たると確定した場合は、warm を BLOCKED とする） | 未充足（§3.4 の BLOCKED 条件、§10 の B4 と B11） |
| (d) §3.4 の BLOCKED 解除条件（3 条件） | 対象外 | 対象外（gate の BLOCKED は §14.6 で維持） | 未充足 |
| 判定 | (b) の承認後に開始できる。(a)、(c) は充足。(e) は (b) の承認の中で確認する | 動的 MPR 検出が UNPROVEN のため BLOCKED。試行 2 を開始しない。warm 系列の試行 1 は実行しない。warm 系列全体を実行しない（§14.12 の 7、§14.13、§14.15）。(e) の確認と §14.2 の前提は、この BLOCKED の解除後に行う | BLOCKED。試行を実行しない |

**判定 II: Issue #295 の ROM-Primitive 実装の開始条件（測定とは別）**

| Issue 本文の開始条件（原文） | 状態 | 根拠 |
| --- | --- | --- |
| 「Contract/Fixture/Diagnostic の根拠」が確定 | 一部充足 | idle の観測契約は `ad-reset-contract.md`、`ad-reset-fixture.md`、`ad-reset-diagnostic.md` にある（UNCONFIRMED: 本書で内容を確認していない）。busy の根拠は UNKNOWN（`$04` / `$80` の意味） |
| 「必要なハード状態」が確定 | 未充足 | TRANSFER_BUSY の BLOCKED 解除条件（§3.4）が未解消 |
| 「前提 PR」が main に統合 | 充足（CONFIRMED） | #403、#404 |

判定 II は未充足である。判定 I の PLAY_ACTIVE の測定条件が充足されても、判定 II は充足しない。

**判定 III: Issue #295 の完了条件（測定とは別）**

| Issue 本文の完了条件（原文の要旨） | 状態 | 根拠 |
| --- | --- | --- |
| 既知の ADPCM controller reset と busy 状態の最小 1 モードのみ実装、ジャンプテーブル未接続 | 未充足 | busy に対応する区分（TRANSFER_BUSY）は BLOCKED。PLAY_ACTIVE の測定結果だけでは、busy を含む実装の完了条件を満たさない（オーナー裁定） |
| メモリ・MMIO 側作用、有界 timeout、負例をゲスト実 CPU で検証。根拠不足は BLOCKED | 未充足 | 本書の測定は未実施。ゲスト実 CPU での検証は、判定 I の測定後に別途必要 |
| 原則 3 ファイル、差分 200〜250 行目安。独立レビュー、関連 CI、実測を区別した証拠台帳を記録 | 未充足 | 独立レビュー未実施（NOT_RUN）。CI 未配線（§13 の 7） |

注: 独立レビュー（判定 I の (b)）は Issue の開始条件ではない。本書の承認手続きとして扱う（§13 の 5）。承認前は PLAY_ACTIVE も warm も測定を行わない。実測の開始条件は §14.15 に定める。現時点で測定の開始を承認したものではない。

**warm の実行状態（改訂 R12）:** warm（試行 2）は、§14.13 の動的 MPR 検出が UNPROVEN であるため **BLOCKED** である。試行 2 は開始せず、warm の実行状態は BLOCKED とする。この規則は §14.12 の 7 と同一である。warm 系列の試行 1 は実行しない。PLAY 測定を別に行う場合は、その判定は §3.5 の判定 I による。

## 4. reset 要求の発行方法（手順のみ）

1. T_r の時点（フレーム境界。区分名に関わらず同じ）で、ハーネスが `Adpcm::Write($0D, $80)` を 1 回だけ呼ぶ（bit7 = 1、bit5 = 0）。
2. 直後に R2a の読戻し（`Adpcm::Read($0D)`、副作用なし、`adpcm_inline.h:72`）で、読み戻した値を記録する（判定は R2a）。
3. reset の効果は `Clock` の中の `CheckReset` で初めて現れる（`adpcm_inline.h:29-38`、`:335-344`）。`WriteControl` は `m_control` を書くだけである（`adpcm_inline.h:292-307`）。したがって採取点 P2 は、`RunToVBlank` を 1 回完了した時点とし、その間に `Clock` が 1 回以上呼ばれることを S3 の Clock 呼出し回数で記録する。
4. reset 要求はハーネスからのみ発行する。CPU 命令による `$180D` への書込みは行わない（§10 の B9）。
5. reset の発行は 1 試行につき 1 回とする。再発行はしない。
6. `arm-B` として、bit5 を 1 のまま残す値（`$A0`）での要求を別に測る。`arm-B` は正例の集計に入れない。`arm-B` の扱いは §11 と §13 に従う。

## 5. reset 前後で採取する範囲

採取は次の 4 点で行う。

- P0: 発行直前（T_r と同じ）。
- P1: `Adpcm::Write($0D, $80)` の直後（Clock は呼ばれていない）。
- P2: P1 の後の `RunToVBlank` 1 回の完了時（`CheckReset` を含む Clock が 1 回以上呼ばれたことを S3 で記録する）。S2 の `m_control`、`m_playing`、`m_read_cycles`、`m_write_cycles`、`m_length` をこの時点で記録する（判定は §9.2 の R2c で、判定は別文書へ回す）。
- P3: 正常観測完了の時点（P2 の後に `RunToVBlank` をさらに 1 回正常に完了した時点。§6.1）、または §7 の停止時（終了状態を記録した時点）。300 フレームの安全上限に到達した時点は、P3 の正常観測完了ではない（§6.1 の `BUDGET_EXCEEDED`）。

各点で次を記録する。

| 項目 | 採取範囲 | 方法 |
| --- | --- | --- |
| CPU の PC | PC の値 | CPU 状態の読取り（ハーネス側） |
| CPU の他のレジスタ | A、X、Y、S、P、MPR0〜MPR7 | 同上。MPR は 8 バイト全部 |
| RAM | `m_wram[0x0000–0x1FFF]`（8 KiB） | 配列のダンプ。非 SGX 媒体では `$F8`〜`$FB` の全てが `m_wram[0]` に写るため（`memory.cpp:245-252`、`m_media->IsSGX()` が偽の場合）、この 8 KiB が CPU から見える WRAM の全体である。SGX 媒体は本契約の対象外 |
| ADPCM 状態 | S1（`$180C`）と S2（内部状態一式、`$0D` の読戻しを含む） | §3.1 の経路。読取りのみ |
| IRQ 状態 | P の I フラグ、IRQ マスクと保留フラグ | P は CPU 状態から読む。IRQ マスクと保留フラグの保持場所は固定版ソースで確認してから記録する。確認できるまでは UNKNOWN と記録し、推測で埋めない。バス上の IRQ 関連レジスタ（`$1402`、`$1403`）は読まない（§10 の B2） |

採取した値は、判定に使わず、そのまま測定ログに残す。期待値との比較は本書の範囲外である。

## 6. 有界の実行予算と timeout（測定前に決める）

本節の数値は、**測定を打ち切るための上限（測定上限）** であり、次のいずれでもない。

- ハードウェアの仕様や実時間。PC Engine の実フレームレート、ADPCM の実時間は UNKNOWN であり、フレーム数を秒やクロック数に換算する根拠はない。
- 期待される結果や判定値。判定は §9.2 の基準で、測定後に別の結果文書で行う。
- 測定の実績。本契約の区分（§8）に対応する試行は 0 件である。契約の外の既存記録として PLAY_ACTIVE の 25 試行がある（§15）。それらに本契約の上限が適用されたかは記録から確認できず、本契約の集計には入れない。

数値はすべて本文書が選んだ値である。選定理由は下表の「理由」欄に記す。

| 項目 | 測定上限の値 | 適用範囲 | 理由（本文書が選んだ根拠） | 判定の実施点 |
| --- | --- | --- | --- | --- |
| 1 試行の安全上限（§6.1 の `FRAME_SAFETY_CAP`） | §6.1 の値 | 全区分の試行 | 1 試行が際限なく続かないための上限。根拠となる実測・仕様はない。リポジトリ内の既存値（`geargrafx_ad_reset.cpp` 32 行の `kPlayBudgetFrames = 240`）とは別の値で、互換性は主張しない | `RunToVBlank` の完了時 |
| 区分成立の確認までの待ち上限（§6.1 の `OBSERVE_WAIT_FRAMES`） | §6.1 の値（§2.1 の手順 6） | **PLAY_ACTIVE 専用** | 再生中の観測が成立しない試行を打ち切るための上限。数値は PR #404 の guest 実行（`geargrafx_ad_reset_guest.cpp` 24 行、`kFrames = 60`）と一致するが、由来は別。TRANSFER_BUSY には待ち上限を設定していない（BLOCKED。§3.4） | `RunToVBlank` の完了時 |
| 1 試行のマスタークロック累計 | 上限なし。S3 で記録する | 全区分 | 区分ごとに換算根拠が必要になるため、上限は置かず記録のみとする | `RunToVBlank` の完了時 |
| 1 試行の壁時計 timeout（§6.1 の `TRIAL_WALL_SECONDS`） | §6.1 の値 | 全区分 | ハングした試行を外部から打ち切るための上限。実行時間の測定はなく、根拠は UNKNOWN | 外部の監視（ハーネスを起動する親プロセス）が計時し、超過時にプロセスを終了させ、終了状態 `TIMEOUT` と記録する |
| 1 プロセスの壁時計 timeout（§6.1 の `PROCESS_WALL_SECONDS`） | §6.1 の値（複数試行を含む場合の全体） | §8 の系列 W（1 プロセス 2 試行）を含む全プロセス | 1 プロセスの全体を打ち切るための上限。既存の subprocess timeout（`geargrafx_ad_reset.py` 138 行、300 秒）とは別の値 | 同上。超過時はプロセスを終了させ、その時点の試行を `TIMEOUT` と記録する |

**実施点と執行者:** 上限は次の 2 種類に分け、それぞれの執行者を定める。

- **フレーム境界の上限**（§6.1 の `FRAME_SAFETY_CAP` と `OBSERVE_WAIT_FRAMES`）: ハーネスが判定する。判定は `RunToVBlank` の完了時（フレーム境界）と `Adpcm::Write` の直後に行う。フレーム数は後者では変わらないため、実質的には前者の時点で判定される。
- **壁時計の上限**（§6.1 の `TRIAL_WALL_SECONDS` と `PROCESS_WALL_SECONDS`）: 外部の監視（ハーネスを起動する親プロセス）が判定する。ハーネスはこの上限を検出せず、フレーム境界や `Adpcm::Write` の時点に依存しない。

CPU の命令単位で計数・中断はしない（命令単位のフックは本契約で使わない。§10 の B4）。

- 旧版の「2,000,000 CPU ステップ上限」は、命令単位の計数点が本契約にないため廃止する。実施点を持たない上限は、判定できないためである。
- 安全性の確認方法: 本契約のハーネスは、フレーム境界と `Adpcm::Write` の直後以外では core に介入しない。CPU を途中で止める操作は行わない。これにより、命令単位の強制停止が必要な操作（§3.4 の解除条件、§10 の B4）は実施しない。
- 外部監視によるプロセス終了（`TIMEOUT`）は、CPU 実行の強制停止ではない。実行中の CPU 状態、メモリ、ADPCM 状態を変更せず、プロセスごと終了させるだけである。終了後の状態は読まず、`TIMEOUT` と記録する。この操作は §3.4 の解除条件の対象外であり、本契約で許可する。

上限に達した場合の終了状態は §6.1 の六つの状態に従う。`BUDGET_EXCEEDED` と `TIMEOUT` の試行は、正例・負例のどちらにも含めない（§3.3）。

### 6.1 試行の終了状態（完了条件）と定数（改訂 R11、作業 A）

**定数（ここで一箇所に定義する）:**

| 定数 | 値 | 意味 |
| --- | --- | --- |
| `FRAME_SAFETY_CAP` | 300 フレーム | 1 試行の安全上限。到達は正常完了ではない |
| `OBSERVE_WAIT_FRAMES` | 60 フレーム | PLAY_ACTIVE の成立までの待ち上限（§2.1 の手順 6） |
| `TRIAL_WALL_SECONDS` | 120 秒 | 1 試行の壁時計の上限 |
| `PROCESS_WALL_SECONDS` | 600 秒 | 1 プロセスの壁時計の上限（warm の系列は 2 試行を含む） |

参照の一致: 次の値は、この表の値と一致している。C++ probe `tests/integration/geargrafx_ad_reset_warm.cpp` の `kFrameBudget`（300）、`kObserveWait`（60）、`kTrialSeconds`（120.0）、`kProcessSeconds`（600.0）。既存の full probe（`geargrafx_ad_reset_full.cpp`）も同じ 300、60、120 を用いる。Python runner は `geargrafx_ad_reset_warm.py` の `PROCESS_TIMEOUT_S`（600）を用いる。**不一致（次段階で修正する）:** `geargrafx_ad_reset_warm.py` の `SERIES_TIMEOUT_S = 120` は、試行単位の値であり、warm の 1 プロセス（2 試行を含む）の上限として誤っている。次段階の実装で、プロセス単位の `subprocess` の上限を `PROCESS_WALL_SECONDS`（600）に合わせる。本改訂では実装を変更しない。

**試行の六つの状態:**

| 状態 | 条件 | 区分との関係 |
| --- | --- | --- |
| `COMPLETED`（正常観測完了） | PLAY_ACTIVE が成立し、CONTROL `$80` の reset を発行して P2 を取得した後、`RunToVBlank` をさらに 1 回正常に完了し、P3 を取得した | 区分は PLAY_ACTIVE |
| `BUDGET_EXCEEDED`（安全上限） | `FRAME_SAFETY_CAP`（300 フレーム）に到達し、P3 を取得する前に停止した | 区分は問わない |
| `BUDGET_EXCEEDED`（待ち上限） | PLAY_ACTIVE が成立する前に `OBSERVE_WAIT_FRAMES`（60 フレーム）に到達した | 区分は `UNOBSERVED_BUSY` |
| `TIMEOUT`（試行） | 1 試行の壁時計が `TRIAL_WALL_SECONDS`（120 秒）を超えた | 区分は問わない |
| `TIMEOUT`（プロセス） | 1 プロセスの壁時計が `PROCESS_WALL_SECONDS`（600 秒）を超えた | 区分は問わない |
| `ABORTED` | その他の異常停止（PC が検査用ゲストの範囲外、hash 不一致、ログ書込みの失敗、予期しない例外、ゲストの MMIO アクセスの検出） | 区分は問わない |

- `COMPLETED` は、正常観測完了の手順（上の一行目）を満たした試行にだけ付ける。この手順は**測定上の規則**であり、ハードウェア仕様や reset 効果の正しさを示すものではない。
- 300 フレームに到達した試行を `COMPLETED` とみなさない。旧版の「予算終了 → `COMPLETED`」の記述は削除した。
- この完了条件は、将来の測定にのみ適用する。既存の 25 試行（§15）は、旧定義の `COMPLETED` を持つが、この定義で再判定せず、遡及的に PASS にしない。

## 7. 安全な停止条件と失敗時の動作

### 7.1 停止条件（いずれかで即時に打ち切る）

停止条件の検出者は、条件ごとに次のとおり分ける（§6 の執行者と同じ）。フレーム数の上限はハーネスがフレーム境界で検出する。壁時計 timeout は外部の監視が検出する。

- S1 と S2 が、区分の成立条件（§3.3）を満たさないまま、§6 の待ち上限に達した（区分 `UNOBSERVED_BUSY`）。ハーネスがフレーム境界で検出する。
- 安全上限 300 フレームに達した（ハーネスがフレーム境界で検出する。終了状態 `BUDGET_EXCEEDED`、§6.1）。
- 壁時計の上限（試行 120 秒、またはプロセス 600 秒）に達した（外部の監視が検出し、`TIMEOUT` と記録する。ハーネスは検出しない。§6.1）。
- ハーネスが、ゲストの PC を検査用ゲストの範囲外に検出した。
- 固定版 commit、または合成 ROM の hash が、記録済みの値と一致しなかった。
- ログの書込みに失敗した。
- 予期しない例外が発生した。
- ゲストによる ADPCM または IRQ の MMIO アクセスをハーネスが検出した（検出できた場合のみ。検出できない場合は §10 の BLOCKED として扱う）。
- 媒体の `IsReady` が偽だった（§14.2。`ResetMedia` は何もせず戻るため、成功扱いにしない）。
- ブレークポイント一覧が空でなかった（§14.2）。
- W1 または W2 の項目が設定値（または書いた値）と一致しなかった（§14.7、§14.12）。系列を停止し、該当試行を BLOCKED とする。

### 7.2 失敗時の動作

- 停止条件に当たった試行は、その時点で破棄する。終了状態（§3.3）と理由を一語で記録し、正例・負例に含めない。
- 終了状態の付け方: 停止条件（§7.1）に当たった試行には、原因に対応する終了状態を付ける。待ち上限（60 フレーム）または安全上限（300 フレーム）なら `BUDGET_EXCEEDED`、試行 120 秒またはプロセス 600 秒の壁時計なら `TIMEOUT`、その他は `ABORTED`（§6.1 の六つの状態）。区分 `UNOBSERVED_BUSY` は、待ち上限による打切りの試行に付き、その終了状態は `BUDGET_EXCEEDED` である。`COMPLETED` は、§6.1 の正常観測完了（P3 の取得まで）に到達した試行だけに付ける。予算終了は `COMPLETED` ではない。
- 同じ実行の中で、パラメータを変えて再試行しない。
- 次の試行は、§8 の規則に従う新しいインスタンスまたは新しいプロセスで始める。停止した試行の状態を持ち越さない。
- 終了コードは非 0 とする。失敗を 0 で終えない。

## 8. 実験区分の定義（起動形態）

**単位:** 1 試行 = §2〜§5 の一連の手順 1 回分。1 プロセス = 1 回の起動。

**系列:** 同一 ROM・同一 hash で連続起動する一群。系列には次の 2 種類を使う。

- 系列 S（cold と new-process 用）: 5 プロセスを連続で起動する。各プロセスは 1 試行のみ行う。
- 系列 W（warm 用）: 1 プロセスの中で同じインスタンスを使い、2 試行を続けて行う。開始条件は改訂 R5 の §14 に従う（同一インスタンスに対する明示的なコアリセットと、ゲスト再開始点の確定。状態の持ち越しは採用しない）。

**区分の判定規則（排他的）:**

| 区分 | 判定規則 | 含める条件 |
| --- | --- | --- |
| cold | 系列 S の先頭プロセスの試行 | 新規インスタンスで行う、系列の 1 件目 |
| new-process | 系列 S の 2 件目以降のプロセスの試行 | 直前に同じ系列の別プロセスが終了していること。別プロセスで新しく起動したインスタンスを使い、前プロセスの結果を入力に使わない |
| warm | 系列 W の 2 試行目 | 同じプロセス内で、1 試行目と**同一の** Geargrafx インスタンスを使う 2 回目の試行。別プロセスの試行は含まない |
| （区分外）ウォームアップ | 系列 W の 1 試行目 | 同一インスタンスを warm の試行の前に温める試行。どの区分にも入れず、正例にも負例にも使わない。記録は残すが集計に入れない |

- warm の 2 試行目（同一インスタンス、同一プロセス）と、new-process（別プロセス、別インスタンス）は、インスタンスが同一か否か、プロセスが同一か否かで読み分ける。
- 上の 3 区分は排他的である。系列 S の試行は、先頭なら cold、2 件目以降なら new-process に必ず 1 つ入る。系列 W の試行は、2 試行目だけが warm に入り、1 試行目はウォームアップとして区分外になる。
- 測定の回数: 系列 S を 5 本、系列 W を 5 本行う。区分ごとの試行数は cold 5、new-process 20、warm 5 である。
- §3.3 の区分（PLAY_ACTIVE など）と、本節の区分（cold など）は直交する。両方を試行ごとに記録する。
- `arm-B`（§4.6）は、上の 3 区分と組み合わせて記録するが、正例の集計には入れない。
- 区分の判定に必要な情報（プロセス ID、系列番号、プロセス内の試行番号、インスタンス作成の有無）をログに残す。

## 9. 再現性の確認

### 9.1 比較の単位

- 比較は、同じ §3.3 の区分と、同じ §8 の区分（cold、warm、new-process のいずれか）に入る試行どうしで行う。区分をまたぐ比較は情報としてのみ記録する。

### 9.2 個別判定項目（測定前に基準を固定する）

各項目は独立に判定する。ある項目が不合格でも、他の項目の判定を隠さない。

| ID | 項目 | 判定基準 |
| --- | --- | --- |
| R0 | 終了状態と hash | 終了状態が `COMPLETED`、終了コードが 0、合成 ROM と固定版 commit の hash が、比較する全試行で同一 |
| R1 | busy（区分）成立の観測 | §3.3 の正例条件（T_b と T_r の両方）を満たすこと。TRANSFER_BUSY は BLOCKED のため判定しない |
| R2a | reset 要求の読戻し | P1 の `Adpcm::Read($0D)` が、書き込んだ値 `$80`（`arm-B` では `$A0`）と一致すること。値は書込みの確認であり、reset の効果の期待値ではない |
| R2b | reset 効果の機会 | P1 の後に `RunToVBlank` が 1 回以上完了し、その間に Clock の呼出しが 1 回以上あったことを S3 で記録していること（reset の効果そのものは判定しない） |
| R2c | reset の効果（記録のみ、判定しない） | P2 の S2 で `m_control`、`m_playing`、`m_read_cycles`、`m_write_cycles`、`m_length` を記録する。本書では判定基準を置かない。値の判定は、測定後に別の結果文書で行う（期待値は本書で決め打ちしない） |
| R3 | 有界予算内での終了 | §6 の上限に達せず終了していること |
| R4 | 観測データの取得完了 | §5 の P0〜P3 を取得し、ログに記録されていること |
| R5 | 初期化済み領域の再現性 | ハーネスが設定した値（ADDRESS、CONTROL、WRAM の 0 初期化、ゲストバイト列 `$3E00` 以降）の、比較する試行の全件での一致 |
| R6 | 初期値が未確定な領域の差異 | ハーネスが書いていない WRAM の残り、および初期値が確定していない領域（IRQ マスクの保持場所など）。差異は不合格ではなく、差分として記録する（件数と位置） |

- 同じ試行でも、R0〜R5 が合格し、R6 に差異が出ることがある。その場合は両方を記録する。
- R6 の差異は、再現性の欠陥として隠さず、§9.4 の欄に記録する。
- 独立検査結果として、§5 の全採取値（P0〜P3 すべて）のバイト単位一致を別に記録する。これは唯一の合否基準にしない。

### 9.3 測定の回数

- 区分ごとの最小試行数: cold 5、new-process 20、warm 5（§8）。
- 各区分の試行数が最小数に達しない場合、その区分の再現性は判定しない。

### 9.4 不安定な値の記録欄

値が区分内で一致しなかった場合は、次の形式で記録する（欄は空でも残す）。

| 項目（§5 の行） | §3.3 の区分 | §8 の区分 | 系列・プロセス・試行番号 | 値 | 差分（先頭の相違位置、バイト） | 再現条件（インスタンス作成の有無、プロセス内の順番） |
| --- | --- | --- | --- | --- | --- | --- |
| （記入） | （記入） | （記入） | （記入） | （記入） | （記入） | （記入） |

### 9.5 判定基準の固定

- 本節（§9.1〜§9.4）の基準は、測定の前に確定する。
- 測定後に基準を変更しない。変更が必要になった場合は、既存の測定結果を破棄し、新しい版の契約として記録し直す。

## 10. 観測しない・実行しない項目（BLOCKED）

以下は、副作用を事前に遮断できない、または安全条件が成立しないため、本契約で観測も実行もしない。

**出典について:** B 番号は本文の独自の列挙である。#173 の Issue 本文には B 番号も表もない。各項の根拠は、リポジトリ内の該当する文書と行である。

| ID | 項目 | 根拠（リポジトリ内の出典） | 出典の範囲 |
| --- | --- | --- | --- |
| B1 | busy 中の、ゲストによる ADPCM MMIO（`$1808`〜`$180E`）への書込み | `docs/173-safety-monitor-limits.md:61`（汎用の MMIO 書込みは、既知 PC の場合を除き事前に遮断できない） | ADPCM のレジスタ個別の証拠は無い（UNKNOWN）。ADPCM の書込み経路は `adpcm_inline.h:91-122` |
| B2 | IRQ に関わるバス操作（`$1402`、`$1403` への書込み、読取りを含む）、IRQ ハンドラ入口の観測 | `docs/173-safety-monitor-limits.md:55-56`、`:61`（`$1402`、`$1403` の書込みは検出のみで完遂し、事前阻止不能）。IRQ 入口は `docs/research/366-irq-entry-status.md` | 読取りは `docs/173-safety-monitor-limits.md` の表の対象外（UNKNOWN）。保守的に BLOCKED とする |
| B3 | 転送中の、デバイス起点の書込み（ブロック転送、DMA） | `docs/research/367-device-write-audit.md`（デバイス起点の RAM 書込みの源泉監査。DEV-01 は `UpdateDMA` を指す。本書の行範囲は `adpcm_inline.h:191-226`）、`docs/173-write-path-inventory.md:42`（W17 は UNKNOWN、未監査） | 監査の範囲に依る |
| B4 | 任意の PC、先頭 opcode での実行の中断 | `docs/research/365-st-opcode-boundary.md`（ST 命令境界の副作用前遮断の判定記録。#365 の残作業は OPEN であり、その根拠として引く。フレーム内中断をしない根拠は本出典ではなく、本書の方針である） | 中断はフレーム境界の外では行わない（§6） |
| B5 | 純正 System Card の `JSR $E030`（AD_RESET 本体）の呼出し | `docs/research/ad-reset-contract.md:3`（`$E030` は候補エントリ。`BLOCKED_ABI`） | 候補であり、呼出先の実行は未確認 |
| B6 | CD 側の MMIO のうち `$1802`、`$1804` への操作、および表に無い `$1800`、`$1801`、`$1803` への操作 | `docs/173-safety-monitor-limits.md:52`（`$1802`）、`:53`（`$1804`）は事前阻止不能。`$1800`、`$1801`、`$1803` は表に無い（UNKNOWN） | 表に無い範囲は保守的に BLOCKED とする。`ResetMedia` が呼ぶ `CdRom::Reset`、`ScsiController::Reset`、`CdRomAudio::Reset` の直接の書込みは、Memory::Write の経路を通らない（`cdrom.cpp:113-129`、`scsi_controller.cpp:176-204`、`cdrom_audio.cpp:115-131`）。ただし、これらは `UpdateBackupRam`（`cdrom.cpp:128`、`memory_inline.h:544-558`。CPU 可視の `$F7` の写像を変える）、`UpdateIRQs`（`scsi_controller.cpp:200`、`scsi_controller_inline.h:92-106`。CD 側の IRQ 状態を変える）、`StopAudio` と `SetCurrentSector`（`scsi_controller.cpp:201-202`）、`InvalidateSectorCache`（`cdrom_audio.cpp:130`）を呼ぶ。前二つは B2 と CPU 可視の写像に関わるため、B6 の範囲に当たるかは断定せず、独立レビューで確認する。当たると確定した場合は、warm を BLOCKED とする |
| B7 | 純正 BIOS、市販 ROM、私有 ROM（`rom/`）の使用 | `docs/research/ad-reset-contract.md` の §7（System Card の BIOS バイト等を含めない。`AGENTS.md`、`docs/provenance.md` の除外） | 方針 |
| B8 | 割込みの発生を伴う実行（IRQ の許可を CPU 側で行うこと） | `docs/173-safety-monitor-limits.md:55-56`、`docs/research/366-irq-entry-status.md` | B2 と同じ |
| B9 | CPU 命令による `$180D`（reset 要求）の書込み | B1 と同じ根拠。reset の効果は `Clock` 内の `CheckReset` で現れる（`adpcm_inline.h:29-38`） | reset 要求はハーネスからのみ行う（§4） |
| B10 | 転送開始の CPU 命令による発行（`ad_trans`、`ad_play` の呼出し） | `docs/research/ad-reset-contract.md`（`BLOCKED_ABI`）、`docs/research/ad-trans-contract.md` | 純正 callee の ABI が未確定 |
| B11 | `$0B`（DMA 経路）による転送 busy の成立、および SCSI 信号の設定 | `adpcm_inline.h:191-226`（DMA は SCSI の信号に依存）、`docs/research/367-device-write-audit.md`（DEV-01）、`docs/173-write-path-inventory.md:42`（W17） | §3.4 の 2 |

BLOCKED の項目は **11 件**（B1〜B11）である。初版は 10 件で、B11 を追加した。

加えて、区分 **TRANSFER_BUSY 全体が BLOCKED**（§3.4）である。

## 11. 結果の区分

- 本契約の手順で測定された事実は **MEASURED** と記録する。
- MEASURED は、固定版 Geargrafx、合成 ROM、ハーネスの範囲内の事実である。
- MEASURED を、公開 System Card ABI の **VERIFIED** に昇格させない。実機、第 2 エミュレータ、純正 BIOS での確認がない限り、VERIFIED とは主張しない。
- `release_eligible=false`、`DEV_CALLABLE` 不可を維持する。
- 結果は区分ごとに別々に報告する。PLAY_ACTIVE の PASS を、TRANSFER_BUSY の PASS として報告しない。
- 測定の結果は、本書の期待値を埋めるのではなく、別の結果文書に記録する。

## 12. idle reset（既存）と busy reset（新規）の分離

| 項目 | idle reset（既存） | busy reset（本書の対象） |
| --- | --- | --- |
| 出典 | device 層: `ad-reset-diagnostic.md` §3・§5（#294、記録あり。ログ本体は本書で未読）。guest 層: PR #403（merged 2026-10-21T00:57:46Z）と PR #404（merged 2026-10-21T01:42:48Z）の本文の記述（ログ本体は未読） | 本書（新規） |
| 前提条件 | アイドル（PLAYING = 0、busy なし）から reset | §3.3 の区分ごとの成立条件を T_b と T_r で確認した後に reset |
| 観測の範囲 | 既存の観測はいずれも、busy からの reset ではない。PR #404 本文は実コアでの実行結果を記すが、本書はその値を再掲しない（ログ未読のため）。実機・第 2 エミュレータは未実施（NOT_RUN、PR #404 本文） | TRANSFER_BUSY の観測 0 件（§3.4 の BLOCKED）。契約の外の既存記録として PLAY_ACTIVE の 25 試行がある（§15。契約の集計には入れない） |
| 既存の再生中 reset との関係 | #294 の `neg-reset-playing` は、CONTROL `$A0`（bit5 残存）での reset である。本書の `arm-B` に相当する。PLAY_ACTIVE（CONTROL `$80` での reset）の結果ではない | PLAY_ACTIVE（CONTROL `$80` での reset）を測定する。TRANSFER_BUSY は BLOCKED |
| 区分 | §8 の cold、warm、new-process の区分は、既存の device 層観測（idle）には適用していない。契約の外の PLAY_ACTIVE 記録 25 試行は §15 を参照 | §3.3 の測定区分のうち PLAY_ACTIVE は busy reset ではない。PLAY_ACTIVE の測定は、§3.5 の判定 I のうち (a)、(b)、(c)、(e) の充足後に開始できる（(b) は未充足のため、現時点では開始しない。BLOCKED ではない）。busy reset（TRANSFER_BUSY）の区分は BLOCKED（§3.4） |
| 独立レビュー・CI | 独立レビュー未実施（診断 §5、PR #404 本文）。CI 未配線（PR #404 本文、§13 の 7） | 測定、独立レビュー、TRANSFER_BUSY の BLOCKED 解除条件（§3.4）の判定 |

idle reset の結果を busy reset の結果として数えない。両者は別の観測として記録する。

## 13. 残件と Issue #295 の範囲

Issue #295 の完了条件は「既知の ADPCM controller reset と busy 状態の最小 1 モード」である（Issue 本文の完了条件 1）。Issue 本文は PLAY_ACTIVE に言及しない。本書は、Issue の「busy」を TRANSFER_BUSY 系の主題として位置づける（§3.3、§12）。PLAY_ACTIVE の測定結果は、判定 III（完了条件）を満たさない（オーナー裁定）。TRANSFER_BUSY は BLOCKED（§3.4）であり、Issue の「busy」と本書の TRANSFER_BUSY の対応関係は UNKNOWN である。出典で確定するまで、対応を主張しない。

PLAY_ACTIVE（CONTROL `$80` での reset）の観測は、Issue の「busy」とは別の区分の観測である。Issue の完了条件 1 を満たす根拠としては扱わない。`arm-B`（`$A0`）は、#294 の既存観測との対応を見るための比較用の参照であり、Issue の完了条件とは結び付けない。

1. PLAY_ACTIVE の実コア測定（§8 の区分ごとの最小数、§9 の判定を併記）。開始は §3.5 の判定 I の (a)、(b)、(c)、(e) の充足後。判定 II と III とは独立である。
2. TRANSFER_BUSY の BLOCKED 解除条件（§3.4 の 3 点）の判定。解除できない場合は、転送 busy 中の reset を #295 の範囲外とするか、別の子 Issue に分けるかを判定する。
3. `arm-B`（bit5 を残す要求）を #295 の範囲に含めるか、比較用の参照として別に扱うかの判定。
4. IRQ マスクと保留フラグの保持場所の確認（§5 の UNKNOWN の解消）。
5. 独立再レビュー、CI への配線の判断。
6. 測定結果が出た後の、期待値の判定（本書とは別の文書で行う）。
7. CI 未配線（CONFIRMED）: `Makefile` に `ad_reset` の記載はなく、`integration-test` の探索パターン `test_*.py` は `geargrafx_ad_reset*.py` に一致しない（origin/main の `git grep` で確認）。既存の観測は手動実行のみである。
8. 参照番号の不一致（UNCONFIRMED）: PR #403 本文は device 層診断を #397 と書き、`ad-reset-diagnostic.md` は #294 と書く。正しい番号は未確認。
9. 既存の上限値（CONFIRMED）: 既存コードには 256 回ポーリング（`src/ad_reset_idle.s` 21 行）、240 フレーム、300 秒の 3 値がある。本書の §6 の値とは別であり、統合するかどうかは未判定。
10. AD_RESET 用の patch（出典の状態: UNKNOWN。前提項目の状態: BLOCKED）: `tests/integration/patches` に `ad_reset` の記載はなく、適用対象は未確認。全 patch の適用はしない。

ROM-Primitive の実装（`ad_reset_busy` の guest 実装など）は、本書の残件 1 と 2 が揃うまで開始しない。

## 14. warm の開始条件（改訂 R5、方式 a）

本節は §8 の warm の開始条件を定める。§8 の既存の文言は消さず、本節が warm の手順として優先する。

### 14.1 採用する方式と採用しない方式

- **採用（方式 a）**: 同一 Geargrafx インスタンスに対する明示的なコアリセット（`GeargrafxCore::ResetMedia(false)`）と、ゲストの再開始点の確定。
- **不採用（方式 b）**: 前の試行の状態を持ち越して次の試行に使うこと。
- 方式 a を「リセットを伴わない連続動作」とは表現しない。方式 a は、明示的なリセットを経て同一インスタンスで再開始する試行である。
- 方式 a の reset が初期化しない範囲（ブレークポイント一覧、ディスアセンブルレコード、TurboLink 関連、LaserActive の一部、BRAM の扱い）には、前の試行の残存状態が残りうる。これは R6 の差異として記録する（§9.2）。
- reset が初期化する範囲は、固定版ソースの読取りに基づく（未実測）。CPU（PC は `$FFFE` から読む。A、X、Y、S、P は既定で乱数。I=1。IRR、IDR=0。コールスタックをクリア。run-to-breakpoint 要求をクリア（ブレークポイントの一覧は消えない。§14.2））、WRAM 8 KiB、MPR0〜6（既定で乱数）と MPR7=0、ADPCM（状態、CONTROL=0、サンプルレート `$0F`、ADPCM RAM 64 KiB をゼロ）、IRQ（CPU の IDR、IRR、VDC、CD、ADPCM の end、half フラグ）。
- 根拠（固定版 b49ae82a の読取り）: `src/geargrafx_core.h:81`、`src/geargrafx_core.cpp:385-407`、`src/memory.cpp:100-126`、`src/adpcm.cpp:142-172`、`src/huc6280.h:139`、`:142`、`:175`。

### 14.2 前提条件（ResetMedia の前に確認する）

- 媒体が読み込まれていること。`ResetMedia` は媒体が読み込まれていない（`IsReady` が偽）と何もせず戻る（`geargrafx_core.cpp:387-388`）。戻り値はないため、ハーネスは呼出し前に `GeargrafxCore::GetMedia()->IsReady()` を読んで記録する（`geargrafx_core.h:103`、`media.h:43`）。偽なら試行を停止する（§7.1）。成功扱いにしない。
- `SetResetValues` と `SetResetValue` は、`ResetMedia` より前に呼ぶ（値は reset の消費時に読まれる。§1）。
- ブレークポイント一覧が空であること。試行の開始前に、W0 の前と W1 の後の 2 回、ブレークポイント一覧の件数が 0 であることを読んで記録する。読取りの経路は `HuC6280::GetBreakpoints()`（`huc6280.h:172`、`const std::vector<GG_Breakpoint>*` を返す）で、件数は要素数とする。測定の前に固定版ソースで再確認する。空でなければ、その試行は BLOCKED（実行しない）とし、系列を停止する。根拠: `ResetBreakpoints` は `m_breakpoints.clear()` を行い（`huc6280.cpp:297-299`）、`HuC6280::Reset`（`huc6280.cpp:207-270`）からは呼ばれていない（この範囲の確認による）。したがって `ResetMedia` は一覧を消さない。
- GeargrafxCore の生成時の挙動は未確認である。確認したのは `Memory` の生成で既定値の `Reset` が走ること（`memory.cpp:97`）だけである。
- 同一インスタンスであることの証拠を §14.5 の手順で残す。

### 14.3 順序（系列 W の各試行で同じ順序）

本書の P0〜P3（§5、reset 発行の前後）と名前が衝突するため、本節では ResetMedia の前後を W0〜W2 と呼ぶ。

1. （試行 1 のみ）インスタンスを作成し、合成 ROM を読み込む。インスタンス作成時に既定値による `Reset` が一度走るため、以下で上書きする。
2. `GetMemory()->SetResetValues(0, 0, 0, 0)`（mpr、wram、card_ram、arcade_card）と `GetHuC6280()->SetResetValue(0)` を呼ぶ（`memory.h:77`、`memory.cpp:280-286`、`huc6280.h:142`）。
3. 媒体の `GeargrafxCore::GetMedia()->IsReady()` を読んで記録する（§14.2。`geargrafx_core.h:103`、`media.h:43`）。偽なら系列を停止し、該当試行を BLOCKED とする。
4. **W0 を採取する**（ResetMedia の前。残存状態。記録のみで判定しない）。
5. `GeargrafxCore::ResetMedia(false)` を呼ぶ。
6. **W1 を採取する**（ResetMedia の直後、再配置の前）。
7. 再配置: `GetMemory()->GetWorkingRAM()` への memcpy（既存のハーネスと同じ。または `Memory::TryPoke`、`memory.h:66-67`）でゲストのバイト列を置く。`SetMpr(0, $00)`、`SetMpr(1, $F8)`、`SetMpr(2..7, $00)`（`memory.h:69`）。MPR0 を `$00` にする理由は、ADPCM をハーネスの device 層で操作するため、MMIO ページ `$FF` を外すことである。probe の同じ設定は `feature/issue-295-probe-full-capture@f152171` の `tests/integration/geargrafx_ad_reset_full.cpp` の `:215-218` にある。`LoadHuCardFromBuffer` は使わない（ROM を再ロードするため）。
8. PC の設定: `GetHuC6280()->GetState()->PC->SetValue(0x3E00)`（`huc6280.h:59-73`）。任意で `ResetDebuggerExecutionState()`（`huc6280.h:175`）を呼ぶ。呼ぶかどうかは測定前に固定し、ログに記録する。S を `$FF`、P を `$04`（I=1）に設定する（`GetState()->S->SetValue(0xFF)`、`GetState()->P->SetValue(0x04)`。probe は `feature/issue-295-probe-full-capture@f152171` の `tests/integration/geargrafx_ad_reset_full.cpp` の `:223-224`、#404 は `tests/integration/geargrafx_ad_reset_guest.cpp:63-64` で同じ）。reset が乱数化する S と P を、ハーネスが決めた値に置く。PC、S、P の設定の直後に読み戻し、設定値と一致することを記録する（一致しない場合は §14.12 の規則に従う）。
9. **W2 を採取する**（再配置と PC 設定の後）。
10. 順序は固定する: (i) 静的チェック（W2 の直後に、配置済みのバイト列と MPR 設定を検査する）、(ii) 動的 MPR 検出（§14.13。選択肢 (b) では動的検出を行わず `NOT_IMPLEMENTED` と記録する。PC の設定後、ADPCM を操作する前に、最初の `RunToVBlank` を行う）。(i) と (ii) の結果を記録し、失敗なら系列を停止し、該当試行を BLOCKED とする。(ii) の「結果」は `NOT_IMPLEMENTED` を含む。選択肢 (b) の下では (ii) の結果は `NOT_IMPLEMENTED` であり、手順 11 には進まない。(b) の下では、warm 系列の試行 1 も実行しない（手順 1 から 11 を実行しない）。PLAY 測定を独立に走らせるかは別判定とし、warm 系列としては実行しない。
11. 試行 1 と試行 2 の両方で、§2.1 の PLAY_ACTIVE の手順（ADPCM の設定、T_b、T_r、reset 発行、P0〜P3）を最後まで行う。試行 2 だけを warm として計上する。試行 1 は区分外である（§14.9）。

### 14.4 ゲストの開始点

- 開始点の論理アドレスは `$3E00` とする。ここに `BRA *`（バイト列 `$80 $FE`）を置く。論理アドレスの定義は #404 の `tests/integration/geargrafx_ad_reset_guest.cpp:20-21`、MPR 設定と配置の順序は同 `:55-62` にある。バイト列の根拠は二つである。第一に、既存の生ログの `bytes_hex`（`"80fe"`）。第二に、probe の出典 `feature/issue-295-probe-full-capture@f152171` の `tests/integration/geargrafx_ad_reset_full.cpp` の `:220-221`（`wram[kStubIndex] = 0x80;` と `wram[kStubIndex + 1] = 0xFE;`）。
- MPR1 = `$F8` により、論理 `$3E00` は WRAM のインデックス `$1E00` に対応する。
- ゲストは MMIO にアクセスしない。ADPCM の操作はハーネスが device 層で行う。

### 14.5 同一プロセス・同一インスタンスの証拠

系列 W の各プロセスについて、次を試行 1 と試行 2 の開始時に記録する。

- プロセス ID（OS の PID）。試行 1 と試行 2 で一致すること。
- インスタンス識別子: `GeargrafxCore` オブジェクトのポインタ値と、プロセス内のインスタンス作成の通し番号（1）。試行 1 と試行 2 で一致すること。
- ハーネス側の `ResetMedia` 呼出し回数（reset 世代）。試行 1 の W1 で 1、試行 2 の W1 で 2 となること。
- いずれかが一致しない場合は、その系列を停止し、方式 a の結果として採用しない。

### 14.6 #173 との整合

- gate は BLOCKED のまま維持する（前提項目の状態。§0 の軸に従う）。本節は gate を解除しない。
- 各 `ResetMedia` の後に、§14.3 の 10 の順序（静的チェック、次に動的 MPR 検出）で、§14.13 の選択肢 (b) に従い動的 MPR 検出を行わず `NOT_IMPLEMENTED` と記録する。静的チェックの結果は記録する。
- TRANSFER_BUSY は実行しない（§3.4 により BLOCKED。試行の実行状態は BLOCKED）。
- 集計は CONTROL=`$80` の標準試行だけとする。`$A0`（arm-B）は集計に混ぜない。

### 14.7 採取の時点と R5 の判定

- W0（ResetMedia の前）: 残存状態。CPU、MPR、WRAM、ADPCM、IRQ を記録する。判定しない。
- W1（ResetMedia の直後）: 次の項目について、設定値（または固定版ソースの読取りで定まる値）との一致を確認する。これらは設定値との一致の確認であり、ハードウェアの期待値ではない。区分内の全試行で同じ項目を確認する。いずれかが一致しない場合は、試行 2 を開始せず、系列を停止し、該当試行を BLOCKED とする（§14.12）。
  - WRAM 全 8 KiB（`m_wram[0x0000–0x1FFF]`）: 設定値 0（`SetResetValues` の wram。`memory.cpp:122-125`）。
  - MPR0〜MPR6: 設定値 0（`SetResetValues` の mpr。`memory.cpp:106-118`）。MPR7: 0（`memory.cpp:104`）。
  - CPU の P: I フラグが 1。IRR と IDR が 0（固定版 `huc6280.cpp` の Reset の範囲、§14.1）。
  - ADPCM: CONTROL（`$0D` の読戻し）が 0、サンプルレートが `$0F`、ADPCM RAM 64 KiB がゼロ（`adpcm.cpp:142-172`）。
- W2（再配置と PC 設定の後）: 次の全項目が、書いた値と一致することを確認する（R5）。PC の読戻し（`0x3E00`）、S と P の読戻し（`$FF`、`$04`）、MPR0〜MPR7 の読戻し（期待値は MPR0=`$00`、MPR1=`$F8`、MPR2〜7=`$00`。§14.3 の 7 と一致）、ゲストのバイト列（`80 FE`）の読戻し。不一致は試行 2 を開始せず、系列を停止し、該当試行を BLOCKED とする（§14.12）。
- W0 と W1 の差は、reset の効果として記録のみ行う（R2c と同じ扱い。判定は別文書）。
- 残存状態（W0）と再初期化後の状態（W1、W2）を混同しない。W0 の値を、再初期化後の期待値として使わない。

### 14.8 系列と試行数

- 系列 W を 5 本、それぞれ独立して実行する。1 系列は新しいプロセス 1 つで行う。
- 系列間で状態を共有しない。出力先は系列ごとに新しい一時ディレクトリとする。
- 1 系列の内訳: 試行 1（ウォームアップ。終了状態を採取。区分外）、試行 2（warm。計上）。
- 計上される warm は、系列ごとに 1 試行である。全体で 5 件とする。
- 試行 1 の終了状態が `COMPLETED` 以外の場合、その系列を停止し、試行 2 は実行しない（§7.2）。`COMPLETED` の定義は §6.1 による（正常観測完了。予算終了は含まない）。区分 `UNOBSERVED_BUSY` で待ち上限に達した試行 1 は §7.1 の停止条件に当たるため、`COMPLETED` ではなく、系列を停止する。したがって、試行 1 が `COMPLETED` であれば、その区分は `PLAY_ACTIVE` である。試行 2 へ進むための開始条件は §14.12 に一覧する。

### 14.9 ウォームアップ（試行 1）

- 試行 1 は、§14.3 の 1〜10 の手順を行い、続けて §2.1 の PLAY_ACTIVE の手順を最後まで行う。試行 2 と同じ形式で全項目を採取する。
- 試行 1 の最終状態は、試行 1 の P3（§5 の P3。正常観測完了の P3、または §7 の停止時）とする。P3 の時点の PC、MPR、WRAM、ADPCM 状態、IRQ 状態、S2 の全項目を記録し、終了状態（§3.3）を付ける。P0〜P2 も §5 と同じ形式で記録する。
- 試行 1 は区分外である（§8）。正例にも負例にも使わない。終了状態は系列の停止条件（§14.8）にのみ使う。

### 14.10 停止後の規則

- §7.2 を維持する。停止した試行の後は、方式 a を以後使わない。
- 停止があった時点で測定を止め、報告して独立レビューを受ける。

### 14.11 cold と new-process

- §8 の cold と new-process は変更しない。方式 a は warm にだけ適用する。

### 14.12 試行 2 の開始条件（一覧）

試行 2 は、次の 1 から 8 の全てが成立したときに限り開始する。一つでも成立しなければ、試行 2 を開始せず、系列を停止し、該当試行を BLOCKED とする。

1. 試行 1 が `COMPLETED` である（§14.8、§7.2）。
2. 媒体の `GeargrafxCore::GetMedia()->IsReady()` が真である（`geargrafx_core.h:103`、`media.h:43`）。試行 2 の W0 の前と W1 の後の 2 回を記録する（§14.2）。
3. ブレークポイント一覧の件数が 0 である。試行 2 の W0 の前と W1 の後の 2 回を記録する（§14.2）。
4. W1 の全項目（§14.7）が設定値と一致し、ハーネス側の `ResetMedia` 呼出し回数が 2 である（§14.5）。ADPCM RAM（64 KiB）のゼロの判定は、§14.14 の測定専用アクセサが独立レビューを通過するまで `NOT_CAPTURED` とし、PASS にしない。
5. W2 の全項目（§14.7）が書いた値と一致する。MPR の期待値は MPR0=`$00`、MPR1=`$F8`、MPR2〜7=`$00` である（§14.3 の 7 と一致）。
6. PC の設定経路が安定している。PC の設定直後の読戻しが `0x3E00` と一致し、かつ、動的 MPR 検出の最初の `RunToVBlank` の完了後も PC が `0x3E00` である（この後半は、動的 MPR 検出を行う場合にのみ適用する。選択肢 (b) では動的検出を行わず、後半の適用対象はない）。読戻しが一致しない場合、または設定後に PC が変化した場合は、経路が不安定とみなし、その試行を BLOCKED とし、系列を停止する。
7. 静的チェック（§14.3 の 10(i)）が合格し、かつ動的 MPR 検出（§14.13）が証明されている。現在は証明できていない（UNPROVEN）ため、この条件は `NOT_IMPLEMENTED` とし、PASS にしない。その結果、試行 2 は開始せず、warm は BLOCKED とする。
8. 同一性の証拠（§14.5）が、試行 1 と試行 2 で一致している。

### 14.13 動的 MPR 検出（改訂 R12。選択肢 (b) を採用）

**判定: 動的 MPR 検出は UNPROVEN（証明できていない）。** 既存の検出器（`tests/integration/geargrafx_reference_jsr.cpp`）は `step_debugger = true` と命令単位の停止に依存しており、§6 と §10 B4 の「CPU 命令単位の強制停止をしない」と両立しない。フレーム境界での受動的な記録（MPR の比較）だけでは、フレーム内の MPR 変化を排除できない。

- **選択肢 (b)（採用）:** 動的 MPR 検出は実装しない。§14.12 の 7 は `NOT_IMPLEMENTED` とし、試行 2 を開始しない。warm は BLOCKED とする（§3.5、§14.12）。
- **選択肢 (a)（今回は採用しない）:** 命令単位の安全性を別途証明できた場合にのみ、契約を改訂して再検討する。その証明は次を満たす必要がある。(1) `step_debugger = true` を使わない。(2) CPU 命令単位の強制停止をしない（§6 の実施点を変えない）。(3) §6 と §6.1 の予算・停止条件を緩和しない。(4) MPR の変更後に危険な MMIO 操作が実行されないことを、設計で示す。フレーム境界の比較を命令単位の検出と同等とみなさない。合成 `BRA *` ゲストの静的検査（`TAM` と `CLI` を含まないこと、MPR0 = `$00` で I/O ページを論理アドレスに現さないこと）は候補であるが、この契約では証明とみなさない。

### 14.14 ADPCM RAM のアクセサ（改訂 R11、作業 C）

§14.7 と §14.12 の 4 の ADPCM RAM（64 KiB）は、`adpcm.h:95` で非公開（private）であり、公開アクセサがない。固定版 Geargrafx の**測定専用コピー**に、最小限の読取り専用アクセサを追加する方針を定める。

- 追加するもの: `Adpcm` に `const u8* GetRam() const`（名前は実装時に確定する）を一つ追加する。戻り値は `m_adpcm_ram` の先頭（サイズ `0x10000`）を指す。
- 条件:
  1. 読取り専用。`const` で宣言し、内部バッファを書き換えない。
  2. ADPCM RAM 64 KiB 全体を確認できる（サイズ `0x10000` を、呼出し側が文書化された定数として使う）。
  3. 呼出しで Clock、ADPCM の状態、他の装置の状態が変化しない（副作用なし）。
  4. 戻り値の有効期間は、同じ `Adpcm` オブジェクトの生存期間中。採取はフレーム境界で、全体を一度にコピーする。コピーは取得時点の値であり、その後の Clock で変わりうる。
  5. 上流 commit（`b49ae82a`）と測定パッチを別々に記録する。測定パッチは、測定専用コピーにだけ適用する。
  6. 既存の worktree や clone は上書きしない。測定専用コピーは、新しいディレクトリに作る。
  7. 非侵襲性の回帰テスト: 元のコア（`b49ae82a`）と改変後のコアで、同じ固定の手順（既存の probe の記録と同じ手順）を実行し、既存の観測項目が一致することを確認する。このテストは実装の段階で行う。本契約では実行しない。
  8. 独立レビューで CRITICAL と MAJOR が 0 になるまで、実測に使わない。
- 判定: 承認されるまで、ADPCM RAM のゼロの判定は `NOT_CAPTURED`。承認後は、64 KiB 全体がゼロであることを、設定値との一致として判定する。
- 区別: 固定 Geargrafx（上流 `b49ae82a`）と、測定専用の改変コアは区別する。改変コアの結果は、改変コアの結果として記録し、上流の結果として扱わない。

### 14.15 実測の前提（改訂 R11、作業 E）

- 現時点で、測定の開始を承認したものではない。
- 実測の開始条件（すべて必要）:
  1. 本契約の独立レビューで CRITICAL と MAJOR が 0。
  2. §14.14 の ADPCM RAM のアクセサ、および §14.13 の動的 MPR 検出（現在は選択肢 (b) であり、warm は BLOCKED。(a) は §14.13 の条件で改めて検討するまで採用しない）。ADPCM RAM のアクセサは、warm の BLOCKED とは独立に、独立レビューを通過する必要がある。
  3. §3.5 の判定 I の (b)（独立レビューによる承認）が充足。
4. warm runner の `SERIES_TIMEOUT_S = 120` を、試行単位（`TRIAL_WALL_SECONDS` = 120 秒）と、プロセス単位（`PROCESS_WALL_SECONDS` = 600 秒）に分けて修正する。現行の 120 秒は、2 試行を含むプロセスの上限として誤っているため、この修正は測定の開始条件である。
- warm の測定: §14.13 の (a) が別途証明され、契約が改訂されるまで開始しない（BLOCKED）。
- 結果の扱い: warm の結果を VERIFIED（System Card ABI）へ昇格させない。結果は MEASURED の範囲に限る。
- 集計: CONTROL `$80`（標準）と `$A0`（arm-B）は混合しない。`$A0` は集計に含めない。
- 維持する判定: TRANSFER_BUSY は BLOCKED のまま。既存 25 試行は R4 FAIL、R2b NOT_EVALUATED のまま（§15）。
- 生ログ: 既存 25 試行の生ログは保存する（§15）。新しい系列は、この改訂 R11 以後の契約リビジョンで記録する。
- 固定 Geargrafx と計測用改変コアは区別する（§14.14）。

## 15. 改訂履歴

- **改訂 R5（本節 §14 の追加）**: 同一 Geargrafx インスタンスに対する明示的なコアリセット（`ResetMedia(false)`）による warm の開始条件（方式 a）を定めた。方式 b（状態の持ち越し）は採用しない。既存の文言は削除していない。追加箇所は §14、§8 の warm の行への参照、本節の改訂履歴。測定は行っていない。独立レビューを受けるまで warm の測定は開始しない。
- **旧リビジョンの場所（PR #577、ブランチ `feature/issue-295-busy-reset-contract`）**:
  - 初版: `c1d353912bb6b094b8eb774d93e68b1ab8601591`
  - 修正 2 回目: `8fd587e16fce1c3145ddbed5cdd5baff025f5ff0`
  - 修正 3 回目（独立レビュー不承認への対応）: `a021efb1202df907d500fbaf685f496474407748`
  - 修正 4 回目（再レビュー条件付き承認への対応。改訂 R5 の直前）: `97de277aea236f388e4dc58b57ae1ac9264dca22`
- **契約の外の既存記録（本契約の集計には含めない）**: 25 試行を確認した。内訳は cold 5、new-process 20 で、すべて区分 PLAY_ACTIVE、CONTROL `$80`、終了状態 `COMPLETED` である。`clock_calls` は `NOT_CAPTURED` のため、R2b（Clock 呼出しの機会）の根拠にはならない。W0〜W2 の採取もない。
  - 前回の記載（cold 4、new-process 1）は、ファイル数を数えた誤りであった。試行数は上記のとおりである。
  - 取得に使った probe: 固定版 `73ef6ac`（ブランチ `feature/issue-295-probe-full-capture`、`tests/integration/geargrafx_ad_reset_full.py` と `.cpp`）。
  - 生ログは測定者の作業領域（リポジトリ外）に保存してある。ファイルと試行数、SHA-256 は次のとおり。

| 生ログのファイル名 | 試行数 | 内訳 | SHA-256 |
| --- | --- | --- | --- |
| cold-1.out | 1 | cold 1 | ff89cdd9f1015fdd04e7bc6ab0b7299302dd1b66147f9b63ae084e12a687025c |
| cold-2.out | 1 | cold 1 | 027df673622f73cc7709e6e9f98a8ba6029540d9f6bb0a3d93f0e773bc6cd0e4 |
| cold-3.out | 1 | cold 1 | bbbe157f4cf6ad7722046b828e610f126978d5040d44c34e0dd1dc4175895abe |
| cold-4.out | 1 | cold 1 | e8d44fc5987e457411693d6e04320984ee7c84d53d8fa17a51321b31be6ae066 |
| newproc-21.out | 21 | cold 1（repeat 0）、new-process 20（repeat 1〜20） | 3443510bdf2c6378d6d5f9264956c020e813e1b7f271f86d3e39b09ea30887f9 |

  - 合計 25 試行（cold 5、new-process 20）。
  - 判定（本契約の基準を既存記録に適用した結果。遡及的な PASS にしない）: R4（§5 の P0〜P3 の取得）は **FAIL**。P0〜P3 の全 100 点で `adpcm.play_pending`、`adpcm.read_cycles`、`adpcm.write_cycles` が `NOT_CAPTURED`（各 100 件）、S3 の `clock.clock_calls` と `clock.master_clock_cycles` が `NOT_CAPTURED`（各 25 件）である。R2b（Clock 呼出しの機会）は **NOT_EVALUATED**（`clock_calls` が未採取のため）。R0、R1、R2a、R2c、R3、R5、R6 は、既存記録に本契約の基準を適用していないため判定しない（未判定）。全件が `COMPLETED` であることは終了状態の事実であり、判定の PASS ではない。
  - 別の記録（契約の外）: #294 の harness（`tests/integration/geargrafx_ad_reset.py`）による new-process 5 回の要約。PC、MPR、WRAM は未採取である。契約の集計には含めない。この要約も同じ作業領域に保存してある。
- **改訂 R5 の修正（独立レビュー不承認への対応）**: §3.5 を区分ごとに分けた（PLAY_ACTIVE と warm は (a)、(b)、(c)、TRANSFER_BUSY だけが BLOCKED）。§6、§12、§13 の件数と開始条件の表記を §3.5 に合わせた。§14.2 にブレークポイント一覧の確認を追加した。§14.3 の順序と §14.9 の試行 1 の定義を揃えた（試行 1 も §2.1 の手順を最後まで行い、P3 を最終状態とする）。§14.7 の W1 の確認項目を広げた。S と P、MPR の設定を probe 73ef6ac と揃えた。§15 の既存記録を 25 試行に訂正した。
- **改訂 R6（独立レビューの裁定反映）**: 承認時（97de277）と現行の差分を行単位で記録する表を PR 本文に残した（削除はしない）。承認時の `BLOCKED` の意味に戻し、`BLOCKER` は使わない。§3.5 を三つの独立した判定（I: 測定、II: Issue の実装、III: Issue の完了）に分けた。PLAY_ACTIVE の測定条件（(a)、(b)、(c)、(e)）と TRANSFER_BUSY の BLOCKED を別の行に書いた。MAJOR の対応箇所は §16 に記した。過去の 25 試行は、遡及的に PASS にしない。新契約の下での判定も行わない。
- **改訂 R7（独立再レビューの指摘への対応）**: 試行 2 の開始条件を §14.12 に一覧化し、W1、W2、PC 設定の不一致を系列停止と BLOCKED に定めた。COMPLETED の定義を §7.2 に置き、§14.8 をそれに揃えた。既存 25 試行の判定を上の段落に記録した（R4 FAIL、R2b NOT_EVALUATED）。§3.5 の (e) を warm でも独立レビューで確認する扱いに揃え、ResetMedia が呼ぶ CD、SCSI、CD-audio の Reset（`geargrafx_core.cpp:1237-1239`）を根拠に記した。§7.1 に IsReady 偽とブレークポイント非空を加えた。§12 の重複を削除した。本文から内部の指摘記号を除いた。直前の版は `72599ef`（改訂 R6）。過去の 25 試行は、遡及的に PASS にしない。新契約の下での判定も行わない。
- **改訂 R11（Wave 7 裁定、契約のみ。実装と測定はしない）**: 作業 A: 予算終了の判定を §6.1 の六つの状態に分け、「予算終了 → `COMPLETED`」を削除した（§5 の P3、§7.1、§7.2、§14.8、§14.9 を整合）。定数（300 フレーム、60 フレーム、120 秒、600 秒）を §6.1 に一箇所で定義し、C++ probe と Python runner の参照を記録した。現行の warm runner の `SERIES_TIMEOUT_S = 120` はプロセス上限と一致しないため、次段階の実装で修正する。作業 B: §14.13 で動的 MPR 検出の選択肢 (a)（条件付き）と (b) を定めた。作業 C: §14.14 で ADPCM RAM の測定専用アクセサの方針を定めた。作業 E: §14.15 で実測の前提を定めた。既存 25 試行の `end_state` の `COMPLETED` は旧定義（予算終了を含む）であり、新定義の `COMPLETED` ではない。この定義で再判定せず、遡及的な PASS にしない。§15 の判定（R4 FAIL、R2b NOT_EVALUATED）は変えない。
- **改訂 R12（動的 MPR 検出の裁定反映）**: §14.13 の動的 MPR 検出を UNPROVEN とし、選択肢 (b) を採用した。既存の検出器は `step_debugger = true` と命令単位の停止に依存し、§6 と §10 B4 と両立しない。このため §14.12 の 7 は `NOT_IMPLEMENTED`、試行 2 は開始せず、warm は BLOCKED（§3.5、§14.12、§14.15）。選択肢 (a) は、命令単位の安全性を別途証明できた場合にのみ、契約を改訂して再検討する。ADPCM RAM のアクセサ（§14.14）は、warm の BLOCKED とは独立に要件として残し、測定には使わない。warm probe（HEAD 6c46562）は変更しない。現状どおり試行 2 を開始せず BLOCKED で終わることを確認する対象とする。
- **warm のログ**: 改訂 R5 の下ではまだ存在しない。独立レビュー後の測定で取得する。

## 16. 独立レビューの指摘への対応（改訂 R6）

| 指摘 | 対応箇所 |
| --- | --- |
| 測定開始ゲートと Issue 実装ゲートの混同 | §3.5 の判定 I（測定の開始）、判定 II（Issue の実装開始）、判定 III（Issue の完了）を独立に記載。判定 I の充足は判定 II と III を意味しない |
| warm 第 1 試行の操作と COMPLETED 条件 | §14.3 の 11（試行 1 も §2.1 の手順を最後まで行う）、§14.9（試行 1 の最終状態は P3）、§14.8（COMPLETED の定義と、試行 2 へ進む条件） |
| ResetMedia(false) の前提条件 | §14.2（媒体の IsReady、SetResetValues の順序、戻り値がないこと、no-op の扱い） |
| 残存ブレークポイントの干渉 | §14.2（`GetBreakpoints()` で件数 0 を確認。W0 の前と W1 の後の 2 回）。根拠は `huc6280.cpp:297-299` と `:207-270` |
| reset 後の W0〜W2 の採取と初期化検証 | §14.3 の 4、6、9、§14.7（W0 は判定しない。W1 は設定値との一致。W2 は書いた値との一致） |
| 静的安全チェックと動的 MPR 検出の実施順序 | §14.3 の 10（静的チェック → 動的 MPR 検出 → ADPCM 操作の順）、§14.6 |
| 過去 25 試行の集計記録との整合 | §6、§12、§15（契約の外の記録。遡及的な PASS にしない。新契約の下での判定は行わない） |

前回（R6）の MAJOR は 6 件であり、本表はその本文に含まれる 7 項目を個別に対応づけた。

## 17. 独立再レビューの指摘への対応（改訂 R7）

| 指摘 | 対応箇所 |
| --- | --- |
| MAJOR 1: 試行 2 へ進む条件が不完全 | §14.12（一覧。W1、W2、PC 設定の不一致は系列停止と BLOCKED。PC 設定経路の不安定は BLOCKED）、§14.3 の 3 と 10、§14.7（W1、W2） |
| MAJOR 2: 試行 1 の COMPLETED の定義の矛盾 | §7.2（定義と終了状態の付け方）、§14.8（§7.2 に揃えた。UNOBSERVED_BUSY の待ち上限による打切りは停止） |
| MAJOR 3: 既存 25 試行の判定が未記録 | §15 の判定の段落（R4 FAIL、R2b NOT_EVALUATED、他は未判定。PASS にしない） |
| MAJOR 4: §3.5 判定 I の (e) が warm 側で断定 | §3.5 の判定 I の (e) 行と判定行（独立レビューで確認。根拠は `geargrafx_core.cpp:1205`、`:1237-1239`） |
| MAJOR 5: PR 本文の既存ログ件数 | PR 本文の該当節を §15 に合わせた |
| MINOR 6: 件数表記の不一致 | §16 末尾、PR 本文の MAJOR 節 |
| MINOR 7: §12 の観測の範囲の重複 | §12 の「観測の範囲」の行 |
| MINOR 8: PR 本文の commit 参照 | PR 本文の改訂 R5 の行（`46990e2` を `3691655` に訂正） |
| MINOR 9: 停止条件の追加 | §7.1 の 2 項目（IsReady 偽、ブレークポイント非空） |
| MINOR 10: 内部の指摘記号 | §14.2、§14.3 の 8 から除去 |
