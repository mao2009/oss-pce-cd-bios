"""Static checks using actual pinned actionlint and assembler."""
import ast
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from environment import ROOT, executable, core_path

# The guard and its run allowlist are read from origin/main, never from the checked tree (Issue #511).
TRUSTED_REF = "origin/main"
TRUST_FILES = ("tools/artifact_guard.py", "tools/artifact_guard_run_allowlist.json")
# Local development only: trust the checked tree when origin/main is unavailable. Refused under CI.
LOCAL_TRUST_OPT_IN = "OSS_PCE_DEV_TRUST_LOCAL"


def git_output(repository, *arguments):
    return subprocess.run(["git", *arguments], cwd=repository, check=True,
                          capture_output=True, text=True).stdout.strip()


def resolve_commit(repository, ref):
    try:
        return git_output(repository, "rev-parse", "--verify", f"{ref}^{{commit}}")
    except subprocess.CalledProcessError as error:
        raise ValueError(f"cannot resolve Git comparison commit: {ref}") from error


def in_ci():
    return os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true"


@contextlib.contextmanager
def trusted_root(repository=ROOT):
    """Yield a directory holding TRUST_FILES as committed on origin/main, at the same relative paths.

    Fails closed: a missing ref or file raises. Only LOCAL_TRUST_OPT_IN=1 outside CI trusts the checked tree
    instead, and only when origin/main itself is unavailable."""
    if os.environ.get(LOCAL_TRUST_OPT_IN) == "1":
        if in_ci():
            raise ValueError(f"{LOCAL_TRUST_OPT_IN}=1 is refused under CI (fails closed)")
        probe = subprocess.run(["git", "rev-parse", "--verify", f"{TRUSTED_REF}^{{commit}}"],
                               cwd=repository, capture_output=True)
        if probe.returncode != 0:
            print(f"WARNING: {TRUSTED_REF} unavailable; local development trusts the checked tree "
                  f"({LOCAL_TRUST_OPT_IN}=1). CI runs the guard from .trusted-main and is protected regardless.",
                  file=sys.stderr)
            yield repository
            return
    try:
        resolve_commit(repository, TRUSTED_REF)
    except ValueError as error:
        raise ValueError(f"{TRUSTED_REF} is unavailable, so the trusted guard and allowlist cannot be read "
                         "(fails closed); run git fetch origin main") from error
    # Only this context owns the temporary trust snapshot. The standard library manages its cleanup.
    with tempfile.TemporaryDirectory(prefix="trusted-main-") as directory:
        tmp = Path(directory)
        for rel in TRUST_FILES:
            try:
                data = subprocess.run(["git", "cat-file", "blob", f"{TRUSTED_REF}:{rel}"], cwd=repository,
                                      check=True, capture_output=True).stdout
            except subprocess.CalledProcessError as error:
                raise ValueError(f"{TRUSTED_REF}:{rel} is missing (fails closed)") from error
            (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp / rel).write_bytes(data)
        yield tmp


def guard_matches_trusted(trusted, root=ROOT):
    """Refuse a checked guard that differs from the trusted copy, so a pull request cannot weaken enforced_check.

    Compared as Git blobs: a Windows checkout with core.autocrlf converts line endings, so raw bytes would differ."""
    rel = "tools/artifact_guard.py"
    if Path(trusted).resolve() == Path(root).resolve():
        return   # dev opt-in (see trusted_root): the checked tree is the trust root, nothing to compare
    checked = git_output(root, "hash-object", f"--path={rel}", "--", rel)
    expected = git_output(root, "hash-object", "--no-filters", "--", str(Path(trusted) / rel))
    if checked != expected:
        raise ValueError(f"{rel} differs from {TRUSTED_REF}: the guard may only change through trusted-main policy "
                         "(Issue #511)")


