# Issue #311: 実コア計測の安全前提の点検（純正 JSR は BLOCKED）

## 目的
API 契約の実コア計測に必要な観測項目（A/X/Y/P、SP、MPR、RAM 差分、PC 復帰）が、
既存の核ハーネス（`tests/integration/geargrafx_core_harness.py`、#6 系）で取得できるかを点検し、
観測器自体を自作ルーチンの正例・負例で検証する。本記録は **観測器の検証** であり、
BIOS ABI の証拠ではない。

## 範囲と禁止事項
- 純正 System Card BIOS は読み込まず、JSR も実行しない。核ハーネスの対象は DEV ROM の
  `$E800-$EFFF` テストオーバーレイ内の自作ルーチンに限られる。
- `rom/` は参照しない。ABI は推測しない。`VERIFIED` / `DEV_CALLABLE` は付与しない。
  `release_eligible` は変更しない。

## 純正 JSR（BLOCKED）
| 項目 | 状態 | 理由 |
| --- | --- | --- |
| 純正 BIOS 入口への JSR（`EX_*` / slot 呼出し） | **BLOCKED（未実行）** | 純正 callee の実行は未承認。ハーネスは DEV ROM 以外を読まず、プラグイン `target` は DEV ROM の bank0 シンボルに限られる（`validate()`）。 |
| 参照 BIOS の書込みガード（`geargrafx_reference_guard.py`、`geargrafx_reference_jsr.cpp`） | 本 PR では未実行 | 合成 BIOS のみで動く別ゲートであり、本 Issue の範囲では再実行していない。 |
| 観測器による「純正 JSR の拒否」 | 対象外 | 純正 JSR を配線しないことで担保する。JSR を自動検出して拒否する機構はない。 |

## 観測項目の点検結果
実行条件: pinned Geargrafx `b49ae82a012eade566d72e9d61c9d95a5caa4da3`、HuCARD ロード（ディスクなし）、
cold/warm 各 2048 命令、DEV ROM（`make dev-rom MODE=debug`）。

| 観測項目 | 取得可否 | 根拠（コード） | 実コア負例 |
| --- | --- | --- | --- |
| A / X / Y（入力と復帰後） | 取得可 | trace の `harness_call` / `harness_after` | `neg_wrong_expectation`（a 不一致を FAIL） |
| P（N/Z/C/V、マスク比較） | 取得可 | `p_mask` で比較。B(0x10)/T(0x20) は比較対象外 | `selftest_add_carry_out`（C/N/Z を確認） |
| SP | 取得可（記録と釣り合い判定） | 呼出し前後の `sp`、期待値 `0xFF` | `neg_sp_unbalanced`（戻り時に SP が 1 ずれるルーチン。CI の実コアで FAIL / RETURNED を確認。判定ロジックは合成レコードのホスト試験でも確認） |
| MPR0-7 | 取得可 | 呼出し前後の `mpr[8]` を比較 | `neg_mpr_changed`（MPR2 を `tma`→`eor #$FF`→`tam` で必ず変えるルーチン。初期値に依存しない。CI の実コアで FAIL / RETURNED を確認） |
| PC 復帰 | 取得可 | `harness_done` チェックポイントへの到達を `RETURNED` と判定 | `neg_hang`（TIMEOUT）、`neg_brk`（TRAPPED） |
| RAM 差分（ZP 窓 `$E0-$FF`、コード上の番地 `$00E0-$00FF`、32 バイト） | 取得可 | 呼出し前後の 32 バイトを比較（番地の対応は下記「番地表記」） | `neg_undeclared_ram_write`（未宣言の書込みを FAIL） |
| RAM 差分（ZP 窓の外） | **取得不可（欠落）** | スナップショットは `$2200`/`$2220` の 32 バイトのみ | なし |
| スタックの内容（ページ 1、`$0100-$01FF`） | **記録のみ・判定に使わない（欠落）** | プローブは WRAM オフセット `$01F8-$01FF` を `stack_01f8_hex` として記録するが、判定（`judge`）では参照しない。CPU のスタックページとの対応も未検証（フィクスチャは MPR0=`$FF` を設定する。後述の番地表記を参照） | なし |
| I/O（MMIO）書込み | **取得不可（未監視）** | 核ハーネスは I/O を観測しない。IRQ/MMIO は別プローブ（`geargrafx_irq_register.py`、`geargrafx_mmio1804_*.py`、#173 記録）の領域 | なし |
| IRQ / BRK 受理 | **部分的（未監視）** | 呼出し時 P.I=1（`sei` 済み）。IRQ/BRK ベクタは `harness_trap` に集約され、受理されれば `TRAPPED`。プローブは `irr` / `idr`（IRQ 関連レジスタ）を記録するが判定に使わない。IRQ 源（タイマ・VDC 等）の発生は監視しない | `neg_brk`（BRK のみ。IRQ の実コア負例はなし） |
| サイクル数・タイミング | 取得不可 | ステップ予算（2048 命令）のみ | なし |

