"""Prototype test-staging selector (Phase 2 prep, Issue #852).

This module decides a staging level (L0/L1/L2/L3, plus manual-only L4) from
the change set between BASE and HEAD plus uncommitted inputs. It never runs
tests itself: it only prints the existing commands to run. It adds no new
verification logic and changes no test body, Makefile target, workflow, or
Required Check.

Map-format isolation: the change->test correspondence table (Task-A) is read
through the MapLoader interface below. The normal CLI path uses
CanonicalJsonMapLoader with the canonical Task-A envelope
``test-staging-map-v0`` (default tools/test_staging_map.json, or an
explicit --map, which is still loaded as canonical). ProvisionalJsonMapLoader
understands only the *provisional* envelope
``test-staging-impact-map/provisional-0`` and is isolated to explicit
synthetic-test use (callers pass loader= explicitly); a provisional
envelope via the normal CLI path is rejected as inconsistent and falls
back to L3 (fail-closed). Nothing in the selector hardcodes Task-A's
schema beyond the canonical loader's strict validation.

Fail-closed summary: BASE unresolvable (ref absent, shallow history, fetch
failure when --fetch is given), any diff-collection failure, missing or
inconsistent map, unclassified file, untracked file, rename handling
abnormality, unknown --api value, or an empty L0-L2 selection all select L3.
A SKIP, BLOCKED-as-pass, missing-tool, or 0-test result is never PASS: every
emitted command exports OSS_PCE_REQUIRE_CC65=1 so a missing cc65 FAILs
instead of skipping, and an empty selection escalates to L3.

Evidence boundaries: L0-L2 verdicts are printed PARTIAL and are never a
substitute for L3. Local L3 (commands runnable in this worktree) and
Required CI (the 6 Required Checks, which need CI trust boundaries such as
the trusted guard and artifact handoff) are different evidence; MERGE_READY
needs both. Reusing a prior result additionally requires the evidence
fingerprint (HEAD/BASE, input bytes, map content+id, selector id, API
choice, level, command set, toolchain, env/conditions) to match exactly;
selecting commands is never reported as execution success.
"""

import argparse
import fnmatch
import hashlib
import json
import os
import platform
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

LEVELS = ("L0", "L1", "L2", "L3")
LEVEL_RANK = {name: index for index, name in enumerate(LEVELS)}
MANUAL_ONLY_LEVELS = ("L4",)
REQUIRE_CC65_ENV = "OSS_PCE_REQUIRE_CC65"

# Provisional envelope tag understood by ProvisionalJsonMapLoader. This is a
# prototype-test tag only; the canonical Task-A format tag is owned by Task-A
# and will be unknown to this loader (unknown tags -> inconsistent -> L3).
PROVISIONAL_FORMAT = "test-staging-impact-map/provisional-0"

# Existing make targets the prototype is allowed to reference. Anything else
# in a map is an inconsistency (-> L3), so a map can never invent commands.
KNOWN_MAKE_TARGETS = frozenset({
    "setup", "build", "test", "test-real-core", "host-test", "check",
    "clean", "release", "integration", "integration-test",
    "block-write-guard", "block-transfer-matrix", "block-mid-refusal",
    "stack-access-guard", "stack-methods", "stack-methods-compare",
    "stack-s5", "stack-s6", "stack-irq-timing", "stack-mpr-buffer",
    "caller-stack-contract", "st-boundary", "block-preblock",
    "verify-build-diff", "rom-provenance",
    "dev-rom", "dev-test", "dev-integration", "dev-sweep",
    "dev-bcdbin-sweep", "dev-mul16s-sweep", "dev-release-gate-blocked",
})

# Mandatory local-L3 command set: the single source of truth for EVERY L3
# path (input-collection failure, empty change set, untracked inputs,
# unclassified files, empty selections, map missing/inconsistent, L3 rule
# hits, L4 auto-escalation). Derived from the Makefile (build, test,
# test-real-core, check including the verify-build-diff audit,
# rom-provenance reproduce set, dev-integration including dev-rom and both
# full sweeps, dev-release-gate-blocked), the six Required CI checks
# (separate evidence, never executed here), the Phase 1 Task-A map v0
# (DEV/provenance/release-gate/applicable-gate coverage), and the #850
# survey test-full sequence. `make release` is BLOCKED by design and is
# never selected; release *verification* is the existing `verify-build-diff`
# audit (debug/release diff + --report, also run inside `make check`), not a
# release build -- selecting a build is never reported as verification
# success, and selecting commands is never reported as execution success.
# Every L3 path emits exactly this set (partial=False, unselected=[]); the
# displayed argv is exactly the command that runs. L0-L2 output lists the
# unselected remainder of this sequence for auditability.
FULL_L3_SEQUENCE = [
    {"make": "build"},
    {"make": "test"},
    {"make": "test-real-core"},
    {"make": "check"},
    {"make": "verify-build-diff"},
    {"make": "rom-provenance"},
    {"make": "dev-integration"},
    {"make": "dev-release-gate-blocked"},
]

REQUIRED_CI_CHECKS = (
    "bios-dev", "verify", "guard-bios-dev",
    "guard-verify", "guard-core-harness", "guard-bcdbin-core",
)


class GitError(Exception):
    """A git input-collection step failed; carries the fail-closed reason."""

    def __init__(self, kind, detail):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


class MapError(Exception):
    """The impact map is missing or inconsistent; carries the reason."""


@dataclass
class ChangedInputs:
    """All change inputs Issue #852 requires the selector to handle."""

    head: str
    base: str
    committed: list = field(default_factory=list)  # [(status, path)]
    staged: list = field(default_factory=list)  # [(status, path)]
    unstaged: list = field(default_factory=list)  # [(status, path)]
    untracked: list = field(default_factory=list)  # [path]

    def all_classifiable(self):
        """Every tracked change as (origin, status, path) tuples."""
        for origin, entries in (("committed", self.committed),
                                ("staged", self.staged),
                                ("unstaged", self.unstaged)):
            for status, path in entries:
                yield origin, status, path


@dataclass
class Selection:
    """The selector decision plus everything needed to audit it."""

    level: str
    selected: list = field(default_factory=list)  # command dicts
    unselected: list = field(default_factory=list)  # command dicts
    reasons: list = field(default_factory=list)  # per-file "path: reason"
    fallback: bool = False
    fallback_reason: str = "none"
    partial: bool = True
    fingerprint: dict = field(default_factory=dict)

    def to_dict(self):
        return {
            "level": self.level,
            "selected": self.selected,
            "unselected": self.unselected,
            "reasons": self.reasons,
            "fallback": self.fallback,
            "fallback_reason": self.fallback_reason,
            "partial": self.partial,
            "fingerprint": self.fingerprint,
        }


# ---------------------------------------------------------------------------
# Git input collection (Issue #852 acceptance inputs).
# ---------------------------------------------------------------------------

# Repo-local git binding (Issue #852, reviews 6096132688 B-1 and
# 6096255224 object-database isolation). Every git subprocess below runs
# through _run_git with BOTH an explicit ``git -C <repo>`` binding AND a
# sanitized environment: the repo-binding variables below are stripped so
# a hostile caller environment (exported GIT_DIR/GIT_WORK_TREE pointing at
# another checkout, a ceiling/discovery/namespace/prefix redirect, a
# foreign object database, an alternate/quarantine object dir, a graft/
# shallow/replace history overlay, a redirected index, or a config-file/
# -c-pair redirect) can never rebind HEAD/BASE/merge-base resolution,
# committed/staged/unstaged diffs, untracked listing, blob/index
# regular-file checks, blob fetches, fingerprint hashing, or the explicit
# --fetch update target to the wrong repository. Binding therefore holds
# even under a hostile env (no refusal dance needed: isolation is total,
# -C + cwd + clean env). Audit basis: `git rev-parse --local-env-vars`
# (repo-local scope) plus the object-db/index/config/fetch sources
# (object-file.c quarantine, setup.c worktree/prefix, config.c cascade,
# read-cache.c index version, replace-object machinery). Transport, auth,
# and identity GIT_* (SSH/ASKPASS/HTTP/proxy/allow-protocol, author/
# committer, pager/diff drivers, traces, pathspec glob flags, optional
# locks, notes/attr drivers, exec-path, perf/test knobs) are left
# untouched: they carry no repo/object-db identity (the fetch URL comes
# from the bound repo's own origin once the config redirects below are
# stripped), and stripping them would gratuitously break normal settings.
# GIT_CONFIG_NOSYSTEM is likewise kept (it only suppresses system config
# -- a fail-safe direction, never a foreign redirect). No blanket refuse
# of unknown GIT_*: that would be a generic OS sandbox (out of scope)
# and would break normal communication. Unisolatable git failures still
# fail closed (GitError -> L3, reuse refused) at the call sites.
_GIT_REPO_BINDING_VARS = (
    # Worktree/gitdir/index binding (review 6096132688 B-1).
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_IMPLICIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_INDEX_VERSION",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
    "GIT_CEILING_DIRECTORIES",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM",
    "GIT_PREFIX",
    # Object-database binding (review 6096255224): a foreign object dir
    # (write path: fetch stores here), foreign alternates (read path),
    # or a foreign quarantine dir would let `git -C A fetch/read`
    # resolve or persist objects via repo B.
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_QUARANTINE_PATH",
    # History-identity binding: graft/shallow/replace overlays rewrite
    # commit parentage and merge-base/HEAD resolution.
    "GIT_GRAFT_FILE",
    "GIT_SHALLOW_FILE",
    "GIT_NO_REPLACE_OBJECTS",
    "GIT_REPLACE_REF_BASE",
    # Config binding: one-shot file redirects and -c pairs override the
    # bound repo's own .git/config (e.g. url.insteadOf fetch rewrites).
    "GIT_CONFIG",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_SYSTEM",
)
# Dynamic-suffix config pairs (GIT_CONFIG_COUNT=N reads GIT_CONFIG_KEY_<i>
# /GIT_CONFIG_VALUE_<i>): stripped by prefix since the names are indexed.
_GIT_REPO_BINDING_VAR_PREFIXES = (
    "GIT_CONFIG_KEY_",
    "GIT_CONFIG_VALUE_",
)


def _git_env_for_repo():
    """Sanitized environment for one bound git invocation (no repo bind)."""
    env = dict(os.environ)
    for var in _GIT_REPO_BINDING_VARS:
        env.pop(var, None)
    for var in [var for var in env
                if var.startswith(_GIT_REPO_BINDING_VAR_PREFIXES)]:
        env.pop(var, None)
    return env


def _bind_repo(repo):
    """Resolve ``repo`` to a SINGLE absolute path BEFORE any git use.

    Issue #852, reviews 6096685265 + 6096688910 re-review: pre-fix,
    _run_git passed BOTH ``git -C str(repo)`` AND ``cwd=str(repo)``, so a
    relative repo "A" with an independent nested repo "A/A" applied the
    relative path twice (subprocess chdir into A, then git -C into A
    again) and every git operation bound the INNER repo while the map,
    ledger, Makefile, dependencies, fingerprint, and --fetch target
    referenced the OUTER repo (all exit 0, no GitError). Resolving to one
    absolute path first makes the handed-to-git binding idempotent
    (absolute + absolute cannot double-apply). Pure path operation: no
    git runs and no file bytes are read here. Anything unusable (not a
    str/PathLike, empty, NUL, undecodable, not an existing directory)
    raises GitError (callers escalate to full L3 with an unknown
    fingerprint, so reuse is refused). Review 6096986826 MAJOR: a spec
    containing a ``..`` segment is refused lexically (split-parts check
    BEFORE abspath/normalization and before ANY git call) via the same
    GitError path, so an ambiguous spec that would resolve elsewhere can
    never bind git ops to the wrong repo while the map, ledger, and
    fingerprint use the requested one.
    """
    try:
        given = os.fspath(repo)
    except (TypeError, ValueError) as error:
        raise GitError("repo",
                       f"repo spec is not a path: {error}") from error
    if isinstance(given, bytes):
        try:
            given = os.fsdecode(given)
        except (OSError, ValueError, UnicodeError) as error:
            raise GitError("repo",
                           f"repo spec is not decodable: {error}") from error
    if not isinstance(given, str) or not given or "\0" in given:
        raise GitError("repo", f"repo spec is not usable: {given!r}")
    if ".." in Path(given).parts:
        raise GitError(
            "repo",
            f"repo spec contains '..': {given!r} is refused")
    bound = os.path.abspath(given)
    try:
        is_dir = os.path.isdir(bound)
    except (OSError, ValueError) as error:
        raise GitError(
            "repo",
            f"repo {given!r} is not resolvable: {error}") from error
    if not is_dir:
        raise GitError(
            "repo", f"repo {given!r} is not an existing directory")
    return bound


