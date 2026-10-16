"""Provenance guard: scan changed content for prohibited artifacts (Issue #8).

A signature scanner is a tripwire, not a guarantee against infringement and not
a legal clearance. See docs/provenance.md. Rules, in order:
  1. sha256 in provenance/denylist.sha256 -> always fail (no allowlist bypass).
  2. Firmware/CD-image signatures in content -> always fail.
  3. Prohibited extensions, unallowlisted binaries, third-party paths, and
     third-party license markers in code files -> fail unless allowlisted.
Allowlist (provenance/allowlist.tsv: "<sha256>\t<path>") is hash-pinned, so any
content change needs a new entry. Editing the allowlist or denylist requires
PROVENANCE_APPROVAL=granted, which CI derives from a maintainer-applied label.
That gate only unlocks list edits; it never overrides rules 1-2. Editing this
guard itself is not gated here: human review is the control for that.
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
GATED = {ALLOWLIST, DENYLIST}
APPROVAL_ENV = "PROVENANCE_APPROVAL"
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

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
# Written with escapes so this file does not contain the marker strings it searches for.
LICENSE_MARKERS = re.compile(r"SPDX\-License|Copyright \(c\)|GNU General Public License|Permission is hereby granted")


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


def scan_file(rel, content, allowlist, denylist):
    """Return a list of violation strings for one file's path and bytes."""
    problems = []
    digest = hashlib.sha256(content).hexdigest()
    allowed = (digest, rel) in allowlist
    suffix = PurePosixPath(rel).suffix.lower()
    if digest in denylist:
        problems.append("sha256 is on the denylist")
    for name, pattern in SIGNATURES.items():
        if pattern.search(content):
            problems.append(f"{name} signature")
    if allowed:
        return problems
    if suffix in PROHIBITED_EXTENSIONS:
        problems.append(f"prohibited extension {suffix}")
    if b"\x00" in content[:8192]:
        problems.append("unallowlisted binary content")
    parts = PurePosixPath(rel).parts
    if parts[0].lower() in THIRD_PARTY_DIRS or rel == ".gitmodules":
        problems.append("third-party path or submodule")
    if suffix in CODE_EXTENSIONS and LICENSE_MARKERS.search(content[:4096].decode("utf-8", "replace")):
        problems.append("third-party license marker in code (needs audit + allowlist)")
    return problems


def check_repo(repo, base=None, approval=None):
    """Raise ProvenanceViolation listing every failing path; return the number of files scanned."""
    approval = os.environ.get(APPROVAL_ENV, "") if approval is None else approval
    allowlist = load_allowlist(repo)
    denylist = load_denylist(repo)
    paths = changed_paths(repo, base)
    gated = sorted(p for p in paths if p in GATED)
    failures = []
    if gated and approval != "granted":
        failures.append(f"gated files changed without maintainer approval ({APPROVAL_ENV}=granted): {', '.join(gated)}")
    for rel in paths:
        file = repo / rel
        if not file.is_file():
            continue
        for problem in scan_file(rel, file.read_bytes(), allowlist, denylist):
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
        count = check_repo(ROOT, base)
    except (ProvenanceViolation, ValueError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"provenance-guard FAIL: {error}") from error
    print(f"PASS provenance guard: {count} files scanned against {base or 'all tracked files'}")


if __name__ == "__main__":
    main()
