# テスト段階化による開発効率改善：調査・設計（Issue #850）

> **Scope:** 技術調査・設計・非侵襲的性能計測のみ。本番CI・Required Checks・テスト本体・CLAUDE.md・AGENTS.md の変更は含まない。
> **Base:** `origin/main`（調査時 `d27a65ba`, Merge PR #842）。専用ブランチ `agent/test-staging-survey-20261127`。修正時に最新 `origin/main`（`7041972e`、#848 統合）を照合し、CI 構成・分類記述を整合させた。計測値は調査時 base の実測であり改変していない。
> **Evidence boundary:** 本書の PASS/OK は記載の証拠範囲にのみ適用される。部分計測の成功を全回帰成功として扱わない。
> **Local media:** `./rom/` は存在せず（`ls rom/` で確認）、商用媒体・参照BIOS には一切アクセスしていない。

関連: #845（verify-full timeout 30→60、P0 安定化・適用済み）、#846（CI 並列化の設計・計測 Issue）、#848（#846 の実装。`origin/main` `7041972e` に統合済み）、#849（ツールチェーン事前ビルド調査、OPEN）。本調査はこれらと独立した改善として扱う。

---

## 1. 現行テスト体系

### 1.1 実行経路の全体像

開発者が日常的に実行する経路は `Makefile` ターゲットであり、CI はそのうちの固定サブセットを固定順序で実行する。

| 経路 | コマンド | 内容 | 実行者 |
| --- | --- | --- | --- |
| host ゲート（必須） | `make test` | `build` 依存。`OSS_PCE_REQUIRE_CC65=1` で `tests/host` 全量発見実行。Geargrafx 不要。cc65 欠落は SKIP ではなく FAILURE | CI (`verify-full`)、開発者、nightly |
| real-core ゲート（必須） | `make test-real-core` | `integration`（直接スクリプト 22 単位）＋ `integration-test`（`tests/integration/test_*.py`、715 テスト） | CI、開発者、nightly |
| ローカル専用 host | `make host-test` | build なし・integration なし。cc65 欠落時は該当テスト SKIP（必須ゲートではない） | 開発者のみ |
| 静的解析 | `make check` | `tools/contracts.py`、`tools/check.py`、`tools/c3_static_check.py`、`tools/verify_build_diff.py` | CI、開発者、nightly、`bios-dev-full` |
| 再現性 | `rom-provenance` 相当（CI ではインライン手順） | 同一入力リビルドのバイト同一性、`manifest.json` の `rom_sha256` 照合 | CI（verify-full 内）、nightly |
| release 検証 | `make build MODE=release` ＋ `cmp` ＋ `tools/build.py --verify` | debug/release バイト同一、`make release` は未完成 BIOS のため常に `BLOCKED` 終了（exit 1） | CI、開発者（CONTRIBUTING 推奨手順） |
| DEV ROM 系（opt-in） | `make dev-rom`、`dev-test`、`dev-integration`、`dev-*-integration` 等 | 256 KiB DEV ROM（81 スロット）と診断群。`bios-dev-full` が実行する範囲のみ CI 必須 | `bios-dev-full`、開発者（opt-in） |
| 定期検証 | nightly ワークフロー | 変更検知時のみ full セット再実行（下記 1.3） | schedule / workflow_dispatch |
| CI 外 opt-in | `routine-real-core`、`api-unimplemented-integration`、`api-entry-integration`、`slot02-return-integration`、`public-dev-gap-integration`、`dev-mul16s-sweep` 等 | ワークフロー run ステップ allowlist 固定のため CI 未接続、または手動 large-sweep。開発者の明示的実行のみ。`dev-bcdbin-sweep` / `dev-sweep` は `make dev-integration` 経由で必須 CI に含まれるためここには含めない（§1.3(f)） | 開発者（opt-in） |

CONTRIBUTING.md の推奨開発者手順は `make setup` → `make build` → `make test` → `make test-real-core` → `make check` → `make clean` → `make build`（＋ release 比較）であり、**1 タスクあたり全回帰が複数回実行される構造**になっている。これが本調査の問題意識である。

### 1.2 GitHub Actions ワークフローと Required Checks

| ワークフロー | ジョブ | timeout | 役割 |
| --- | --- | --- | --- |
| `ci.yml` | `scope` | 5 分 | PR コード・ツールチェーンを実行せず docs_only 判定。**現在 fast path は無効化**（`docs_only=false` 固定）。host suite が `docs/*.md` を読むため軽量パスの被覆を証明できないことが理由 |
| `ci.yml` | `scope` → `provenance` → 並列群（`host-tests` / `integration-direct` / `real-core-shard-0..3` / `static-release`）→ `verify-full`（集約） | 5 / 5 / 15〜25 / 60 分（#845 で 30→60 に緩和、Owner 承認済みインフラ予算） | #848（`origin/main` `7041972e` に統合済み）により同一テスト集合を並列実行。`host-tests`（`make test` 相当）・`integration-direct`（`integration` 22 単位）・`real-core-shard-0..3`（unittest 715 を計測重みで分割）・`static-release`（`make check`＋release＋再現）を `verify-full` が fail-closed 集約（manifest 検証・SHA/run/attempt binding・欠落/重複/未分類検出）＋中間 artifact handoff。テスト集合・Required 名に変更なし |
| `ci.yml` | `verify`（**Required**） | 5 分 | `verify-full` の結果を強制する ruleset 用名前維持ジョブ |
| `ci.yml` | `guard-verify`（**Required**） | 10 分 | trusted main の `artifact_guard.py` で配置・stage・verify を検証し、検証済み fixture を公開 |
| `api-source.yml` | `scope` | 5 分 | 同上（docs_only=false 固定） |
| `api-source.yml` | `bios-dev-full` | 20 分 | `contracts.py`、`build_rom.py --check-source`、`dev-rom`、`test_api*.py`、`dev-integration`、`dev-startup/reset-entry/cd-media-state/cdread/cdload-exec/cdreset/cdda/trace/frame-tracer`、debug/release バイト比較、`dev-release-gate-blocked`（exit 3 + `BLOCKED:` のみ PASS）、`make check`、証拠検証、中間 artifact handoff |
| `api-source.yml` | `bios-dev`（**Required**） | 5 分 | `bios-dev-full` の結果を強制する ruleset 用名前維持ジョブ |
| `api-source.yml` | `guard-bios-dev`（**Required**） | 20 分 | bios-dev 成果物の guard 検証・公開 |
| `api-source.yml` | `attest-cdread`、`verify-cdread-attestation`、`guard-cdread-verdict`、`attest-cdread-verdict`、`verify-cdread-verdict` | — | CDREAD 証拠の attestation 系（本調査の計測対象外、体系の一部として記録） |
| `core-bcdbin.yml` | `bcdbin-core`（30 分）、`guard-bcdbin-core`（**Required**、10 分） | — | EX_BCDBIN 実コア検証と guard |
| `core-harness.yml` | `core-harness`（30 分）、`guard-core-harness`（**Required**、10 分）、`dev-boot-internal-integration`、`experimental-bios-boot-integration`、`dev-ipl-boot-integration`（各 30 分） | — | HuC6280 コア検証ハーネス等と guard |
| `nightly.yml` | `plan`（5 分）→ `build`（20 分）→ `guard-nightly`（20 分） | — | main の exact SHA と前回成功 artifact を比較し、変更がある場合のみ full セット実行（change-aware、成功時記録） |
| `pages.yml` | `validate`、`build`、`deploy` | 5/10/10 分 | サイト公開（テスト体系外） |

