"""Prototype test-staging selector (Phase 2 prep, Issue #852).

This module decides a staging level (L0/L1/L2/L3, plus manual-only L4) from
the change set between BASE and HEAD plus uncommitted inputs. It never runs
tests itself: it only prints the existing commands to run. It adds no new
verification logic and changes no test body, Makefile target, workflow, or
Required Check.

Map-format isolation: the change->test correspondence table (Task-A) is read
through the MapLoader interface below. The only loader shipped here
understands the *provisional* envelope
``test-staging-impact-map/provisional-0`` used for prototype tests. The
canonical Task-A format is NOT defined here; when Task-A lands, a new
loader implementing MapLoader must replace the provisional one, and any map
whose ``format`` tag is not recognised is treated as inconsistent and falls
back to L3 (fail-closed). Nothing in this file hardcodes Task-A's schema.

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
fingerprint (HEAD, inputs, env, toolchain, conditions) to match exactly.
"""

import argparse
import fnmatch
import hashlib
import json
import os
import platform
import re
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

# Representative local-L3 command set (existing commands only). L0-L2 output
# lists the unselected remainder from this sequence for auditability.
FULL_L3_SEQUENCE = [
    {"make": "build"},
    {"make": "test"},
    {"make": "test-real-core"},
    {"make": "check"},
    {"release-verify": True},
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

def _real_runner(repo, *args):
    try:
        proc = subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True,
            timeout=120)
    except (OSError, subprocess.SubprocessError) as error:
        raise GitError("git-execution", str(error)) from error
    if proc.returncode != 0:
        raise GitError("git-exit",
                       f"git {' '.join(args)} exited {proc.returncode}: "
                       f"{proc.stderr.strip()[-500:]}")
    return proc.stdout


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


def collect_changes(repo, base, runner=_real_runner, fetch=False):
    """Capture committed/staged/unstaged/untracked changes (Issue #852).

    Raises GitError (fail-closed -> L3) when BASE cannot be resolved, history
    is shallow, a fetch fails, or any diff fails.
    """
    repo = str(repo)
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
    untracked = []
    for chunk in runner(repo, "status", "--porcelain=v1", "-z",
                        "--untracked-files=all", "--").split("\0"):
        if chunk.startswith("??"):
            path = chunk[2:].strip()
            if path:
                untracked.append(path)
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

    def load(self, path):
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

    def load(self, path):
        raw_path = Path(path)
        if not raw_path.is_file():
            raise MapError(f"impact map is missing: {path}")
        try:
            document = json.loads(raw_path.read_text())
        except (OSError, ValueError) as error:
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
            target = (ROOT / value).resolve()
            try:
                target.relative_to(ROOT.resolve())
            except ValueError as error:
                raise MapError(f"script {value!r} escapes the repository") from error
            if target.suffix != ".py" or not target.is_file():
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
# Interim dependency pin (pre-#856-merge): read-only fetch of
# origin/agent/test-staging-map-v0-20261128 gave this HEAD. This loader was
# built against that revision's schema. It is NOT final verification: after
# Task-A (#856) integrates, re-fetch the merged map, re-run the connection
# tests plus make check / make test, and update this pin to the merged HEAD.
CANONICAL_SOURCE_HEAD = "cf428568dceecc6f7ed0607a182f222f28148e54"

CANONICAL_LEVELS = ("L0", "L1", "L2", "L3", "L4")
CANONICAL_MATCH_TYPES = ("exact", "exact-list", "glob")
CANONICAL_CI_CLASSES = ("required-ci", "ci-nonrequired",
                        "ci-outside-optin", "push-only")
CANONICAL_API_SLOT_COUNT = 81

_MAKE_TARGET_RE = re.compile(r"^([A-Za-z0-9_.-]+)\s*:[^=]*$")
_HEX40_RE = re.compile(r"^[0-9a-f]{40}$")
_DOTTED_HOST_RE = re.compile(r"^tests\.host\.([A-Za-z_][A-Za-z0-9_]*)$")


