# ex_bcdbin（BCD→二進数）独自仕様

Issue #71。`src/ex_bcdbin.s` は本プロジェクトが独自に定めた仕様で書いた内部ルーチンである。
純正 BIOS の EX_BCDBIN の ABI とは **照合していない**。
呼出契約は HYPOTHESIS のままで、確定したとは主張しない。ジャンプテーブル未接続で、
API の既存スタブは従来どおりである。

## 仕様（本プロジェクト独自）

| 項目 | 内容 |
| --- | --- |
| 入力 | A = packed BCD 1バイト、2桁。上位ニブル = 十の位、下位ニブル = 一の位（`$00`〜`$99`） |
| 前提 | D フラグ = 0（ADC を使うため。D=1 での動作は未定義） |
| 正常 | C = 0、A = 二進値 0〜99 |
| 不正 BCD | どちらかのニブルが 9 を超える場合は C = 1、A = 入力値のまま |
| 保存 | X、Y を保存。スタックは均衡し、スタック以外のメモリは書かない。スタック使用は PHX / PHY の 2 バイト（JSR の戻り先 2 バイトは別） |
| 未定義 | N / Z / V フラグ |

## 検証

ホスト：`python3 -m unittest tests.host.test_ex_bcdbin -v`（モデル照合 3 件。`make test` の host discover で実行される）。
実コアの負例 5 件（core 不在 / ca65 不在 / ld65 不在 / 起動失敗 / タイムアウト）は host ではなく `tests/integration/test_ex_bcdbin_real_core.py`（`RealCoreFailurePathTests`）にあり、`make integration-test` で実行される（Issue #333 で host から分離済み）。
実コア：`python3 tests/integration/geargrafx_bcdbin.py`。次の 2 経路で実行される。
- 専用ワークフロー `.github/workflows/core-bcdbin.yml`（ubuntu-24.04、`make setup` 後に実行）。終了コードがそのままジョブ結果になり、ログは artifact `bcdbin-core-log-<sha>` に保存する。実コアの 2 tests（全256入力、変異3種）の主な証拠はこの run である。
- `make test-real-core` 経由（`make test` には含まれない。`ci.yml` と `nightly.yml` が必須ステップとして実行する）。
- ホスト参照モデル：全 256 入力を独立な判定式と照合し、0 / 9 / 10 / 99 や `$0A` / `$9A` / `$A0` / `$FF`
  などの境界値も確認する。BIN→BCD→BIN の往復はモデル内の符号化で確認しており、EX_BINBCD（#70）が
  できたら差し替える予定である。
- 実コア：`tests/fixtures/bcdbin_harness.s` が全 256 入力で `ex_bcdbin` を呼び、A / P / X / Y と最終 SP を
  作業 RAM に書く。`geargrafx_smoke.execute` で cold / warm の両方を読み出してモデルと照合する。
  一の位チェック（`CMP #10`）・十の位チェック（`CMP #$A0`）・十の位の重み（`ADC #10`）を書き換えた ROM が
  不一致として検出されることも実コアで確かめる。Linux 以外、または pinned cc65 / コアが無いときは BLOCKED として終了コード 3 で終了する（`geargrafx_harness.py` の BLOCKED=3 に合わせた。SKIP による成功終了も PASS もしない）。実行時の失敗（起動失敗・タイムアウト・検証不一致）は終了コード 1。