Ruleset `protect-main` の Required Checks（全 6 件、API 照会で確認）:

- `bios-dev`、`verify`、`guard-bios-dev`、`guard-verify`、`guard-core-harness`、`guard-bcdbin-core`

**本調査・将来の段階化は上記 6 件のいずれも変更・削減・改名しない。** `verify` / `bios-dev` は名前維持ジョブであり、実体ジョブのリネームは ruleset 変更になるため対象外とする。

### 1.3 テスト分類台帳

#### (a) host テスト（`tests/host`）

- ファイル数: 255（`test_*.py`）。`def test` メソッド数: **5944**（grep 集計）。
- 必須ゲート `make test`（`OSS_PCE_REQUIRE_CC65=1`）では全量実行。cc65 依存テストは `@requires_cc65`（`tools/environment.py`）で FAIL-closed。
- 主な分類（ファイル名基準、代表例）:
  - API 契約・実装: `test_api_contracts.py`、`test_api_table.py`、`test_api_build.py`、`test_api_*.py`（EX_GETVER/JOYSNS/JOYREP/SCRSIZ/2123/744 境界等）、`test_api_release_gate.py`、`test_api_halt_classify.py`、`test_api_commercial_media.py`（商用媒体ポリシー）
  - ABI 検証層: `test_abi1_reference_review.py`、`test_abi3_static_triage.py`、`test_abi_verification_tiers.py`
  - 安全性ゲート（host 側）: `test_717_peek_failclosed.py`、`test_718_huc6280_adt_fixtures.py`、`test_719_wram_deny_allow.py`、`test_720_mutation_gaps.py`、`test_721_transferend_noresume.py`、`test_722_five_xfer.py`、`test_723_coldwarm_observer.py`、`test_724_build_diff_audit.py`、`test_725_failclosed.py`、`test_725_p10_failclosed.py`、`test_726_367_evidence.py`、`test_737_h45_preblock.py`、`test_365_st_boundary_record.py`、`test_367_*.py`（device-write-audit / matrix-runner / midrefusal）
  - 変異テスト（host 側）: `test_720_mutation_gaps.py`、`test_cd_boot_loader_mutations.py`、`test_744_norange_mutant.py`、matrix 系（TII/NOP 置換、no-CLD 等の negative 実行）
  - provenance・artifact: `test_artifact_guard*.py`、`test_rom_provenance*.py`（`test_ci_sharding.py` 等は #848 ブランチのみ、本 main には存在しない）
  - 契約・fixture: `test_adreset_fixture.py`、`test_adtrans_*`、`test_cdimage*`、`test_cdread*`、`test_cdreset*`、`test_cdda_*`、`test_bm_format*`、`test_bm_free*`、`test_c3_static*`、`test_cc65_gate.py`（SKIP/FAIL ポリシー自体のテスト）、`test_696_default_image_hash.py`、`test_744_*`（IPL 21bit LBA、builder 選択域等）
  - DEV ROM host: `test_api*.py` の一部、`test_startup.py`、`test_reset_entry.py`、`test_cd_media_state.py`、`test_cdload_exec.py`、`test_cdreset_bus.py`（`bios-dev-full` がパターン指定で実行）
- `make host-test`（build なし）は同一ディレクトリを発見実行するが、cc65 欠落時は SKIP 許容の**非必須**経路。必須ゲートは `make test` のみ。

#### (b) real-core テスト（`make test-real-core` = `integration` ＋ `integration-test`）

- `integration` ターゲット（直接スクリプト 22 単位、`make` 単位での列挙）:
  `geargrafx_smoke.py`（証拠出力つき）、`geargrafx_reference_guard.py`、`geargrafx_reference_guard_issue173.py`、`geargrafx_mmio1804_trap.py`、`geargrafx_mmio1804_preblock.py`、`geargrafx_irq_register.py`、`geargrafx_st_bypass.py`、`geargrafx_bcdbin.py`、および `block-write-guard`、`block-transfer-matrix`、`block-mid-refusal`、`stack-access-guard`、`stack-methods`、`stack-methods-compare`、`stack-s5`、`stack-s6`、`stack-irq-timing`、`stack-mpr-buffer`、`caller-stack-contract`、`st-boundary`、`block-preblock`（各 `make` サブターゲット経由）。