### 観測窓の照合（本点検で確認）
- RAM 観測窓は ZP `$E0-$FF`（コード上の番地 `$00E0-$00FF`、32 バイト）。番地の扱いは下記「番地表記」を参照。
- 番地表記（コード上の番地と物理位置を分けて記す）:
  - ZP 窓: フィクスチャのコード上の番地は `$00E0-$00FF`（`sta $E0,x` / `lda $E0,x`）。
    ZP の往復（呼出し前に書いた値が呼出し後の写しに戻ること）は CI の `neg_undeclared_ram_write` で確認。
    物理位置（MPR0=`$FF` の I/O 扱いを含む）は未確認。
  - スタック: コード上の番地は CPU `$0100-$01FF`（`pha` / `pla`）。プローブの `stack_01f8_hex` は WRAM オフセット
    `$01F8-$01FF` を記録するが、CPU スタックページとの対応は未確認。
  - スナップショット: `$2200`（pre）と `$2220`（post）はコード上の番地。プローブ出力の `work_ram_0200_hex` は
    WRAM オフセット `$0200-$023F`（64 バイト）。前半 32 バイトは入力シード、後半 32 バイトは呼出し後の ZP 写し。
    比較対象は ZP 窓の 32 バイトのみ。
  - 注意: フィクスチャは `tam #$01` で MPR0=`$FF` を設定する。標準的な HuC6280 のマップでは `$0000-$1FFF` が
    I/O 領域に当たると読めるため、コード上の `$00E0` や `$0100-$01FF` の物理位置は未確認である。

## 観測器の検証結果（自作ルーチン）
下表の結果列は **現行版（e536a6e）の CI 実コア記録**（run 39351203515、job 118395533079、core-harness ジョブ SUCCESS）。
ログ行は `PASS core harness: 10 cases behaved as declared (cold+warm)`。

| ケース | 期待 | 結果（現行版、CI） |
| --- | --- | --- |
| selftest_add（正例） | PASS / RETURNED | PASS / RETURNED |
| selftest_add_carry_out（正例、C フラグ） | PASS / RETURNED | PASS / RETURNED |
| neg_wrong_expectation | FAIL / RETURNED | FAIL / RETURNED |
| neg_undeclared_ram_write | FAIL / RETURNED | FAIL / RETURNED |
| neg_hang | FAIL / TIMEOUT | FAIL / TIMEOUT |
| neg_brk | FAIL / TRAPPED | FAIL / TRAPPED |
| neg_mpr_changed（本 PR で追加） | FAIL / RETURNED | FAIL / RETURNED（SP/MPR not balanced） |
| neg_sp_unbalanced（本 PR で追加） | FAIL / RETURNED | FAIL / RETURNED（SP/MPR not balanced） |
| plugin_demo_dev_rom_ex_getver（DEV ROM 回帰、観測値） | PASS / RETURNED | PASS / RETURNED |

プラグイン判定の負例として、期待値を誤らせた `plugin_neg_wrong_a`（a=17 に対し 18 を期待。**この JSON はリポジトリに追跡されておらず、再現は一時ファイルで行った記録のみ**）は
`FAIL` となり、ハーネスは非ゼロで終了した（`UNEXPECTED` として扱われる）。

