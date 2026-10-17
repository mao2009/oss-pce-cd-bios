# CD_READ 署名付き判定（verdict）スキーマ v1（Issue #153 / #156）

本書は、信頼できる main の CI（生成側、#156）が出力し、利用側 `tools/verdict_consumer.py`（#153）が
独立に再検証する **CD_READ 互換性判定 artifact** の形式を定める。スキーマの所有者は #153。
生成側と利用側はこの文書に一致させる。食い違いは **すべて fail closed**（VERIFIED にしない）。

**現状: VERIFIED の本番経路は無効。** `protect-main` の必須承認は 0 件であり（#124 方針: 案 A 実施まで案 C）、
利用側の定数 `VERIFIED_PATH_ENABLED = False` と `main_rules.required_approving_review_count >= 1` の両方が
満たされない限り、どの入力でも結果は BLOCKED。T6〜T8 と callee ABI（#139、`spec/api_contracts.json` slot 3）は
本スキーマで昇格できない。VERIFIED に到達しても Geargrafx 上の transport 層のエミュレータ証跡であり、
実機互換性・BIOS 互換性の主張ではない。

## 1. ファイルと署名エンベロープ

| 項目 | 値 |
| --- | --- |
| ファイル名 | `cdread-verdict.json`（UTF-8、JSON オブジェクト 1 個、最大 65536 バイト） |
| 署名方式 | Sigstore keyless。GitHub Artifact Attestation（`actions/attest-build-provenance`、SLSA provenance v1）で **verdict ファイル自体を subject** として署名する |
| predicate type | `https://slsa.dev/provenance/v1` |
| OIDC issuer | `https://token.actions.githubusercontent.com` |
| 署名 workflow | `mao2009/oss-pce-cd-bios/.github/workflows/api-source.yml`（`buildSignerURI` = `https://github.com/mao2009/oss-pce-cd-bios/.github/workflows/api-source.yml@refs/heads/main`） |
| source ref | `refs/heads/main` |
| trigger | `push` または `workflow_dispatch` |
| runner | GitHub-hosted（self-hosted は拒否） |
| bundle の受け渡し | GitHub の attestation API、またはオフライン用に `gh attestation download` 等で得た Sigstore bundle（JSON / JSONL）ファイル |

署名の検証は利用側が **自分で** `gh attestation verify` を実行して行う。呼出側が渡す gh 出力 JSON・実行記録・
branch rules は受け取らない（PR #154 の結論: それらは関数に渡った時点でただの JSON で、偽造と区別できない）。
利用側が実行するコマンド（`COMMIT` は利用側 checkout の `git rev-parse HEAD`。呼出側は指定できない）:

```
gh attestation verify cdread-verdict.json \
  --repo mao2009/oss-pce-cd-bios \
  --signer-workflow mao2009/oss-pce-cd-bios/.github/workflows/api-source.yml \
  --source-ref refs/heads/main --source-digest COMMIT \
  --cert-oidc-issuer https://token.actions.githubusercontent.com \
  --predicate-type https://slsa.dev/provenance/v1 \
  --deny-self-hosted-runners --format json [--bundle BUNDLE]
```

gh が非 0 終了・未インストール・タイムアウト・JSON 不正の場合は BLOCKED。gh が成功しても、利用側は出力を
信用せず以下を再確認する: subject に **利用側が再計算した verdict バイト列の sha256** が含まれること、
`statement.predicateType` が `https://slsa.dev/provenance/v1` であること、証明書の
`issuer`・`sourceRepositoryURI`・`sourceRepositoryDigest`（= COMMIT）・`sourceRepositoryRef`・`buildSignerURI`・
`buildSignerDigest`（= COMMIT）・trigger・`runnerEnvironment`（#125 `certificate_findings` と同じ規則）、
`runInvocationURI` の run ID / attempt が verdict の `producer.run_id` / `producer.run_attempt` と一致すること。

## 2. フィールド

すべて必須。未知のフィールド、型違い（`true` を整数として使う等の bool/int 混同を含む）、重複キー、
`NaN`/`Infinity` はすべて拒否（BLOCKED）。