- `integration-test`（`tests/integration/test_*.py` 32 ファイル、`def test` 715 件＝ CI 計測の 715 unittest と一致）:
  `test_apicall_capacity.py`、`test_apicall_vdc_snapshot.py`、`test_build_core.py`、`test_cd_base_proto_v1.py`、`test_cd_read_link_v1.py`、`test_debugger_setup.py`、`test_dev_g5_profile.py`、`test_ex_bcdbin_real_core.py`、`test_ex_dspoff_real_core.py`、`test_g5_*.py`（2base、chain_7api_24_36、dotmod_24、expD_next_probe、integrated_chain、irqon_2c、memopen_4a、progress、rcroff_2b、satclr_36、scrmod_25、sprdma_35、cdread_commercial 等の G5 群）、`test_geargrafx_frame_tracer_synthetic_cd.py`、`test_getver_observation_real_core.py`、`test_ipl_loader_g5_*.py`、`test_irq_366_entry_execbp.py`、`test_matched_selection_realcore.py`、`test_mpr1_tam_irq.py`、`test_run_input_guest_real_core.py` 他。
- すべて pinned Geargrafx（現行 `b49ae82a`、debugger ビルド併用）上の合成メディア診断。商用ゲーム互換の証拠ではない（`docs/testing.md` の証拠境界）。

#### (c) 静的解析（`make check`、4 単位）

| 単位 | 役割 | 性質 |
| --- | --- | --- |
| `tools/contracts.py` | 81 スロット API 契約の整合性（BIOS 検証 NOT_RUN を明示） | ms オーダー、fail-closed |
| `tools/check.py` | Python/shell 構文、Actions schema/expression、HuC6280 構文、ツール検出、whitespace（staged/unstaged 含む） | actionlint 1.7.7（pin 済み）使用 |
| `tools/c3_static_check.py` | C3 静的検査（BLOCKED / NOT_STATIC / STATIC_OK の三値。STATIC_OK はテキスト整合のみで PASS ではない） | 生産 allowlist UNDECIDED を維持 |
| `tools/verify_build_diff.py` | Issue #724: debug/release 差分監査、検証専用パッチ・GPL 非混入ゲート | バイト同一性は再ビルド比較で確認 |

#### (d) ABI 検証・安全性ゲート

- `tools/reference_jsr_gate.py`: 参照 BIOS JSR 実行ゲート。現行 **許可 0/81**（runner NONE のため実行不可）。本調査では判定基準・許可数を変更しない。
- ABI VERIFIED 0/81、`release_eligible=false`、`make release` の BLOCKED（exit 1）、`dev-release-gate-blocked`（exit 3 + `BLOCKED:` のみ PASS）はいずれも現行維持。
- `tools/provenance_guard.py`（変更内容に対するライセンス由来監査、CI 先頭で実行）＋ `tests/host/test_provenance_guard.py`。
- `tools/artifact_guard.py`（trusted main 境界、配置・stage・verify、run 書き込み allowlist `artifact_guard_run_allowlist.json`）。
- `tools/rom_provenance.py --reproduce`（Issue #335: 同一入力リビルドのバイト同一性＋ manifest 記述性、fail-closed）。
- 商用媒体ポリシー: `tools/commercial_media.py`、`test_api_commercial_media.py`、`./rom` 非アクセス。本調査の計測はすべて合成 fixture のみ。

#### (e) 再現性検証

- `make build`（debug）→ `make build MODE=release` → `cmp` → `tools/build.py --verify`。
- `rom_provenance.py --reproduce smoke --mode debug` ＋ `cmp` ＋ manifest 照合。
- `bios-dev-full` では DEV ROM 系 7 種の debug/release バイト比較。
- いずれも「再ビルドして比較する」独立性を要件とし、キャッシュ命中・既存バイナリ流用を検証成功として扱わない（#846 の設計条件と同一）。

#### (f) 変異テスト

- real-core 変異: `block-transfer-matrix`（計測時 1017 applied / 1017 detected、3 excluded）、smoke の TII→NOP 置換 negative、各 DEV API の mutant 拒否（例: MA_MUL8U の no-CLD mutant 3/3 拒否）、`st-boundary` の 22 runs × 9 mutation kinds＋positive control。
- host 変異: 上記 (a) の変異系テスト群。`tools/verify_build_diff.py` の same-length 置換への盲目性は文書化済み（`verify_dev_rom` のバイト同一性で補完）。
- sweep の分類（推移的呼び出しで判定。直接のワークフロー呼び出しの有無だけでは判定しない）: `dev-bcdbin-sweep`（全 0..255×18 条件）と `dev-sweep`（全 0..255）は `Makefile` の `dev-integration` レシピ（`$(MAKE) dev-bcdbin-sweep` / `$(MAKE) dev-sweep`）に組み込まれ、`bios-dev-full` の `make dev-integration` 経由で**必須 CI に含まれるため L3**。`dev-mul16s-sweep` のみ Makefile コメントで "Not run by CI" の CI 外 opt-in であり **L4 候補**。

### 1.4 テスト間の依存関係

```text
setup（pinned toolchain 取得・ビルド: cc65 71746c8 / Geargrafx b49ae82a / actionlint 1.7.7）
 └─ build（HuCARD fixture 8 KiB、src/＋リンカスクリプト→ build/debug/）
     ├─ test（host 全量。build 成果物を読むテストを含む。OSS_PCE_REQUIRE_CC65=1）
     ├─ integration（直接 22 単位。smoke ROM＋ pinned core が前提）
     ├─ integration-test（unittest 715。core＋fixture が前提）
     ├─ check（contracts は build 不要だが、check.py の一部と verify_build_diff は build 成果物に依存）
     ├─ release 検証（release ビルド＋ cmp＋ --verify）
     └─ rom_provenance 再現（独立リビルド＋ cmp＋ manifest 照合）
dev-rom（tools/build_rom.py。HuCARD fixture とは別成果物）
 └─ dev-test / dev-integration / dev-*-integration（各診断 ROM＋ core）
guard 系（中間 artifact handoff＋ trusted main の guard。実体ジョブ成功が前提）
nightly plan（main SHA 比較）→ 変更時のみ build 節点以降を再実行
```

要点:

