# PR #814 — stdin-program guard focused code review (2026-11-18)

- Target: [PR #814](https://github.com/mao2009/oss-pce-cd-bios/pull/814)
- Reviewed HEAD: `93b41de4dc2a93c4454e56585f358585fb423dac`
- Base observed: `fa694281eb4eec4b04beca14278635cb5ca0902a`
- Issue: [#637](https://github.com/mao2009/oss-pce-cd-bios/issues/637) (must remain OPEN pending Owner)
- Outcome: **CHANGES_REQUESTED / NOT MERGE_READY**. Two MAJOR-level stdin-as-program analysis gaps are identified below.
- Review scope: direct inspection of guard implementation and `StdinProgramTests` / mutant coverage; harmless shell-semantics checks. The original guard test suite was **not rerun** in this review environment, so ALLOW decisions below are static code-path findings awaiting decision-level RED confirmation.
- Changes in this documentation PR: review document only. No guard, tests, ROM, ABI, or existing PR #814 branch modifications.

## MAJOR-1 — pipe-fed stdin escapes through a user-defined shell function

**Relevant code:** `.claude/hooks/guard_destructive.py` lines 538–824 (`split_segments`), 2216–2293 (`stdin_program_refusal`), 2550–2730 (`analyze_argv`), 2740–2795 (`analyze_text`).

**Harmless reproducer:**

```bash
f() { node; }
printf 'console.log(123)\n' | f
```

The function body `node` occurs in an *unfed* definition. At call time, `f` is a separate pipe-fed segment, but the guard does not associate it with the previously defined `node` body. `stdin_program_refusal` matches interpreter names in the current segment; `analyze_argv` accepts unknown no-argument commands. The guard is therefore expected to ALLOW even though the real shell runs `node` with script code on stdin. An analogous redirection form is `f() { node; }; f < code.js`.

**Expected regression (decision-level, never run user-provided code in the test):** `guard.decide(...)` must return `DENY` for the pipe and input-redirection function invocations; use a dedicated stdin/unknown-function classification rather than an accidental generic refusal. Safe controls must remain ALLOW: `f() { node script.js; }; printf data | f`, `f() { cat; }; printf data | f`, and unpiped `f() { node; }; f` if no code reaches stdin.

**Repair options:** Track shell function definitions and conservative calls with stdin provenance and lexical scope, or fail-closed on pipe-fed/redirected calls to locally defined functions whose bodies may execute stdin-as-program interpreters. Do not infer from the untainted definition alone that a later invocation is safe.

## MAJOR-2 — stdin redirection is dropped when analyzing nested `sh -c` / `bash -c`

**Relevant code:** `.claude/hooks/guard_destructive.py` lines 626–654 (redirection marker; only output redirects saved in `rd`), 2193–2213 (`_has_input_redirect`), 2313–2323 (`shell_rule`), and 2740–2795 (`analyze_text`).

**Harmless reproducer:**

```bash
sh -c 'if true; then node; fi' < code.js
# Equivalently: sh -c 'if true; then node; fi' <<< 'console.log(456)'
```

The outer shell has an explicit stdin input redirection, but `shell_rule` forwards only the `fed` (pipe) boolean. The child `analyze_text` therefore sees `node` with `inherited_fed=False` and *no* redirection. The outer `stdin_program_refusal` does not reliably identify `node` inside a quoted multi-word shell program (`node;` is not an exact interpreter word), so the outer and inner checks can both miss this form.

**Expected regression (decision-level):** DENY on the two reproducers and on `bash -c 'if true; then python3; fi' < code.py`; retain ALLOW for `sh -c 'node script.js' < data.txt` (stdin only data) and `sh -c 'echo ok' < data.txt`.

**Repair:** Track stdin-origin as a first-class property for both pipe and input redirection; propagate it through shell-string execution, nested wrappers and relevant subprocess paths. A parent-local redirection should not be converted into a global taint for independent top-level commands. Continue to distinguish stdin-as-code from stdin-as-data. Mutation tests should remove redirect inheritance to show RED detection.

## Evidence and limitations

1. Source on reviewed HEAD: `split_segments` and `analyze_text` propagate the `piped` / `fed_list` state but do not track called shell functions or pass input-redirection provenance to `shell_rule`.
2. `StdinProgramTests` exercises direct interpreter invocations, pipe-fed compound constructs, nested `sh -c` with *pipe-fed* input, and command substitutions. It does not contain the two reproducer classes above.
3. Shell execution semantics were confirmed with harmless `node` examples in a local Linux Bash test: the function-pipe form printed `123`; the nested-shell here-string form printed `456`. This confirms code is delivered over stdin, **not** the actual guard verdict.
4. No exact `guard.decide` execution was available in this reviewing environment. Treat the ALLOW prediction as a high-confidence static finding, not a recorded RED result. Add targeted RED→GREEN tests on the PR branch before accepting.
5. Previously reported guard/host CI results and 17 existing mutants do not test these missing provenance edges. They are not evidence that the findings are covered.
6. `case` / compound over-taint, depth fail-closed and Windows/PowerShell runtime verification remain separate limitations. Do not interpret this review as asserting a general security boundary: the hook is best effort.

## Required follow-up for PR #814

- Add targeted DENY tests and safe ALLOW controls for both findings; prove baseline RED on the reviewed HEAD and GREEN after the fix.
- Add mutation tests for function-call taint and input-redirection inheritance, including independent-command over-taint controls.
- Re-run relevant guard tests, full host suite, `make check`, and required CI on the new PR HEAD; report actual results.
- Keep PR #814 unmerged and Issue #637 OPEN until explicit Owner authorization. Do not trigger CodeRabbit merely to produce this audit.
- Correct the PR body: it currently reflects outdated Draft/test counts and `Closes #637` semantics; use `Refs #637` if the issue must remain open after merge.

## Record

This document is a review-only artifact and must not be mistaken for a code fix or merge approval.