| フィールド | 型 | 規則 |
| --- | --- | --- |
| `schema` | str | 厳密に `oss-pce-cd-bios/cdread-verdict/v1` |
| `repository` | str | 厳密に `mao2009/oss-pce-cd-bios`（fork は不一致） |
| `commit` | str (40 桁小文字 hex) | 判定対象の main commit。利用側 checkout の HEAD と証明書の `sourceRepositoryDigest` の両方に一致 |
| `ref` | str | 厳密に `refs/heads/main` |
| `producer` | object | `{"workflow": str, "run_id": int, "run_attempt": int}`。`workflow` は `.github/workflows/api-source.yml`。`run_id`/`run_attempt` は正の整数（bool 不可）で、証明書の `runInvocationURI` と一致 |
| `issued_at` | str | UTC、`YYYY-MM-DDTHH:MM:SSZ` 形式のみ |
| `expires_at` | str | 同形式。`issued_at < expires_at`、有効期間は最大 30 日（2592000 秒ちょうどは可、1 秒超過で拒否） |
| `geargrafx` | object | `{"revision": str, "source_archive_sha256": str}`。`tools/dependencies.json` の pin と一致 |
| `evidence` | object | `{"artifact_sha256", "rom_sha256", "bin_sha256", "cue_sha256"}`、各 64 桁小文字 hex。`artifact_sha256` は #125 で検証した `cdread-evidence.json` の sha256。`rom_sha256` は利用側が渡された ROM バイトから再計算した値と一致し、denylist（`provenance/denylist.sha256`）に載っていないこと |
| `provenance` | object | `{"result": "PASS", "verifier": "tools/evidence_provenance.py"}`。生成側が信頼済みジョブ内で自ら実行した `gh attestation verify` + `evidence_provenance.verify()` の結果。`PASS` 以外は拒否 |
| `main_rules` | object | `{"source": "github-api", "required_approving_review_count": int}`。生成側ジョブが GitHub API（`rules/branches/main`）から取得した値。int（bool 不可）、0 以上。**1 未満なら BLOCKED**（現状 0） |
| `claims` | object | キーは厳密に `T1`〜`T8`。値は str。T1〜T5 は `VERIFIED` / `BLOCKED` のいずれか。T6〜T8 は台帳（`tools/cd_read_contract.py` `TRANSPORT_CLAIMS`）の宣言値と完全一致（T6 `HYPOTHESIS`、T7/T8 `UNKNOWN`）。T6〜T8 に `VERIFIED` 等が入った verdict は全体を拒否 |

例（合成値。実在の署名・run ではない）:

```json
{
  "schema": "oss-pce-cd-bios/cdread-verdict/v1",
  "repository": "mao2009/oss-pce-cd-bios",
  "commit": "0123456789abcdef0123456789abcdef01234567",
  "ref": "refs/heads/main",
  "producer": {"workflow": ".github/workflows/api-source.yml", "run_id": 1, "run_attempt": 1},
  "issued_at": "2026-10-16T00:00:00Z",
  "expires_at": "2026-10-23T00:00:00Z",
  "geargrafx": {"revision": "<pin>", "source_archive_sha256": "<pin>"},
  "evidence": {"artifact_sha256": "<hex64>", "rom_sha256": "<hex64>", "bin_sha256": "<hex64>", "cue_sha256": "<hex64>"},
  "provenance": {"result": "PASS", "verifier": "tools/evidence_provenance.py"},
  "main_rules": {"source": "github-api", "required_approving_review_count": 0},
  "claims": {"T1": "VERIFIED", "T2": "VERIFIED", "T3": "VERIFIED", "T4": "VERIFIED", "T5": "VERIFIED",
             "T6": "HYPOTHESIS", "T7": "UNKNOWN", "T8": "UNKNOWN"}
}
```

## 3. 利用側の判定（fail-closed の既定）

出発点は常に `cd_read_contract.compat_status()`（宣言 VERIFIED はすべて BLOCKED、T6〜T8 は宣言値）。
次の **すべて** が成り立つときに限り、台帳で VERIFIED と宣言され、かつ verdict の `claims` が `VERIFIED` の
T1〜T5 だけを VERIFIED にする。1 つでも欠けたら一切昇格しない（部分昇格なし）。

