# 最終報告書（日本語）

日付: 2026-10-16
作業ブランチ: `pr170`（HEAD `1960d2b`, push 先 `origin/test/reference-bios-scenarios-90`, PR #170 Draft）
関連ブランチ: `feat/40-ex-getver`（HEAD `59b5613`, PR #174 Draft, Issue #40 紐付け）
ベース: `main` = `49d2c1d`（マージコミット `7518c59`/`10e9fd6` 経由の先行作業を含む）

本レポートは**実行結果の記録**であり、System Card 3.0 / OSS BIOS の互換性・起動保証を行うものではない。実測はスロット `$1E` のみ。推定・NOT RUN の区別を明示する。

---

## 1. BIOS 読み込み結果（合成テストROMは利用、私的BIOSは読み込み・検証のみ）

- 実対象（私的、コミット・公開禁止）: `rom/syscard3.pce`、262,656 バイト、512 バイトヘッダ付き、sha256 `df4f75feebb95e53dfef72dea0787df743e3474ba046ff5a4cb88e34dda93ff1`。コア DB による識別名 "Super CD-ROM System [3.0] (J). CRC: 6D9A73EF"。改変・コピー・CI 持ち込みは行っていない。
- 観測ランではこの私的 BIOS を `LoadBiosFromBuffer(..., true)` で読み込み、合成ディスク（32セクタ、MODE1/2352、ゼロ埋め）を添付した。
- CI / 公開テストでは**完全合成**の 256 KiB ダミーBIOS + 合成CUE/BINを使用（`tests/integration/geargrafx_reference_guard.py`）。私的メディアに依存しない。

評価: 読み込み自体は成功（ヘッダ除去後、コアが "BIOS loaded" をログ出力）。**起動・商用ゲーム互換は未検証。**

---

## 2. コア実行（實測、ピンアップ Geargrafxコア）

- ピン: `geargrafx-b49ae82a012eade566d72e9d61c9d95a5caa4da3` のデバッガー有効ビルド（`/tmp/oss-pce-cd-bios-tools-1000/` にキャッシュ、コアコードのリポジトリへのコピーなし、GPLリンク実行物はローカルのみ）。
- 単命令ステップ実行、`HuC6280::AddBreakpoint` による CPUアドレス書込みブレークポイント、`Memory::GetMemoryMapWrite` による書込み禁止、MPR7 ≠ 0xFF によるハードウェアページ隔離、1命令予算強制を組み合わせた**コア内執行**を行った。
- プローブJSONは stderr 経由（コアログは stdout）。

評価: コア上での実CPUステップ実行と観測は成功。実コア観測は**エミュレータ動作であり、実ハードウェアの証跡ではない**。

---

## 3. RESET 観測（實測、私的BIOS × 実コア、1命令）

コマンド（成功）:
```
python3 tools/reference_bios_runner.py /tmp/opencode/refbios/reset-config.json \
  --adapter tools/geargrafx_reference_adapter.py \
  --output /tmp/opencode/refbios/reset-obs.json
```
結果 (`OBSERVED LOCAL_ONLY`):
- PC=0xE0F4、MPR7=0、I=1/D=0/T=0/B=0。
- A/X/Y/SP と MPR0–6 は PRNG シード（`time(NULL)`）由来で、1秒以内は同一値。
- **コア固有の重要発見**: 非テストビルドでは `ZERO_PAGE_ADDR=0x2000` / `STACK_ADDR=0x2100`（`huc6280.h`）。ゼロページと8ビットスタックは**ページ1**にあり、実ハードウェア（ページ0）とずれる。JSRのスタックプッシュは `$21FE/$21FF` に着地し、allowlistは `$2000..$4000`（ページ1）をカバーする必要があった。

---

## 4. EX_GETVER 実行可否（slot $1E、實測 → 可能、ガード付き）

- ガード解放前は `slot-1e` パスは明示的に BLOCKED（post-run空ram_diffでは不充分）。
- `Memory::GetMemoryMapWrite`（防止）+ `AddBreakpoint`（検出）+ MPR0xFF隔離 + 命令単位予算による**コア内強制**を実装し、負例・正例を実コアで検証した後、パスを開放（CIは合成BIOS/合成CDで実行）。
- ゲスト呼び出し `LDA #$AA; LDX #$55; LDY #$CC; JSR $E05A; BRA *` が実BIOSのslot `$1E` (`0xE000+3*0x1E = $E05A`, テーブルバイト `4C 2D F0` = `JMP $F02D`) に到達し、手続き `$F02D` (`LDX $FEC2; LDY $FEC3; RTS`) から呼び出し元に戻ることを実コアで確認。
- 負例 `neg-write` (宛先 0x4000, CD-ROM RAMバンク0): `detected=true`, 書込み未着地。正例 `pos-write` (宛先 0x3E50, ワークRAM): `guard_after=0x5A` 着地。双方合成BIOSでPASS（`make test` / `make integration` に組込済）。

