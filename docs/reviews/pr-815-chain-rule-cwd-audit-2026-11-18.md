# PR #815 — Chain rule / CWD scope focused code review (2026-11-18)

## Review baseline

- Target PR: https://github.com/mao2009/oss-pce-cd-bios/pull/815
- Reviewed HEAD: `5cef52e68b5d21d7d91c3ac7d18329b6e3368ba6`
- PR base SHA observed: `9ddb461b32f8ce639b37a6c93f6d31b42c5aa133`
- Issue: #636 (OPEN; no merge/close authorization)
- Review verdict: **NOT MERGE_READY / CHANGES_REQUESTED**
- Method: remote source inspection, review threads, GitHub Actions status; GNU Bash and Git reference documentation; harmless Bash `pwd` verification in a temporary directory. Guard `decide()` was **not executed**; all predicted ALLOW decisions require targeted decision-level RED verification on the reviewed HEAD.
- GitHub Actions: four PR-triggered workflows reported success at the reviewed HEAD. 14 inline review threads; two unresolved (listed below).
- Only this documentation is changed in the accompanying review PR; no code, tests, ROM, commercial data, or original #815 branch altered.

## CRITICAL 1 — `eval` CWD changes are discarded

**Code:** `.claude/hooks/guard_destructive.py` lines 214–219 (`_nested_text`), 1966–1967 (`eval`), 1972–1977 (`cd`), 2011–2049 (segment processing).

`eval` executes its command in the *calling shell* and can change its current directory. Here `_nested_text` analyzes the evaluated text in a copy of `Ctx` and merges back only `tainted`. A nested `cd` therefore changes the copy's `cwd` but not the parent. Later `rm` is judged against the old CWD, while the real shell executes it against the new CWD.

**Decision-only RED fixture (never execute a delete command):** make a temporary `repo/` with `repo/file.txt`, and a file with the same basename in its parent. Use the repo as `cwd` and send `eval 'cd ..' && rm file.txt` as text to `guard.decide`. The expected current-path defect is ALLOW: the guard sees `repo/file.txt`; the real shell would resolve the target at `../file.txt`, outside the repository. Desired outcome DENY, ideally `rm-outside-repo` or a dedicated conservative `scope` verdict. Confirm actual current verdict before modifying code.

**Repair:** separate current-shell execution (`eval`, relevant sourcing/brace semantics) from true subprocess/subshell analysis; merge CWD changes when shell semantics require it. Keep uncertainty fail-closed. Verify regular `eval 'cd sub' && rm inner.txt` is analyzed in the new cwd.

## CRITICAL 2 — parenthesized subshell `cd` leaks into parent CWD

**Code:** `split_segments` lines 609–731, specifically lines 719–724; `analyze_text` lines 2011–2052.

Bash `( list )` runs in a subshell. `split_segments` drops the parentheses and `analyze_text` analyzes every segment against one mutable `Ctx`. The `cd` inside parentheses therefore updates the parent's `cwd`, even though a real Bash subshell does not.

**Decision-only RED fixture:** in a temporary repo with existing `repo/file.txt`, `repo/sub/`, and an existing `../file.txt` outside the repo, pass `(cd sub) && rm ../file.txt` to `guard.decide` with cwd `repo`. In the predicted defect, guard resolves `../file.txt` relative to `repo/sub/` (the in-repo file) and ALLOWs, whereas Bash keeps its parent cwd at `repo/` and `rm ../file.txt` would address the out-of-repo file. No actual deletion is necessary or permitted in this regression test.

**Repair:** treat `(...)` as a scoped subshell; restore parent cwd at exit while conservatively merging filesystem-change taint, since filesystem changes performed by the child can outlive the subshell. Preserve nested grouping, pipelines and command substitutions. Do not conflate `{ ...; }` (current-shell grouping) with `(...)` (subshell).

**Shell-semantics verification:** isolated harmless `pwd` run from `repo/`: `eval 'cd sub' && pwd` printed `repo/sub`, whereas `(cd sub) && pwd` printed `repo`. Reference: GNU Bash Command Grouping https://www.gnu.org/s/bash/manual/html_node/Command-Grouping.html and Bourne Shell Builtins https://www.gnu.org/software/bash/manual/html_node/Bourne-Shell-Builtins.html. This is not a guard verdict test.

**Scope note:** the underlying scoping weaknesses may predate PR #815, but they directly contradict #636's promise that tracked cwd changes preserve path-judgment correctness, so they block merge readiness of this safety fix.

## MAJOR 3 — `git worktree move` is omitted as a chain taint producer

**Code:** `git_rule`, lines 873–899. The code marks `worktree add` as `rewrite`, but not `worktree move`. The move operation changes directory paths on the filesystem and can invalidate hook-time path judgments used by later commands.

**Decision-only regression:** `git worktree move from-wt to-wt && rm file.txt` with an existing in-repo `file.txt` should not pass the chain rule solely because the subcommand is `move`. Check actual baseline verdict and original guard policy on moving worktrees, then either DENY the move operation (possibly preferable under other-worktree safety rules) or conservatively classify it as a taint producer. This is a *static gap*, not proof of a reproduced malicious path replacement.

Reference: https://git-scm.com/docs/git-worktree .

## Existing unresolved CodeRabbit MINOR threads

1. `PRRT_kwDOVA3gN8644_pb`: false taint from an impossible named coprocess parse. The current code at lines 1854–1866 analyzes the suffix on a disposable `ctx.copy()`, and the new `test_coproc_simple_command_has_no_name` expects ALLOW. This appears addressed in current HEAD; verify the test and reply/resolve rather than reopening the same finding without evidence.
2. `PRRT_kwDOVA3gN8644_pq`: `ChainRuleMutationTests.test_chain_tests_detect_each_mutant` lines 4301–4306 considers a test `ERROR:` as detection; a fixture exception could pass instead of an expected assertion failure. Still visible in reviewed source. Require named expected `FAIL:` assertions, reject `ERROR:`, and check the return code. This is a test-quality MINOR.

## Required closure evidence

1. Add precise decision-level RED tests for CRITICAL 1 and CRITICAL 2 with temporary files only; never execute `rm` or worktree mutation to reproduce a guard verdict.
2. Fix scope semantics and verify RED→GREEN including `eval`, parentheses, braces, nested subshells, `sh -c`, command substitutions, and both ALLOW and DENY controls. Confirm cwd and filesystem-taint inheritance independently.
3. Add mutation tests for lost eval cwd propagation and leaking subshell cwd, and for `worktree move` classification if accepted. Tests must fail on behavior changes, not import/runner failures.
4. Rerun guard/full host tests, `make check`, page checks, Required CI at final HEAD. Report NOT_RUN honestly.
5. Resolve the still-applicable CodeRabbit MINOR and verify all threads settled; update PR body with actual current state and test counts.
6. Do not merge #815, close #636, alter the main branch, or touch `./rom`. Owner authorization is required.

**Verdict:** `CHANGES_REQUESTED`, not merge-ready. This review document is not an implementation or an authorization to merge.