def _verify_repo_root(bound):
    """Check the bound path IS the actual git worktree root.

    Runs ``git rev-parse --show-toplevel`` through the SAME sanitized
    boundary (sanitized env, single absolute ``cwd``, no -C, so the check
    itself can neither double-resolve nor be redirected at another
    checkout's objects) and requires the reported root to equal the bound
    path (symlink-insensitive comparison). A requested SUBDIRECTORY of a
    repo therefore fails closed instead of silently mixing the parent
    repo's diffs with the requested path's map/ledger/Makefile/pins; a
    missing repo, a non-git dir, a bare repo, or any abnormal git response
    fails closed the same way (GitError -> full L3, unknown fingerprint,
    reuse refused). Linked worktrees keep working: their reported root is
    the worktree path itself.
    """
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], cwd=bound,
            capture_output=True, text=False, timeout=120,
            env=_git_env_for_repo())
    except (OSError, subprocess.SubprocessError) as error:
        raise GitError(
            "repo",
            f"repo {bound!r} is not verifiable: {error}") from error
    if proc.returncode != 0:
        raise GitError(
            "repo", f"repo {bound!r} is not a git worktree root: "
            f"{proc.stderr.decode('utf-8', errors='replace').strip()[-500:]}")
    try:
        out = proc.stdout.decode("utf-8")
    except UnicodeDecodeError as error:
        raise GitError(
            "repo",
            f"repo {bound!r} gave an abnormal git response: "
            f"{error}") from error
    lines = [line for line in out.splitlines() if line.strip()]
    if len(lines) != 1:
        raise GitError(
            "repo",
            f"repo {bound!r} gave an abnormal git response")
    actual = os.path.normcase(os.path.realpath(lines[0].strip()))
    want = os.path.normcase(os.path.realpath(bound))
    if actual != want:
        raise GitError(
            "repo",
            f"repo {bound!r} is inside git worktree "
            f"{lines[0].strip()!r}: implicit parent-repo discovery "
            "is refused fail-closed")
    return bound


def _run_git(repo, *args):
    """Run one git subcommand bound to ``repo`` (sanitized env, one path).

    The repo is unified to a SINGLE absolute path BEFORE any git use
    (_bind_repo: a relative spec resolves exactly once against the
    process cwd) and verified against the actual worktree root
    (_verify_repo_root: ``rev-parse --show-toplevel`` inside the same
    sanitized boundary, so the check itself cannot be misbound). The same
    absolute value is then handed to BOTH ``git -C`` and ``cwd``:
    absolute + absolute is idempotent, so the old relative
    double-resolution (nested independent repo silently bound instead of
    the requested one) is gone, while the argv shape (explicit -C) and
    the env isolation are unchanged.

    Covers EVERY git subprocess path: HEAD/BASE/merge-base resolution,
    committed/staged/unstaged diffs, untracked listing, explicit --fetch,
    ls-tree/ls-files regular-file checks, and show blob/index fetches.
    Raises GitError on execution failure; a nonzero exit is returned on
    the CompletedProcess for the caller to interpret (regular-file
    checks treat it as non-regular, the runner treats it as git-exit).
    """
    bound = _bind_repo(repo)
    _verify_repo_root(bound)
    try:
        return subprocess.run(
            ["git", "-C", bound, *args], cwd=bound,
            capture_output=True, text=False, timeout=120,
            env=_git_env_for_repo())
    except (OSError, subprocess.SubprocessError) as error:
        raise GitError("git-execution", str(error)) from error


def _real_runner(repo, *args):
    # Binary capture + strict UTF-8 decode (never text mode): universal-
    # newline translation would silently rewrite filenames containing CR
    # (``a\rb`` reported as ``a\nb`` -- a different, possibly
    # nonexistent path), and undecodable bytes would mojibake paths.
    # Both cases are unidentifiable inputs, so both fail closed: a decode
    # failure raises GitError (callers escalate to L3) instead of
    # misidentifying the path. Exit text is decoded lossily for the
    # message only (never parsed as a path).
    # Repo binding: delegated to _run_git (one absolute path handed to
    # explicit -C + cwd AND a sanitized env), so repo/object-db/history/
    # config overrides in the caller env (gitdir/worktree/index, object/
    # alternate/quarantine dirs, graft/shallow/replace overlays,
    # config-file/-c redirects, ceiling/discovery/namespace/prefix) can
    # never redirect this call at another checkout's objects, and a
    # relative spec can never double-resolve into a nested repo.
    proc = _run_git(repo, *args)
    if proc.returncode != 0:
        raise GitError("git-exit",
                       f"git {' '.join(args)} exited {proc.returncode}: "
                       f"{proc.stderr.decode('utf-8', errors='replace').strip()[-500:]}")
    try:
        return proc.stdout.decode("utf-8")
    except UnicodeDecodeError as error:
        raise GitError("git-decode",
                       f"git output is not valid UTF-8: {error}") from error


_STATUS_TOKEN = re.compile(r"^[A-Z][0-9]*$")


def _parse_name_status(output):
    """Parse `git diff --name-status -z --no-renames` output.

    With -z, git separates the status letter and the path with NUL (not
    TAB); without -z it uses TAB. Both forms are accepted here. Renames
    are disabled by the caller, so a rename always arrives as a
    delete+add pair; this function therefore never invents rename coupling.
    """
    entries = []
    chunks = output.split("\0")
    index = 0
    while index < len(chunks):
        chunk = chunks[index]
        index += 1
        if not chunk:
            continue
        if "\t" in chunk:
            status, _, path = chunk.partition("\t")
            if not status or not path:
                raise GitError("diff-parse", f"unparseable entry: {chunk!r}")
            entries.append((status[0], path))
        elif (_STATUS_TOKEN.match(chunk) and index < len(chunks)
                and chunks[index]):
            entries.append((chunk[0], chunks[index]))
            index += 1
        else:
            raise GitError("diff-parse", f"unparseable entry: {chunk!r}")
    return entries


def attempt_fetch(repo, base, runner=_real_runner):
    """Fetch BASE explicitly (only when the caller opts in with --fetch).

    Read-only by default; a fetch failure is fail-closed L3, never silent.
    """
    try:
        runner(repo, "fetch", "origin", base)
    except GitError as error:
        raise GitError("fetch", f"BASE fetch failed for {base!r}: {error}") from error


def _refuse_hidden_index_flags(repo, runner=_real_runner):
    """Fail closed when any tracked path can hide worktree changes (Issue #852).

    Review 6096382075: ``git diff``/``status`` silently miss worktree edits
    behind the assume-unchanged or skip-worktree index flags, so a hidden
    L3 edit classifies as L1 (or as no change at all) and the evidence
    fingerprint -- which never saw the hidden bytes -- stays reusable
    across the hidden edit. ``git ls-files -v -z`` exposes the flags as
    the per-entry tag: exactly ``H`` is a normal cached entry, while ``h``
    (assume-unchanged), ``S`` (skip-worktree), ``s`` (both), or any other
    abnormal tag (unmerged/removed/changed/killed/other, either case)
    means the worktree diff is untrustworthy. ANY non-``H`` tag -- or any
    malformed, undecodable, or failed ls-files output -- raises GitError
    (callers escalate to full L3 with an unknown fingerprint, so reuse is
    refused). Read-only: index flags are never modified or cleared, and no
    file bytes are hashed here. Runs through the bound ``runner`` (same
    repo-bound env isolation as every other git call). Entries are exactly
    ``<TAG><one space><path>`` NUL-terminated; the path is taken verbatim
    (no strip: leading/trailing-whitespace filenames must round-trip, so
    only the fixed two-character prefix is inspected).
    """
    output = runner(repo, "ls-files", "-v", "-z", "--")
    for chunk in output.split("\0"):
        if not chunk:
            continue
        if len(chunk) < 3 or chunk[1] != " ":
            raise GitError("hidden-index-flags",
                           f"unparseable ls-files entry: {chunk!r}")
        tag = chunk[0]
        if tag == "H":
            continue
        path = chunk[2:]
        if tag in ("h", "S", "s"):
            raise GitError(
                "hidden-index-flags",
                f"tracked path {path!r} carries index flag {tag!r} "
                "(assume-unchanged/skip-worktree hides worktree changes)")
        raise GitError(
            "hidden-index-flags",
            f"tracked path {path!r} carries abnormal index flag {tag!r} "
            "(worktree changes may be hidden)")


def collect_changes(repo, base, runner=_real_runner, fetch=False):
    """Capture committed/staged/unstaged/untracked changes (Issue #852).

    Raises GitError (fail-closed -> L3) when BASE cannot be resolved, history
    is shallow, a fetch fails, any diff fails, or any tracked path carries
    an assume-unchanged/skip-worktree (or abnormal) index flag.
    """
    repo = str(repo)
    _refuse_hidden_index_flags(repo, runner)
    if runner(repo, "rev-parse", "--is-shallow-repository").strip() == "true":
        raise GitError("shallow",
                       "shallow history cannot establish the comparison")
    if fetch:
        attempt_fetch(repo, base, runner)
    try:
        base_sha = runner(repo, "rev-parse", "--verify", f"{base}^{{commit}}").strip()
    except GitError as error:
        raise GitError("base",
                       f"BASE {base!r} is not resolvable locally: {error}") from error
    head = runner(repo, "rev-parse", "HEAD").strip()
    try:
        merge_base = runner(repo, "merge-base", base_sha, head).strip()
    except GitError as error:
        raise GitError("base",
                       f"merge-base of {base!r} and HEAD failed: {error}") from error
    try:
        committed = _parse_name_status(
            runner(repo, "diff", "--name-status", "-z", "--no-renames",
                   merge_base, head, "--"))
        staged = _parse_name_status(
            runner(repo, "diff", "--cached", "--name-status", "-z",
                   "--no-renames", "--"))
        unstaged = _parse_name_status(
            runner(repo, "diff", "--name-status", "-z", "--no-renames", "--"))
    except GitError as error:
        raise GitError("diff", f"change detection failed: {error}") from error
    try:
        status_output = runner(repo, "status", "--porcelain=v1", "-z",
                               "--untracked-files=all", "--")
    except (GitError, UnicodeDecodeError, ValueError) as error:
        raise GitError("diff",
                       f"change detection failed: {error}") from error
    untracked = []
    for chunk in status_output.split("\0"):
        if not chunk:
            continue
        if not chunk.startswith("??"):
            continue
        # Porcelain -z emits exactly ``??`` + ONE separator space + the
        # raw path (no quoting, NUL-terminated, so whitespace/tab/newline
        # inside the name round-trips). Strip ONLY that fixed prefix:
        # strip()/lstrip()/normalization would destroy leading/trailing
        # whitespace filenames (``sample `` is not ``sample``). A chunk
        # without the separator, or with an empty path, is unidentifiable
        # -> fail closed (GitError -> L3), never silently skipped.
        rest = chunk[2:]
        if not rest.startswith(" ") or len(rest) < 2:
            raise GitError("diff",
                           f"unparseable untracked entry: {chunk!r}")
        untracked.append(rest[1:])
    return ChangedInputs(head=head, base=base_sha, committed=committed,
                         staged=staged, unstaged=unstaged, untracked=untracked)


# ---------------------------------------------------------------------------
# Impact-map loading behind an interface (Task-A owns the canonical format).
# ---------------------------------------------------------------------------

class MapLoader:
    """Interface for change->test correspondence tables (Task-A dependency).

    Concrete loaders validate their envelope strictly: anything missing or
    inconsistent raises MapError, which the caller turns into fail-closed L3.
    """

    # How select() matches rules to files. "max" keeps the highest matching
    # level (provisional semantics); "first-match" uses entry order
    # (Task-A canonical semantics). Loaders declare their mode; select()
    # defaults to "max" so existing callers are unaffected.
    match_mode = "max"

    def load(self, path, repo=None):
        """Return (rules, apis) or raise MapError."""
        raise NotImplementedError

    def resolve_api(self, api):
        """Normalise an --api identifier to the apis-dict key, or itself."""
        return api


@dataclass
class MapRule:
    pattern: str
    level: str
    commands: list
    reason: str
    api: str = ""
    # Canonical exact/exact-list entries match literally; only glob
    # entries use fnmatch. Provisional rules are always glob (legacy).
    literal: bool = False


