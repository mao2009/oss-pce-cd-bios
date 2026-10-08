"""Static checks using actual pinned actionlint and assembler."""
import ast
import json
import os
from pathlib import Path
import subprocess

from environment import ROOT, executable, core_path


def git_output(repository, *arguments):
    return subprocess.run(["git", *arguments], cwd=repository, check=True,
                          capture_output=True, text=True).stdout.strip()


def resolve_commit(repository, ref):
    try:
        return git_output(repository, "rev-parse", "--verify", f"{ref}^{{commit}}")
    except subprocess.CalledProcessError as error:
        raise ValueError(f"cannot resolve Git comparison commit: {ref}") from error


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
