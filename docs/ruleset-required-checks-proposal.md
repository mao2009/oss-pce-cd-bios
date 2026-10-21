# main ruleset の必須チェック: 変更案（提案のみ・未適用）

Issue [#440](https://github.com/mao2009/oss-pce-cd-bios/issues/440)。対象は ruleset `protect-main`（ID `25220357`）。
この文書は変更案を記録するだけである。ruleset は**オーナーの承認なしに変更しない**。この PR も ruleset を変更しない。

## 現状（2026-10-21 に読取専用の `gh api` で確認）

`gh api repos/mao2009/oss-pce-cd-bios/rulesets/25220357` の `required_status_checks` は次の 2 件だけである。

- `bios-dev`
- `verify`

`pull_request` ルールは `required_approving_review_count: 0`、`bypass_actors: []` である。そのため、次のジョブが失敗していても merge は許可される。

| check（context） | workflow | 必須か | 現状 |
| --- | --- | --- | --- |
| `core-harness` | `core-harness.yml`（pull_request） | いいえ | 失敗しても merge 可能 |
| `bcdbin-core` | `core-bcdbin.yml`（pull_request） | いいえ | 失敗しても merge 可能 |
| `guard-bios-dev` | `api-source.yml`（`needs: bios-dev`） | いいえ | 失敗しても merge 可能 |
| `guard-verify` | `ci.yml`（`needs: verify`） | いいえ | 失敗しても merge 可能 |
| `guard-core-harness` | `core-harness.yml`（`needs: core-harness`） | いいえ | 失敗しても merge 可能 |
| `guard-bcdbin-core` | `core-bcdbin.yml`（`needs: bcdbin-core`） | いいえ | 失敗しても merge 可能 |

`guard-*` は上流ジョブに `needs` で依存する。上流が失敗すると `skipped` になり、skipped は必須チェックで成功扱いになる。そのため、guard を必須にするだけでは不十分で、上流ジョブ（`core-harness`、`bcdbin-core`、`bios-dev`、`verify`）も必須にする必要がある。

以下は必須に含めない。
- `attest-*`、`verify-cdread-*`、`guard-cdread-verdict`、`attest-cdread-verdict`、`verify-cdread-verdict`（`api-source.yml`）は `push` かつ main のときだけ動き、PR では skipped になる。
- `guard-nightly` と `nightly.yml` は schedule 専用である。
- `pages.yml` は PR で動く。必須にするかどうかは別途判断する（本案の対象外）。

## 提案する変更

必須チェックに、`core-harness`、`bcdbin-core`、`guard-bios-dev`、`guard-verify`、`guard-core-harness`、`guard-bcdbin-core` の 6 件を追加する。`pull_request` ルール、`bypass_actors`、`conditions` などは変えない。

本文の JSON（ruleset PUT 用の本体）:

```json
{
  "name": "protect-main",
  "target": "branch",
  "enforcement": "active",
  "conditions": {"ref_name": {"exclude": [], "include": ["refs/heads/main"]}},
  "bypass_actors": [],
  "rules": [
    {"type": "deletion"},
    {"type": "non_fast_forward"},
    {"type": "pull_request", "parameters": {
      "required_approving_review_count": 0,
      "dismiss_stale_reviews_on_push": true,
      "required_reviewers": [],
      "require_code_owner_review": false,
      "require_last_push_approval": false,
      "required_review_thread_resolution": false,
      "require_extra_approval_for_unattributed_changes": true,
      "allowed_merge_methods": ["merge", "squash", "rebase"]}},
    {"type": "required_status_checks", "parameters": {
      "strict_required_status_checks_policy": false,
      "do_not_enforce_on_create": false,
      "required_status_checks": [
        {"context": "bios-dev"},
        {"context": "verify"},
        {"context": "core-harness"},
        {"context": "bcdbin-core"},
        {"context": "guard-bios-dev"},
        {"context": "guard-verify"},
        {"context": "guard-core-harness"},
        {"context": "guard-bcdbin-core"}
      ]}}
  ]
}
```

適用コマンド（オーナーが承認した後にだけ実行する。この PR では実行していない）:

```sh
gh api --method PUT repos/mao2009/oss-pce-cd-bios/rulesets/25220357 --input ruleset-25220357.json
```

`ruleset-25220357.json` は上の JSON を保存したファイルである。適用前には、同じ読取で現状との差分を確認すること。

## 注意

- 変更後は、PR の check 名が 8 件すべて実行されることを確認する。`core-harness` などが `Expected — Waiting for status` のまま止まる場合は、workflow の trigger を確認する。
- `core-bcdbin.yml` と `core-harness.yml` は `pull_request` に branch filter がない。そのため、すべての PR で実行される。
- `required_approving_review_count: 0` は変更しない。承認数は別の判断である。