class ProvisionalJsonMapLoader(MapLoader):
    """Loader for the prototype-test envelope only (NOT the Task-A format).

    Expected document::

        {"format": "test-staging-impact-map/provisional-0",
         "rules": [{"pattern": "src/overrides/*.s", "level": "L1",
                    "commands": [{"make": "dev-rom"}], "reason": "...",
                    "api": "OPTIONAL-API-TAG"}],
         "apis": {"API-NAME": {"commands": [...], "reason": "..."}}}

    Command objects are one of ``{"make": "<existing-target>"}``,
    ``{"unittest": "<pattern>", "suite": "host"|"integration"}``, or
    ``{"script": "tests/...py", "args": [...]}``. Anything else is an
    inconsistency (-> L3).
    """

    def load(self, path, repo=None):
        try:
            raw = _read_bytes_strict(path, repo)
        except OSError:
            raise MapError(f"impact map is missing: {path}")
        try:
            document = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as error:
            raise MapError(f"impact map is not valid JSON: {error}") from error
        if not isinstance(document, dict):
            raise MapError("impact map root must be an object")
        if document.get("format") != PROVISIONAL_FORMAT:
            raise MapError(
                "impact map format "
                f"{document.get('format')!r} is not recognised "
                f"(expected {PROVISIONAL_FORMAT!r}; the canonical Task-A "
                "format needs its own loader) -> L3")
        raw_rules = document.get("rules")
        if not isinstance(raw_rules, list):
            raise MapError("impact map 'rules' must be a list")
        rules = [self._check_rule(index, entry) for index, entry in enumerate(raw_rules)]
        apis = document.get("apis", {})
        if not isinstance(apis, dict):
            raise MapError("impact map 'apis' must be an object")
        checked_apis = {}
        for name, entry in apis.items():
            if not isinstance(entry, dict):
                raise MapError(f"api {name!r} entry must be an object")
            commands = entry.get("commands")
            if not isinstance(commands, list) or not commands:
                raise MapError(f"api {name!r} must list at least one command")
            checked_apis[name] = {
                "commands": [self._check_command(cmd) for cmd in commands],
                "reason": str(entry.get("reason", f"Task-A provisional api {name}")),
            }
        return rules, checked_apis

    def _check_rule(self, index, entry):
        if not isinstance(entry, dict):
            raise MapError(f"rule #{index} must be an object")
        pattern = entry.get("pattern")
        level = entry.get("level")
        commands = entry.get("commands")
        reason = entry.get("reason")
        if not pattern or not isinstance(pattern, str):
            raise MapError(f"rule #{index} needs a non-empty pattern")
        if level not in LEVELS:
            raise MapError(f"rule #{index} has unknown level {level!r}")
        if not isinstance(commands, list) or not commands:
            raise MapError(f"rule #{index} must list at least one command")
        if not reason or not isinstance(reason, str):
            raise MapError(f"rule #{index} needs a reason string")
        api = entry.get("api", "")
        if not isinstance(api, str):
            raise MapError(f"rule #{index} api tag must be a string")
        return MapRule(pattern=pattern, level=level,
                       commands=[self._check_command(cmd) for cmd in commands],
                       reason=reason, api=api)

    def _check_command(self, command):
        if not isinstance(command, dict) or len(command) != 1:
            raise MapError(f"command must be a single-key object: {command!r}")
        (kind, value), = command.items()
        if kind == "make":
            if value not in KNOWN_MAKE_TARGETS:
                raise MapError(f"unknown make target {value!r} "
                               "(maps may only reference existing commands)")
            return {"make": value}
        if kind == "unittest":
            if not isinstance(value, str) or not value:
                raise MapError("unittest command needs a pattern string")
            return {"unittest": value, "suite": "host"}
        if kind == "suite-test":
            if not isinstance(value, dict):
                raise MapError("suite-test command needs an object value")
            pattern, suite = value.get("pattern"), value.get("suite")
            if not pattern or suite not in ("host", "integration"):
                raise MapError(f"bad suite-test command: {command!r}")
            return {"unittest": pattern, "suite": suite}
        if kind == "script":
            if not isinstance(value, str) or not value:
                raise MapError("script command needs a path string")
            raw_target = ROOT / value
            try:
                if raw_target.is_symlink():
                    raise MapError(f"script {value!r} is a symlink "
                                   "(refused fail-closed) -> L3")
                target = raw_target.resolve()
            except OSError as error:
                raise MapError(f"script {value!r} is unreadable: {error} "
                               "-> L3") from error
            try:
                target.relative_to(ROOT.resolve())
            except ValueError as error:
                raise MapError(f"script {value!r} escapes the repository") from error
            if target.is_symlink() or target.suffix != ".py" or not target.is_file():
                raise MapError(f"script {value!r} is not an existing "
                               "repository python script")
            return {"script": value}
        raise MapError(f"unknown command kind {kind!r} "
                       "(maps may only reference existing commands)")


# ---------------------------------------------------------------------------
# Canonical Task-A map loader (Phase 1 v0, Issue #854).
# ---------------------------------------------------------------------------

# Canonical envelope identity (owned by Task-A; the provisional loader above
# must never accept it, and this loader must never accept the provisional
# envelope -- either direction falls back to L3, never a misread).
CANONICAL_MAP_NAME = "test-staging-map-v0"
CANONICAL_SCHEMA_VERSION = 1
# FINAL dependency pin (Task-A map v0, PR #856 HEAD): read-only fetch of
# origin/agent/test-staging-map-v0-20261128 confirmed HEAD 2ab14d08. This
# loader implements that revision's FINAL schema: api_slots rows carry NO
# name (identity by slot number; display/API names resolve via the
# spec/api_slots.json ledger, the single source), reasons use the
# slot-number form (e.g. $07), and tests-fixtures is narrowed to *.s.
# This supersedes the interim cf428568 pin. Post-merge re-verification
# (re-run on the merged map + re-request review) is still required after
# Owner merges #856; see docs/test-staging-wrapper-design-852.md §4a.
CANONICAL_SOURCE_HEAD = "2ab14d08fc9fbdc704da1b1f07c96501850b542e"

CANONICAL_LEVELS = ("L0", "L1", "L2", "L3", "L4")
CANONICAL_MATCH_TYPES = ("exact", "exact-list", "glob")
CANONICAL_CI_CLASSES = ("required-ci", "ci-nonrequired",
                        "ci-outside-optin", "push-only")
CANONICAL_API_SLOT_COUNT = 81

_MAKE_TARGET_RE = re.compile(r"^([A-Za-z0-9_.-]+)\s*:[^=]*$")
_HEX40_RE = re.compile(r"^[0-9a-f]{40}$")
_DOTTED_HOST_RE = re.compile(r"^tests\.host\.([A-Za-z_][A-Za-z0-9_]*)$")


def _makefile_targets(repo=None):
    """Parse live Makefile targets (a map may only reference real ones).

    Bound to the target repo (B-4): the CLI path passes ``--repo`` so the
    diff, map, ledger, Makefile, and pins all come from the same checkout.
    A missing/unreadable/symlink-parent Makefile is MapError (fail-closed
    L3, never a silent fallback to another checkout). Direct callers
    without a repo use ROOT (legacy unit-test boundary).
    """
    base = Path(repo) if repo is not None else ROOT
    candidate = base / "Makefile"
    try:
        text = _read_bytes_strict(candidate, base).decode("utf-8")
    except (OSError, ValueError, UnicodeDecodeError) as error:
        raise MapError("cannot read Makefile for target validation: "
                       f"{error} -> L3") from error
    targets = set()
    for line in text.splitlines():
        match = _MAKE_TARGET_RE.match(line)
        if match:
            targets.add(match.group(1))
    return targets