def enforce_guard(root=ROOT):
    """Execute the committed trusted guard in a separate isolated interpreter.

    The PR's artifact_guard.py is not imported, and the trusted guard reads its
    own approved table. Candidate files are passed as data. This is a local
    defense-in-depth check: the actual security boundary is the trusted guard
    executed independently by the guard-verify CI job.
    """
    with trusted_root(root) as trusted:
        guard_matches_trusted(trusted, root)
        trusted_script = Path(trusted) / "tools" / "artifact_guard.py"
        if trusted_script.is_symlink() or not trusted_script.is_file():
            raise ValueError("trusted artifact guard is missing or linked (fails closed)")
        # -I ignores PYTHONPATH and excludes candidate checkout paths from imports.
        # The guard's default CLI runs both upload and approved-placement checks.
        result = subprocess.run([sys.executable, "-I", str(trusted_script),
                                 "--root", str(Path(root).resolve())],
                                cwd=trusted, capture_output=True, text=True)
        if result.returncode != 0:
            reason = (result.stderr or result.stdout).strip()
            raise ValueError(f"trusted artifact guard refused (exit {result.returncode}): {reason}")
        if result.stdout:
            print(result.stdout, end="")


def check_git_diff(repository, event_name="local", event=None, base=None):
    """Check committed event changes as well as staged and unstaged changes."""
    if git_output(repository, "rev-parse", "--is-shallow-repository") == "true":
        raise ValueError("shallow Git history cannot establish the comparison; checkout fetch-depth: 0")
    head = resolve_commit(repository, "HEAD")
    event = event or {}
    if event_name == "pull_request":
        base = event.get("pull_request", {}).get("base", {}).get("sha")
        if not base:
            raise ValueError("PR base SHA missing from event; cannot check committed diff")
        base = resolve_commit(repository, base)
        try:
            base = git_output(repository, "merge-base", base, head)
        except subprocess.CalledProcessError as error:
            raise ValueError("PR merge base cannot be resolved") from error
    elif event_name == "push":
        if resolve_commit(repository, event.get("after", "missing-push-after")) != head:
            raise ValueError("push event SHA does not match checked-out HEAD")
        base = event.get("before")
        if not base or base == "0" * 40:
            raise ValueError("push comparison base unavailable; refusing an incomplete diff check")
    elif base is None:
        # Locally prefer the feature branch's complete merge-base diff.
        probe = subprocess.run(["git", "rev-parse", "--verify", "origin/main^{commit}"],
                               cwd=repository, capture_output=True, text=True)
        if probe.returncode == 0:
            base = git_output(repository, "merge-base", probe.stdout.strip(), head)
        else:
            parents = git_output(repository, "rev-list", "--parents", "-n", "1", head).split()
            base = parents[1] if len(parents) > 1 else None
    if base is None:
        committed = ["diff-tree", "--root", "--check", "-r", head]
    else:
        base = resolve_commit(repository, base)
        committed = ["diff", "--check", base, head]
    for arguments in (committed, ["diff", "--check"], ["diff", "--cached", "--check"]):
        subprocess.run(["git", *arguments], cwd=repository, check=True)
    print(f"PASS Git committed diff: {base or 'root'} -> {head}, staged and unstaged changes")


def main():
    for path in sorted(ROOT.rglob("*.py")):
        if not any(part.startswith(".") for part in path.relative_to(ROOT).parts):
            ast.parse(path.read_text(), filename=str(path))
    for path in [*ROOT.glob("scripts/*.sh"), *ROOT.glob("tools/*.sh")]:
        subprocess.run(["bash", "-n", str(path)], check=True)
    # actionlint parses YAML and checks expressions, workflow schema and job wiring.
    subprocess.run([executable("actionlint"), "-shellcheck=", "-pyflakes=",
                    *map(str, sorted(ROOT.glob(".github/workflows/*.yml")))], check=True)
    # Run both the upload and placement checks from the trusted guard in an
    # isolated interpreter; do not execute any candidate artifact_guard code.
    enforce_guard(ROOT)
    core_path()
    output = ROOT / "build/check"
    output.mkdir(parents=True, exist_ok=True)
    subprocess.run([executable("ca65"), "--cpu", "huc6280", "-o", str(output / "opcodes.o"),
                    str(ROOT / "tests/fixtures/opcodes.s")], check=True)
    event_name = os.environ.get("GITHUB_EVENT_NAME", "local")
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if event_name in ("pull_request", "push") and not event_path:
        raise ValueError("GitHub event payload unavailable; cannot resolve comparison")
    event = json.loads(Path(event_path).read_text()) if event_path else {}
    check_git_diff(ROOT, event_name, event, os.environ.get("CHECK_BASE"))
    print("PASS Python/shell syntax, Actions schema/expressions, HuC6280 syntax, tool detection, whitespace")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, SyntaxError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"check: {error}") from error