- `test` と `test-real-core` はともに `build` 成果物を共有するが、CI では逐次実行のため build は 1 回。ローカルで個別に叩いても `make` の増分判定で再ビルドは最小化される（後述の重複は「テスト実行」の重複であり、ビルド自体の重複は小さい）。
- `integration` ターゲットの 22 単位はすべて同一 pinned core・同一 fixture 系に依存し、相互の順序依存はない（各プロセス独立・fresh tmp system dir）。ただし probe コンパイル等の feasiblity コストが実行ごとに再発火する（#846 で refire cost として指摘）。
- host 255 ファイル間にも原則順序依存はない（`unittest discover` 順）。cc65 ゲート等の環境依存のみ。
- `verify` / `bios-dev` / guard 系は実行結果の集約・公開であり、テスト選択の単位ではない。

### 1.5 実行時間の計測結果

**凡例:** [CI] は CI 実測（旧 serial の #846 baseline run `45941187061`、および #848 統合後の現行 CI run `45990514643` の報告値、ubuntu-24.04）。[LOCAL] は本調査の実測（同一 worktree、Python 3.12.3、8 vCPU、`TOOLS_DIR=/tmp/oss-pce-cd-bios-tools-1000` に pin 済みツールがキャッシュ済み、`JOBS` 既定）。両者は Runner・キャッシュ状態・並列度が異なるため直接比較しない。

| 対象 | [CI] 実測 | [LOCAL] 実測（本調査） |
| --- | --- | --- |
| `make setup`（ツール取得・ビルド） | 約 1m20s（baseline） | **未計測**（キャッシュ済みのため。新規取得時間は不明とし、#849 の調査範囲とする） |
| `make build`（増分・キャッシュ済み） | baseline 内訳に含まれる（単独値なし） | **0.07s**（`PASS fixture structure: 8192 bytes`） |
| host 全量（`make test` 相当） | 約 3m51s（5935 tests、run 45941187061） | **309.7s（5m11s）、Ran 5939 tests、OK (skipped=3, expected failures=3)** |
| real-core 全量（`make test-real-core` 相当） | 約 23m50s（715 unittest で 982.107s＋直接スクリプト群） | **未計測**（全量未実行。以下の単体のみ実測） |
| serial 合計（旧 verify-full body） | 約 29m09s（baseline）。分散あり（23m30s の回も報告） | — |
| 並列化後（#848 統合後の現行 CI） | 約 12m33s wall（run `45990514643`: host 9m59s / integration-direct 8m18s / shards 4〜7m / static-release 1m44s / verify-full 集約 1m47s）。約 57% 削減。同一テスト集合・同一 Required 名 | —（CI 構成の変更でありローカル再現対象外） |
| `make check` 全量 | static-release 1m44s の一部（#848 並列時。単独値は baseline 内訳なし） | **2.5s**（内訳: contracts.py 0.06s、check.py 2.2s、c3＋verify_build_diff が残差） |
| 直接スクリプト例: smoke | — | **0.27s** |
| 直接スクリプト例: st-bypass | — | **4.0s** |
| 直接スクリプト例: st-boundary | — | **4.7s** |
| 直接スクリプト例: block-transfer-matrix | — | **40.0s**（MUTATION 1017 applied / 1017 detected） |
| `make dev-rom` | bios-dev-full（20 分枠）の一部 | **0.9s** |
| dev-integration 例: MA_MUL8U（18 cases） | 同上の一部 | **4.9s** |
| host 単体ファイル例: `test_api_contracts`（13 tests） | — | **0.08s**（発見・起動込みで 1s 未満） |

読み取り:

- ボトルネックは real-core（旧 serial CI の約 8 割。並列化後も shards＋direct が wall の主体）。host は CI 3m51s / LOCAL 5m11s で、開発ループへの挿入には重い。
- 一方で `make check`（2.5s）、`contracts.py`（0.06s）、host 単体ファイル（1s 未満）、`dev-rom`（0.9s）、単一 DEV API 実コア（約 5s）はいずれも軽量。**変更点に紐づくサブセットだけを回せば L0（30 秒）・L1（2 分）は十分に射程内**である。
- 重い直接スクリプト（block-transfer-matrix 40s 級）が L2（5 分）の予算を圧迫するため、L2 には「必要な real-core」の選別が必須（後述 3.2）。

### 1.6 重複実行されるテスト

1. **開発ループ内の全回帰反復**: CONTRIBUTING 推奨手順（test → test-real-core → check → clean → build → release 比較）を 1 修正ごとに繰り返す運用。同一 HEAD に対する有効な結果があっても再実行される。削減余地が最大。
2. **clean 後の再 build＋再 test**: `make clean` は `build/` を消去するため、直後の `make build`＋後続テストがフル再実行になる。証拠取得のための意図的再実行と、習慣的再実行の区別がない。
3. **debug/release 二重ビルド＋ `cmp`**: 再現性のための意図的重複（削減対象外。L3 に保持）。
4. **rom_provenance の独立リビルド**: 独立性のための意図的重複（削減対象外。L3 に保持）。
5. **probe コンパイルの refire**: 各 real-core 実行時の probe ビルド再発火。同一性が証明できれば再利用可能だが、現行は毎回再発火（#846 の E4 候補。削減には同一性証明が必要で、本調査の実装対象外）。
6. **nightly の full 再実行**: change-aware のため無変更時は skip。重複は小さい。
7. **`make test` と `make host-test` の混同**: 後者は build なし・SKIP 許容の非必須経路だが、必須ゲートと誤認して両方回すと重複になる。運用ルールで明確化する。

### 1.7 AI エージェントが実装途中で実行しているテスト

CONTRIBUTING・過去 PR の証拠報告から、エージェントは実装途中に以下を実行している（または実行を求められる）:

- `make build`（構文・リンカエラーの即時確認）
- `python3 -m unittest tests.host.test_<対象>`（単体ファイルの即時確認）
- `make test`（host 全量。1 回 5 分級）
- `make test-real-core`（real-core 全量。CI では 20 分級）
- `make check`（静的解析。2.5s と軽量だが full host と抱き合わせで回りがち）
- `make dev-rom`＋対象 `dev-*-integration`（DEV API 実装時）

問題は「どこまで回せば次に進めるか」の判断がエージェントごとにまちまちで、(a) 不安から全回帰を反復する、(b) 逆に軽い確認だけで進み選択漏れを起こす、の両極があることである。段階化はこの判断を**コマンド選択という機械的操作**に置き換える。