def _makefile_targets():
    """Parse live Makefile targets (a map may only reference real ones)."""
    try:
        text = (ROOT / "Makefile").read_text(encoding="utf-8")
    except OSError as error:
        raise MapError("cannot read Makefile for target validation: "
                       f"{error}") from error
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
         "api_slots": [{"slot": 0..80, "name", "dev_implemented",
                          "dev_source", "level", "tests", "reason"}]}

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

    def __init__(self):
        self._api_aliases = {}

    def resolve_api(self, api):
        """Accept slot names and slot-number spellings (63, 0x3F, $3F)."""
        if api is None:
            return None
        return self._api_aliases.get(str(api), str(api))

    def load(self, path):
        raw_path = Path(path)
        if not raw_path.is_file():
            raise MapError(f"canonical map is missing: {path}")
        try:
            document = json.loads(raw_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
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
        slot_names = self._check_slots(slots)
        targets = _makefile_targets()
        dev_source_to_slot = {}
        for slot in slots:
            source = slot.get("dev_source")
            if source:
                dev_source_to_slot[source] = slot["name"]
        rules = []
        for index, entry in enumerate(entries):
            rules.extend(self._convert_entry(index, entry, targets, static_set,
                                             dev_source_to_slot))
        apis = {}
        aliases = {}
        for slot in slots:
            name = slot["name"]
            number = slot["slot"]
            commands = self._convert_tests(slot["tests"], static_set,
                                           targets, f"api-slot {name}",
                                           require_commands=True)
            apis[name] = {
                "commands": commands,
                "reason": str(slot.get("reason") or f"canonical slot {name}"),
            }
            for alias in (name, str(number), f"{number:X}",
                          f"{number:02X}", f"{number:02x}",
                          f"0x{number:X}", f"0x{number:x}",
                          f"0x{number:02X}", f"${number:02X}"):
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
        ids = [entry["id"] for entry in entries]
        if len(set(ids)) != len(ids):
            raise MapError("canonical entry ids must be unique -> L3")

    def _check_slots(self, slots):
        if not isinstance(slots, list):
            raise MapError("canonical 'api_slots' must be a list -> L3")
        if len(slots) != CANONICAL_API_SLOT_COUNT:
            raise MapError(
                f"canonical api_slots has {len(slots)} rows, expected "
                f"{CANONICAL_API_SLOT_COUNT} -> L3")
        seen_ids = set()
        seen_names = set()
        for slot in slots:
            if not isinstance(slot, dict):
                raise MapError("canonical api_slot must be an object -> L3")
            number = slot.get("slot")
            name = slot.get("name")
            if (not isinstance(number, int) or isinstance(number, bool)
                    or not 0 <= number <= 80):
                raise MapError(f"canonical api_slot number {number!r} is "
                               "not a slot 0..80 -> L3")
            if not name or not isinstance(name, str):
                raise MapError("canonical api_slot needs a non-empty name "
                               "-> L3")
            if number in seen_ids:
                raise MapError(f"duplicate canonical slot number {number} "
                               "-> L3")
            if name in seen_names:
                raise MapError(f"duplicate canonical slot name {name!r} "
                               "-> L3")
            seen_ids.add(number)
            seen_names.add(name)
            if slot.get("level") not in CANONICAL_LEVELS:
                raise MapError(f"canonical slot {name} has unknown level "
                               f"{slot.get('level')!r} -> L3")
            source = slot.get("dev_source")
            if source is not None and not (isinstance(source, str)
                                           and source):
                raise MapError(f"canonical slot {name} dev_source must be a "
                               "path or null -> L3")
            tests = slot.get("tests")
            if not isinstance(tests, dict):
                raise MapError(f"canonical slot {name} tests must be an "
                               "object -> L3")
            for key in ("host", "integration_scripts", "dev_targets",
                        "static_units"):
                if key not in tests or not isinstance(tests[key], list):
                    raise MapError(f"canonical slot {name} tests misses list "
                                   f"{key!r} -> L3")
        if sorted(seen_ids) != list(range(CANONICAL_API_SLOT_COUNT)):
            raise MapError("canonical api_slots must cover slots 0..80 "
                           "exactly once -> L3")
        return seen_names

    def _convert_entry(self, index, entry, targets, static_set,
                       dev_source_to_slot):
        level = entry["level"]
        reason = entry["reason"]
        # Entries may list zero commands: an L3 entry with empty tests
        # means "the evidence is L3 itself" (e.g. CI definitions, site
        # sources). select() then runs the full local-L3 sequence
        # fail-closed; a 0-command L0-L2 classification escalates to L3.
        commands = self._convert_tests(entry["tests"], static_set, targets,
                                       f"entry {entry['id']}",
                                       require_commands=False)
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
        elif match_type == "exact-list":
            seen = set()
            patterns = []
            for item in entry["match"]:
                if item not in seen:
                    seen.add(item)
                    patterns.append(item)
        else:
            patterns = [entry["match"]]
        rules = []
        for pattern in patterns:
            rules.append(MapRule(
                pattern=pattern, level=level, commands=list(commands),
                reason=f"{reason} (canonical entry {entry['id']} "
                       f"{match_type} {pattern!r} -> {entry['level']})",
                api=dev_source_to_slot.get(pattern, "")))
        return rules

    def _convert_tests(self, tests, static_set, targets, owner,
                       require_commands):
        commands = []
        for target in tests["dev_targets"]:
            if target not in targets:
                raise MapError(f"{owner}: unknown Makefile target "
                               f"{target!r} (maps may only reference existing "
                               "commands) -> L3")
            commands.append({"make": target})
        for ref in tests["host"]:
            commands.append(self._canonical_host_command(ref, owner))
        for script in tests["integration_scripts"]:
            commands.append(self._canonical_script_command(script, owner))
        for unit in tests["static_units"]:
            if unit not in static_set:
                raise MapError(f"{owner}: unknown static unit {unit!r} "
                               "-> L3")
        if tests["static_units"] and {"make": "check"} not in commands:
            commands.append({"make": "check"})
        if not commands and require_commands:
            raise MapError(f"{owner}: tests must list at least one command "
                           "(a 0-test selection is never PASS) -> L3")
        return commands

    def _canonical_host_command(self, ref, owner):
        if not isinstance(ref, str) or not ref:
            raise MapError(f"{owner}: bad host ref {ref!r} -> L3")
        dotted = _DOTTED_HOST_RE.match(ref)
        if dotted:
            module = dotted.group(1)
            if not (ROOT / "tests" / "host" / f"{module}.py").is_file():
                raise MapError(f"{owner}: host ref {ref!r} has no such test "
                               "module -> L3")
            return {"unittest": f"{module}.py", "suite": "host"}
        if ref.startswith("tests/host/") and ref.endswith(".py"):
            pattern = ref[len("tests/host/"):]
            if not pattern or ".." in ref:
                raise MapError(f"{owner}: bad host ref {ref!r} -> L3")
            host_dir = ROOT / "tests" / "host"
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

    def _canonical_script_command(self, script, owner):
        if (not isinstance(script, str) or not script.startswith(
                "tests/integration/") or not script.endswith(".py")
                or ".." in script or "*" in script or "?" in script):
            raise MapError(f"{owner}: bad integration script {script!r} "
                           "-> L3")
        target = (ROOT / script).resolve()
        try:
            target.relative_to(ROOT.resolve())
        except ValueError as error:
            raise MapError(f"{owner}: script {script!r} escapes the "
                           "repository -> L3") from error
        if not target.is_file():
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
    if "release-verify" in command:
        return ["make", "build", "MODE=release"]
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


def evidence_fingerprint(repo, changed, mode="debug", extra_env=None):
    """Build the reuse fingerprint for the current selection inputs."""
    entries = sorted(f"{origin}:{status}:{path}"
                     for origin, status, path in changed.all_classifiable())
    entries.extend(f"untracked:??:{path}" for path in sorted(changed.untracked))
    digest = hashlib.sha256("\n".join(entries).encode()).hexdigest()
    toolchain = "unknown"
    try:
        deps = json.loads((Path(repo) / "tools" / "dependencies.json").read_text())
        toolchain = json.dumps({name: deps[name].get("revision")
                                for name in sorted(deps)}, sort_keys=True)
    except (OSError, ValueError, KeyError, AttributeError):
        toolchain = "unknown"
    return {
        "head": changed.head,
        "base": changed.base,
        "inputs_sha256": digest,
        "python": platform.python_version(),
        "platform": sys.platform,
        "toolchain": toolchain,
        "mode": mode,
        "env": dict(extra_env or {}),
    }


def evidence_reusable(stored, current):
    """A stored result is reusable only on exact fingerprint equality.

    Unknown toolchains never reuse: without pins, "same toolchain" cannot be
    established, so reuse is refused fail-closed.
    """
    required = ("head", "base", "inputs_sha256", "python", "platform",
                "toolchain", "mode", "env")
    for key in required:
        if stored.get(key) != current.get(key):
            return False
    if current.get("toolchain") == "unknown":
        return False
    return True


def _l3_selection(reasons, fallback_reason, fingerprint, selected=None):
    full = [_with_required_env({"make": target} if isinstance(target, str) else target)
            for target in ["build", "test", "test-real-core", "check",
                           "rom-provenance", "dev-integration",
                           "dev-release-gate-blocked"]]
    return Selection(level="L3", selected=selected if selected is not None else full,
                     unselected=[], reasons=reasons, fallback=True,
                     fallback_reason=fallback_reason, partial=False,
                     fingerprint=fingerprint)


def select(changed, rules, apis, api=None, fingerprint=None,
           match_mode="max"):
    """Classify every change; anything unclassifiable selects L3.

    match_mode "max" (provisional) takes the highest matching rule level;
    "first-match" (Task-A canonical) uses entry order, so the first rule
    matching the path (and the --api tag, when given) wins. Both modes are
    fail-closed: no match, or an L3 match, selects L3.
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
            first = next((rule for rule in rules
                          if fnmatch.fnmatchcase(path, rule.pattern)
                          and (api is None or rule.api == api)), None)
            hits = [first] if first is not None else []
        else:
            hits = [rule for rule in rules
                    if fnmatch.fnmatchcase(path, rule.pattern)
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
        wanted = apis[api]["commands"]
        selected = [cmd for cmd in selected if cmd in wanted] or list(wanted)
        reasons.append(f"api:{api}: {apis[api]['reason']}")
        level_rank = max(level_rank, LEVEL_RANK["L1"])
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
        return _l3_selection(reasons, "map-rule-requires-L3", fingerprint,
                             selected=[_with_required_env(cmd) for cmd in deduped])
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
    lines.append("SKIP-POLICY: a SKIP, BLOCKED-as-pass, missing-tool, or "
                 "0-test result is never PASS; every command below exports "
                 f"{REQUIRE_CC65_ENV}=1 so a missing cc65 FAILs.")
    lines.append(f"SELECTED ({len(selection.selected)}):")
    for command in selection.selected:
        lines.append(f"  env {REQUIRE_CC65_ENV}=1 -- "
                     + " ".join(command["argv"]))
    lines.append(f"UNSELECTED ({len(selection.unselected)}):")
    for command in selection.unselected:
        lines.append("  (not run at this level) " + " ".join(command["argv"]))
    lines.append(f"REASONS ({len(selection.reasons)}):")
    for reason in selection.reasons:
        lines.append(f"  {reason}")
    lines.append("EVIDENCE-FINGERPRINT: "
                 + json.dumps(selection.fingerprint, sort_keys=True))
    lines.append("REUSE-RULE: reuse a prior result only when its fingerprint "
                 "matches exactly (same HEAD/input/env/toolchain/conditions); "
                 "an old HEAD result is never claimed for a new HEAD.")
    return "\n".join(lines) + "\n"


def run_selector(repo, base, map_path=None, api=None, fetch=False,
                 mode="debug", loader=None, runner=_real_runner,
                 requested_level=None):
    """Collect inputs, load the map, select, and return (selection, report)."""
    if requested_level in MANUAL_ONLY_LEVELS:
        changed = ChangedInputs(head="manual", base=base)
        fingerprint = {"head": "manual", "base": base, "inputs_sha256": "manual",
                       "python": platform.python_version(), "platform": sys.platform,
                       "toolchain": "manual", "mode": mode, "env": {}}
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
        fingerprint = evidence_fingerprint(repo, changed, mode=mode)
        selection = _l3_selection(
            [f"input-collection failed ({error.kind}): {error.detail}"],
            f"{error.kind}", fingerprint)
        return selection, render(selection)
    fingerprint = evidence_fingerprint(repo, changed, mode=mode)
    try:
        active_loader = loader or ProvisionalJsonMapLoader()
        if map_path is None:
            raise MapError("no impact map supplied (Task-A canonical map "
                           "pending) -> L3")
        rules, apis = active_loader.load(map_path)
        resolve = getattr(active_loader, "resolve_api", None)
        if api is not None and callable(resolve):
            api = resolve(api)
        match_mode = getattr(active_loader, "match_mode", "max")
    except MapError as error:
        selection = _l3_selection([f"impact-map: {error}"],
                                  "map-missing-or-inconsistent", fingerprint)
        return selection, render(selection)
    selection = select(changed, rules, apis, api=api,
                       fingerprint=fingerprint, match_mode=match_mode)
    return selection, render(selection)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Prototype test-staging selector (Issue #852). "
                    "Prints existing commands; runs nothing.")
    parser.add_argument("--repo", default=str(ROOT))
    parser.add_argument("--base", default="origin/main",
                        help="comparison ref (default: origin/main)")
    parser.add_argument("--map", default=None,
                        help="impact-map JSON (Task-A canonical pending)")
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