- 陽性対照：変異試験の前に、未変異 ROM が実コアで全256入力を通ることを assert する。陽性対照が通らなければ変異の結果は PASS にならない。
- 負例（`tests/host/test_ex_bcdbin.py` の `RealCoreFailurePathTests`）：core 不在、ca65 不在、ld65 不在、起動失敗、タイムアウトを個別に非ゼロ終了させる。終了コードと原因の文字列は Linux でだけ確認する。
- Windows では core 不在・ca65/ld65 不在・起動失敗・タイムアウトの経路は検証されない。プラットフォーム判定（BLOCKED）で止まるため、非ゼロ終了のみ確認する。
- 条件マトリクス（Issue #416、独自仕様の検証であり純正 BIOS の検証ではない）：`python3 tests/integration/geargrafx_bcdbin_conditions.py [--output evidence.json] [--inject expect-value|expect-sp]`。`tests/fixtures/bcdbin_cond_harness.s` が入力 C を 0 / 1、X/Y を 4 組、呼出時 SP を `$FF` / `$80` / `$C0` に変えて `ex_bcdbin` を 16 入力ずつ呼ぶ（8 条件 × 16 入力 × cold/warm）。期待値は `tests/host/test_ex_bcdbin.py` の独立モデルから導き、手計算の 6 例で先に確認する。SP 均衡は各呼出の直後の SP で見る。負例 6 件を毎回実行し、すべて拒否されることを確認する（誤った期待値 2、ROM 変異 4：スタック不均衡、無限ループ、SP 表、観測 SP）。`--inject` は誤った期待値を入れて終了コード 1 を得る。終了コードは 0 / 1 / 3（BLOCKED）。
- 既存ハーネス・ドライバを再利用しない理由：`tests/fixtures/bcdbin_harness.s` は X=`$5A`、Y=`$A5`、SP=`$FF`（`ldx #$ff` / `txs`）を固定し、C を前の呼出しから引き継いだまま 256 入力を回す。入力 C・X/Y・SP を変えられず、結果配置も既存の検証（`check_ram`）と結合している。`tests/integration/geargrafx_bcdbin.py` の検証器も X=`$5A`、Y=`$A5`、SP=`$FF` を固定しており、流用すると既存ゲートの判定を変えることになる。そのため条件マトリクスは別ハーネスとし、モデルだけを `test_ex_bcdbin.py` から再利用する。
- 停止と無限ループ：無限ループは実コアではフレーム内で打ち切られ、戻らないことはなかった（マーカー未書込として拒否）。実行のタイムアウト（60 秒）による拒否の経路はコードにあるが、実行では発火していない。

## 契約項目の確定状況（監査）

| 項目 | 状態 | 根拠 |
| --- | --- | --- |
| 独自仕様：入力、正常・不正の戻り値、C、X/Y 保存、スタック均衡 | 確定（独自仕様として実コア検証済み） | 実コア全256入力・cold/warm、変異3種 |
| 独自仕様：メモリ非書込 | コード読みによる確定（実コアでの直接検証ではない） | `src/ex_bcdbin.s` の読解。実コアで観測したのは結果領域・SP・MPR のみで、スタック以外のメモリを全域比較してはいない |
| 独自仕様：D = 0 前提 | 前提として記述のみ。D = 1 は未検証（仕様上未定義） | ハーネスは `cld` で起動 |
| 独自仕様：入力時の C | 入力 C = 0 / 1 の実コア検証あり（条件マトリクス、#416）。出力 C は CMP の結果で決まり、入力 C に依存しない | 実コア 8 条件 × 16 入力 × cold/warm。純正 BIOS の検証ではない |
| 独自仕様：X/Y の任意初期値、呼出時 SP（`$FF` 以外） | 実コア検証あり（#416）。SP は `$80` / `$C0` を含む | 条件マトリクス |
| 純正 BIOS EX_BCDBIN：呼出方法（引数レジスタ） | 未確定（UNKNOWN） | api_contracts.json slot 61 |
| 純正 BIOS EX_BCDBIN：戻り値、破壊レジスタ、保存レジスタ | 未確定（UNKNOWN） | 同上。HuC は selector のみ（HYPOTHESIS） |
| 純正 BIOS EX_BCDBIN：エントリ `0xE0B7` | HYPOTHESIS（ソース一致のみ、実行証明なし） | pcengine.inc |

純正 BIOS の契約が未確定のため、ジャンプテーブル接続（DEV_CALLABLE）は行わない。
接続の前提は、純正 BIOS 側の呼出・戻り値・破壊レジスタを実機またはエミュレータ実行で確定すること。

Geargrafx での観測は実機での動作を証明しない。BIOS 互換も主張しない。