---

## 2. 段階化の設計案

性能目標は目標であり、達成のために検証を削減しない。各段階の成功は上位段階の成功を意味しない（§5 の運用ルールで強制）。

### L0: QUICK（目標 30 秒以内）

目的: 変更ファイルに対する即時検証。タイプミス・構文・配置違反の即時摘出。

| # | 内容 | 対応する現行コマンド | 実測根拠 |
| --- | --- | --- | --- |
| Q1 | Python/shell 構文チェック（変更ファイル） | `check.py` の構文節点相当 | check 全量 2.2s の部分集合 |
| Q2 | HuC6280 構文プローブ（`.s` 変更時） | `check.py` のアセンブラ節点相当 | 同上 |
| Q3 | 契約整合（API 表変更時） | `tools/contracts.py` | **0.06s** |
| Q4 | whitespace・配置検証（変更ファイル） | `check.py` の Git 節点相当 | 同上 |
| Q5 | 変更ファイルに対応する host 単体ファイル | `python3 -m unittest tests.host.test_<対象>` | **0.08s**（例） |

L0 は現行 CI の一部分の再実行ではなく、**現行コマンドの部分適用**である。新規検証の導入ではないため安全性ゲートに触れない。

### L1: FOCUSED（目標 2 分以内）

目的: 変更対象 API・モジュールの単体・境界・異常系・回帰。

| # | 内容 | 対応する現行コマンド | 実測根拠 |
| --- | --- | --- | --- |
| F1 | 対象 API の host テスト（`test_api_*`、`test_ma_*` 等のパターン指定。現行 `dev-test` と同一パターン） | `python3 -m unittest discover -s tests/host -p 'test_api*.py'` 等 | ファイル単体 0.1s 級、パターン群でも 10s 級以内と見込む（要計測） |
| F2 | DEV ROM ビルド（API 実装時） | `make dev-rom` | **0.9s** |
| F3 | 対象 API の dev-integration（1〜2 本） | `tests/integration/geargrafx_dev_<api>.py` | **4.9s**（例: MA_MUL8U 18 cases） |
| F4 | 対象の回帰テスト（修正対象の既存失敗・境界） | 既存の対象テスト | 同上 |

F1 のパターンは現行 `dev-test`（`test_api*.py`＋`test_ma*.py`＋`dev-integration`）と同一であり、新規テスト選択ロジックを要しない。

### L2: IMPACT（目標 5 分以内）

目的: 間接依存・共有処理・ABI・メモリ領域への影響確認。

| # | 内容 | 対応する現行コマンド | 実測根拠 |
| --- | --- | --- | --- |
| I1 | 関連 host テスト（依存モジュール。§3 の対応表で決定） | `tests.host.test_<関連>` 複数 | 各 0.1〜数 s |
| I2 | 関連する直接スクリプト（`integration` ターゲットの部分集合） | 例: `st-boundary` 4.7s、`st-bypass` 4.0s | 単体 4〜5s 級 |
| I3 | 必要な real-core unittest（関連モジュール。例: `test_g5_*` 対象分） | `-p` / モジュール指定実行 | 要計測（§7） |
| I4 | 共有処理変更時は `make check` 全量 | `make check` | **2.5s** |
| I5 | ABI・メモリ配置変更時は契約＋構造検証 | `contracts.py`＋`verify_dev_rom_layout/structure` 相当 | 要計測 |

L2 の予算圧迫要因は 40s 級の重い直接スクリプト（block-transfer-matrix 等）である。無関係な重スクリプトを L2 に含めない選別が必須であり、選別根拠は §3 の対応表に一元化する。

### L3: FULL（現行全回帰と同等以上）

現行の必須セットを**無条件に**実行する。削減・置換・並列化による意味変更なし。**DEV 変更の有無を理由に必須検証を省略しない**（`api-source.yml` の `bios-dev-full` は `scope` の `docs_only=false` 固定により、非 DEV 変更でも必須実行される。現行 CI に「DEV 変更時のみ実行」の条件分岐は存在しない）。

```text
make setup → make build → make test → make test-real-core → make check
→ release 検証（release build＋cmp＋--verify）
→ rom_provenance 再現一式
→ bios-dev-full 相当（無条件: dev-rom/test/integration 群＋ debug/release 比較＋ release-gate-blocked。
  dev-integration は dev-bcdbin-sweep / dev-sweep を含む。§1.3(f)）
→ core-harness / BCDBIN 系の該当ゲート（ローカル実行可能な範囲）
```

ローカル L3（開発者が実行する全回帰）と Required CI（6 Required Checks を生む CI 実行）は**別の証拠**として区別する。両者の関係:

- ローカル L3 の範囲: 上記のローカル実行可能な必須ゲート群。provenance guard、artifact guard の trusted-main 検証、中間 artifact handoff という CI 固有の信頼境界は含まない。
- Required CI の範囲: 上記と同等の検証を CI 環境で実行し、6 Required Checks（`bios-dev`・`verify`・`guard-bios-dev`・`guard-verify`・`guard-core-harness`・`guard-bcdbin-core`）の成功証拠を生むもの。provenance guard、artifact guard の配置・stage・verify、中間 artifact handoff を含み、ローカルでは再現できない信頼境界を含む。現行の必須検証集合（core-harness、BCDBIN、guard、release 検証を含む）を維持し、省略・代替を認めない。
- MERGE_READY の必要条件は**ローカル L3 の成功かつ Required CI（6 件）の成功**の両方である。**ローカル L3 の成功だけでは MERGE_READY と判定しない。**
- 同一 HEAD・同一入力・同一検証条件に対する有効な証拠の再利用は許容する（§5 の流用条件）。Required CI の省略や代替は認めない。

現行 CI（旧 serial 29 分級／#848 並列統合後の 12 分級。§1.5）と同等以上の検証範囲を必須とする。

### L4: EXTENDED（現行必須外の追加検証）