class CanonicalJsonMapLoader(MapLoader):
    """Loader for the Task-A canonical map (Issue #854, Phase 1 v0).

    Expected envelope::

        {"name": "test-staging-map-v0", "schema_version": 1,
         "base_commit": "<40-hex>", "required_checks": [...6...],
         "static_units_defined": [...],
         "entries": [{"id", "match_type", "match", "level",
                        "category", "ci_class", "required_ci",
                        "tests": {"host", "integration_scripts",
                                  "dev_targets", "static_units"},
                        "reason"}, ...],
         "fallback": {...same shape, match "**", level "L3"...},
         "api_slots": [{"slot": 0..80, "dev_implemented",
                          "dev_source", "level", "tests", "reason"}]}

    api_slots rows carry NO name (FINAL Task-A form): identity is the slot
    number, and display/API names resolve via the spec/api_slots.json
    ledger (the single source -- a row duplicating a ledger name, or a
    reason embedding one, is deformed -> L3, so not-wired negative tests
    never trip on the map). Slot-number spellings (63, 0x3F, $3F -- decimal
    as decimal, 0x-/$-prefixed hex; bare hex is banned as ambiguous) and
    ledger slash-name parts (CD_SUBRQ for "CD_SUBRQ / CD_SUBRD") resolve
    to the slot's ledger name via resolve_api(). An api_slots row keeps its
    own level in the apis table, and --api never lowers the required level:
    requesting a slot whose level is L3/L4 runs the full local-L3 sequence.

    Classification is first-match-wins in entry order (the canonical
    fallback entry is last and matches everything). A document carrying the
    provisional ``format`` tag, a wrong name/version, a non-40-hex
    base_commit fingerprint, renamed Required Checks, unknown Makefile
    targets, unknown host refs/scripts/static units, or a non-81-slot
    table is inconsistent (-> L3, never a partial read).

    Level L4 (manual-only opt-in, never a Required substitute) is escalated
    to L3 at load time: automatically selecting L4 commands would under-test,
    so L4-classified changes run the full regression fail-closed.
    """

    match_mode = "first-match"

    def __init__(self, repo=None):
        self._api_aliases = {}
        self._repo = repo

    def resolve_api(self, api):
        """Accept ledger names, slash-name parts, and slot-number spellings.

        Aliases are populated at load() from the spec/api_slots.json
        ledger (63, 0x3F, $3F, MA_MUL8U, CD_SUBRQ all resolve to their
        slot's ledger name). Bare hex ("3F", "A") is never an alias:
        it collides with decimal spellings ("10" is slot 10 decimal,
        never slot 0x10), so it passes through unchanged and fails
        closed as unknown-api -> L3 in select(). Anything else unknown
        likewise passes through unchanged -> L3.
        """
        if api is None:
            return None
        return self._api_aliases.get(str(api), str(api))

    def load(self, path, repo=None):
        effective = repo if repo is not None else (
            self._repo if getattr(self, "_repo", None) is not None else ROOT)
        if repo is not None or getattr(self, "_repo", None) is not None:
            # Repo-bound load (the normal CLI always binds --repo here):
            # the map must live inside the bound repo. An absolute --map
            # from another checkout would otherwise mix that checkout's
            # map bytes with this repo's ledger/Makefile/diff/pins, so it
            # is refused BEFORE any read (fail-closed L3). Unbound loads
            # keep the isolated explicit-synthetic-fixture boundary
            # (temp fixtures validated without a repo anchor). base_commit
            # is a content fingerprint of the map document, never proof
            # of which checkout it came from -- only containment binds it.
            try:
                base = Path(effective).resolve()
            except (OSError, ValueError) as error:
                raise MapError(f"canonical map repo {effective!r} is not "
                               f"resolvable: {error} -> L3") from error
            candidate = Path(path)
            if not candidate.is_absolute():
                candidate = base / candidate
            try:
                resolved = candidate.resolve()
            except (OSError, ValueError) as error:
                raise MapError(f"canonical map {path!r} is not "
                               f"resolvable: {error} -> L3") from error
            try:
                inside = (resolved == base or base in resolved.parents)
            except (OSError, ValueError):
                inside = False
            if not inside:
                raise MapError(
                    f"normal-CLI --map {path!r} is outside the bound repo "
                    f"{effective}: cross-repo mixing refused fail-closed "
                    "-> L3 (bind target repo+map+ledger+Makefile+ "
                    "dependencies consistently; use a repo-relative --map "
                    "or the repo-bound default)")
        try:
            raw = _read_bytes_strict(path, effective)
        except OSError:
            raise MapError(f"canonical map is missing: {path}")
        try:
            document = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as error:
            raise MapError(f"canonical map is not valid JSON: {error}") from error
        if not isinstance(document, dict):
            raise MapError("canonical map root must be an object")
        if "format" in document:
            raise MapError(
                "non-canonical envelope tag "
                f"{document.get('format')!r}: the provisional envelope is "
                "never treated as canonical -> L3")
        if document.get("name") != CANONICAL_MAP_NAME:
            raise MapError(
                f"canonical map name {document.get('name')!r} is not "
                f"{CANONICAL_MAP_NAME!r} -> L3")
        if document.get("schema_version") != CANONICAL_SCHEMA_VERSION:
            raise MapError(
                "canonical map schema_version "
                f"{document.get('schema_version')!r} is not "
                f"{CANONICAL_SCHEMA_VERSION} -> L3")
        base_commit = document.get("base_commit")
        if (not isinstance(base_commit, str)
                or not _HEX40_RE.match(base_commit)):
            raise MapError("canonical map needs a 40-hex base_commit "
                           "fingerprint -> L3")
        required = document.get("required_checks")
        if (not isinstance(required, list)
                or not all(isinstance(item, str) and item
                           for item in required)
                or set(required) != set(REQUIRED_CI_CHECKS)):
            raise MapError("canonical map required_checks must be exactly "
                           f"the {len(REQUIRED_CI_CHECKS)} Required Checks -> L3")
        static_defined = document.get("static_units_defined")
        if (not isinstance(static_defined, list) or not static_defined
                or not all(isinstance(unit, str) and unit
                           for unit in static_defined)):
            raise MapError("canonical map 'static_units_defined' must be a "
                           "non-empty string list -> L3")
        static_set = set(static_defined)
        entries = document.get("entries")
        if not isinstance(entries, list) or not entries:
            raise MapError("canonical map 'entries' must be a non-empty "
                           "list -> L3")
        fallback = document.get("fallback")
        if not isinstance(fallback, dict):
            raise MapError("canonical map 'fallback' must be an object -> L3")
        self._check_entry_shape(entries, fallback, len(entries))
        if entries[-1].get("id") != fallback.get("id"):
            raise MapError("canonical fallback must be the last entry "
                           "(first-match-wins needs it last) -> L3")
        if fallback.get("match") != "**" or fallback.get("level") != "L3":
            raise MapError("canonical fallback must be match '**' at L3 "
                           "-> L3")
        slots = document.get("api_slots")
        ledger_names = self._load_ledger_names(effective)
        self._check_slots(slots)
        targets = _makefile_targets(effective)
        dev_source_to_slot = {}
        for slot in slots:
            source = slot.get("dev_source")
            if source:
                dev_source_to_slot[source] = ledger_names[slot["slot"]]
        rules = []
        for index, entry in enumerate(entries):
            rules.extend(self._convert_entry(index, entry, targets, static_set,
                                             dev_source_to_slot, effective))
        apis = {}
        aliases = {}
        for slot in slots:
            number = slot["slot"]
            name = ledger_names[number]
            commands = self._convert_tests(slot["tests"], static_set,
                                           targets, f"api-slot ${number:02X}",
                                           require_commands=True,
                                           repo=effective)
            apis[name] = {
                "commands": commands,
                "reason": str(slot.get("reason") or f"canonical slot ${number:02X}"),
                "level": slot["level"],
            }
            parts = [name] + [part.strip() for part in name.split("/")]
            # Slot-number spellings: decimal-as-decimal ("63" is slot 63),
            # 0x-prefixed hex ("0x3F"), and $-prefixed hex ("$3F").
            # Bare hex ("3F", "3f", "A") is BANNED: it collides with
            # decimal spellings ("10" is slot 10 decimal, never slot
            # 0x10 == 16), so a bare-hex-looking input is ambiguous and
            # unknown inputs fail closed to L3 in select(). Any alias
            # claimed by two slots is deformed -> L3, never first-wins.
            spellings = [str(number),
                         f"0x{number:X}", f"0x{number:x}",
                         f"0x{number:02X}", f"0x{number:02x}",
                         f"${number:X}", f"${number:x}",
                         f"${number:02X}", f"${number:02x}"]
            for alias in parts + spellings:
                if not alias:
                    continue
                if alias in aliases and aliases[alias] != name:
                    raise MapError(
                        f"canonical slot alias {alias!r} is ambiguous "
                        f"({aliases[alias]!r} vs {name!r}) -> L3")
                aliases.setdefault(alias, name)
        self._api_aliases = aliases
        return rules, apis

    def _check_entry_shape(self, entries, fallback, fallback_index):
        for index, entry in enumerate(entries + [fallback]):
            where = "fallback" if index == fallback_index else f"entry #{index}"
            if not isinstance(entry, dict):
                raise MapError(f"canonical {where} must be an object -> L3")
            for key in ("id", "match_type", "match", "level",
                        "category", "ci_class", "required_ci",
                        "tests", "reason"):
                if key not in entry:
                    raise MapError(f"canonical {where} "
                                   f"({entry.get('id')!r}) misses {key!r} -> L3")
            if not entry.get("id") or not isinstance(entry["id"], str):
                raise MapError(f"canonical {where} needs a non-empty id -> L3")
            if entry["match_type"] not in CANONICAL_MATCH_TYPES:
                raise MapError(
                    f"canonical {where} has unknown match_type "
                    f"{entry['match_type']!r} -> L3")
            match = entry["match"]
            if entry["match_type"] == "exact-list":
                if (not isinstance(match, list) or not match
                        or not all(isinstance(item, str) and item
                                  for item in match)):
                    raise MapError(f"canonical {where} exact-list needs a "
                                   "non-empty string list -> L3")
            elif not isinstance(match, str) or not match:
                raise MapError(f"canonical {where} needs a non-empty match "
                               "pattern -> L3")
            if entry["level"] not in CANONICAL_LEVELS:
                raise MapError(f"canonical {where} has unknown level "
                               f"{entry['level']!r} -> L3")
            if (not isinstance(entry.get("category"), str)
                    or not entry["category"].strip()):
                raise MapError(f"canonical {where} needs a category string "
                               "-> L3")
            if entry["ci_class"] not in CANONICAL_CI_CLASSES:
                raise MapError(f"canonical {where} has unknown ci_class "
                               f"{entry['ci_class']!r} -> L3")
            if not isinstance(entry["required_ci"], bool):
                raise MapError(f"canonical {where} required_ci must be a "
                               "bool -> L3")
            if (not isinstance(entry["reason"], str)
                    or not entry["reason"].strip()):
                raise MapError(f"canonical {where} needs a reason string "
                               "-> L3")
            tests = entry["tests"]
            if not isinstance(tests, dict):
                raise MapError(f"canonical {where} tests must be an object "
                               "-> L3")
            for key in ("host", "integration_scripts", "dev_targets",
                        "static_units"):
                if key not in tests or not isinstance(tests[key], list):
                    raise MapError(f"canonical {where} tests misses list "
                                   f"{key!r} -> L3")
                for item in tests[key]:
                    if not isinstance(item, str) or not item:
                        raise MapError(
                            f"canonical {where} tests[{key!r}] holds a "
                            f"non-string entry {item!r} -> L3")
        ids = [entry["id"] for entry in entries]
        if len(set(ids)) != len(ids):
            raise MapError("canonical entry ids must be unique -> L3")

    def _load_ledger_names(self, repo=None):
        """Resolve slot-number -> candidate name via spec/api_slots.json.

        Bound to the target repo (B-4): the ledger is read from the same
        checkout as the diff/map/Makefile/pins. A missing, unreadable,
        symlink-parent, or deformed ledger is inconsistent -> L3
        (fail-closed, never unnamed, never a silent ROOT fallback when a
        repo is given).
        """
        base = Path(repo) if repo is not None else ROOT
        ledger_path = base / "spec" / "api_slots.json"
        try:
            ledger = json.loads(
                _read_bytes_strict(ledger_path, base).decode("utf-8"))
        except (OSError, ValueError, UnicodeDecodeError) as error:
            raise MapError(f"canonical slot ledger {ledger_path} is "
                           f"missing or not valid JSON: {error} -> L3") from error
        raw = ledger.get("slots") if isinstance(ledger, dict) else None
        if not isinstance(raw, list):
            raise MapError("canonical slot ledger needs a 'slots' list -> L3")
        names = {}
        for entry in raw:
            if not isinstance(entry, dict):
                raise MapError("canonical slot ledger entries must be "
                               "objects -> L3")
            slot_id = entry.get("id")
            candidate = entry.get("candidate_name")
            if (not isinstance(slot_id, int) or isinstance(slot_id, bool)
                    or not 0 <= slot_id <= 80):
                raise MapError(f"canonical slot ledger id {slot_id!r} is "
                               "not a slot 0..80 -> L3")
            if not candidate or not isinstance(candidate, str):
                raise MapError(f"canonical slot ledger id {slot_id} has no "
                               "candidate name -> L3")
            if slot_id in names:
                raise MapError(f"duplicate canonical slot ledger id "
                               f"{slot_id} -> L3")
            names[slot_id] = candidate
        if sorted(names) != list(range(CANONICAL_API_SLOT_COUNT)):
            raise MapError("canonical slot ledger must resolve every slot "
                           "0..80 -> L3")
        return names

    def _check_slots(self, slots):
        if not isinstance(slots, list):
            raise MapError("canonical 'api_slots' must be a list -> L3")
        if len(slots) != CANONICAL_API_SLOT_COUNT:
            raise MapError(
                f"canonical api_slots has {len(slots)} rows, expected "
                f"{CANONICAL_API_SLOT_COUNT} -> L3")
        seen_ids = set()
        for slot in slots:
            if not isinstance(slot, dict):
                raise MapError("canonical api_slot must be an object -> L3")
            if "name" in slot:
                raise MapError(
                    f"canonical api_slot {slot.get('slot')!r} duplicates "
                    "the ledger name: FINAL rows carry no 'name' (identity "
                    "by slot number, names resolve via spec/api_slots.json) "
                    "-> L3")
            number = slot.get("slot")
            if (not isinstance(number, int) or isinstance(number, bool)
                    or not 0 <= number <= 80):
                raise MapError(f"canonical api_slot number {number!r} is "
                               "not a slot 0..80 -> L3")
            if number in seen_ids:
                raise MapError(f"duplicate canonical slot number {number} "
                               "-> L3")
            seen_ids.add(number)
            if slot.get("level") not in CANONICAL_LEVELS:
                raise MapError(f"canonical slot ${number:02X} has unknown "
                               f"level {slot.get('level')!r} -> L3")
            source = slot.get("dev_source")
            if source is not None and not (isinstance(source, str)
                                           and source):
                raise MapError(f"canonical slot ${number:02X} dev_source "
                               "must be a path or null -> L3")
            tests = slot.get("tests")
            if not isinstance(tests, dict):
                raise MapError(f"canonical slot ${number:02X} tests must "
                               "be an object -> L3")
            for key in ("host", "integration_scripts", "dev_targets",
                        "static_units"):
                if key not in tests or not isinstance(tests[key], list):
                    raise MapError(f"canonical slot ${number:02X} tests "
                                   f"misses list {key!r} -> L3")
                for item in tests[key]:
                    if not isinstance(item, str) or not item:
                        raise MapError(
                            f"canonical slot ${number:02X} tests[{key!r}] "
                            f"holds a non-string entry {item!r} -> L3")
        if sorted(seen_ids) != list(range(CANONICAL_API_SLOT_COUNT)):
            raise MapError("canonical api_slots must cover slots 0..80 "
                           "exactly once -> L3")

    def _convert_entry(self, index, entry, targets, static_set,
                       dev_source_to_slot, repo=None):
        level = entry["level"]
        reason = entry["reason"]
        # Entries may list zero commands: an L3 entry with empty tests
        # means "the evidence is L3 itself" (e.g. CI definitions, site
        # sources). select() then runs the full local-L3 sequence
        # fail-closed; a 0-command L0-L2 classification escalates to L3.
        commands = self._convert_tests(entry["tests"], static_set, targets,
                                       f"entry {entry['id']}",
                                       require_commands=False, repo=repo)
        if level == "L4":
            # Manual-only opt-in is never auto-selected: escalate fail-closed
            # to L3 and discard the L4 opt-in commands (validated above) so
            # L4-classified changes run the full local-L3 sequence --
            # running the opt-in commands as "L3" would under-test.
            level = "L3"
            commands = []
            reason = ("canonical-L4-escalated-to-L3 (opt-in only, never a "
                      f"Required substitute): {reason}")
        match_type = entry["match_type"]
        if match_type == "exact":
            patterns = [entry["match"]]
            literal = True
        elif match_type == "exact-list":
            seen = set()
            patterns = []
            for item in entry["match"]:
                if item not in seen:
                    seen.add(item)
                    patterns.append(item)
            literal = True
        else:
            patterns = [entry["match"]]
            literal = False
        rules = []
        for pattern in patterns:
            rules.append(MapRule(
                pattern=pattern, level=level, commands=list(commands),
                reason=f"{reason} (canonical entry {entry['id']} "
                       f"{match_type} {pattern!r} -> {entry['level']})",
                api=dev_source_to_slot.get(pattern, ""), literal=literal))
        return rules

    def _convert_tests(self, tests, static_set, targets, owner,
                       require_commands, repo=None):
        base = Path(repo) if repo is not None else ROOT
        commands = []
        for target in tests["dev_targets"]:
            if not isinstance(target, str) or not target:
                raise MapError(f"{owner}: bad dev target {target!r} "
                               "(must be a non-empty string) -> L3")
            if target not in targets:
                raise MapError(f"{owner}: unknown Makefile target "
                               f"{target!r} (maps may only reference existing "
                               "commands) -> L3")
            commands.append({"make": target})
        for ref in tests["host"]:
            commands.append(self._canonical_host_command(ref, owner, base))
        for script in tests["integration_scripts"]:
            commands.append(self._canonical_script_command(script, owner, base))
        for unit in tests["static_units"]:
            if not isinstance(unit, str) or not unit:
                raise MapError(f"{owner}: bad static unit {unit!r} "
                               "(must be a non-empty string) -> L3")
            if unit not in static_set:
                raise MapError(f"{owner}: unknown static unit {unit!r} "
                               "-> L3")
        if tests["static_units"] and {"make": "check"} not in commands:
            commands.append({"make": "check"})
        if not commands and require_commands:
            raise MapError(f"{owner}: tests must list at least one command "
                           "(a 0-test selection is never PASS) -> L3")
        return commands

    def _canonical_host_command(self, ref, owner, repo=None):
        base = Path(repo) if repo is not None else ROOT
        if not isinstance(ref, str) or not ref:
            raise MapError(f"{owner}: bad host ref {ref!r} -> L3")
        dotted = _DOTTED_HOST_RE.match(ref)
        if dotted:
            module = dotted.group(1)
            if not (base / "tests" / "host" / f"{module}.py").is_file():
                raise MapError(f"{owner}: host ref {ref!r} has no such test "
                               "module -> L3")
            return {"unittest": f"{module}.py", "suite": "host"}
        if ref.startswith("tests/host/") and ref.endswith(".py"):
            pattern = ref[len("tests/host/"):]
            if not pattern or ".." in ref:
                raise MapError(f"{owner}: bad host ref {ref!r} -> L3")
            host_dir = base / "tests" / "host"
            if "*" in pattern or "?" in pattern or "[" in pattern:
                try:
                    names = [item.name for item in host_dir.iterdir()
                             if item.is_file()]
                except OSError as error:
                    raise MapError(f"{owner}: cannot list tests/host: "
                                   f"{error} -> L3") from error
                if not [name for name in names
                        if fnmatch.fnmatchcase(name, pattern)]:
                    raise MapError(f"{owner}: host pattern {ref!r} matches "
                                   "nothing -> L3")
            elif not (host_dir / pattern).is_file():
                raise MapError(f"{owner}: host ref {ref!r} has no such file "
                               "-> L3")
            return {"unittest": pattern, "suite": "host"}
        raise MapError(f"{owner}: bad host ref {ref!r} (dotted "
                       "tests.host.MOD or slash tests/host/PATTERN.py only) "
                       "-> L3")

    def _canonical_script_command(self, script, owner, repo=None):
        base = Path(repo) if repo is not None else ROOT
        if (not isinstance(script, str) or not script.startswith(
                "tests/integration/") or not script.endswith(".py")
                or ".." in script or "*" in script or "?" in script):
            raise MapError(f"{owner}: bad integration script {script!r} "
                           "-> L3")
        raw_target = base / script
        if _path_has_symlink_parent(raw_target):
            raise MapError(f"{owner}: script {script!r} traverses a symlink "
                           "(refused fail-closed) -> L3")
        try:
            if raw_target.is_symlink():
                raise MapError(f"{owner}: script {script!r} is a symlink "
                               "(refused fail-closed) -> L3")
            target = raw_target.resolve()
        except OSError as error:
            raise MapError(f"{owner}: script {script!r} is unreadable: "
                           f"{error} -> L3") from error
        try:
            target.relative_to(base.resolve())
        except ValueError as error:
            raise MapError(f"{owner}: script {script!r} escapes the "
                           "repository -> L3") from error
        if target.is_symlink() or not target.is_file():
            raise MapError(f"{owner}: script {script!r} is not an existing "
                           "repository python script -> L3")
        return {"script": script}