1. verdict バイト列が 65536 バイト以下で、§2 の全規則を満たす。
2. 利用側が実行した `gh attestation verify` が成功し、§1 の再確認がすべて一致。
3. `commit` = 利用側 checkout の HEAD = 証明書の commit。
4. 渡された ROM の sha256 = `evidence.rom_sha256`、かつ denylist 外。
5. 現在時刻 `now` が `issued_at <= now < expires_at`（`expires_at` ちょうどは期限切れ）。
6. `main_rules.required_approving_review_count >= 1`。
7. `VERIFIED_PATH_ENABLED` が True（現状 False。変更は独立した人間の承認者によるレビューを経た PR でのみ行う）。

### 3.1 昇格を許す事実の出所と条件（生成側 `tools/cdread_verdict.py`、#156）

VERIFIED 昇格の**事実の出所（source of facts）**は、署名の有無や生成主体ではなく、信頼済み main CI ジョブが
`gh attestation verify` で真正性を確認した `cdread-evidence.json`（#125）の **シナリオ表**である。生成側は
台帳 `TRANSPORT_CLAIMS` の宣言値をそのままコピーせず、各 T1〜T5 について、その claim が引用する全シナリオ
（`tools/cd_read_contract.py` の `SCENARIO_KIND`）が認証済み証跡に期待どおりの種別で揃っている場合に限り、
その claim を `VERIFIED` とする。1 つでも欠落・種別違いならばその claim は `BLOCKED`。T6〜T8 は従来どおり
宣言値（`HYPOTHESIS`/`UNKNOWN`）で固定する。

生成側はこれに加えて `main_rules.required_approving_review_count >= 1` を要求し、満たさない限り T1〜T5 を
`BLOCKED` のまま出力する。現状 `protect-main` の必須承認は 0 件なので、**本番の verdict は常に BLOCKED**で
あり、この安全側の状態が既定である。生成側が `VERIFIED` を書いた場合でも、利用側は §3 の 1〜7 を独立に
再確認するため、`VERIFIED_PATH_ENABLED` と承認数ゲートを満たすまで昇格しない。

**事実（compatibility evidence）と署名の真正性（authenticity）は分離する。** 何が成り立つかは認証済み証跡の
シナリオ被覆が決め、誰が生成したかは verdict の署名（利用側が実行する `gh attestation verify`）が決める。
署名が存在するだけでは claim を `VERIFIED` にせず、逆に証跡のシナリオ被覆だけでは署名が無いため昇格しない
（§1 と本節の全条件が必要）。したがって、実コアのシナリオ証跡を欠く入力や、署名だけ真似た入力は、どちらも
`BLOCKED` のままになる。往復契約は `tests/host/test_verdict_contract.py` が同じチェックアウトで検証する。

| 状況 | 結果 |
| --- | --- |
| 署名なし・bundle なし・gh 失敗・gh なし | BLOCKED |
| 改ざん（署名後に 1 バイトでも変更） | BLOCKED（subject digest 不一致） |
| 別 repo / fork / 別 workflow / 別 ref / PR trigger / self-hosted / 別 issuer | BLOCKED |
| 別 commit / 別 ROM / 期限切れ / 未来発行 / 有効期間超過 | BLOCKED |
| T6〜T8 を VERIFIED と主張 | BLOCKED（verdict 全体を拒否） |
| すべて正しいが承認 0 件、または `VERIFIED_PATH_ENABLED = False` | BLOCKED（現状の本番の結果） |

CLI: `python3 tools/verdict_consumer.py cdread-verdict.json --rom ROM [--bundle BUNDLE]`。
VERIFIED を 1 件以上出したときだけ exit 0、それ以外は exit 3（BLOCKED、#119 と同じ）。
テスト（`tests/host/test_verdict_consumer.py`）は gh をモジュール内の `_gh` 差し替えで模擬する。模擬は
テスト専用で、本番では使われない。テストで署名済みとして扱う digest は合成 verdict のものだけである。