- 長時間 sweep（`dev-mul16s-sweep` 等の CI 外 sweep。"Not run by CI" が明示されたものに限る。`dev-bcdbin-sweep` / `dev-sweep` は必須 CI に含まれるため L3 であり、L4 に含めない）
- 探索的検証・ストレステスト・追加の変異テスト（将来追加の高コスト検証の受け皿）
- nightly の change-aware full とは別軸の、明示的 opt-in 長時間検証

**現行の必須テストを L4 へ移動しない。** L4 の結果は参考情報であり、Required Checks の代替にならない。

---

## 3. 変更影響範囲の判定方式

### 3.1 方式の比較

| 方式 | 内容 | 決定性・再現性 | 保守コスト | 判定 |
| --- | --- | --- | --- | --- |
| A. 静的対応表 | ファイル/モジュール/API → テストの明示マップ（例: `src/overrides/slot_3F.s` → `test_api_*`＋`geargrafx_dev_mul8u.py`） | ◎（表が真実の源泉。レビュー可能） | ○（API 追加時に 1 行追加） | **採用推奨** |
| B. 依存関係グラフ | import・include・ビルド依存からの自動導出 | ○（導出は決定的だが、実行時依存・ゲスト側依存の捕捉が不完全） | △（導出器の保守） | 補助として検討 |
| C. 明示的テストタグ | テスト側に `api:MA_MUL8U` 等のタグを付与し選択 | ○（表と同等。既存 5944 テストへの後付けが必要） | ×（後付け・維持コスト大） | 将来案（L4 枠で段階導入可） |
| D. AI による推測 | 変更内容からの動的判断 | ×（非決定的。同一差分で選択が変わりうる） | — | **不採用**（単独使用を禁止。A の表引きの補助に限定） |

**方式 A（静的対応表）を正本とし、D（AI 推測）は選択根拠に使わない。** AI の役割は「表を引いてコマンドを選ぶ」機械的操作に限定する（§5）。

### 3.2 対応表の初期案（例）

```text
src/overrides/slot_XX.s ──→ tests/host/test_api_*.py（対応 API）＋ tests/integration/geargrafx_dev_*.py（対応 API）
src/api_table.s / tools/build_rom.py ──→ L3（ABI・メモリ配置への影響のため選別不能扱い）
src/common/*、リンカスクリプト、Makefile ──→ L3（共有コード）
tools/*.py（テスト選択ロジック自体・guard・build 系）──→ L3
.github/workflows/* ──→ CI 変更として別扱い（本調査の実装対象外。Required Checks 変更なし）
docs/*.md ──→ L3（docs-only fast path は無効維持。host suite が docs を読むため選別不能）
tests/** ──→ 変更されたテスト自身＋ L3（テスト選択漏れ検出のため。§6）
未分類ファイル ──→ L3（fail-closed）
```

### 3.3 fail-closed フォールバック条件（L3 への引き上げ）

以下に一つでも該当したら L1/L2 で止めず L3 を実行する:

1. 影響範囲が特定できない（対応表にないファイルの変更を含む）
2. 未分類ファイルが変更された
3. 共有コード（ビルド・共通処理・リンカ・Makefile・ツール基盤）が変更された
4. テスト選択ロジック自体（対応表・ラッパー）が変更された
5. ABI・メモリ配置・安全性ゲート（reference JSR、release gate、allowlist、guard）に触れる変更
6. 複数 API・複数モジュールにまたがる変更で、L2 の選別に確信が持てない場合

**不明な変更をテスト対象外として扱わない。** 迷ったら L3。

未コミット入力の取扱い（staged / unstaged / untracked / 削除・改名、BASE 取得失敗・差分取得失敗時の動作、対応表未登録・不整合時の動作、検証入力の同一性確認）は Phase 2 実装時の受入条件として Issue #852 に正式移管した（独立レビュー 6089974846 MEDIUM 1 / FOLLOW_UP）。判定失敗時は fail-closed で L3。

---

## 4. コマンド体系案

既存の `Makefile` 規約（小文字ハイフン連結、`make <動詞>-<対象>`）との整合を優先し、新規ターゲットは**既存コマンドの薄いラッパー**とする（新規検証ロジックを持たない）。名前は例示であり、実装 Issue での確定を前提とする。

| 案 | 内容 | 実体（既存コマンドの部分適用） |
| --- | --- | --- |
| `make test-quick` | L0 | 変更ファイルに対する構文・契約・配置の即時検証（Q1〜Q5） |
| `make test-focused API=<対象>` | L1 | 対象 API の host パターン＋ `dev-rom` ＋対象 dev-integration（F1〜F4） |
| `make test-impact BASE=<base>` | L2 | `BASE`（既定 `origin/main`）との差分→対応表→関連テスト群（I1〜I5） |
| `make test-full` | L3 | 現行フルセットの別名（実体は現行手順と同一。導入は任意） |
| `make test-extended` | L4 | 長時間 opt-in 群の束ね実行 |

設計原則:

- AI エージェントが複雑な選択を毎回判断しなくてよい（`API=`・`BASE=` の 2 引数のみ）。
- 実行開始時に**実行テスト・未実行テスト・選択理由（対応表の該当行）・検証範囲（Lx の定義）**を標準出力に明示する（選択の監査可能性）。
- ラッパーは現行ターゲットを呼び出すだけで、テスト本体・ゲート・閾値を一切変更しない。
- `BASE` の既定は `origin/main` の merge-base。HEAD・入力・環境・ツールチェーン・検証条件のいずれかが異なる結果の流用はしない（§5）。未コミット入力の詳細な取扱いは #852 に移管。

---

## 5. AI エージェントの運用ルール案

| フェーズ | 実行段階 | 根拠 |
| --- | --- | --- |
| 実装途中（編集〜初回確認） | L0・L1 | 30 秒〜2 分でフィードバック。現行の「不安からの全回帰」を置換 |
| 修正完了時 | L2 | 間接影響の確認（5 分枠） |
| 独立コードレビュー後 | L2 または内容に応じた L3 | レビュー指摘が共有部・ABI に及ぶ場合は L3（§3.3） |
| PR | L3（ローカル）＋ Required CI 6 件の成功 | ローカル全回帰と CI 証拠の両方が必要。ローカル L3 のみでは MERGE_READY としない（§2 L3） |
| 統合前 | 対象 HEAD＋最新 main との整合を確認した L3 | main 進展時の drift 検出（現行 CONTRIBUTING の branch 方針と同一） |
| 定期検証 | L4（＋現行 nightly） | 長時間・探索的検証の受け皿 |