# ---------------------------------------------------------------------------
# Selection.
# ---------------------------------------------------------------------------

def _command_argv(command):
    if "make" in command:
        return ["make", command["make"]]
    if "unittest" in command:
        suite = command.get("suite", "host")
        return ["python3", "-m", "unittest", "discover", "-s",
                f"tests/{suite}", "-p", command["unittest"], "-v"]
    if "script" in command:
        return ["python3", command["script"], *command.get("args", [])]
    raise MapError(f"cannot render command: {command!r}")


def _with_required_env(command):
    """Every emitted command exports OSS_PCE_REQUIRE_CC65=1.

    Fail-closed: a map cannot downgrade this. A missing cc65 therefore FAILs
    instead of becoming a silent SKIP, so missing-tool output is never PASS.
    """
    rendered = dict(command)
    rendered["argv"] = _command_argv(command)
    rendered["env"] = {REQUIRE_CC65_ENV: "1"}
    return rendered


# Selector identity stamped into every fingerprint. Bumped whenever the
# selection semantics change (L3 set, alias rules, api-level handling,
# exact-vs-glob matching, first-match-before-API narrowing, map-repo
# binding, git-repo binding (single-absolute-path + worktree-root
# verification), toolchain execution identity, API-union, untracked-prefix
# parsing). Results from an older selector never reuse against a newer one.
SELECTOR_ID = "test_staging_select/l3full-maprepo-toolchain-untracked-nomix-v5"

# Top-level input paths that must never be content-hashed (private
# reference inputs, not fixtures). A change touching them still classifies
# normally, but the fingerprint records inputs_sha256 "unknown" (refuse
# reuse) instead of reading the bytes.
FORBIDDEN_INPUT_TOPS = frozenset({"rom"})

# Fingerprint values that force reuse refusal (fail-closed). "manual" is
# the L4 opt-in placeholder head; anything unidentifiable refuses.
_UNIDENTIFIABLE = ("unknown", "manual")

# Allowlisted CLI-effective environment captured into the fingerprint.
# No blanket os.environ dump (no secrets, no absolute paths): only the
# mode, the cc65 gate, and a hash of TOOLS_DIR (identity, not the path).
# Any change in these conditions changes the fingerprint so old evidence
# never reuses under new conditions.
_EFFECTIVE_ENV_KEYS = ("MODE", REQUIRE_CC65_ENV)


def _lexical_input_ok(path):
    """Lexical pre-check for a repo-relative input path (no read)."""
    if not isinstance(path, str) or not path:
        return False
    if Path(path).is_absolute():
        return False
    parts = path.replace("\\", "/").split("/")
    if not parts or any(part in ("", ".", "..") for part in parts):
        return False
    return True


def _worktree_target_safe(repo, rel_path):
    """Resolve a worktree input to a safe regular file, or None.

    Fail-closed: lexical failure, forbidden top, any symlink on the
    path (including the final component), a resolved location outside
    the repository, or a non-regular file all return None WITHOUT
    reading any bytes. Callers map None to "unknown" (refuse reuse).
    """
    if not _lexical_input_ok(rel_path):
        return None
    parts = rel_path.replace("\\", "/").split("/")
    if parts[0] in FORBIDDEN_INPUT_TOPS:
        return None
    try:
        repo_resolved = Path(repo).resolve()
    except OSError:
        return None
    cur = Path(repo)
    for part in parts:
        cur = cur / part
        try:
            if cur.is_symlink():
                return None
        except OSError:
            return None
    target = Path(repo) / rel_path
    try:
        if target.is_symlink():
            return None
        resolved = target.resolve()
    except OSError:
        return None
    try:
        if resolved != repo_resolved and repo_resolved not in resolved.parents:
            return None
    except OSError:
        return None
    try:
        if not target.is_file():
            return None
    except OSError:
        return None
    return target


def _map_spec_refused_pre_read(path):
    """Lexical pre-refusal for a map/config spec (no FS read, no bytes).

    Returns True when the spec must be refused BEFORE any read API runs:
    empty/non-string, NUL byte, any ``..`` segment (repo-outside escape),
    or any ``rom`` segment (direct forbidden-area spec). Temp-dir
    authorized fixtures (``--map`` test paths) never contain these
    segments, so they pass; a sentinel under ``rom/`` or a ``..`` escape
    is refused with zero read-API calls (spy-proven).
    """
    if isinstance(path, os.PathLike):
        try:
            path = os.fspath(path)
        except (OSError, ValueError, TypeError):
            return True
    if not isinstance(path, (str, bytes)):
        return True
    text = path.decode("utf-8", errors="strict") if isinstance(path, bytes) else path
    if not text or "\0" in text:
        return True
    segments = text.replace("\\", "/").split("/")
    for segment in segments:
        if segment == "..":
            return True
        if segment == "rom":
            return True
    return False


def _path_has_symlink_parent(path):
    """True when any parent prefix or the final component is a symlink.

    Uses only lstat-style ``is_symlink`` calls (never ``read_bytes``), so
    a sentinel behind a parent symlink is never opened. Missing prefixes
    simply report False for that prefix; the caller still requires a
    regular final file.
    """
    try:
        candidate = Path(path)
        if candidate.is_symlink():
            return True
        absolute = candidate.absolute()
        # Walk every prefix from the anchor down to the parent.
        cur = absolute.parent
        seen = set()
        while True:
            key = str(cur)
            if key in seen:
                break
            seen.add(key)
            try:
                if cur.is_symlink():
                    return True
            except OSError:
                return True
            parent = cur.parent
            if parent == cur:
                break
            cur = parent
    except OSError:
        return True
    return False


def _read_bytes_strict(path, repo=None):
    """Read a map/config file only if it is a safe regular file.

    Common safe-read boundary shared by the fingerprint side
    (``_map_identity``) and the loader side (both map loaders, the slot
    ledger, the Makefile, ``dependencies.json``): direct forbidden specs
    (``rom`` segment) and ``..`` escapes are refused pre-read with zero
    read-API calls, any symlink on the path (final component AND every
    parent prefix) is refused, and non-regular files (directories, fifos,
    devices, missing) are refused WITHOUT reading. Returns the bytes, or
    raises OSError on refusal (callers map to unknown/MapError). The
    optional ``repo`` refines the forbidden-top check for repo-relative
    specs; absolute temp-dir fixtures (explicit ``--map`` test paths)
    pass the same filesystem-level checks without a repo anchor.
    """
    if _map_spec_refused_pre_read(path):
        raise OSError(f"refused map/config spec pre-read: {path}")
    candidate = Path(path)
    try:
        if candidate.is_symlink():
            raise OSError(f"refused symlink map/config read: {path}")
    except OSError as error:
        raise OSError(f"refused symlink map/config read: {path}: {error}") from error
    if _path_has_symlink_parent(path):
        raise OSError(f"refused parent-symlink map/config read: {path}")
    try:
        if not candidate.is_file():
            raise OSError(f"refused non-regular map/config read: {path}")
    except OSError as error:
        raise OSError(f"refused non-regular map/config read: {path}: {error}") from error
    return candidate.read_bytes()


def _git_blob_is_regular(repo, spec, path):
    """True only if the git object at spec is a regular blob (no read)."""
    try:
        proc = _run_git(repo, "ls-tree", spec, "--", path)
    except GitError:
        return False
    if proc.returncode != 0:
        return False
    try:
        out = proc.stdout.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError:
        return False
    if not out:
        return False
    # "<mode> <type> <sha>\t<path>"; regular files are 100644/100755.
    # Symlinks (120000), gitlinks (160000), trees are non-regular -> False.
    mode = out.split(" ", 1)[0]
    return mode in ("100644", "100755")


def _git_staged_is_regular(repo, path):
    """True only if the index entry is a regular blob (no read)."""
    try:
        proc = _run_git(repo, "ls-files", "-s", "--", path)
    except GitError:
        return False
    if proc.returncode != 0:
        return False
    try:
        out = proc.stdout.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError:
        return False
    if not out:
        return False
    mode = out.split(" ", 1)[0]
    return mode in ("100644", "100755")


def _input_content_hash(repo, origin, status, path):
    """Hash the bytes behind one changed input, or a stable marker.

    Returns the sha256 of the input bytes, the string "deleted" for a
    delete (no bytes to hash), or "unknown" when the bytes cannot be
    established without reading a forbidden area (fail-closed: the caller
    turns the whole fingerprint unidentifiable). Never reads forbidden
    areas: an escaping path, a symlink/.. traversal, a repo-outside
    resolution, a non-regular file, or a path under rom/ is "unknown"
    unread. Direct forbidden specs are refused pre-read (no git/FS call).
    """
    if not _lexical_input_ok(path):
        return "unknown"
    parts = path.replace("\\", "/").split("/")
    if parts[0] in FORBIDDEN_INPUT_TOPS:
        return "unknown"
    if status == "D":
        return "deleted"
    try:
        if origin == "committed":
            if not _git_blob_is_regular(repo, "HEAD", path):
                # Covers missing blobs (deleted-after-rename etc.) and
                # non-regular blobs (symlink/gitlink): fail-closed. The
                # caller maps a genuinely deleted path via status "D";
                # anything else unconfirmable refuses reuse instead of
                # hashing link text or failing open.
                return "unknown"
            proc = _run_git(repo, "show", f"HEAD:{path}")
            if proc.returncode != 0:
                return "unknown"
            return hashlib.sha256(proc.stdout).hexdigest()
        if origin == "staged":
            if not _git_staged_is_regular(repo, path):
                return "unknown"
            proc = _run_git(repo, "show", f":{path}")
            if proc.returncode != 0:
                return "unknown"
            return hashlib.sha256(proc.stdout).hexdigest()
        target = _worktree_target_safe(repo, path)
        if target is None:
            # Missing worktree file: it may be deleted (not yet staged).
            # Distinguish "deleted" from "unreadable symlink/outside"
            # without reading: a lexical path whose parent chain has no
            # symlink and whose resolved location is inside the repo but
            # which simply does not exist is "deleted"; any symlink,
            # outside resolution, forbidden top, or non-regular is
            # "unknown" (refuse reuse).
            try:
                candidate = Path(repo) / path
                if candidate.is_symlink():
                    return "unknown"
                if candidate.exists():
                    return "unknown"
            except OSError:
                return "unknown"
            # Re-verify the missing path would resolve inside the repo.
            try:
                repo_resolved = Path(repo).resolve()
                resolved = (Path(repo) / path).resolve()
                if (resolved != repo_resolved
                        and repo_resolved not in resolved.parents):
                    return "unknown"
            except OSError:
                return "unknown"
            return "deleted"
        return hashlib.sha256(target.read_bytes()).hexdigest()
    except (GitError, OSError, subprocess.SubprocessError, ValueError):
        return "unknown"


