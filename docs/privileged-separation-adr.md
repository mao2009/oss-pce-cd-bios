# ADR: 特権ジョブと候補コードの分離（Issue #544、オーナー裁定の選択肢 b）

状態: 提案（Draft PR のまま。MAJOR が残る間はマージしない）

## 決定

1. **特権ジョブは候補コードを実行しない。** 特権ジョブが実行するのは、信頼された SHA の checkout 上のスクリプトだけとする。
   PR の候補は、ハッシュ・構文解析・ディレクトリ列挙の対象としてのみ読む。
2. **書込みは 2 つの領域に限る。** 専用の一時領域 `build/tmp/` と、個別に認めたステージング領域 `build/stage/`。
   この規則は、特権の書込み（`cp`、`tee`、`mkdir`）の宛先と、信頼されたスクリプトの後ろの引数の両方に適用する。
   `gh` は書込み範囲の規則の対象ではない。`gh` は `repos/` で始まる単一の GET と `attestation verify` の二つに限る。
   例外は、スクリプトとフラグの組ごとに列挙した読取り専用の対（`TRUSTED_SCRIPT_READ_ONLY_INPUTS`。現在は
   `policy_guard.py` の `--candidate .candidate` と `--trusted .trusted` だけ）。この例外はオーナーの承認が必要な項目である。
   `.git`、ソース、trusted checkout（`.trusted-main/`、`.trusted/`）、candidate checkout（`.candidate/`）への書換えは禁止。
3. **特権操作の前提は三つ。** (a) 信頼された SHA（実装済み: 特権の checkout は信頼された main のみ）、
   (b) 承認済みスクリプト（実装は run: の完全一致の承認表による。スクリプト本体のハッシュ検査は未実装）、
   (c) 成果物の検証結果（sha256 の manifest。検証は既存の `--verify`、manifest の強制は未実装）。
   いずれかが欠ければ fail closed とする。
4. **非特権 CI と特権 CI を分離する。** 非特権ジョブ（`contents: read`、secret なし）は PR のコードを実行してよいが、
   その出力は信頼されたガードが公開前に検査する。特権ジョブは別のワークフローに置く。

## 根拠

静的な閉包解析（PR のスクリプトが呼ばれるかを追跡する方式）は、レビューを重ねても MAJOR が残った。
特権ジョブが候補コードを実行しないと決めれば、証明すべき範囲は小さくなる。ただし、閉包検査は残っている。
閉包検査（`closure_problems`）は、特権の run: が実行しうるファイル（承認済みの trusted スクリプトと、その import）を
信頼木と比べ、候補の symlink と、実行されるディレクトリへの追加を拒否する。特権ジョブの規則（許可コマンド、書込み範囲）と併用する。

## 既存の MAJOR 2 件の原因と解消条件

| 指摘 | 原因 | 新設計での解消 | 残る条件 |
|---|---|---|---|
| 書込みで PR の symlink（例: `build -> .trusted-main/tools`）を通り、信頼木へ届く | 書込み先を文字列で判定し、拒否リストで範囲を決めていた。リンク検査は配置・政策の検査にだけあった | 書込み先を `build/tmp/`、`build/stage/` の許可リストに限った（`writer_destinations` と `_in_staging_area`、試験 `StagingAreaOnly` 12 件）。リンクは `main()` の全モードで検査（`link_problems`） | 特権ジョブのワークフローで、ガードより前に書込みを行うステップが無いこと（全件確認が必要） |
| 試験の多くが、拒否の理由ではなく別の規則で通っている | 閉包解析の規則が多く、理由を検査していなかった | 閉包検査は併用する。試験 `StagingAreaOnly` は拒否の理由の文字列を検査する形に直し済み | 閉包の他の試験の理由文字列の検査（未了） |

## 特権ジョブと非特権ジョブの境界（現行 main との非互換を含む）

- 特権ジョブ: `write` 権限、`id-token`、`environment`、secret の参照、`pull_request_target`、権限の未指定のいずれかを持つジョブ。
- 現行の `#529` は、承認表の trusted checkout（`sparse-checkout: tools`）と `--check-policy` の配線を `guard-verify` に入れる。
  この変更は main の現行ガードと互換でない。main のガードは PR 側のワークフローの形を固定しているため、
  **main にこの変更が入るまで、PR の CI のガードジョブは失敗する（bootstrap 設計として明示）。**
- 特権ジョブの分離（ワークフローの分割）は、このブランチでは未実施。分割の前に、特権ジョブが候補コードを実行していないことを
  ワークフローの全件確認で示す必要がある。

## 最小実証計画

1. 特権ジョブの雛形を 1 つ作り、`gh` の読取りと `build/stage/` への書込みだけで動くことを CI で示す（候補コードは実行しない）。
2. symlink を含む候補で、書込みが拒否されることを試験する（`link_problems` と `StagingAreaOnly`）。
3. 書込み範囲の試験（許可外・`..`・`.git`・trusted 木・`--target-directory=`）を CI で実行し、すべて拒否されることを示す。
4. 上記を満たした後に、ワークフロー分割の PR を別に出す（この PR のマージ条件ではない）。

## 範囲外（オーナー判断）

- 承認済みスクリプトの内容を承認表へ列挙する方式は採らない（オーナー裁定）。
- ruleset、required check `policy-guard`、App ID の固定は、オーナー管理として保留する。
- 信頼された依存先の検証（外部ツリー、`tools/setup.py` の checksum）は、既存の `REVIEWED_EXTERNAL` の範囲に留まる。