結果の流用条件（すべてを満たす場合のみ再実行を省略可）:

1. 同一 HEAD（merge-SHA 一致）
2. 同一入力・同一環境（OS/arch/ツール pin・`TOOLS_DIR` 由来の同一性を含む）
3. 同一ツールチェーン・同一検証条件（MODE、env 変数、`--full` 等のフラグ）
4. 有効な証拠（PASS/FAIL の記録、SKIP・BLOCKED の PASS 扱い禁止）

上記を一つでも欠く場合は流用せず再実行する。**旧 HEAD の結果を新 HEAD の結果として主張しない**（#848 の run＋attempt binding と同一原則）。

既存 CI の実行条件・Required Checks の変更は本調査の対象外であり、運用ルールはローカル開発ループのみに適用する。

---

## 6. 安全性評価

### 6.1 不変条件（すべて維持）

- [ ] 現行テスト集合を削減しない（L3 = 現行集合。L0〜L2 は開発ループの追加選択肢であり CI の置換ではない）
- [ ] Required Checks 6 件を削減・改名しない
- [ ] 安全性ゲートを緩和しない（reference JSR 許可 0/81、ABI VERIFIED 判定基準、`release_eligible=false`、`make release` BLOCKED、dev-release-gate-blocked の exit 3 判定）
- [ ] 商用媒体・`./rom` にアクセスしない（本調査の計測は合成 fixture のみ。CI への商用実行許可を与えない）
- [ ] docs-only fast path を安易に導入しない（現行の無効化を維持。host suite の docs 読込が被覆証明の blocker であることを確認済み）
- [ ] 部分テストの成功を全回帰成功として扱わない（L0〜L2 の PASS 表示に「Lx PARTIAL、L3 ではない」旨を明示する設計）
- [ ] 部分テストのみで MERGE_READY と判定しない（MERGE_READY の必要条件はローカル L3 の成功かつ Required CI 6 件の成功。ローカル L3 のみ・Required CI の省略や代替は認めない）

### 6.2 テスト選択漏れの検出（fail-closed 設計）

1. **対応表の完全性ゲート**: 対応表にない変更ファイルは自動的に L3（§3.3-1,2）。新規 API・新規テストは対応表への登録を必須化し、未登録のまま L1/L2 で PASS しても L3（＝現行全量）が漏れを捕捉する二重構造。
2. **L3 の定期的実行**: PR 前・統合前は必ず L3。L1/L2 のみの長期運用を禁止。
3. **選択理由の出力**: `test-impact` は選択根拠（対応表の行）を出力し、レビューで選択の妥当性を検証可能にする。
4. **選択ロジック自体の変更は L3**: ラッパー・対応表の変更自体が L3 トリガ（§3.3-4）。選択器のバグが選択器で隠蔽される構造を避ける。

### 6.3 残存リスク

- 対応表の陳腐化（新規 API 追加時の登録漏れ）→ 上記 6.2-1,2 の二重構造で緩和。登録漏れは L3 実行コストの増加として顕在化し、安全性の低下としては顕在化しない設計。
- L2 の選別誤り（関連テストの過小選択）→ L2 は MERGE_READY 根拠にならないため、誤りは開発ループの手戻りに留まる。
- 所要時間のばらつき（旧 serial 23〜29 分の分散、#848 並列統合後の 12 分級、runner 差）→ 段階の時間は性能目標であり SLO ではない。超過時は検証を削らず、超過事実を記録して対応表・選別の見直し材料にする。

---

## 7. 期待効果と根拠

### 7.1 比較軸（現行方式 vs 段階化後）

| 指標 | 現行方式（実測・推定を区別） | 段階化後（見込み。実測は導入後に計測） |
| --- | --- | --- |
| 実装開始→最初の結果取得 | `make build` 数秒＋手動の単体実行（統制なし） | **L0: 数秒〜30 秒**（contracts 0.06s 等の実測に基づく） |
| 修正 1 回あたりのテスト時間 | host 全量 5 分（LOCAL 実測）＋ real-core 全量（CI 20 分級。LOCAL 全量は未計測） | **L1: 1〜2 分**（dev-rom 0.9s＋API 実コア 4.9s×数本＋host パターン。API 1 件の代表例で推定） |
| レビューループ 1 周 | 全回帰反復（現行推奨手順で host＋real-core＋check＋clean＋build） | **L2: 5 分以内**（関連 subset のみ。重スクリプト除外が条件） |
| エージェント 1 タスクの総テスト時間 | 全回帰 × N 回（N はエージェントの判断依存で不定） | L0/L1 × N ＋ L2 × 1〜2 ＋ L3 × 1（PR 前）。**全回帰回数を 1〜2 回に固定** |
| 全回帰実行回数 | 任务あたり複数回（習慣的反復） | **PR 前・統合前の 1〜2 回**（同一 HEAD の有効結果は流用）。PR 時は Required CI 6 件も実行（省略・代替不可） |
| CPU 使用時間 | 旧 serial 29 分級／現行並列 12 分級（CI）。LOCAL 全量は未計測 | L0〜L2 の subset 分のみ。L3 は現行と同一（削減なし） |
| テスト選択の正確性 | エージェントの判断依存（ばらつき） | 対応表による決定的選択＋ L3 の二重捕捉 |
| 運用負担 | 「どこまで回すか」の判断コスト | コマンド選択のみ（`API=`・`BASE=`） |
| 選択漏れリスク | 過小実行の可能性（統制なし） | fail-closed＋L3 必須化で現行以下に抑制（§6.2） |

**実測と推定の区別:** 上表の現行方式欄の数値は §1.5 の実測値。段階化後欄は代表例（API 1 件）の実測断片からの推定であり、**複数代表パターンでの実測は導入 Issue の計測タスクとする**（§9）。L1/L2 の時間目標は性能目標であり、超過時は検証を削らない（§2 冒頭）。

