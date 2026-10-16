"""Provenance guard: scan changed content for prohibited artifacts (Issue #8).

A signature scanner is a tripwire, not a guarantee against infringement and not
a legal clearance. See docs/provenance.md. Rules, in order:
  1. sha256 in provenance/denylist.sha256 -> always fail (no allowlist bypass).
  2. Firmware/CD-image signatures in content -> always fail.
  3. Prohibited extensions, unallowlisted binaries, third-party paths, and
     third-party license markers in code files -> fail unless allowlisted.
Allowlist (provenance/allowlist.tsv: "<sha256>\\t<path>") is hash-pinned, so any
content change needs a new entry. Editing the allowlist, denylist or approver list
requires a verified maintainer approval (see pr_approval): the newest
provenance-approved label event must come from a login listed in the PR base's
provenance/approvers.txt, who is not the PR author and holds admin or maintain
role on the repository. The label is never trusted on its own, and the approval
only unlocks list edits; it never overrides rules 1-2. Editing this guard itself
is not gated here: human review is the control for that.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check import git_output, resolve_commit  # noqa: E402  (shared Git helpers)

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = "provenance/allowlist.tsv"
DENYLIST = "provenance/denylist.sha256"
APPROVERS = "provenance/approvers.txt"
GATED = {ALLOWLIST, DENYLIST, APPROVERS}
APPROVAL_LABEL = "provenance-approved"
APPROVER_ROLES = {"admin", "maintain"}
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

CHUNK_SIZE = 1 << 20  # bounded reads: large media is never loaded whole
OVERLAP = 64          # longer than every marker below, so chunk-spanning matches are still seen

PROHIBITED_EXTENSIONS = {".pce", ".hcd", ".bin", ".iso", ".img", ".cue", ".ccd", ".sub", ".toc",
                         ".chd", ".cdi", ".rom", ".wav", ".zip", ".7z", ".rar", ".tar", ".gz",
                         ".exe", ".dll", ".so", ".o", ".elf", ".a"}
THIRD_PARTY_DIRS = {"vendor", "third_party", "third-party", "external", "extern", "deps", "submodules"}
CODE_EXTENSIONS = {".s", ".asm", ".inc", ".c", ".h", ".cpp", ".cfg", ".py", ".sh", ".yml", ".yaml"}
# Signatures: ISO9660 volume descriptor (type 1, "CD001", version 1) and CD-ROM raw sector sync.
SIGNATURES = {
    "ISO9660 volume descriptor": re.compile(rb"\x01CD001\x01"),
    "CD-ROM raw sector sync": re.compile(rb"\x00\xff{10}\x00"),
}
# Marker text is split into adjacent literals so this file does not contain the text it scans for;
# tests/host/test_provenance_guard.py scans this file to keep that true.
LICENSE_MARKERS = re.compile(b"|".join(re.escape(text.encode()) for text in (
    "SPDX-" "License", "Copyright " "(c)", "GNU General " "Public License", "Permission is hereby " "granted")))


class ProvenanceViolation(Exception):
    pass


def load_allowlist(root):
    path = root / ALLOWLIST
    entries = set()
    if path.exists():
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if not line.strip() or line.startswith("#"):
                continue
            digest, _, rel = line.partition("\t")
            if not re.fullmatch(r"[0-9a-f]{64}", digest) or not rel:
                raise ProvenanceViolation(f"{ALLOWLIST}:{number}: expected '<sha256>\\t<path>'")
            entries.add((digest, rel))
    return entries


def load_denylist(root):
    path = root / DENYLIST
    digests = set()
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip() and not line.startswith("#"):
                digests.add(line.split()[0].lower())
    return digests


def changed_paths(repo, base):
    """Return the scan set: all tracked files without a base, else paths changed from base."""
    if base is None:
        output = git_output(repo, "ls-files", "-z")
    else:
        output = subprocess.run(["git", "diff", "--name-only", "-z", "--no-renames",
                                 "--diff-filter=ACMR", base, "--"], cwd=repo, check=True,
                                capture_output=True, text=True).stdout
    return sorted(p for p in output.split("\0") if p)


def resolve_base(repo, event_name="local", event=None, base=None):
    if event_name == "pull_request":
        sha = (event or {}).get("pull_request", {}).get("base", {}).get("sha")
        if not sha:
            raise ValueError("PR base SHA missing from event; cannot scope the provenance diff")
        return git_output(repo, "merge-base", resolve_commit(repo, sha), "HEAD")
    if event_name == "push":
        before = (event or {}).get("before")
        return None if not before or before == "0" * 40 else resolve_commit(repo, before)
    if base:
        return resolve_commit(repo, base)
    probe = subprocess.run(["git", "rev-parse", "--verify", "origin/main^{commit}"], cwd=repo,
                           capture_output=True, text=True)
    return git_output(repo, "merge-base", probe.stdout.strip(), "HEAD") if probe.returncode == 0 else None


def scan_stream(rel, stream, allowlist, denylist):
    """Return violation strings for one file, read in CHUNK_SIZE pieces without loading it whole."""
    digest = hashlib.sha256()
    head = b""          # first 8192 bytes, for the binary sniff
    tail = b""          # last OVERLAP bytes, so markers split across chunks are still matched
    found = set()
    licensed = False
    while chunk := stream.read(CHUNK_SIZE):
        digest.update(chunk)
        head = (head + chunk)[:8192]
        window = tail + chunk
        found.update(name for name, pattern in SIGNATURES.items() if pattern.search(window))
        licensed = licensed or LICENSE_MARKERS.search(window) is not None
        tail = window[-OVERLAP:]
    sha = digest.hexdigest()
    allowed = (sha, rel) in allowlist
    suffix = PurePosixPath(rel).suffix.lower()
    problems = []
    if sha in denylist:
        problems.append("sha256 is on the denylist")
    problems.extend(f"{name} signature" for name in SIGNATURES if name in found)
    if allowed:
        return problems
    if suffix in PROHIBITED_EXTENSIONS:
        problems.append(f"prohibited extension {suffix}")
    if b"\x00" in head:
        problems.append("unallowlisted binary content")
    parts = PurePosixPath(rel).parts
    if parts[0].lower() in THIRD_PARTY_DIRS or rel == ".gitmodules":
        problems.append("third-party path or submodule")
    if suffix in CODE_EXTENSIONS and licensed:
        problems.append("third-party license marker in code (needs audit + allowlist)")
    return problems


def load_approvers(repo, base):
    """Read the approver list from the PR base, never from the PR head: a PR cannot add its own approvers."""
    if not base:
        return set()
    result = subprocess.run(["git", "show", f"{base}:{APPROVERS}"], cwd=repo, capture_output=True, text=True)
    if result.returncode != 0:
        return set()
    return {line.strip() for line in result.stdout.splitlines() if line.strip() and not line.startswith("#")}


def approval_from_events(events, pr_author, approvers, role_of):
    """True only if the latest provenance-approved label event is a trusted maintainer's labeling.

    events: issue timeline events (oldest first). role_of(login) -> repository role name.
    """
    label_events = [e for e in events if e.get("event") in ("labeled", "unlabeled")
                    and (e.get("label") or {}).get("name") == APPROVAL_LABEL]
    if not label_events:
        return False
    latest = sorted(label_events, key=lambda e: e.get("created_at", ""))[-1]
    actor = (latest.get("actor") or {}).get("login", "")
    if latest["event"] != "labeled" or not actor or actor == pr_author or actor not in approvers:
        return False
    return role_of(actor) in APPROVER_ROLES


def github_api(*arguments):
    return subprocess.run(["gh", "api", *arguments], check=True, capture_output=True, text=True).stdout


def pr_approval(event_name, event, repo, base):
    """Query GitHub for a verified approval of the list edits. Any missing piece means no approval."""
    if event_name != "pull_request":
        return False
    pr = (event or {}).get("pull_request", {})
    slug = os.environ.get("GITHUB_REPOSITORY", "")
    number, author = pr.get("number"), (pr.get("user") or {}).get("login", "")
    approvers = load_approvers(repo, base)
    if not (slug and number and author and approvers):
        return False
    events = [json.loads(line) for line in
              github_api(f"repos/{slug}/issues/{number}/events", "--paginate", "--jq", ".[]").splitlines() if line]
    return approval_from_events(
        events, author, approvers,
        lambda login: github_api(f"repos/{slug}/collaborators/{login}/permission", "--jq", ".role_name").strip())


def check_repo(repo, base=None, approve=lambda: False):
    """Raise ProvenanceViolation listing every failing path; return the number of files scanned.

    approve() is consulted only when gated list files changed, so unrelated PRs never call the API.
    """
    allowlist = load_allowlist(repo)
    denylist = load_denylist(repo)
    paths = changed_paths(repo, base)
    gated = sorted(p for p in paths if p in GATED)
    failures = []
    if gated and not approve():
        failures.append(f"gated files changed without maintainer approval ({APPROVAL_LABEL} label from a trusted maintainer): {', '.join(gated)}")
    for rel in paths:
        file = repo / rel
        if not file.is_file():
            continue
        with file.open("rb") as stream:
            for problem in scan_stream(rel, stream, allowlist, denylist):
                failures.append(f"{rel}: {problem}")
    if failures:
        raise ProvenanceViolation("\n".join(failures))
    return len(paths)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", help="comparison ref when not running under GitHub Actions")
    args = parser.parse_args(argv)
    event_name = os.environ.get("GITHUB_EVENT_NAME", "local")
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    event = json.loads(Path(event_path).read_text()) if event_path else {}
    try:
        base = resolve_base(ROOT, event_name, event, args.base or os.environ.get("CHECK_BASE"))
        count = check_repo(ROOT, base, approve=lambda: pr_approval(event_name, event, ROOT, base))
    except (ProvenanceViolation, ValueError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"provenance-guard FAIL: {error}") from error
    print(f"PASS provenance guard: {count} files scanned against {base or 'all tracked files'}")


if __name__ == "__main__":
    main()