評価: EX_GETVER スロットのゲストJSR/RTSは**実コア上で実行・戻り可能**。他80スロット＋9 PSGサブコマンドは未解放。

---

## 5. 実測 API 仕様（EX_GETVER、slot $1E、1スロットのみ、實測、確定的）

| 項目 | 値 |
| --- | --- |
| スロット | `$1E`（入口 `$E05A`、ジャンプテーブル ROM `0x5A` = `4C 2D F0`） |
| 手続き | `$F02D`: `LDX $FEC2` / `LDY $FEC3` / `RTS` |
| 戻り値 | X=3（メジャー）、Y=0（マイナー）→ バージョン "3.0"、**A保持**（呼出し前 0xAA → 後も 0xAA） |
| フラグ後 | P=$06（Y=0によりZ=1） |
| SP | $FF（呼出し前と同一、スタック釣り合い） |
| 命令数 | 8（呼び出し元プロローグ含む） |
| ram_diff | `$21FE`/`$21FF` のみ（JSR 戻りアドレスプッシュ） |
| ガード | `detected=false`, `status=no_violation` |

再現: 複数ランで同一結果（同一秒内は同一シード）。出力スキーマ `oss-pce-cd-bios/reference-observation/v1`、`status: OBSERVED`、`trust: LOCAL_ONLY_NOT_VERIFIED`。**VERIFIED ではない**（ABI承認・実ハードウェア比較未完了）。

---

## 6. OSS 実装機能（Issue #40、PR #174 Draft）

- `src/overrides/slot_1E.s`: OSS独自実装のEX_GETVER（私的BIOSのバイトや逆コードを一切使用しない）。
  - `LDX #3; LDY #0; RTS`：X=メジャー、Y=マイナー、A保持、バランスの取れたRTS。
  - 実測契約（X=3,Y=0,A保持）に合わせた。エントリはジャンプテーブル経由 `$E000+3*$1E=$E05A`。
- `tools/build_rom.py`: `verify_dev_rom` によるオーバーライド本体のビルド時検証分離（他DEV診断ROMも共用）。リリースゲートは依然ブロック（未実装80スロット、`abi: UNVERIFIED`、`release_eligible: false`）。
- 実コア検証（`make dev-integration`）: `api_jsr_1E` プローブで cold 時 a=0x11（呼出し元）/ x=3 / y=0 / p=6 / sp=255 / 継続復帰、RAM上の呼出し元A保持、スロット01/03等は非復帰スタブのまま。

評価: **1スロットのみ実装**。実装 ≠ 互換性保証 ≠ 起動保証。

---

## 7. テスト通過/失敗数

- `pr170` (作業ツリー): `make test` → **689 tests OK**、`make check` → PASS（`tools/contracts.py` + `tools/check.py`）。`tests/integration/geargrafx_reference_guard.py` は `make integration` に組込済でPASS。
- `pr40` (feat/40-ex-getver): `make test` → **716 tests OK**、`make dev-test` → 48 tests OK、`make dev-integration` → PASS（実コア EX_GETVER契約アサート含む）、`make check` → PASS。`make dev-release-gate` → exit 3 `BLOCKED: 80 unimplemented API slots`（想定通り）。

失敗なし。合成/実コアテストはいずれもグリーン。

---

## 8. 残ブロッカー（正直に記載）

1. **測定済みAPIは1スロットのみ**（EX_GETVER slot $1E）。他80スロット＋9 PSGサブコマンドは `BLOCKED / NOT RUN`。レビュー済みfixture・実コア負例・独立ABI承認が未完了。
2. **System Card起動・商用 CD-ROM² ゲーム起動は未検証**。本レポートのいかなる記述も起動保証ではない。
3. **ABI未検証**（`abi: UNVERIFIED`、`override_checks: structural-only`）。実測はピンアップGeargrafxコア上のみで、実ハードウェア・他エミュレータ比較なし。
4. **コア特異事項**: スタック/ゼロページがページ1（実ハードウェアはページ0）。OSS BIOSはこのコア挙動で動作させる必要がある。
5. 実行インフラ（証跡提出・署名・夜lyビルド等）は別Issue（#103 #124等）に残る。