### 7.2 代表的変更パターン別の見込み（推定）

1. **単一 DEV API 実装**（例: `slot_3F.s` 変更）: L1 = dev-rom 0.9s＋host 対応数秒＋dev-integration 4.9s → **約 30 秒以内**。現行の host 全量（5 分）＋ real-core 全量との差が最大。
2. **host のみ変更**（例: 契約テスト追加）: L1 = 対象ファイル数秒。real-core 不要の判定が対応表で可能。
3. **共有コード変更**（例: `tools/build_rom.py`、`api_table.s`）: 即 L3（選別不能）。効率改善なし＝安全性優先の設計通り。
4. **docs のみ変更**: 現行通り L3（full CI）。fast path 導入なし＝改善なし。host suite の docs 読込が解消されるまで維持。

---

## 8. 段階的な導入計画

1. **Phase 0（本 Issue #850）**: 調査・設計・非侵襲計測。成果物は本ドキュメント＋調査 PR。main への機能変更なし。
2. **Phase 1**: 対応表 v0（§3.2）の作成とレビュー。対象は現行 API（DEV 18＋ stubs）と主要モジュール。成果物は docs＋表ファイル。CI・Makefile 変更なし。
3. **Phase 2**: ラッパーターゲット（§4）の追加。既存ターゲットの呼び出しのみ。新規検証ロジックなし。CI 接続なし（ローカル専用）。受入条件（未コミット入力の取扱い: staged/unstaged/untracked/削除・改名、BASE・差分取得失敗時、対応表未登録・不整合時、入力同一性確認）は Issue #852 に移管済み（独立レビュー 6089974846 MEDIUM 1 / FOLLOW_UP）。
4. **Phase 3**: AI 運用ルール（§5）の試行と計測（複数代表パターンでの L0〜L2 実測、選択正確性、漏れ検出の動作確認）。
5. **Phase 4（別 Issue）**: 効果・安全性の再評価を経て、CI への接続要否を判断。要否判断自体を Owner 承認事項とする。本調査は Phase 4 の実施を承認しない。

各 Phase は独立 Issue 化し、Owner 承認なしに main へ統合しない。

---

## 9. 未解決事項

- [ ] `make setup` の新規取得時間（本調査ではキャッシュ済みのため未計測。#849 の範囲）
- [ ] LOCAL 環境での `make test-real-core` 全量時間（未計測。重スクリプト群の合計値は旧 serial CI の 23m50s のみ）
- [ ] `dev-test` パターン（`test_api*.py`＋`test_ma*.py`）の LOCAL 実測時間
- [ ] L2 関連 subset（例: G5 系 `test_g5_*`＋直接スクリプト数本）の実測時間
- [x] #848 の統合状態（`origin/main` `7041972e` に統合済みを確認。現行 CI の各段階時間は §1.5 の並列行に記録。今後の比較基準は統合後の現行 CI とする。旧 serial 値は履歴として保持）
- [ ] 対応表 v0 の網羅性（81 スロット× host/integration の完全マッピングは Phase 1 の作業）
- [ ] テストタグ方式（方式 C）の将来性判断（Phase 3 以降）
- [ ] Windows/WSL 環境は対象外（Linux CI のみが必須環境。`docs/testing.md` の既定と同一）

---

## 10. 採用可否の推奨

**推奨: 採用（Phase 1→2→3 の順に段階導入）。** 理由:

1. **安全性の不変条件を満たせる設計**であること（§6.1 の 7 項目はいずれも「現行の維持」であり、新規の緩和を含まない）。
2. **L3 が現行全回帰と同一（無条件実行）**であり、品質保証の削減がゼロであること。MERGE_READY の必要条件をローカル L3 の成功かつ Required CI 6 件の成功とし、ローカル L3 のみでの判定・Required CI の省略や代替を認めない。段階化は開発ループの実行順序・範囲の最適化に限定される。
3. **効果の根拠が実測断片で裏付け**られること（L0/L1 の構成要素はいずれも秒オーダーで実測済み）。
4. **#846/#848/#849 と直交**し、競合・重複がないこと（CI 並列化は wall-time 短縮、ツールチェーン事前ビルドは setup 短縮、本調査は開発ループの実行回数・範囲の最適化）。
5. **fail-closed の漏れ検出**（§6.2）により、選択漏れリスクが現行（統制なしの過小実行）より低減する見込みであること。

ただし Phase 4（CI 接続）は本調査の推奨範囲外とし、Phase 3 の計測結果をもって別途 Owner 判断を求める。また docs-only fast path の再有効化は本調査のいずれの Phase でも行わない。

---

## 11. 作業記録（証拠境界の明示）

- 実行したこと: `origin/main` 確認（調査時 `d27a65ba`、修正時最新 `7041972e`＝#848 統合済み）、既存 Issue/PR 調査（#845/#846/#848/#849 と重複なし）、AGENTS.md・CONTRIBUTING.md・`docs/testing.md`・`docs/building.md` 確認、Issue #850 起票、専用ブランチ作成、Makefile・全ワークフロー・ruleset（Required 6 件）調査、LOCAL 計測（§1.5 の表）、独立レビュー 6089974846 対応（MAJOR 2 件の設計書修正、MEDIUM 1 の #852 移管）、新 main での不変条件維持確認（docs-only 無効・JSR 0/81・VERIFIED 0/81・`release_eligible=false`）。
- 実行していないこと: 本番 CI・Required Checks・テスト本体・CLAUDE.md・AGENTS.md の変更（なし）、`./rom`・商用媒体へのアクセス（`rom/` 不在を確認のみ）、`main` への統合（Owner 承認なしに行わない）。
- 未計測の明示: setup 新規取得時間、LOCAL real-core 全量時間（§9 に記録し、PASS と主張しない）。

---

*Provenance: original analysis (MIT). No proprietary content. No `./rom` access. Pinned tools: cc65 `71746c8`, Geargrafx `b49ae82a` (cache reuse, read-only timing only).*
