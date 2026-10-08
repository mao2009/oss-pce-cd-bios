#!/usr/bin/env bash
# Called by scheduled workflow on main. Not a firmware compatibility test.
set -euo pipefail

# The schedule runs from main; use bootstrap feature until PR #101 lands.
if git cat-file -e "origin/main:tools/build_rom.py" 2>/dev/null; then
  source_ref=main
elif git show-ref --verify --quiet refs/remotes/origin/feat/100-api-stub-table; then
  source_ref=feat/100-api-stub-table
else
  echo "::error::No buildable source (main or feat/100-api-stub-table)"
  exit 1
fi
source_sha="$(git rev-parse "refs/remotes/origin/$source_ref")"
printf 'source_ref=%s\nsource_sha=%s\n' "$source_ref" "$source_sha" >> "$GITHUB_OUTPUT"
workflow_fingerprint="$(sha256sum .github/workflows/nightly.yml tools/nightly_plan.sh | sha256sum | cut -d' ' -f1)"
printf 'workflow_fingerprint=%s\n' "$workflow_fingerprint" >> "$GITHUB_OUTPUT"

# A prior failed nightly must be retried even if source did not change.
runs_file="$RUNNER_TEMP/nightly-history.json"
gh run list --repo "$GITHUB_REPOSITORY" --workflow nightly.yml \
  --limit 100 --json databaseId,status,conclusion > "$runs_file"
latest="$(jq -r '[.[] | select(.status == "completed")][0].conclusion // ""' "$runs_file")"
retry=false
case "$latest" in
  failure|cancelled|timed_out|action_required|stale) retry=true ;;
esac

# A skipped-success run has no state artifact. Find last actual successful build.
previous_sha=""
previous_ref=""
previous_fingerprint=""
while IFS= read -r run_id; do
  [[ -n "$run_id" ]] || continue
  dir="$RUNNER_TEMP/nightly-baseline-$run_id"
  mkdir -p "$dir"
  if gh run download "$run_id" --repo "$GITHUB_REPOSITORY" \
      --name nightly-success-state --dir "$dir" >/dev/null 2>&1; then
    if [[ -f "$dir/nightly-state.json" ]]; then
      previous_sha="$(jq -r '.source_sha // ""' "$dir/nightly-state.json")"
      previous_ref="$(jq -r '.source_ref // ""' "$dir/nightly-state.json")"
      previous_fingerprint="$(jq -r '.workflow_fingerprint // ""' "$dir/nightly-state.json")"
      if [[ "$previous_sha" =~ ^[0-9a-f]{40}$ ]] && [[ -n "$previous_ref" ]]; then
        break
      fi
    fi
  fi
  previous_sha=""
  previous_ref=""
  previous_fingerprint=""
done < <(jq -r '.[] | select(.status == "completed" and .conclusion == "success") | .databaseId' "$runs_file")

build=true
reason=first-build-or-state-unavailable
if [[ "$EVENT_NAME" == workflow_dispatch && "$FORCE" == true ]]; then
  reason=manual-force
elif [[ "$retry" == true ]]; then
  reason=retry-previous-failure
elif [[ -z "$previous_sha" ]]; then
  reason=first-build-or-state-unavailable
elif [[ "$previous_ref" != "$source_ref" ]]; then
  reason=build-branch-changed
elif [[ "$previous_fingerprint" != "$workflow_fingerprint" ]]; then
  reason=nightly-workflow-changed
elif ! git cat-file -e "$previous_sha^{commit}" 2>/dev/null; then
  reason=previous-commit-unavailable
elif ! git merge-base --is-ancestor "$previous_sha" "$source_sha"; then
  reason=history-rewritten
else
  # Tests/toolchains/workflow affect output assurance; markdown-only does not.
  relevant=(src/ spec/ tools/ tests/ Makefile .github/workflows/nightly.yml .github/workflows/api-source.yml)
  if git diff --quiet "$previous_sha" "$source_sha" -- "${relevant[@]}"; then
    build=false
    reason=no-relevant-changes
  else
    reason=source-or-test-changed
  fi
fi

printf 'build=%s\nreason=%s\n' "$build" "$reason" >> "$GITHUB_OUTPUT"
{
  echo "### Nightly decision"
  echo "Source: $source_ref at $source_sha"
  echo "Last successful build: $previous_ref at $previous_sha"
  echo "Build: $build ($reason)"
  echo "This schedule checks daily; only the build job is skipped."
} >> "$GITHUB_STEP_SUMMARY"