---

## 9. PRs/Issues

- PR #170（Draft, `origin/test/reference-bios-scenarios-90`, `documentation`ラベル）: 参照テスト計画・安全側実行境界・コア内書込みガード・guard統合テスト・test-plan改訂・EX_GETVER実測記録。タイトル/本文を最新状態に更新済。CI通過を待ってレビュー。
- PR #174（Draft, `feat/40-ex-getver`, `documentation`+`enhancement`ラベル）: Issue #40 EX_GETVER OSS実装。Issue #40にコメントで紐付け。
- Issue #40 `[API][P0][EX] $1E EX_GETVER`: 実装進行中（実装コードあり、ABI未検証のため完了ではない）。
- コミット: `pr170` に3コミット（ガード実装/証明、ガード付きslot-1e実行、test-plan改訂）；`feat/40-ex-getver` に1コミット（EX_GETVER実装＋テスト）。

---

## 10. 次のAPI作業

1. **PR #170 / #174 のレビュー対応とCI完了待ち**（Draft維持、required CI successまでマージしない）。
2. **次の1スロット**（優先度順の候補: CD_RESET `$01`、CD_READ `$03`、BCD変換等）について、実測→OSS実装→実コア検証のサイクルを繰り返す。1 Issue / 1 PR を維持。
3. **ABI台帳の更新**：`docs/abi-contracts.md` のslot `$1E` を「実測あり（1スロット）」（実ハードウェア未確認のため`VERIFIED`にはしない）。
4. **日本語最終レポートのこの版**をPR #170に添付/参照。
5. 互換性誇張の禁止：どの段階も「起動・商用ゲーム動作 VERIFIED」と主張しない。

---

### 実証根拠の分類記号

- **實測**: 私的BIOS/実コア/合成メディアによる実際のCPU実行観測（§3, §4, §5, §6の実コア検証）。
- **合成**: 合成BIOS/合成CDによるCIテスト（`geargrafx_reference_guard.py`）。
- **推定**: 未検証の仮定（コアページ1のハードウェア相違等）。
- **NOT RUN**: 未実行のAPI/シナリオ（80スロット＋9 PSG、起動、商用ゲーム）。

---

### 補足：メモリ保護の是正（セキュリティレビュー対応）

PR #170のガード付き参照実行で、物理バンクエイリアスによる書込み保護の抜け穴を実コアで確認し是正した。

- **欠陥（實測）**: `Memory::GetMemoryMapWrite()`は物理バンク（MPR値）単位であり、MPR0==MPR1==0xF8（ページ0とページ1が同一ワークRAMバンク）かつページ1のみ許可リストに入っていると、`STA $0000`（ページ0）が書込み禁止をすり抜けてワークRAMに到達した。CPUアドレス書込みブレークポイントも`[$4000, $FFFF]`のみを対象としていたため、ページ0への書込みは検出されなかった。実コア観測: `detected=false`, `reason=forbidden_write_not_detected`, 書込み先が`ram_diff`で`0x2000`と誤報告された。
- **是正**: (1) 実行前に物理バンクが許可ページと非許可ページを共有する場合、プローブを終了コード8で拒否（fail closed, `BLOCKED`）; (2) ページ0をWRAMバンクではなくROMバンク0にマップし、エイリアスを解消; (3) 非許可CPUアドレス全域`[0, allow_start) ∪ [allow_end, $FFFF]`をCPUアドレス書込みブレークポイントで監視（ブレークポイントは`Memory::Write()`内でストア実行前に発火、検出；`map_write`のdeny-by-defaultで防止）; (4) ガード判定を対象アドレスの前後バイト等価＋ブレークポイント検出フラグで行う。
- **結果（RED→GREEN）**: 是正前は`STA $0000/$1FFF`が`detected=false`で書込み到達（RED）。是正後は`detected=true`, `ram_diff`空（書込み抑制）, 許可ページ`$2000/$3FFF/$3E50`は書込み到達かつ未検出（GREEN）。エイリアス構成（MPR0==MPR1）は終了コード8で拒否。`make test` 689テスト OK、`make check` PASS。

この是正後も、slot `$1E`以外の80スロット＋9 PSGサブコマンド、ST0/ST1/ST2など`Memory::Write()`を経由しないI/O経路、実ハードウェア動作、System Card起動は未検証／BLOCKEDのまま。