def _map_identity(map_path, repo=None):
    """Identify the impact map by content hash plus envelope id.

    Returns (map_sha256, map_id). Anything missing, unreadable,
    symlink/parent-symlink/non-regular, forbidden-spec/``..``-escape, or
    unparseable is ("unknown", "unknown") so reuse is refused
    fail-closed. Reads via the common safe-read boundary (same as the
    loaders) so a symlink map, a parent-symlink traversal, a direct
    forbidden spec, or an outside-repo ``..`` never opens the sentinel:
    the read API is uncalled (spy-proven). The envelope id is the
    provisional ``format`` tag or ``name/v<schema>@<base_commit>`` for
    the canonical map; bytes alone are not trusted to identify the
    schema.
    """
    if not map_path:
        return "unknown", "unknown"
    try:
        raw = _read_bytes_strict(map_path, repo)
    except OSError:
        return "unknown", "unknown"
    except OSError:
        return "unknown", "unknown"
    digest = hashlib.sha256(raw).hexdigest()
    try:
        document = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return "unknown", "unknown"
    if not isinstance(document, dict):
        return "unknown", "unknown"
    if "format" in document:
        tag = document.get("format")
        if not isinstance(tag, str) or not tag:
            return "unknown", "unknown"
        return digest, f"provisional:{tag}"
    name = document.get("name")
    schema = document.get("schema_version")
    base_commit = document.get("base_commit")
    if not isinstance(name, str) or not name:
        return "unknown", "unknown"
    if not isinstance(base_commit, str) or not _HEX40_RE.match(base_commit):
        return digest, f"{name}/v{schema}@unidentified"
    return digest, f"{name}/v{schema}@{base_commit}"


def _normal_cli_map_target(repo, map_path):
    """Resolve the normal-CLI map input to a repo-bound path (ZERO reads).

    Issue #852, review 6096132688 B-2: the normal CLI path used to
    fingerprint (read) the map BEFORE the loader's containment check, so
    an absolute --map from another checkout was opened twice (initial
    fingerprint plus finalise re-generation, plus once more on the
    GitError path) before being refused. This gate unifies
    containment/lexical/symlink refusal BEFORE the first read of any map
    input on the normal CLI path -- the default repo-bound map, a
    relative --map, an absolute --map, and the GitError-path raw
    --map alike. It performs only lexical checks plus lstat-style
    ``is_symlink`` probes (never ``read_bytes``): a refused spec raises
    MapError with zero read-API calls (spy-proven), the caller reports
    full L3 with an ``unknown`` map identity (reuse refused), and no
    traceback escapes. The explicit synthetic-loader lane (an explicit
    ``loader=`` argument) bypasses this gate deliberately: temp-only
    fixtures outside any repo stay loadable there and never mix into the
    normal path.
    """
    try:
        base_resolved = Path(repo).resolve()
    except (OSError, ValueError) as error:
        raise MapError(f"normal-CLI map repo {repo!r} is not resolvable: "
                       f"{error} -> L3") from error
    if map_path is None:
        return str(Path(repo) / "tools" / "test_staging_map.json")
    if _map_spec_refused_pre_read(map_path):
        raise MapError(
            f"normal-CLI --map {map_path!r} refused pre-read "
            "(empty, NUL, '..' escape, or forbidden-area spec) -> L3")
    if isinstance(map_path, os.PathLike):
        try:
            given = os.fspath(map_path)
        except (OSError, ValueError, TypeError) as error:
            raise MapError(f"normal-CLI --map {map_path!r} is not a "
                           f"usable spec: {error} -> L3") from error
    else:
        given = map_path
    if isinstance(given, bytes):
        try:
            given = os.fsdecode(given)
        except (OSError, ValueError, UnicodeError) as error:
            raise MapError(f"normal-CLI --map {map_path!r} is not a "
                           f"usable spec: {error} -> L3") from error
    if not isinstance(given, str) or not given:
        raise MapError(f"normal-CLI --map {map_path!r} is not a usable "
                       "spec -> L3")
    candidate = Path(given)
    relative = not candidate.is_absolute()
    if relative:
        candidate = base_resolved / candidate
    try:
        if candidate.is_symlink():
            raise MapError(f"normal-CLI --map {map_path!r} is a symlink "
                           "(refused fail-closed) -> L3")
        if _path_has_symlink_parent(candidate):
            raise MapError(
                f"normal-CLI --map {map_path!r} traverses a symlink "
                "(refused fail-closed) -> L3")
        resolved = candidate.resolve()
    except MapError:
        raise
    except (OSError, ValueError) as error:
        raise MapError(f"normal-CLI --map {map_path!r} is not resolvable: "
                       f"{error} -> L3") from error
    try:
        inside = (resolved == base_resolved
                  or base_resolved in resolved.parents)
    except (OSError, ValueError):
        inside = False
    if not inside:
        raise MapError(
            f"normal-CLI --map {map_path!r} is outside the bound repo "
            f"{repo}: cross-repo mixing refused fail-closed "
            "-> L3 (bind target repo+map+ledger+Makefile+dependencies "
            "consistently; use a repo-relative --map or the repo-bound "
            "default)")
    if relative:
        # Same repo-relative spelling the CLI path always used (the gate
        # verified the resolved location above; the loader re-verifies).
        return str(Path(repo) / given)
    return str(candidate)


def _resolve_pinned_tool_path(tools_root, revision, name, binary):
    """Resolve the actually-executed file for one pinned tool (no run).

    Resolution mirrors the real build side (tools/environment.py
    executable(): explicit ``CA65``/``LD65`` env override, else the
    pinned ``<TOOLS_DIR>/cc65-<revision>/bin/*`` location, via
    shutil.which). An override carrying a forbidden (``rom`` segment) or
    outside (``..``) spec is refused BEFORE which() runs, so a forbidden
    input causes zero filesystem probing; the found path is vetted again
    before any read or execution (see _tool_execution_target_safe).
    """
    override = os.environ.get(binary.upper())
    if override:
        if _map_spec_refused_pre_read(override):
            return None
        import shutil as _shutil
        found = _shutil.which(override)
        return Path(found) if found else None
    return Path(tools_root) / f"{name}-{revision}" / "bin" / binary


def _tool_execution_target_safe(resolved, repo):
    """Vet an executed-tool path BEFORE any read or execution (or None).

    The unified safe boundary for the toolchain side, mirroring the
    map/config side (_read_bytes_strict): lexical pre-refusal of
    forbidden (``rom`` segment) and outside (``..``) specs with zero FS
    calls, then lstat-only symlink checks (final component AND every
    parent prefix), then in-repo refusal, then non-regular refusal. No
    bytes are read and nothing is executed on refusal. ``repo`` may be
    None (lexical + symlink checks only); pass the target repo so
    in-repo tool locations are refused too.
    """
    if _map_spec_refused_pre_read(resolved):
        return None
    try:
        text = (os.fspath(resolved) if isinstance(resolved, os.PathLike)
                else resolved)
        candidate = Path(text)
        if candidate.is_symlink():
            return None
        if _path_has_symlink_parent(candidate):
            return None
        if repo is not None:
            base = Path(repo).resolve()
            got = candidate.resolve()
            if got == base or base in got.parents:
                return None
        if candidate.is_symlink() or not candidate.is_file():
            return None
    except (OSError, ValueError):
        return None
    return candidate


def _tools_dir_vetted_path(raw, repo):
    """Vet TOOLS_DIR BEFORE any enumerate/stat/read/execute (or None).

    Lexical forbidden/outside specs are refused with zero FS calls; only
    then do lstat-only symlink checks and the in-repo comparison run. A
    refused TOOLS_DIR makes both the toolchain and the env verdict
    unidentifiable (refuse reuse), so neither path can bypass the other.
    """
    if _map_spec_refused_pre_read(raw):
        return None
    try:
        candidate = Path(raw)
        if candidate.is_symlink():
            return None
        if _path_has_symlink_parent(candidate):
            return None
        resolved = candidate.resolve()
        base = Path(repo).resolve()
        if resolved == base or base in resolved.parents:
            return None
    except (OSError, ValueError):
        return None
    return resolved


def _hash_tool_file(path, repo=None):
    """Hash one executed tool file, or None when unconfirmable (no run)."""
    target = _tool_execution_target_safe(path, repo)
    if target is None:
        return None
    try:
        return hashlib.sha256(target.read_bytes()).hexdigest()
    except (OSError, ValueError):
        return None


def _verify_tool_reports_pin(tool_path, pin_prefix, repo=None):
    """True only if ``tool --version`` reports the pinned revision.

    The candidate is vetted by the unified boundary BEFORE execution: a
    refused path returns False with zero subprocess calls.
    """
    target = _tool_execution_target_safe(tool_path, repo)
    if target is None:
        return False
    try:
        proc = subprocess.run(
            [str(target), "--version"], capture_output=True, text=True,
            timeout=120)
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    if proc.returncode != 0:
        return False
    output = (proc.stdout or "") + (proc.stderr or "")
    return bool(pin_prefix) and pin_prefix in output


def _cli_effective_env(mode, extra_env=None, repo=None):
    """Capture the CLI-effective conditions allowlist (no dump, no secrets).

    Only MODE, OSS_PCE_REQUIRE_CC65, and a sha256 of the TOOLS_DIR *path
    string* (identity of the location, never its contents, never the raw
    path) are recorded. Tool *content* binding lives solely in
    _declared_toolchain (hashes of the executed ca65/ld65 bytes): this
    function performs zero directory enumeration, zero reads, and zero
    subprocess calls, so it cannot bypass the toolchain refusal -- a
    same-path content change alters the toolchain verdict while this
    identity stays put. A lexically forbidden (``rom``/``..``) or, when
    ``repo`` is given, symlink-parent/in-repo TOOLS_DIR is unidentifiable
    ("unknown", refuse reuse); unset stays "unset". Any change in
    these conditions changes the fingerprint so old evidence never reuses.
    extra_env (unit-test overrides) must be a str->str dict; anything
    else marks the env malformed (refuse reuse).
    """
    try:
        tools_raw = os.environ.get("TOOLS_DIR", "")
        if not tools_raw:
            tools_hash = "unset"
        elif repo is not None:
            tools_hash = ("unknown"
                           if _tools_dir_vetted_path(tools_raw, repo) is None
                           else hashlib.sha256(
                               tools_raw.encode()).hexdigest())
        elif _map_spec_refused_pre_read(tools_raw):
            tools_hash = "unknown"
        else:
            tools_hash = hashlib.sha256(tools_raw.encode()).hexdigest()
    except (OSError, ValueError, UnicodeError):
        tools_hash = "unknown"
    env = {
        "MODE": str(mode),
        REQUIRE_CC65_ENV: str(os.environ.get(REQUIRE_CC65_ENV, "")),
        "TOOLS_DIR_sha256": tools_hash,
    }
    if extra_env is None:
        return env
    if not isinstance(extra_env, dict):
        return {"__malformed__": "non-dict-extra-env"}
    for key, value in extra_env.items():
        if not isinstance(key, str) or not isinstance(value, str):
            return {"__malformed__": "non-string-extra-env"}
        env[key] = value
    return env