## 残作業（欠けている観測項目）
1. ZP 窓の外の RAM 差分（`$2000-$21FF` 全体、またはワーク RAM 全域）を取得する。
2. スタック内容（コード上の番地 CPU `$0100-$01FF`）の差分を取得し、判定に使う。現行の `stack_01f8_hex`（WRAM `$01F8-$01FF`）は記録のみで、CPU スタックページとの対応は未確認。SP 不釣り合いの実コア負例（`neg_sp_unbalanced`）は CI で実行済み。
3. I/O 書込みの観測は、既存の MMIO 監視（#173 系）と結合するかを別 Issue で判断する。
4. 上記の欠落を埋めるまで、観測された RAM 差分は「ZP 窓内に限る」と明記する。

## 検証コマンドと結果
| コマンド | 結果 |
| --- | --- |
| `python3 -m unittest discover -s tests/host -p test_core_harness.py`（9ebb708 時点） | OK（15 テスト） |
| `python3 -m unittest discover -s tests/host -p test_core_harness.py`（HEAD 696c125 の実測） | OK（19 テスト） |
| 同（e536a6e 時点、RED: `neg_sp_unbalanced` 未定義・MPR 負例の XOR 未適用で 2 件 FAIL → GREEN: 18 テスト OK） | ホスト側の確認のみ。実コアは下行の CI で確認 |
| CI 実コア確認（GitHub Actions core-harness、Linux、run 39351203515、job 118395533079、head e536a6e） | **実行済み・SUCCESS**: `PASS core harness: 10 cases behaved as declared (cold+warm)`。`neg_sp_unbalanced` と `neg_mpr_changed` は cold / warm とも FAIL / RETURNED（`SP/MPR not balanced across the call`） |
| CI 実コア確認（head 696c125、run 39355386608、job 118408847936、core-harness SUCCESS） | **実行済み・SUCCESS**: `PASS core harness: 10 cases behaved as declared (cold+warm)`。e536a6e からの差分は `docs/311-harness-safety.md`、`docs/testing.md`、`tests/host/test_core_harness.py` のみ（ハーネス本体・フィクスチャ・DEV ROM は不変） |
| CI 実コア確認（head e53ca7e、run 39357943090、core-harness job 118417019443 / guard-core-harness job 118417575628、ともに success） | **実行済み・SUCCESS**: `PASS core harness: 10 cases behaved as declared (cold+warm)`。e536a6e からの差分は docs と tests のみ。この run の判定文言は `SP/MPR not balanced`（SP / MPR への文言分割は本コミット以降の作業ツリーで未 CI） |
| `make dev-rom MODE=debug`（TOOLS_DIR=既存キャッシュ） | 終了コード 0 |
| `python3 tests/integration/geargrafx_core_harness.py --mode debug` | 終了コード 0、8 ケース宣言どおり |
| 同、`--cases` 誤期待値の負例（検証は 9ebb708 より前の origin/main） | 終了コード 非ゼロ |
| `make test` / `make check` / `make dev-test` | 未実行（本 PR はハーネス自己検証の追加のみ。ホスト試験は上記で実行） |

環境: WSL Ubuntu（user loach）、クリーンクローン `/home/loach/agent311`、TOOLS_DIR=`/home/loach/oss-pce-cd-bios-tools`（cc65 `71746c8`、Geargrafx `b49ae82`）。

## 範囲と限界
- 観測器の検証は自作ルーチンに限る。System Card ABI 互換、実機挙動、純正 BIOS の結果を示さない。
- 合成レコードのホスト試験は判定器の検証であり、実コア証拠ではない。
- IRQ と MMIO は未監視。IRQ は受理された場合のみ `TRAPPED` で検出され、IRQ 源や I/O 書込みは見ない。
- PASS は「観測器が宣言どおり判定した」ことのみを示す。ZP 窓外の書込みは検出できない（上記の欠落）。
