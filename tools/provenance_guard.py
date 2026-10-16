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
role on the repository. Deleting or renaming away a gated file counts as a change
to it. While the base has no approver list (bootstrap), the list the PR adds is
used instead, limited to non-author admins. An empty list at the base is closed.
The label is never trusted on its own, and the approval only unlocks list edits;
it never overrides rules 1-2. Editing this guard itself is not gated here: human
review is the control for that.
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


def changed_entries(repo, base):
    """Return sorted (status, path) pairs: every tracked file as 'M' without a base, else changes from base.

    All statuses are kept (A, M, D, T, ...). Renames are not detected (--no-renames), so a rename
    appears as D of the old path plus A of the new one: a protected old path is gated like a deletion.
    """
    if base is None:
        return sorted(("M", p) for p in git_output(repo, "ls-files", "-z").split("\0") if p)
    output = subprocess.run(["git", "diff", "--name-status", "-z", "--no-renames", base, "--"],
                            cwd=repo, check=True, capture_output=True, text=True).stdout
    fields = [field for field in output.split("\0") if field]
    return sorted(zip(fields[0::2], fields[1::2]))


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


def approvers_at(repo, ref):
    """Logins listed in ref's approver file, or None when the file does not exist at ref."""
    result = subprocess.run(["git", "show", f"{ref}:{APPROVERS}"], cwd=repo, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return {line.strip() for line in result.stdout.splitlines() if line.strip() and not line.startswith("#")}


def trusted_approvers(repo, base, author, role_of):
    """Logins whose label may approve list edits. A PR cannot add itself to the trusted list.

    With a list at the PR base, that list alone counts (an empty list stays closed). Bootstrap, only
    while the base has no list: the list this PR adds, minus the author, limited to repository admins.
    """
    listed = approvers_at(repo, base)
    if listed is not None:
        return listed
    proposed = approvers_at(repo, "HEAD") or set()
    return {login for login in proposed if login != author and role_of(login) == "admin"}


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
    if not (slug and number and author):
        return False

    def role_of(login):
        try:
            return github_api(f"repos/{slug}/collaborators/{login}/permission", "--jq", ".role_name").strip()
        except subprocess.CalledProcessError:
            return ""  # not a collaborator: no role, so no approval

    approvers = trusted_approvers(repo, base, author, role_of)
    if not approvers:
        return False
    events = [json.loads(line) for line in
              github_api(f"repos/{slug}/issues/{number}/events", "--paginate", "--jq", ".[]").splitlines() if line]
    return approval_from_events(events, author, approvers, role_of)


def check_repo(repo, base=None, approve=lambda: False):
    """Raise ProvenanceViolation listing every failing path; return the number of files scanned.

    approve() is consulted only when gated list files changed, so unrelated PRs never call the API.
    """
    allowlist = load_allowlist(repo)
    denylist = load_denylist(repo)
    entries = changed_entries(repo, base)
    paths = sorted({rel for _, rel in entries})
    gated = sorted(p for p in paths if p in GATED)
    failures = []
    if gated and not approve():
        failures.append(f"gated files changed without maintainer approval ({APPROVAL_LABEL} label from a trusted maintainer): {', '.join(gated)}")
    for status, rel in entries:
        if status == "D":  # deleted: never opened; a protected path is already gated above
            continue
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