def _declared_toolchain(repo):
    """Declared toolchain pins plus ACTUAL execution-identity verification.

    Returns a JSON string binding the declared pins to the bytes and
    ``--version`` output of the actually-executed cc65 tools (the same
    ``revision[:9] in --version`` check the build uses in tools/build.py,
    resolved the same way tools/environment.py executable() resolves:
    explicit override via shutil.which, else the pinned
    ``<TOOLS_DIR>/cc65-<revision>/bin/*`` location), or "unknown" when
    identity is unconfirmable. Declared revision plus a TOOLS_DIR
    path/existence check is NOT execution identity: the same path with
    different tool bytes, a different real checkout revision, an empty
    TOOLS_DIR, or a declared-pin-vs-entity mismatch must all refuse
    reuse. Unconfirmable (missing/unparseable dependencies.json, missing
    revisions, forbidden/outside TOOLS_DIR specs vetted before any
    enumerate/stat/read, symlink-parent/irregular reads, TOOLS_DIR inside
    the repo, unresolvable/missing/overridden-forbidden tools,
    ``--version`` failure or pin mismatch) -> "unknown". No blanket env
    collection, no secrets.
    """
    base = Path(repo)
    candidate = base / "tools" / "dependencies.json"
    try:
        deps = json.loads(
            _read_bytes_strict(candidate, base).decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return "unknown"
    try:
        if not isinstance(deps, dict) or not deps:
            return "unknown"
        pins = {}
        for name in sorted(deps):
            entry = deps[name]
            if not isinstance(entry, dict):
                return "unknown"
            revision = entry.get("revision")
            if not isinstance(revision, str) or not revision:
                return "unknown"
            pins[name] = revision
    except (KeyError, AttributeError, ValueError, TypeError):
        return "unknown"
    # Execution identity: TOOLS_DIR must be a confirmable external real
    # directory (not a symlink, not inside the repo, non-empty). The
    # vetted path refuses forbidden (rom segment) and outside (..)
    # specs BEFORE any enumerate/stat/read/execute, so neither this
    # verdict nor the _cli_effective_env identity can be bypassed.
    try:
        raw = os.environ.get("TOOLS_DIR", "")
    except (OSError, ValueError, UnicodeError):
        return "unknown"
    if _tools_dir_vetted_path(raw, repo) is None:
        return "unknown"
    try:
        tools_path = Path(raw)
        if not tools_path.is_dir():
            return "unknown"
        try:
            if not any(tools_path.iterdir()):
                return "unknown"
        except OSError:
            return "unknown"
    except OSError:
        return "unknown"
    # Confirmable execution entities: the actually-used cc65 binaries.
    # Resolve exactly like tools/environment.py (explicit override or the
    # pinned ``<TOOLS_DIR>/cc65-<revision>/bin/*`` location); each
    # candidate is vetted (forbidden/in-repo/symlink/non-regular refusal)
    # BEFORE its bytes are read AND before ``--version`` is executed.
    cc65_rev = pins.get("cc65")
    if not cc65_rev:
        return "unknown"
    tool_hashes = {}
    for binary in ("ca65", "ld65"):
        resolved = _resolve_pinned_tool_path(raw, cc65_rev, "cc65", binary)
        digest = _hash_tool_file(resolved, repo)
        if digest is None:
            return "unknown"
        if not _verify_tool_reports_pin(resolved, cc65_rev[:9], repo):
            return "unknown"
        tool_hashes[binary] = digest
    return json.dumps({"pins": pins, "tools": tool_hashes},
                      sort_keys=True)


def evidence_fingerprint(repo, changed, mode="debug", extra_env=None,
                         map_path=None, api=None, level=None, commands=None):
    """Build the reuse fingerprint for the current selection inputs.

    Covers content plus selection plus execution conditions: HEAD, BASE,
    every committed/staged/unstaged/untracked input (origin, status, path
    AND bytes -- the same path with different bytes is a different
    fingerprint), the map (content hash plus envelope id), the selector
    id, the API choice, the selected level, the selected command set, the
    toolchain pins, and the env/mode conditions. Forbidden areas are never
    hashed: touching them (or any unidentifiable input: unknown/manual
    HEAD, missing map, unknown toolchain) marks the fingerprint
    unidentifiable so reuse is refused. Prefer refuse-on-incomplete: when
    in doubt the fingerprint says "unknown" and evidence_reusable says
    no.
    """
    entries = []
    for origin, status, path in changed.all_classifiable():
        entries.append(
            f"{origin}:{status}:{path}:"
            f"{_input_content_hash(repo, origin, status, path)}")
    for path in sorted(changed.untracked):
        entries.append(
            f"untracked:??:{path}:"
            f"{_input_content_hash(repo, 'untracked', '??', path)}")
    if not entries:
        inputs_digest = hashlib.sha256(b"").hexdigest()
    elif any(entry.rsplit(":", 1)[-1] == "unknown" for entry in entries):
        inputs_digest = "unknown"
    else:
        inputs_digest = hashlib.sha256(
            "\n".join(sorted(entries)).encode()).hexdigest()
    if changed.head in _UNIDENTIFIABLE or changed.base in _UNIDENTIFIABLE:
        inputs_digest = "unknown"
    map_sha256, map_id = _map_identity(map_path, repo)
    toolchain = _declared_toolchain(repo)
    if commands is None:
        commands_digest = "unknown"
    else:
        try:
            commands_digest = hashlib.sha256(
                json.dumps(commands, sort_keys=True,
                           default=str).encode()).hexdigest()
        except (ValueError, TypeError):
            commands_digest = "unknown"
    return {
        "head": changed.head,
        "base": changed.base,
        "inputs_sha256": inputs_digest,
        "map_sha256": map_sha256,
        "map_id": map_id,
        "selector": SELECTOR_ID,
        "api": "none" if api is None else str(api),
        "level": "unknown" if level is None else str(level),
        "commands_sha256": commands_digest,
        "python": platform.python_version(),
        "platform": sys.platform,
        "toolchain": toolchain,
        "mode": mode,
        "env": _cli_effective_env(mode, extra_env, repo),
    }


def evidence_reusable(stored, current):
    """A stored result is reusable only on exact fingerprint equality.

    Prefer-refuse-on-incomplete: any missing key, None, empty string,
    wrong-typed value, malformed envelope, or unidentifiable marker
    ("unknown"/"manual") on EITHER side refuses. In particular
    evidence_reusable({}, {}) is False (no keys, no identity, no reuse).
    Unknown toolchains never reuse: without confirmable pins plus a
    confirmable TOOLS_DIR execution identity, "same toolchain" cannot be
    established, so reuse is refused fail-closed. The same refusal
    applies to any unidentifiable input (unknown/manual HEAD, unknown
    inputs from forbidden areas/symlinks/unreadable bytes,
    missing/unparseable/symlink map, unknown selector, unknown
    level/command set, malformed env): when identity cannot be
    established, reuse is refused.
    """
    if not isinstance(stored, dict) or not isinstance(current, dict):
        return False
    required = ("head", "base", "inputs_sha256", "map_sha256", "map_id",
                "selector", "api", "level", "commands_sha256",
                "python", "platform", "toolchain", "mode", "env")
    for key in required:
        if key not in stored or key not in current:
            return False
        stored_value = stored.get(key)
        current_value = current.get(key)
        if stored_value is None or current_value is None:
            return False
        if stored_value != current_value:
            return False
    # Type/shape gate: malformed fingerprints never reuse.
    for key in ("head", "base", "inputs_sha256", "map_sha256",
                "map_id", "selector", "api", "level",
                "commands_sha256", "python", "platform",
                "toolchain", "mode"):
        stored_value, current_value = stored[key], current[key]
        if (not isinstance(stored_value, str)
                or not isinstance(current_value, str)):
            return False
        if not stored_value or not current_value:
            return False
    for key in ("env",):
        if (not isinstance(stored[key], dict)
                or not isinstance(current[key], dict)):
            return False
        for side in (stored[key], current[key]):
            for env_key, env_value in side.items():
                if (not isinstance(env_key, str)
                        or not isinstance(env_value, str)):
                    return False
        if "__malformed__" in stored[key] or "__malformed__" in current[key]:
            return False
    # Unidentifiable markers on EITHER side refuse (stored unknown must
    # never satisfy a current unknown: equality of two unknowns is not
    # identity).
    for key in ("head", "base", "inputs_sha256", "map_sha256", "map_id",
                "selector", "level", "commands_sha256", "toolchain"):
        if stored[key] in _UNIDENTIFIABLE or current[key] in _UNIDENTIFIABLE:
            return False
    # Selector pinning: results from another selector never reuse.
    if stored["selector"] != SELECTOR_ID or current["selector"] != SELECTOR_ID:
        return False
    return True


def _l3_selection(reasons, fallback_reason, fingerprint):
    """Every L3 path emits the full mandatory local-L3 set.

    L3 is never a subset: a partial (deduped) command list is never
    displayed as full, so callers pass only reasons -- the selected set is
    always FULL_L3_SEQUENCE (partial=False, unselected=[]). A map L3 rule
    listing fewer commands (e.g. a catch-all naming only test/test-real-core
    /check) still runs the full sequence; the rule's subset is
    informational, never the executed set.
    """
    full = [_with_required_env(command) for command in FULL_L3_SEQUENCE]
    return Selection(level="L3", selected=full,
                     unselected=[], reasons=reasons, fallback=True,
                     fallback_reason=fallback_reason, partial=False,
                     fingerprint=fingerprint)


def _rule_matches(path, rule):
    """Match one input path against one rule (B-4: kind-preserving).

    Canonical exact/exact-list rules (literal=True) compare literally
    so metacharacters ([, *, ?) never act as globs; only glob rules use
    fnmatch. Provisional rules are glob-only (literal=False).
    """
    if getattr(rule, "literal", False):
        return path == rule.pattern
    return fnmatch.fnmatchcase(path, rule.pattern)


def select(changed, rules, apis, api=None, fingerprint=None,
           match_mode="max"):
    """Classify every change; anything unclassifiable selects L3.

    match_mode "max" (provisional, isolated) takes the highest matching
    rule level. match_mode "first-match" (Task-A canonical) applies
    entry order BEFORE any --api narrowing: the first path-matching rule
    wins, and a requested --api must equal that rule's api tag, otherwise
    the classification is uncertain -> full L3. --api never skips a
    preceding L3 safety rule, never drops needed commands via
    intersection, and never lowers the required level/commands. Both
    modes are fail-closed: no match, uncertain match, or an L3 match
    selects L3.
    """
    fingerprint = fingerprint or {}
    if match_mode not in ("max", "first-match"):
        return _l3_selection(
            [f"internal: unknown match_mode {match_mode!r} -> L3"],
            "internal-match-mode", fingerprint)
    if not list(changed.all_classifiable()) and not changed.untracked:
        return _l3_selection(
            ["empty-change-set: no committed/staged/unstaged/untracked "
             "inputs, so no L0-L2 subset is selectable"],
            "empty-change-set (reuse only with a matching evidence "
            "fingerprint, otherwise run L3)", fingerprint)
    if changed.untracked:
        return _l3_selection(
            [f"untracked:{path}: untracked files are unregistered by "
             "definition -> L3" for path in sorted(changed.untracked)],
            "untracked-inputs", fingerprint)
    if api is not None and api not in apis:
        return _l3_selection(
            [f"api:{api}: unknown to the impact map -> L3"],
            f"api-not-in-map ({api!r})", fingerprint)
    level_rank = 0
    selected = []
    reasons = []
    for origin, _status, path in changed.all_classifiable():
        if match_mode == "first-match":
            # Canonical: first path match wins, independent of --api.
            # The --api tag is checked AFTER (narrowing), never as a
            # pre-filter, so a preceding L3 safety rule cannot be skipped.
            first = next((rule for rule in rules
                          if _rule_matches(path, rule)), None)
            if first is None:
                return _l3_selection(
                    reasons + [f"{origin}:{path}: no map rule matches -> L3"],
                    "unclassified-file", fingerprint)
            if api is not None and first.api != api:
                return _l3_selection(
                    reasons + [f"{origin}:{path}: first-match rule "
                               f"{first.pattern!r} carries api "
                               f"{first.api!r}, requested api {api!r} "
                               "mismatches (uncertain) -> L3"],
                    "api-first-match-mismatch", fingerprint)
            hits = [first]
        else:
            hits = [rule for rule in rules
                    if _rule_matches(path, rule)
                    and (api is None or rule.api == api)]
            if not hits:
                return _l3_selection(
                    reasons + [f"{origin}:{path}: no map rule matches -> L3"],
                    "unclassified-file", fingerprint)
        top = max(LEVEL_RANK[rule.level] for rule in hits)
        level_rank = max(level_rank, top)
        for rule in hits:
            reasons.append(f"{origin}:{path}: {rule.reason} "
                           f"(pattern {rule.pattern!r} -> {rule.level})")
            selected.extend(rule.commands)
    if api is not None:
        api_level = apis[api].get("level", "L1")
        if api_level not in LEVEL_RANK:
            return _l3_selection(
                reasons + [f"api:{api}: slot level {api_level!r} is not "
                             "a selectable level -> L3"],
                f"api-level-unknown ({api!r})", fingerprint)
        if api_level in ("L3", "L4"):
            # --api must never lower the required level: a slot whose own
            # level is L3 (e.g. not DEV-implemented) or manual-only L4
            # runs the full local-L3 sequence, never a filtered subset.
            reasons.append(f"api:{api}: {apis[api]['reason']} "
                           f"(slot level {api_level} requires full L3)")
            return _l3_selection(reasons, "api-slot-requires-L3",
                                 fingerprint)
        wanted = apis[api]["commands"]
        # B-3 (Wave2): never drop path evidence via overwrite. The
        # selected set is the UNION of the path-rule set and the API slot
        # set (deduped, path order first): a path-rule A+B with API slot B
        # keeps A; differing path/API sets and multi-change same-API inputs
        # keep every needed command. --api still never lowers the required
        # level (level_rank takes the max), and an L3 path or L3 slot still
        # escalates to the full local-L3 sequence below, so an L3 safety
        # rule is always kept (as full L3, never as a filtered subset).
        merged = []
        for command in list(selected) + list(wanted):
            if command not in merged:
                merged.append(command)
        selected = merged
        reasons.append(f"api:{api}: {apis[api]['reason']}")
        level_rank = max(level_rank, LEVEL_RANK[api_level])
    deduped = []
    for command in selected:
        if command not in deduped:
            deduped.append(command)
    if not deduped:
        # L3 entries may legitimately list zero commands (their evidence is
        # L3 itself): run the full local-L3 sequence fail-closed. A 0-command
        # L0-L2 classification can never run as-is, so it also becomes L3.
        if level_rank == LEVEL_RANK["L3"]:
            return _l3_selection(reasons, "map-rule-requires-L3", fingerprint)
        return _l3_selection(
            reasons + ["empty-selection: classification produced zero "
                       "commands; a 0-test run is never PASS -> L3"],
            "empty-selection", fingerprint)
    level = LEVELS[level_rank]
    if level == "L3":
        # Full regression: the matched subset (even non-empty) is
        # informational only and is never emitted as the L3 set.
        return _l3_selection(
            reasons + [f"l3-requires-full: {len(deduped)} matched "
                         "command(s) discarded in favour of the full "
                         "local-L3 sequence; a partial set is never "
                         "displayed as full"],
            "map-rule-requires-L3", fingerprint)
    rendered = [_with_required_env(cmd) for cmd in deduped]
    full_rendered = [_with_required_env(
        {"make": t} if isinstance(t, str) else t) for t in FULL_L3_SEQUENCE]
    have = {json.dumps(cmd["argv"], sort_keys=True) for cmd in rendered}
    unselected = [cmd for cmd in full_rendered
                  if json.dumps(cmd["argv"], sort_keys=True) not in have]
    return Selection(level=level, selected=rendered, unselected=unselected,
                     reasons=reasons, fallback=False, fallback_reason="none",
                     partial=True, fingerprint=fingerprint)


def render(selection):
    """Explicit selected/unselected/reason/fallback report (always printed)."""
    lines = ["TEST-STAGING SELECTION (prototype, Issue #852)",
             f"level: {selection.level}",
             f"fallback: {'yes' if selection.fallback else 'no'}",
             f"fallback-reason: {selection.fallback_reason}"]
    if selection.level in ("L0", "L1", "L2"):
        lines.append(f"PARTIAL: {selection.level} success is NOT full-regression "
                     "success and is never MERGE_READY evidence on its own.")
    if selection.level == "L3":
        lines.append("LOCAL-L3 vs REQUIRED-CI: this selection runs the local "
                     "full-regression commands below. Required CI (checks "
                     + ", ".join(REQUIRED_CI_CHECKS) + ") is separate "
                     "evidence from CI trust boundaries (trusted guard, "
                     "artifact handoff) and is still mandatory for MERGE_READY.")
        lines.append("SELECTION-IS-NOT-SUCCESS: selecting commands is not "
                     "running them; L3 success requires executing every "
                     "command above with exit 0. This report alone is never "
                     "PASS evidence.")
    lines.append("SKIP-POLICY: a SKIP, BLOCKED-as-pass, missing-tool, or "
                 "0-test result is never PASS; every command below exports "
                 f"{REQUIRE_CC65_ENV}=1 so a missing cc65 FAILs.")
    lines.append(f"SELECTED ({len(selection.selected)}):")
    for command in selection.selected:
        # B-5: displayed command must equal the executed argv. shlex.join
        # quotes whitespace/metachars so the report parses back to argv.
        lines.append(f"  env {REQUIRE_CC65_ENV}=1 -- "
                     + shlex.join([str(part) for part in command["argv"]]))
    lines.append(f"UNSELECTED ({len(selection.unselected)}):")
    for command in selection.unselected:
        lines.append("  (not run at this level) "
                     + shlex.join([str(part) for part in command["argv"]]))
    lines.append(f"REASONS ({len(selection.reasons)}):")
    for reason in selection.reasons:
        lines.append(f"  {reason}")
    lines.append("EVIDENCE-FINGERPRINT: "
                 + json.dumps(selection.fingerprint, sort_keys=True))
    lines.append("REUSE-RULE: reuse a prior result only when its fingerprint "
                 "matches exactly (same HEAD/BASE, same input bytes, same "
                 "map content+id, same selector, same API/level/command set, "
                 "same toolchain/env/conditions); "
                 "an old HEAD result is never claimed for a new HEAD, and "
                 "a same-path result with different bytes never reuses.")
    return "\n".join(lines) + "\n"


# Normal-CLI vs explicit-synthetic map lanes: the normal CLI path always
# starts the canonical loader bound to --repo (default
# <repo>/tools/test_staging_map.json, or a repo-relative --map -- both
# inside the target checkout; an absolute --map outside it is cross-repo
# mixing and refused fail-closed). ProvisionalJsonMapLoader is isolated
# to explicit synthetic-test use (callers pass
# loader=ProvisionalJsonMapLoader() directly) with its own temp-fixture
# boundary. There is deliberately no ROOT-anchored default constant: a
# silent fallback to this checkout's files would mis-select when --repo
# differs from ROOT. A missing or inconsistent canonical map is L3
# fail-closed.


def run_selector(repo, base, map_path=None, api=None, fetch=False,
                 mode="debug", loader=None, runner=_real_runner,
                 requested_level=None):
    """Collect inputs, load the map, select, and return (selection, report).

    The normal path uses a CanonicalJsonMapLoader bound to ``repo``: the
    default map is ``<repo>/tools/test_staging_map.json`` and an explicit
    --map is either repo-relative (resolved inside ``repo``) or absolute
    inside ``repo`` -- an absolute --map from another checkout is refused
    fail-closed (cross-repo mixing), never mixed with this repo's
    ledger/Makefile/diff/pins. The refusal happens in
    _normal_cli_map_target BEFORE the first map read, so the fingerprint,
    the loader, the finalise re-generation, and the GitError path all see
    zero bytes of a foreign map (rejected identity is unknown, reuse
    refused). Pass an explicit loader only for isolated synthetic-test
    fixtures. The returned fingerprint is finalised with
    the map identity, API choice, level, and command set so reuse
    comparisons cover content plus selection plus conditions.
    """
    if requested_level in MANUAL_ONLY_LEVELS:
        changed = ChangedInputs(head="manual", base=base)
        fingerprint = {"head": "manual", "base": base,
                       "inputs_sha256": "manual",
                       "map_sha256": "manual", "map_id": "manual",
                       "selector": SELECTOR_ID, "api": "none",
                       "level": "L4", "commands_sha256": "manual",
                       "python": platform.python_version(),
                       "platform": sys.platform,
                       "toolchain": "manual", "mode": mode,
                       "env": _cli_effective_env(mode, None, repo)}
        selection = Selection(
            level="L4",
            selected=[_with_required_env({"make": "dev-mul16s-sweep"})],
            unselected=[_with_required_env({"make": t} if isinstance(t, str) else t)
                        for t in FULL_L3_SEQUENCE],
            reasons=["manual-L4-request: opt-in extended sweep only; "
                     "never a Required-Check substitute"],
            fallback=False, fallback_reason="none", partial=True,
            fingerprint=fingerprint)
        return selection, render(selection)
    try:
        changed = collect_changes(repo, base, runner=runner, fetch=fetch)
    except GitError as error:
        changed = ChangedInputs(head="unknown", base=base)
        if loader is None:
            # Normal CLI: the same pre-read gate as the main path, so the
            # GitError-path raw --map is never opened either (B-2). A
            # refused spec fingerprints as unknown (reuse refused).
            try:
                gated = _normal_cli_map_target(repo, map_path)
            except MapError:
                gated = None
        else:
            gated = map_path
        fingerprint = evidence_fingerprint(
            repo, changed, mode=mode, map_path=gated, api=api,
            level="L3", commands=None)
        selection = _l3_selection(
            [f"input-collection failed ({error.kind}): {error.detail}"],
            f"{error.kind}", fingerprint)
        return selection, render(selection)
    explicit_loader = loader is not None
    active_loader = loader if explicit_loader else CanonicalJsonMapLoader(repo=repo)
    if not explicit_loader:
        # Normal CLI gate (B-2): containment/lexical/symlink refusal
        # BEFORE the first read of any map input (the fingerprint below
        # is that first read). A refused --map yields full L3 with an
        # unknown map identity (reuse refused) and zero read-API calls;
        # the finalise re-generation below then re-reads only the
        # repo-bound map that passed this gate. Explicit synthetic-test
        # loaders bypass the gate (isolated temp-fixture boundary).
        try:
            resolved_map = _normal_cli_map_target(repo, map_path)
        except MapError as gate_error:
            fingerprint = evidence_fingerprint(
                repo, changed, mode=mode, map_path=None, api=api,
                level="L3", commands=None)
            selection = _l3_selection(
                [f"impact-map: {gate_error}"],
                "map-missing-or-inconsistent", fingerprint)
            return selection, render(selection)
    elif map_path is not None:
        given = Path(map_path)
        if given.is_absolute():
            # Explicit absolute --map: authorized external test-only
            # boundary (temp dirs, never real checkouts). Same strict
            # safe-read boundary applies; a different checkout's map never
            # mixes with this repo's ledger/Makefile/diff.
            resolved_map = str(given)
        else:
            # Repo-relative --map: bound to the target repo.
            resolved_map = str(Path(repo) / map_path)
    else:
        # Default map is repo-bound (B-4): <repo>/tools/test_staging_map.json.
        resolved_map = str(Path(repo) / "tools" / "test_staging_map.json")
    fingerprint = evidence_fingerprint(repo, changed, mode=mode,
                                       map_path=resolved_map)
    try:
        import inspect as _inspect
        try:
            _params = list(_inspect.signature(active_loader.load).parameters)
            _takes_repo = len(_params) >= 2
        except (TypeError, ValueError):
            _takes_repo = True
        if _takes_repo and not explicit_loader:
            # Default CLI path: bind ledger/Makefile/scripts to --repo.
            rules, apis = active_loader.load(resolved_map, repo)
        elif _takes_repo:
            # Explicit synthetic-test loader: honour its own binding
            # (stored repo or ROOT legacy boundary); the target repo's
            # diff/fingerprint still applies, and the temp --map stays an
            # isolated authorized fixture (never a real checkout).
            bound = getattr(active_loader, "_repo", None)
            if bound is not None:
                rules, apis = active_loader.load(resolved_map)
            else:
                rules, apis = active_loader.load(resolved_map)
        else:
            # Legacy custom test loaders with load(path) only.
            rules, apis = active_loader.load(resolved_map)
        resolve = getattr(active_loader, "resolve_api", None)
        if api is not None and callable(resolve):
            api = resolve(api)
        match_mode = getattr(active_loader, "match_mode", "max")
    except MapError as error:
        selection = _l3_selection([f"impact-map: {error}"],
                                  "map-missing-or-inconsistent", fingerprint)
        _finalise_selection_fingerprint(
            repo, changed, mode, resolved_map, api, selection)
        return selection, render(selection)
    selection = select(changed, rules, apis, api=api,
                       fingerprint=fingerprint, match_mode=match_mode)
    _finalise_selection_fingerprint(
        repo, changed, mode, resolved_map, api, selection)
    return selection, render(selection)


def _finalise_selection_fingerprint(repo, changed, mode, map_path, api,
                                    selection):
    """Replace the preliminary fingerprint with the complete one.

    select() fingerprints the inputs; the CLI path additionally binds the
    map content+id, the (resolved) API choice, the selected level, and the
    selected command set, so a stored result reuses only for the same
    selection under the same conditions. Direct select() callers (unit
    tests) keep their preliminary fingerprint.
    """
    selection.fingerprint = evidence_fingerprint(
        repo, changed, mode=mode, map_path=map_path, api=api,
        level=selection.level, commands=selection.selected)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Prototype test-staging selector (Issue #852). "
                    "Prints existing commands; runs nothing.")
    parser.add_argument("--repo", default=str(ROOT))
    parser.add_argument("--base", default="origin/main",
                        help="comparison ref (default: origin/main)")
    parser.add_argument("--map", default=None,
                        help="impact-map JSON (default: "
                             "tools/test_staging_map.json, canonical Task-A "
                             "v0; the provisional envelope is synthetic-test "
                             "only and never loads via this CLI)")
    parser.add_argument("--api", default=None)
    parser.add_argument("--fetch", action="store_true",
                        help="fetch BASE before resolving (failure -> L3)")
    parser.add_argument("--mode", default=os.environ.get("MODE", "debug"))
    parser.add_argument("--level", default=None,
                        help="manual request; only L4 is honoured")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.level is not None and args.level not in MANUAL_ONLY_LEVELS:
        parser.error("only --level L4 (manual opt-in) is supported")
    selection, report = run_selector(
        args.repo, args.base, map_path=args.map, api=args.api,
        fetch=args.fetch, mode=args.mode, requested_level=args.level)
    if args.json:
        print(json.dumps(selection.to_dict(), indent=2, sort_keys=True))
    else:
        print(report, end="")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (GitError, MapError) as error:  # pragma: no cover - defensive
        print(f"selector internal error (fail-closed L3): {error}")
        raise SystemExit(1)
