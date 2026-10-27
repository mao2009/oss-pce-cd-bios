"""Issue #544 (child of #511): the trusted-main policy check for run: approvals.

Run from the MAIN copy (--trusted), reading the pull-request copy (--candidate) as data only. Nothing from the candidate is
executed. The policy job (.github/workflows/policy-guard.yml) runs this file from the base SHA of the pull request.

Checks, all against the trusted copy:
  1. policy_files_match: the guard, the run allowlist, this file, the workflows, the actions and the policy workflow are
     byte-identical to the trusted copy. A pull request cannot loosen them.
  2. approval_problems: the trusted guard runs its enforced placement check on the candidate workflows with the TRUSTED
     allowlist. Any run: text that is not approved on main is refused, including a new send command.
  3. script_hash_problems: a script that an approved run: text names (a path under tools/, tests/ or .github/) must be
     byte-identical to the trusted copy, so changing a called script cannot evade the check.
  4. check_record_problems: the GitHub check result for the head SHA. Used by the ruleset verification (see the child issue):
     a missing, renamed, skipped, reused (other head) or foreign-app result is refused.

Static matching cannot prevent network sends from scripts that approved steps call. That is out of guaranteed scope and
rests on least privilege, secret separation and trusted-publishing job separation.
"""
from pathlib import Path
import hashlib
import importlib.util
import re
import sys

POLICY_PATHS = (".github/workflows", ".github/actions", "tools/artifact_guard.py", "tools/artifact_guard_run_allowlist.json",
                "tools/policy_guard.py")
POLICY_WORKFLOW = ".github/workflows/policy-guard.yml"
REQUIRED_CHECK = "policy-guard"
SCRIPT_RE = re.compile(r"(?<![\w./-])((?:tools|tests|\.github)/[\w./-]+)")


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree(base, rel):
    """{relative path: sha256} of one file or of every file under one directory of base."""
    top = Path(base) / rel
    if top.is_file():
        return {rel: _sha(top)}
    if not top.is_dir():
        return {}
    return {path.relative_to(base).as_posix(): _sha(path) for path in sorted(top.rglob("*")) if path.is_file()}


def policy_files_match(candidate, trusted):
    """Problems for every policy path whose files differ from the trusted copy (added, removed or changed)."""
    problems = []
    for rel in POLICY_PATHS:
        mine, theirs = _tree(candidate, rel), _tree(trusted, rel)
        for name in sorted(set(mine) | set(theirs)):
            if mine.get(name) != theirs.get(name):
                problems.append(f"{name} differs from the trusted main copy: a policy change needs trusted-main approval "
                                "(Issue #544)")
    if _tree(trusted, POLICY_WORKFLOW) and not _tree(candidate, POLICY_WORKFLOW):
        problems.append(f"{POLICY_WORKFLOW} is missing: the policy check cannot be removed (Issue #544)")
    return problems


def _trusted_guard(trusted):
    spec = importlib.util.spec_from_file_location("trusted_artifact_guard", Path(trusted) / "tools" / "artifact_guard.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def approval_problems(candidate, trusted):
    """The trusted guard's enforced placement check on the candidate, with the TRUSTED approval table."""
    guard = _trusted_guard(trusted)
    try:
        guard._RUN_ALLOWLIST = guard.load_run_allowlist(trusted)
        guard.check_guard_placement(candidate)
    except ValueError as error:
        return [str(error)]
    finally:
        guard._RUN_ALLOWLIST = None
    return []


def _trusted_run_texts(trusted):
    """Every run: text in the trusted workflows and actions."""
    import yaml
    texts = []
    for path in sorted((Path(trusted) / ".github").rglob("*.yml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        bodies = list((doc.get("jobs") or {}).values()) if isinstance(doc, dict) else []
        if isinstance(doc, dict) and isinstance(doc.get("runs"), dict):
            bodies.append(doc["runs"])
        for body in bodies:
            for step in (body.get("steps") or []) if isinstance(body, dict) else []:
                if isinstance(step, dict) and isinstance(step.get("run"), str):
                    texts.append(step["run"])
    return texts


def script_hash_problems(candidate, trusted):
    """A script named by a trusted run: text must be byte-identical in the candidate."""
    problems, seen = [], set()
    for text in _trusted_run_texts(trusted):
        for rel in SCRIPT_RE.findall(text):
            rel = rel.rstrip(".,;:")
            if rel in seen or not (Path(trusted) / rel).is_file():
                continue
            seen.add(rel)
            if not (Path(candidate) / rel).is_file() or _sha(Path(candidate) / rel) != _sha(Path(trusted) / rel):
                problems.append(f"{rel} is called by an approved run: step and differs from the trusted copy (refused; "
                                "Issue #544)")
    return problems


def check_record_problems(record, head_sha, expected_app_id):
    """The required check result, as the ruleset must see it: the same name, the same head SHA, the expected app, success."""
    if not record:
        return ["no policy-guard result for this head: missing, removed or skipped (refused; Issue #544)"]
    if record.get("name") != REQUIRED_CHECK:
        return [f"check {record.get('name')!r} is not {REQUIRED_CHECK!r} (refused; Issue #544)"]
    if record.get("head_sha") != head_sha:
        return ["the result is for another head SHA (reused result, refused; Issue #544)"]
    if record.get("app_id") != expected_app_id:
        return ["the result comes from another app (fake job, refused; Issue #544)"]
    if record.get("conclusion") != "success":
        return [f"conclusion is {record.get('conclusion')!r}, not success (refused; Issue #544)"]
    return []


def check_candidate(candidate, trusted):
    """All candidate checks against the trusted copy. Returns the list of problems (empty when the candidate passes)."""
    return policy_files_match(candidate, trusted) + approval_problems(candidate, trusted) + script_hash_problems(candidate, trusted)


def main(argv):
    if len(argv) != 4 or argv[0] != "--candidate" or argv[2] != "--trusted":
        raise ValueError("usage: policy_guard.py --candidate DIR --trusted DIR")
    problems = check_candidate(Path(argv[1]), Path(argv[3]))
    if problems:
        raise ValueError("policy guard: " + "; ".join(problems))
    print("PASS policy guard: candidate matches the trusted main policy, approvals and called scripts")


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except ValueError as error:
        raise SystemExit(str(error)) from error
