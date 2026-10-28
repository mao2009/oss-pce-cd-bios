"""Static checks using actual pinned actionlint and assembler."""
import ast
import json
import os
import sys
import tempfile
from pathlib import Path
import subprocess

from artifact_guard import check_upload_artifacts, check_guard_placement, enforced_check
from environment import ROOT, executable, core_path

TRUSTED_ALLOWLIST = "tools/artifact_guard_run_allowlist.json"
TRUSTED_GUARD = "tools/artifact_guard.py"


def git_output(repository, *arguments):
    return subprocess.run(["git", *arguments], cwd=repository, check=True,
                          capture_output=True, text=True).stdout.strip()


def resolve_commit(repository, ref):
    try:
        return git_output(repository, "rev-parse", "--verify", f"{ref}^{{commit}}")
    except subprocess.CalledProcessError as error:
        raise ValueError(f"cannot resolve Git comparison commit: {ref}") from error


def resolve_trusted_allowlist_root():
    """Return a temp directory holding the trusted run-allowlist table from origin/main, or None.

    The allowlist must never be read from the checked tree: a pull request could add its own
    run: texts and self-approve. When origin/main is unavailable (offline local run), None is
    returned and the caller falls back to the local tree with a clear warning. GitHub Actions
    always runs the guard from .trusted-main, so CI is protected regardless.
    """
    try:
        data = git_output(ROOT, "show", f"origin/main:{TRUSTED_ALLOWLIST}")
    except (ValueError, subprocess.CalledProcessError):
        return None
    tmp = Path(tempfile.mkdtemp(prefix="trusted-allowlist-"))
    (tmp / TRUSTED_ALLOWLIST.split("/")[-1]).write_text(data, encoding="utf-8")
    return tmp


def guard_matches_trusted():
    """Refuse when the local guard differs from origin/main's guard (Issue #511).

    A pull request that replaces tools/artifact_guard.py with a weakened copy could disable
    enforced_check entirely. The local copy must be byte-identical to the trusted main copy;
    otherwise only trusted-main policy may change it. This is enforced under GitHub Actions
    (pull_request/push), where the guard runs from .trusted-main and origin/main is the trust
    anchor. Locally (GITHUB_EVENT_NAME unset), only a warning is emitted: a developer who
    intentionally changes the guard must route it through trusted-main policy before CI passes."""
    try:
        trusted = git_output(ROOT, "show", f"origin/main:{TRUSTED_GUARD}")
    except (ValueError, subprocess.CalledProcessError):
        return   # origin/main unavailable: local-only boundary, skip
    local = ROOT / TRUSTED_GUARD
    if not local.is_file():
        return   # nothing to compare locally
    event_name = os.environ.get("GITHUB_EVENT_NAME", "local")
    if local.read_text(encoding="utf-8") != trusted:
        message = (
            f"{TRUSTED_GUARD} differs from origin/main: the guard and its run-allowlist table "
            "may only be changed through trusted-main policy, not by this pull request (Issue #511)")
        if event_name in ("pull_request", "push"):
            raise ValueError(message)
        print(f"WARNING: {message}", file=sys.stderr)


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
    # Fail closed before upload: no linked GPL probe executable or object may reach upload-artifact (#227).
    check_upload_artifacts(ROOT)
    # The local guard must be byte-identical to the trusted main copy: a pull request cannot
    # swap in a weakened guard to disable the run: approval (Issue #511).
    guard_matches_trusted()
    # The guard must sit immediately before each upload in the same job (no build step in between).
    # Run with the trusted allowlist so every run: text must be approved. The allowlist is read
    # from origin/main, never from the checked tree, so a pull request cannot self-approve its
    # own run: text (Issue #511). When origin/main is unavailable (offline local run), the local
    # tree is the trust root: local make check trusts the local tree, CI runs the guard from
    # .trusted-main and is protected regardless.
    trusted_root = resolve_trusted_allowlist_root()
    allowlist_root = trusted_root if trusted_root is not None else ROOT
    if trusted_root is None:
        print("WARNING: origin/main unavailable; local check trusts the local tree. "
              "CI runs the guard from .trusted-main and is protected regardless.",
              file=sys.stderr)
    try:
        enforced_check(ROOT, check_guard_placement, allowlist_root=allowlist_root)
    finally:
        if trusted_root is not None:
            import shutil
            shutil.rmtree(trusted_root, ignore_errors=True)
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
