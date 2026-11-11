"""Debug/release diff audit gate for Issue #724 (design-only, no production change).

Compares verification-only/debug behavior against release claims and refuses
GPL/verification-patch leakage into release inputs. Contamination checks are
static (no Geargrafx core, no private media); the mode-branch check is an AST
body check (mode may only select -g/-l, output paths, or the manifest echo).
When the pinned cc65 is usable the gate additionally rebuilds the debug and
release smoke ROMs and byte-compares them, otherwise byte-identity is SKIPPED
-- a skipped comparison is never reported as PASS. Real-core/free-run/libretro
-equivalence stays UNKNOWN here; this gate never reports production safety PASS.

Release inputs (bytes that can reach a release ROM, plus the build logic that
assembles them) must not contain verification-only hooks or GPL source text.
Verification-only patches stay in tests/integration/patches/ (a GPL-3.0
directory) and are applied only to staged copies under temporary directories.

Usage:
  python3 tools/verify_build_diff.py          # fail-closed check, exit 0/1
  python3 tools/verify_build_diff.py --report # JSON audit report on stdout

Exit codes: 0 PASS (no contamination, gates intact, report emitted with
UNKNOWNs where unmeasured), 1 FAIL (contamination or gate regression).
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import io
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Markers that must never appear in release inputs. Built from adjacent
# literals so this file does not itself contain the scanned byte sequences
# (same split-literal convention as tools/provenance_guard.py).
_GPL_A = "GNU General " "Public License"
_GPL_B = "General " "Public License"
_SPDX = "SPDX-" "License"
_UPSTREAM_COPYRIGHT = "Ignacio Sanchez"
_UPSTREAM_REPO = "drhelius/Geargrafx"

FORBIDDEN_MARKERS = (
    "gg_verify",
    "GG_Verify",
    "GG_VERIFY",
    "VerifyMpr",
    "VerifyBlockWrite",
    "VerifyTransfer",
    "geargrafx-b49ae82",
    _GPL_A,
    _GPL_B,
    _SPDX,
    _UPSTREAM_COPYRIGHT,
    _UPSTREAM_REPO,
)

# Files whose bytes (or assembling logic) can reach a release ROM.
#
# The DEV ROM builder (tools/build_rom.py build()) assembles src/boot.s plus every
# src/overrides/slot_*.s and links with src/bank0.cfg, while ca65 follows
# `.include`/`.incbin` directives inside those sources. Bytes pulled in that
# way ship in ROM bytes exactly like the including file, so the two include
# targets known today (src/ex_bcdbin.s via slot_3D.s, src/ex_binbcd.s via
# slot_3C.s) are listed explicitly AND re-resolved dynamically by
# _include_closure() below, so any future .include/.incbin target is covered
# too. A marker in an unscanned include would otherwise sail through this
# gate while shipping in ROM bytes.
RELEASE_INPUT_GLOBS = (
    "src/boot.s",
    "src/bank0.cfg",
    "src/overrides/*.s",
    "src/ex_bcdbin.s",
    "src/ex_binbcd.s",
    "tests/fixtures/smoke.s",
    "tests/fixtures/smoke.cfg",
    "tools/build.py",
    "tools/build_rom.py",
    "tools/environment.py",
    "tools/rom.py",
    "spec/api_slots.json",
)

# ca65 source-inclusion directives. Quoted form is what ca65 accepts for
# .include/.incbin paths; both quote styles are recognised here so the
# closure cannot be dodged by quoting choice.
_INCLUDE_RE = re.compile(
    r"^\s*\.(?:include|incbin)\s+[\"']([^\"']+)[\"']",
    re.IGNORECASE | re.MULTILINE,
)

# Build-logic modules that must never import the verification-only probes.
BUILD_LOGIC = (
    "tools/build.py",
    "tools/build_rom.py",
    "tools/environment.py",
    "tools/rom.py",
)
FORBIDDEN_IMPORTS = (
    "compile_probe",
    "reference_jsr_gate",
    "geargrafx_api",
    "geargrafx_block_write_guard",
    "geargrafx_stack_access_guard",
    "geargrafx_st_write_guard",
)

# Mode may only select debug info (-g/-l), output paths (listings/maps/labels),
# or the manifest "mode" echo. Everything else that varies by mode (ROM inputs,
# link objects, ROM bytes, imports, file/subprocess effects, return payloads)
# fails closed. This is an AST *body* check, not a line-regex allowlist: every
# `if` / `while` test and every ternary mentioning `mode`, `args.mode`, or a
# MODE-environ lookup (directly or through an alias assigned from a mode test,
# e.g. `debug = mode == 'debug' ...`) has its whole body validated, so a mode
# fork cannot hide behind an allowed-looking first line.
_DEBUG_ONLY_FLAGS = ("-g", "-l")

# Roots an output-path expression may be built from: the assembler output
# directory, per-source object paths, and linker dependency files. Any other
# name inside a would-be path (ROM inputs such as `source`/`config`, mode
# values, handler tables) refuses the path. Only pure `str()`/`repr()`
# coercion may wrap such a path; every other call shape fails closed.
_OUTPUT_ROOT_NAMES = frozenset({"output", "obj", "dep"})
_OUTPUT_PATH_COERCIONS = frozenset({"str", "repr"})

# Names a mode test may flow into without ROM effect: the assembler command
# list (debug flags/listings only) and the `debug` carrier itself. Every later
# use of the carrier in a test is itself a checked mode branch.
_MODE_CARRIERS = ("command", "debug")

# Filesystem/subprocess side effects: forbidden inside a mode-dependent region.
# Mutating list methods are included so link-input lists cannot grow by mode;
# the single exception (`command`, debug flags only) is validated separately
# before the allowlist below applies. These sets are kept only to label the
# known side-effect class in diagnostics; enforcement is the allowlist in
# _check_mode_region (any unlisted call fails closed, so helper indirection
# cannot hide behind a denylist miss).
_EFFECTFUL_CALL_ATTRS = frozenset({
    "run", "call", "check_call", "check_output", "Popen", "system", "popen",
    "write_text", "write_bytes", "open", "mkdir", "unlink", "rmtree",
    "copy", "copyfile", "copy2", "move", "rename", "remove", "symlink",
    "link_to", "touch", "chmod", "write", "writelines", "dump", "save",
    "append", "extend", "insert", "add", "discard", "pop", "clear", "update",
    "setdefault", "execv", "execve", "spawnl", "spawnlp", "spawnv", "spawnvp",
})
_EFFECTFUL_CALL_NAMES = frozenset({
    "open", "exec", "eval", "compile", "__import__", "setattr", "input",
    "breakpoint", "exit", "quit",
})

# Allowlist of bare-function calls provably without ROM effect inside a
# mode-dependent region. Only logging (`print`), pure coercion/introspection
# (`str`/`repr`/`len`/`bool` -- `str(output / ...)` appears inside the allowed
# `command` debug-flag values), and nothing else. Exception construction
# (`ValueError` et al, meaningful only under `raise`/`assert`) is listed
# separately below. Every other `name(...)` -- including helpers such as
# `modify_rom()`, aliases (`f = modify_rom; f()`), and nested closures -- is
# unprovable and fails closed. Method/object calls (`obj.method(...)`) are
# never allowlisted here: only `command.append/extend/insert` with debug-only
# operands pass, validated separately before this list applies.
_MODE_SAFE_CALL_NAMES = frozenset({
    "print", "str", "repr", "len", "bool",
})
_MODE_EXCEPTION_CTORS = frozenset({
    "ValueError", "TypeError", "RuntimeError", "AssertionError", "Exception",
})


def _mentions_mode(node: ast.AST, derived: set[str]) -> bool:
    """True when the expression reads `mode`, `*.mode`, a MODE-environ lookup, or a mode-derived alias."""
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
            if child.id == "mode" or child.id in derived:
                return True
        elif isinstance(child, ast.Attribute) and child.attr == "mode":
            return True
        elif (isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                and child.func.attr == "get" and child.args
                and isinstance(child.args[0], ast.Constant) and child.args[0].value == "MODE"):
            return True
    return False


def _assignment_target_ids(node: ast.AST) -> list[str]:
    """Name ids stored by an Assign/AnnAssign/AugAssign/NamedExpr."""
    holders = node.targets if isinstance(node, ast.Assign) else [node.target]
    ids: list[str] = []
    for holder in holders:
        for child in ast.walk(holder):
            if isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)):
                ids.append(child.id)
    return ids


def _dict_mode_only_echo(node: ast.Dict, derived: set[str]) -> bool:
    """True when every mode-mentioning dict value sits under the `"mode"` key (manifest echo)."""
    for key, value in zip(node.keys, node.values):
        if _mentions_mode(value, derived):
            if not (isinstance(key, ast.Constant) and key.value == "mode"):
                return False
    return True


def _mode_derived_names(tree: ast.AST) -> set[str]:
    """Names assigned anywhere from a mode-mentioning value (fixpoint; fail-closed over-approximation)."""
    derived: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
                continue
            value = node.value
            if value is None or not _mentions_mode(value, derived):
                continue
            if isinstance(value, ast.Dict) and _dict_mode_only_echo(value, derived):
                continue
            for name in _assignment_target_ids(node):
                if name not in derived and name != "mode":
                    derived.add(name)
                    changed = True
    return derived


def _is_output_path_operand(node: ast.AST) -> bool:
    """A `/`-join fragment: a string literal or a rooted output path."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    return _is_output_path_expr(node)


def _is_output_path_expr(node: ast.AST) -> bool:
    """Pure output-directory path expression (structural allowlist, fail-closed).

    Only `output`/`obj`/`dep` roots joined by `/` (or `+` with string
    fragments) and wrapped in a pure `str()`/`repr()` coercion pass. Name
    presence alone never allows: ANY other call -- helpers (`modify_rom()`),
    aliases, methods/objects (`obj.write(...)`), indirect/subscript callees
    (`handlers[mode](...)`), or ROM inputs mixed into the path
    (`str(output / source)`) -- fails, as do ternaries, nested conditionals,
    f-strings, and attribute/subscript access. Anything unrecognized
    returns False so an undetectable side effect can never read as a path.
    """
    if isinstance(node, ast.Call):
        func = node.func
        if not (isinstance(func, ast.Name) and func.id in _OUTPUT_PATH_COERCIONS):
            return False
        if len(node.args) != 1 or node.keywords:
            return False
        return _is_output_path_expr(node.args[0])
    if isinstance(node, ast.Name):
        return node.id in _OUTPUT_ROOT_NAMES and isinstance(node.ctx, ast.Load)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.Add)):
        return (_is_output_path_expr(node.left)
                and _is_output_path_operand(node.right))
    return False


def _is_debug_operand(node: ast.AST, derived: set[str]) -> bool:
    """A value a mode test may select: -g/-l flags, empty, or output paths.

    Lists/tuples hold only flat flag/path elements (nested sequences or
    ternaries fail); anything else must be a strict output-path expression,
    so a call smuggling an output name (`modify_rom(output)`) never reads
    as a debug operand.
    """
    if isinstance(node, ast.Constant):
        return node.value in _DEBUG_ONLY_FLAGS
    if isinstance(node, (ast.List, ast.Tuple)):
        return all(
            (isinstance(elt, ast.Constant) and elt.value in _DEBUG_ONLY_FLAGS)
            or _is_output_path_expr(elt)
            for elt in node.elts
        )
    return _is_output_path_expr(node)


def _command_sequence_ok(node: ast.AST, derived: set[str]) -> bool:
    """Token stream appended to `command`: (`-g` | `-l` <output path>)* only."""
    if isinstance(node, ast.IfExp) and _mentions_mode(node.test, derived):
        return (_command_sequence_ok(node.body, derived)
                and _command_sequence_ok(node.orelse, derived))
    if not isinstance(node, (ast.List, ast.Tuple)):
        return False
    index, elts = 0, node.elts
    while index < len(elts):
        elt = elts[index]
        if isinstance(elt, ast.Constant) and elt.value == "-g":
            index += 1
        elif isinstance(elt, ast.Constant) and elt.value == "-l":
            if index + 1 >= len(elts) or not _is_output_path_expr(elts[index + 1]):
                return False
            index += 2
        else:
            return False
    return True


def _has_mode_ifexp(node: ast.AST, derived: set[str]) -> bool:
    """True when the value already defers a mode choice to a ternary (reported by the ternary rule)."""
    return any(isinstance(child, ast.IfExp) and _mentions_mode(child.test, derived)
               for child in ast.walk(node))


def _release_input_files(root: Path = ROOT) -> list[Path]:
    files: list[Path] = []
    for pattern in RELEASE_INPUT_GLOBS:
        files.extend(sorted((root / pattern).parent.glob(Path(pattern).name))
                     if "*" in pattern else [root / pattern])
    # Keep only files that exist; missing optional override files are not an error here.
    required = [root / "src/boot.s", root / "src/bank0.cfg",
                root / "tests/fixtures/smoke.s", root / "tests/fixtures/smoke.cfg",
                root / "tools/build.py", root / "tools/build_rom.py"]
    for path in required:
        if not path.is_file():
            raise ValueError(f"missing required release input: {path}")
    return [p for p in files if p.is_file()]


def _assembly_roots(root: Path = ROOT) -> list[Path]:
    """Assembly sources the ROM builders feed to ca65 (include-scan seeds)."""
    seeds = [root / "src/boot.s", root / "tests/fixtures/smoke.s"]
    seeds.extend(sorted((root / "src" / "overrides").glob("slot_*.s")))
    return [p for p in seeds if p.is_file()]


def _include_closure(root: Path = ROOT) -> tuple[list[Path], list[str]]:
    """Recursively resolve ca65 .include/.incbin targets under `root`.

    Returns (resolved_files, problems). Any unresolvable directive (missing
    file, unreadable includer, target escaping the repository) is a
    fail-closed problem: an unaudited byte source must refuse, never pass
    silently.
    """
    problems: list[str] = []
    found: list[Path] = []
    seen: set[Path] = set()
    try:
        anchor = root.resolve()
    except OSError as error:
        return [], [f"{root}: cannot resolve release root ({error})"]
    stack = _assembly_roots(root)
    visited: set[Path] = set()
    while stack:
        current = stack.pop()
        try:
            key = current.resolve()
        except OSError as error:
            problems.append(f"{current}: unresolvable release input ({error})")
            continue
        if key in visited:
            continue
        visited.add(key)
        try:
            text = current.read_bytes().decode("utf-8", errors="strict")
        except (OSError, UnicodeDecodeError) as error:
            problems.append(f"{_rel(root, current)}: unreadable release input ({error})")
            continue
        for match in _INCLUDE_RE.finditer(text):
            raw = match.group(1).strip()
            if not raw:
                problems.append(f"{_rel(root, current)}: empty .include/.incbin directive")
                continue
            candidate = (current.parent / raw).resolve() if not Path(raw).is_absolute() else Path(raw)
            try:
                inside = candidate.is_relative_to(anchor)
            except (OSError, ValueError):
                inside = False
            if not inside:
                problems.append(
                    f"{_rel(root, current)}: include {raw!r} escapes the repository (fail-closed)")
                continue
            if not candidate.is_file():
                problems.append(
                    f"{_rel(root, current)}: unresolvable include {raw!r} "
                    "(missing file; unaudited byte source refused)")
                continue
            if candidate not in seen:
                seen.add(candidate)
                found.append(candidate)
            stack.append(candidate)
    return sorted(found), problems


def _rel(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def scan_release_inputs(root: Path = ROOT) -> list[str]:
    """Fail-closed content scan of release inputs for hooks/GPL text."""
    problems: list[str] = []
    include_files, dep_problems = _include_closure(root)
    problems.extend(dep_problems)
    scanned = _release_input_files(root)
    known = {p.resolve() for p in scanned}
    for extra in include_files:
        try:
            key = extra.resolve()
        except OSError:
            continue
        if key not in known:
            known.add(key)
            scanned.append(extra)
    for path in scanned:
        try:
            text = path.read_bytes().decode("utf-8", errors="strict")
        except (OSError, UnicodeDecodeError) as error:
            problems.append(f"{path.relative_to(root)}: unreadable release input ({error})")
            continue
        for marker in FORBIDDEN_MARKERS:
            if marker in text:
                problems.append(
                    f"{path.relative_to(root)}: forbidden marker {marker!r} "
                    "(verification-only/GPL text must not reach release inputs)")
    return problems


def check_build_imports(root: Path = ROOT) -> list[str]:
    """Build logic must not import verification-only probe modules."""
    problems: list[str] = []
    for rel in BUILD_LOGIC:
        path = root / rel
        if not path.is_file():
            problems.append(f"{rel}: missing build-logic file")
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as error:
            problems.append(f"{rel}: unparseable ({error})")
            continue
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        text = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_IMPORTS:
            if forbidden in imported or forbidden in text:
                problems.append(
                    f"{rel}: references verification-only module {forbidden!r}")
        if "patches/" in text and "tests/integration/patches" in text:
            problems.append(f"{rel}: references the verification-only patches directory")
    return problems


def _check_mode_region(stmts: list[ast.stmt], rel: str, derived: set[str],
                       lines: list[str]) -> list[str]:
    """Validate every effect inside a mode-dependent region (body + orelse).

    Allowlist: `raise`/`assert` (validation, including exception constructors
    under them), `print` (logging), assignments to the `command`/`debug`
    carriers (`command` only grows by -g/-l/output paths), pure
    coercion/introspection calls (`str`/`repr`/`len`/`bool`), and pure
    reads/constructors. Everything else -- stores or writes to any other
    name/attribute/item, imports, mode-defined functions, ANY other function
    call (`modify_rom()`, aliases such as `f()`, nested closures), ANY method
    or object call (`obj.write(...)`, `source.with_name(...)`,
    `objects.append(...)`, `subprocess.run(...)`), indirect/dynamic callees
    (`handlers[k]()`, `getattr(...)()`, lambdas), and control-flow escapes --
    fails closed as an unprovable mode-dependent effect.
    """
    problems: list[str] = []
    nodes: list[ast.AST] = []
    for stmt in stmts:
        nodes.extend(ast.walk(stmt))

    def snippet(node: ast.AST) -> str:
        lineno = getattr(node, "lineno", 0)
        line = lines[lineno - 1] if 1 <= lineno <= len(lines) else ""
        return line.strip()[:160]

    direct_targets: set[int] = set()  # Name nodes handled by the assign logic below
    for node in nodes:
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.NamedExpr)):
            holders = node.targets if isinstance(node, ast.Assign) else [node.target]
            for holder in holders:
                for child in ast.walk(holder):
                    if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                        direct_targets.add(id(child))
    for node in nodes:
        lineno = getattr(node, "lineno", 0)
        where = f"{rel}:{lineno}"
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.NamedExpr)):
            value = node.value
            if value is None:
                continue  # bare annotation: no value flows
            targets = _assignment_target_ids(node)
            if all(target in _MODE_CARRIERS for target in targets) and targets:
                if "command" in targets:
                    addition = value
                    if isinstance(node, ast.AugAssign):
                        if not isinstance(node.op, ast.Add):
                            problems.append(
                                f"{where}: mode-dependent `command` update is not `+=`: {snippet(node)!r}")
                        elif not _command_sequence_ok(addition, derived):
                            problems.append(
                                f"{where}: mode-dependent `command` update is not limited to"
                                f" -g/-l/output paths: {snippet(node)!r}")
                    elif (isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add)
                            and (_mentions_mode(value.left, derived) or _mentions_mode(value.right, derived))):
                        other = value.right if _mentions_mode(value.left, derived) else value.left
                        if not _command_sequence_ok(other, derived):
                            problems.append(
                                f"{where}: mode-dependent `command` rebuild adds non-debug operands:"
                                f" {snippet(node)!r}")
                    elif not _command_sequence_ok(value, derived):
                        problems.append(
                            f"{where}: mode-dependent `command` assignment is not limited to"
                            f" -g/-l/output paths: {snippet(node)!r}")
                # `debug` carrier: any value is allowed; every later use of the
                # carrier in a test is itself a checked mode branch.
            elif _has_mode_ifexp(value, derived):
                pass  # reported once by the ternary rule below
            else:
                for target in targets:
                    if target not in _MODE_CARRIERS:
                        problems.append(
                            f"{where}: mode-dependent assignment to {target!r} (ROM inputs/bytes"
                            f" must not vary by mode): {snippet(node)!r}")
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            if id(node) in direct_targets:
                continue  # handled by the assign logic above
            if node.id not in _MODE_CARRIERS:
                problems.append(
                    f"{where}: mode-dependent binding of {node.id!r} (ROM inputs/bytes"
                    f" must not vary by mode): {snippet(node)!r}")
        elif isinstance(node, (ast.Attribute, ast.Subscript)) and isinstance(node.ctx, (ast.Store, ast.Del)):
            problems.append(
                f"{where}: mode-dependent write (ROM inputs/bytes must not vary by mode):"
                f" {snippet(node)!r}")
        elif isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Attribute) and func.attr in ("append", "extend", "insert")
                    and isinstance(func.value, ast.Name) and func.value.id == "command"):
                if func.attr == "append":
                    ok = (len(node.args) == 1 and not node.keywords
                          and _is_debug_operand(node.args[0], derived)
                          and isinstance(node.args[0], ast.Constant))
                elif func.attr == "extend":
                    ok = (len(node.args) == 1 and not node.keywords
                          and _command_sequence_ok(node.args[0], derived))
                else:  # insert: index plus a single -g/-l flag
                    ok = (len(node.args) == 2 and not node.keywords
                          and isinstance(node.args[1], ast.Constant)
                          and node.args[1].value in _DEBUG_ONLY_FLAGS)
                if not ok:
                    problems.append(
                        f"{where}: mode-dependent `command.{func.attr}` is not limited to"
                        f" -g/-l/output paths: {snippet(node)!r}")
            elif isinstance(func, ast.Name) and func.id in (_MODE_SAFE_CALL_NAMES | _MODE_EXCEPTION_CTORS):
                pass  # allowlisted: logging, pure coercion, or validation ctors
            elif (isinstance(func, ast.Name) and func.id in _EFFECTFUL_CALL_NAMES) or (
                    isinstance(func, ast.Attribute) and func.attr in _EFFECTFUL_CALL_ATTRS):
                if isinstance(func, ast.Attribute):
                    callee = func.attr
                else:
                    callee = func.id
                problems.append(
                    f"{where}: mode-dependent side-effect call `{callee}`"
                    f" (ROM inputs/bytes must not vary by mode): {snippet(node)!r}")
            elif isinstance(func, ast.Name):
                problems.append(
                    f"{where}: mode-dependent unprovable function call `{func.id}`"
                    f" (alias/nested/helper calls must not vary by mode;"
                    f" only print/str/repr/len/bool/validation ctors allowed): {snippet(node)!r}")
            elif isinstance(func, ast.Attribute):
                problems.append(
                    f"{where}: mode-dependent unprovable method/object call `{func.attr}`"
                    f" (side-effect call; ROM inputs/bytes must not vary by mode): {snippet(node)!r}")
            else:
                problems.append(
                    f"{where}: mode-dependent call through an unresolvable/indirect callee"
                    f" (ROM inputs/bytes must not vary by mode): {snippet(node)!r}")
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            problems.append(
                f"{where}: mode-dependent import (probe modules must never be mode-gated):"
                f" {snippet(node)!r}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            problems.append(
                f"{where}: mode-dependent definition (behavior must not vary by mode):"
                f" {snippet(node)!r}")
        elif isinstance(node, (ast.Return, ast.Yield, ast.YieldFrom, ast.Break, ast.Continue)):
            problems.append(
                f"{where}: mode-dependent control-flow escape (behavior must not vary by mode):"
                f" {snippet(node)!r}")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            problems.append(
                f"{where}: mode-dependent rebinding of module state: {snippet(node)!r}")
    return problems


def check_mode_branches(root: Path = ROOT) -> list[str]:
    """Mode may only select debug info (-g/-l), output paths, or the manifest echo (AST body check)."""
    problems: list[str] = []
    for rel in ("tools/build.py", "tools/build_rom.py"):
        path = root / rel
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as error:
            problems.append(f"{rel}: unreadable ({error})")
            continue
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError as error:
            problems.append(f"{rel}: unparseable ({error})")
            continue
        lines = text.splitlines()
        derived = _mode_derived_names(tree)
        covered: set[int] = set()  # nodes inside a mode region (reported by the region rule)
        branches: list[tuple[ast.AST, list[ast.stmt], list[ast.stmt]]] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.If, ast.While)):
                if _mentions_mode(node.test, derived):
                    branches.append((node, node.body, node.orelse))
                    for stmt in (*node.body, *node.orelse):
                        covered.update(id(child) for child in ast.walk(stmt))
        for branch_node, body, orelse in branches:
            if id(branch_node) in covered:
                continue  # nested inside an outer mode region: already validated there
            problems.extend(_check_mode_region(body, rel, derived, lines))
            problems.extend(_check_mode_region(orelse, rel, derived, lines))
        for node in ast.walk(tree):
            if id(node) in covered:
                continue
            if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.NamedExpr)):
                value = node.value
                if value is None or not _mentions_mode(value, derived):
                    continue
                targets = _assignment_target_ids(node)
                if targets and all(target in _MODE_CARRIERS for target in targets):
                    if "command" in targets and _mentions_mode(value, derived):
                        # A mode value flowing directly into `command` outside
                        # an `if` region (e.g. `command += (... if mode ... else
                        # ...)`) must still carry only -g/-l/output paths;
                        # otherwise a release-side-only ternary could smuggle
                        # a side-effect call into the assembler command.
                        if isinstance(node, ast.AugAssign):
                            ok = (isinstance(node.op, ast.Add)
                                  and _command_sequence_ok(value, derived))
                        elif (isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add)
                                and (_mentions_mode(value.left, derived)
                                     or _mentions_mode(value.right, derived))):
                            other = (value.right if _mentions_mode(value.left, derived)
                                     else value.left)
                            ok = _command_sequence_ok(other, derived)
                        else:
                            ok = _command_sequence_ok(value, derived)
                        if not ok:
                            lineno = getattr(node, 'lineno', 0)
                            line = lines[lineno - 1] if 1 <= lineno <= len(lines) else ""
                            problems.append(
                                f"{rel}:{lineno}: mode-dependent `command` update is not limited to"
                                f" -g/-l/output paths: {line.strip()[:160]!r}")
                    continue  # carrier flow; `debug` uses are checked as branches
                if isinstance(value, ast.Dict) and _dict_mode_only_echo(value, derived):
                    continue  # manifest "mode" echo
                if _has_mode_ifexp(value, derived):
                    continue  # reported once by the ternary rule below
                problems.append(
                    f"{rel}:{getattr(node, 'lineno', 0)}: mode value flows into"
                    f" {targets[0] if targets else 'an expression'!r} (ROM inputs/bytes must not"
                    f" vary by mode): {lines[getattr(node, 'lineno', 0) - 1].strip()[:160] if 1 <= getattr(node, 'lineno', 0) <= len(lines) else ''!r}")
            elif isinstance(node, ast.IfExp):
                if _mentions_mode(node.test, derived):
                    if not (_is_debug_operand(node.body, derived)
                            and _is_debug_operand(node.orelse, derived)):
                        lineno = getattr(node, "lineno", 0)
                        line = lines[lineno - 1] if 1 <= lineno <= len(lines) else ""
                        problems.append(
                            f"{rel}:{lineno}: mode-dependent ternary selects a non-debug value"
                            f" (only -g/-l/output paths may vary by mode): {line.strip()[:160]!r}")
    return problems


def check_release_gates(root: Path = ROOT) -> list[str]:
    """The existing BLOCKED release gates must still refuse."""
    problems: list[str] = []
    # 1. Makefile `release` target stays exactly the 2-line BLOCKED stub
    # (static recipe check, consistent with PR #769
    # tools/failclosed_gate.py::check_makefile_release; no cross-branch
    # import -- this gate duplicates the whitelist so the two stay
    # consistent via review/serial merge, not via shared code). A blacklist
    # over BLOCKED/exit-1 substrings anywhere in the file would accept a
    # gutted `release:` recipe so long as those strings appeared under some
    # other target, so only the exact `release:` recipe is examined here.
    makefile = root / "Makefile"
    try:
        text = makefile.read_text(encoding="utf-8")
    except OSError as error:
        return [f"Makefile: unreadable ({error})"]
    match = re.search(r"^release:\n((?:\t.*\n?)+)", text, re.M)
    if match is None:
        problems.append("Makefile: missing `release:` target with a TAB-indented recipe")
    else:
        recipe = match.group(1)
        lines = recipe.splitlines()
        if len(lines) != 2:
            problems.append(
                "Makefile: `release` recipe must be exactly 2 lines"
                f" (@echo BLOCKED-stub + @exit 1), found {len(lines)}")
        for index, line in enumerate(lines, start=1):
            if not line.startswith("\t"):
                problems.append(f"Makefile: `release` line {index}: must start with TAB")
                continue
            body = line[1:]
            if body.startswith("-"):
                problems.append(
                    f"Makefile: `release` line {index}: '-' (ignore-error) prefix forbidden"
                    " (would neutralise 'exit 1')")
            if not body.startswith("@"):
                problems.append(
                    f"Makefile: `release` line {index}: must use '@' prefix"
                    f" (got {body[:12]!r}; conditional/silent bypass refused)")
        if lines:
            body1 = lines[0][1:] if lines[0].startswith("\t") else lines[0]
            if not re.fullmatch(r"@echo\s+'BLOCKED:.*'\s+>&2\s*", body1):
                problems.append(
                    "Makefile: `release` line 1 must be \"@echo 'BLOCKED: ...' >&2\""
                    f" (got {body1!r}; preceding build commands refused)")
        if len(lines) >= 2:
            body2 = lines[1][1:] if lines[1].startswith("\t") else lines[1]
            if body2.strip() != "@exit 1":
                if "exit 1" not in body2:
                    problems.append("Makefile: `release` recipe must end with '@exit 1'")
                else:
                    problems.append(
                        f"Makefile: `release` line 2 must be exactly '@exit 1'"
                        f" (got {body2!r}; conditional/chained success refused)")
            if ";" in body2 or "||" in body2 or "&&" in body2 or "|" in body2:
                problems.append(
                    "Makefile: `release` line 2: shell chaining"
                    f" (';'/||/&&/|) forbidden (got {body2!r})")
        if "BLOCKED" not in recipe:
            problems.append("Makefile: `release` recipe lacks BLOCKED")
        if re.search(r"exit\s+0\b", recipe):
            problems.append("Makefile: `release` recipe must not contain 'exit 0'"
                            " (conditional success refused)")
        code_outside_quotes = re.sub(r"'[^']*'", "''", recipe)
        code_no_redir = code_outside_quotes.replace(">&2", "")
        for token in (";", "||", "&&", "|", "`", "$(", "${", ">", "<", "#"):
            if token in code_no_redir:
                problems.append(
                    f"Makefile: `release` recipe: {token!r} outside quoted message forbidden"
                    " (chaining/substitution/redirection refused)")
                break
        if re.search(r"\btrue\b", code_no_redir) or re.search(r"\bfalse\b", code_no_redir):
            problems.append(
                "Makefile: `release` recipe must not contain 'true'/'false'"
                " (success-masking refused)")
        for forbidden in ("build_rom.py", "--mode release", "release_eligible=true",
                          "ALLOWED", "release_eligible = True", "build_rom",
                          "$(PYTHON)", "${", "rm ", "rm\t", "cp ", "mv ",
                          "chmod", "chown", "curl", "wget", "python"):
            if forbidden in recipe:
                problems.append(
                    f"Makefile: `release` recipe must not contain {forbidden!r}"
                    " (preceding build / dangerous statement refused)")
                break
    if "tools/build_rom.py --release-gate" not in text:
        problems.append("Makefile: dev-release-gate no longer routes to tools/build_rom.py --release-gate")
    # 2. build_rom.release_gate must refuse even a fully overridden slot set.
    sys.path.insert(0, str(root / "tools"))
    try:
        import build_rom as _build_rom  # noqa: E402
        for slots in (set(), set(range(81))):
            try:
                _build_rom.release_gate(slots)
            except _build_rom.ReleaseBlocked:
                pass
            except Exception as error:  # noqa: BLE001
                problems.append(f"tools/build_rom.py release_gate raised {type(error).__name__}, not ReleaseBlocked")
            else:
                problems.append("tools/build_rom.py release_gate returned instead of refusing (BLOCKED)")
    except Exception as error:  # noqa: BLE001
        problems.append(f"tools/build_rom.py: cannot exercise release_gate ({error})")
    finally:
        try:
            sys.path.remove(str(root / "tools"))
        except ValueError:
            pass
    # 3. Spec ledgers keep release_eligible=false (top-level where present;
    #    spec/reference_jsr_gate.json carries it per slot, not top-level). None may be true.
    for rel in ("spec/dev_implementations.json", "spec/abi1_reference_review.json",
                "spec/reference_jsr_gate.json", "spec/hardware_observations.json"):
        path = root / rel
        if not path.is_file():
            continue
        try:
            raw = path.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except (OSError, ValueError) as error:
            problems.append(f"{rel}: unreadable ledger ({error})")
            continue
        top = payload.get("release_eligible", None)
        if top is True:
            problems.append(f"{rel}: release_eligible is not false")
        elif top is False:
            pass
        else:
            # No top-level flag: every nested release_eligible must be false and none may be true.
            if '"release_eligible": true' in raw or '"release_eligible":true' in raw:
                problems.append(f"{rel}: nested release_eligible is set")
            nested = []

            def _collect(obj):
                if isinstance(obj, dict):
                    for key, value in obj.items():
                        if key == "release_eligible":
                            nested.append(value)
                        else:
                            _collect(value)
                elif isinstance(obj, list):
                    for value in obj:
                        _collect(value)
            _collect(payload)
            if nested and any(flag is not False for flag in nested):
                problems.append(f"{rel}: nested release_eligible is not all false")
    return problems


def check_patch_isolation(root: Path = ROOT) -> list[str]:
    """Patches stay out-of-tree, GPL-noticed, and unreferenced by release paths."""
    problems: list[str] = []
    patches = root / "tests/integration/patches"
    if not patches.is_dir():
        problems.append("tests/integration/patches: missing verification-only patch directory")
        return problems
    patch_files = sorted(patches.glob("geargrafx-*.patch"))
    if not patch_files:
        problems.append("tests/integration/patches: no pinned Geargrafx diff present")
    if not (patches / "LICENSE").is_file():
        problems.append("tests/integration/patches/LICENSE: missing GPL notice for the patch directory")
    if not (patches / "README.md").is_file():
        problems.append("tests/integration/patches/README.md: missing isolation documentation")
    # No release artifact path may stage the patches directory or a probe object.
    for rel in (".github/workflows/ci.yml", ".github/workflows/api-source.yml",
                ".github/workflows/core-harness.yml", ".github/workflows/core-bcdbin.yml"):
        path = root / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for marker in ("tests/integration/patches", "geargrafx_libretro.so",
                       "TOOLS_DIR", ".tmp", ".o ", ".o\n"):
            if marker.strip() and marker.strip() in text and "path:" in text and marker.strip() in text.split("path:")[1][:400]:
                problems.append(f"{rel}: upload path may stage verification-only/GPL-adjacent content ({marker.strip()!r})")
                break
    # TOOLS_DIR must stay outside the repository (external source isolation).
    env = root / "tools/environment.py"
    try:
        env_text = env.read_text(encoding="utf-8")
    except OSError as error:
        problems.append(f"tools/environment.py: unreadable ({error})")
    else:
        if "must be outside this repository" not in env_text and "outside this repository" not in env_text:
            problems.append("tools/environment.py: TOOLS_DIR external-source isolation guard missing")
    return problems


def _scan_blob(blob: bytes, label: str) -> list[str]:
    """Forbidden-marker scan of one ROM image."""
    problems: list[str] = []
    for marker in FORBIDDEN_MARKERS:
        if marker.encode() in blob:
            problems.append(f"{label} smoke ROM contains {marker!r}")
    return problems


def _rebuild_mode_roms(root: Path) -> tuple[bytes | None, bytes | None, str]:
    """Rebuild debug+release smoke ROMs from `root` sources with the pinned cc65.

    Returns (debug_bytes, release_bytes, note). A None side means that mode did
    not build (reason in note). ROM bytes depend only on sources/config plus the
    pinned toolchain; manifests and git metadata never affect the comparison.
    """
    try:
        sys.path.insert(0, str(root / "tools"))
        try:
            from environment import cc65_skip_reason  # noqa: E402
        except Exception as error:  # noqa: BLE001
            return None, None, f"toolchain check unavailable ({error})"
        reason = cc65_skip_reason()
        if reason is not None:
            return None, None, reason
        try:
            import build as smoke_build  # noqa: E402
        except Exception as error:  # noqa: BLE001
            return None, None, f"cannot import tools/build.py ({error})"
    finally:
        try:
            sys.path.remove(str(root / "tools"))
        except ValueError:
            pass
    results: dict[str, bytes | None] = {}
    notes: list[str] = []
    for mode in ("debug", "release"):
        try:
            with tempfile.TemporaryDirectory(prefix=f"pce-verify-{mode}-") as tmp:
                outdir = Path(tmp) / mode
                with contextlib.redirect_stdout(io.StringIO()):
                    smoke_build.build(outdir, mode=mode,
                                      source=root / "tests/fixtures/smoke.s",
                                      config=root / "tests/fixtures/smoke.cfg")
                results[mode] = (outdir / "smoke-test-not-bios.pce").read_bytes()
        except Exception as error:  # noqa: BLE001
            results[mode] = None
            notes.append(f"{mode}: {type(error).__name__}: {error}")
    return results["debug"], results["release"], "; ".join(notes)


def _pinned_cc65_revision(root: Path) -> str | None:
    """Pinned cc65 revision from this checkout (evidence field, never a build input)."""
    try:
        return json.loads((root / "tools/dependencies.json").read_text(encoding="utf-8"))["cc65"]["revision"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def scan_release_artifacts(root: Path = ROOT) -> tuple[list[str], str, dict]:
    """Byte-compare freshly rebuilt debug+release smoke ROMs (never stale outputs).

    Returns (problems, disposition, evidence). Both modes are always rebuilt
    from the current sources in temporary directories with the pinned cc65;
    pre-existing build/debug+build/release files are NEVER used as byte
    evidence (they may predate the sources and would PASS stale). Disposition
    is CHECKED when both rebuilt bytes were actually compared, SKIPPED
    otherwise -- a skipped comparison is never reported as PASS; see the
    `byte_identity` report field. Evidence carries `compared`, per-mode
    sha256, the pinned toolchain revision, the rebuild status, and the byte
    source.
    """
    cc65_revision = _pinned_cc65_revision(root)
    debug, release, note = _rebuild_mode_roms(root)
    status = note or "both modes rebuilt OK"
    base_source = ("rebuilt from current source with pinned cc65"
                   " (build/ outputs ignored as stale)")
    if debug is None and release is None:
        return [], (f"SKIPPED (no rebuild: {note or 'unavailable'};"
                    " no byte-identity evidence)"), {
            "compared": False, "debug_sha256": None, "release_sha256": None,
            "source": "unavailable",
            "cc65_revision": cc65_revision,
            "rebuild_status": status,
        }
    if debug is None or release is None:
        missing = "debug" if debug is None else "release"
        return ([f"smoke ROM {missing}-mode rebuild failed while the other mode succeeded"
                 f" ({note}): mode-dependent build behavior"],
                f"SKIPPED (asymmetric {missing}-mode rebuild failure: {note})", {
            "compared": False,
            "debug_sha256": hashlib.sha256(debug).hexdigest() if debug is not None else None,
            "release_sha256": hashlib.sha256(release).hexdigest() if release is not None else None,
            "source": base_source,
            "cc65_revision": cc65_revision,
            "rebuild_status": status,
        })
    problems: list[str] = []
    if debug != release:
        problems.append("rebuilt release smoke ROM differs from rebuilt debug"
                        " (mode must only add -g/-l, never ROM bytes)")
    problems.extend(_scan_blob(debug, "rebuilt debug"))
    problems.extend(_scan_blob(release, "rebuilt release"))
    state = "identical" if debug == release else "DIFFERENT"
    return problems, f"CHECKED (byte-{state}, {base_source})", {
        "compared": True,
        "debug_sha256": hashlib.sha256(debug).hexdigest(),
        "release_sha256": hashlib.sha256(release).hexdigest(),
        "source": base_source,
        "cc65_revision": cc65_revision,
        "rebuild_status": status,
    }


def build_report(root: Path = ROOT) -> dict:
    """JSON-serializable audit report. Unmeasured equivalence stays UNKNOWN."""
    input_problems = scan_release_inputs(root)
    import_problems = check_build_imports(root)
    mode_problems = check_mode_branches(root)
    gate_problems = check_release_gates(root)
    isolation_problems = check_patch_isolation(root)
    artifact_problems, artifact_disposition, artifact_evidence = scan_release_artifacts(root)
    if artifact_evidence["compared"] and not artifact_problems:
        byte_verdict = "PASS"
    elif artifact_problems:
        byte_verdict = "FAIL"
    else:
        byte_verdict = "SKIPPED"  # skipped is never reported as PASS
    problems = (input_problems + import_problems + mode_problems
                + gate_problems + isolation_problems + artifact_problems)
    return {
        "schema": "oss-pce-cd-bios/724-build-diff-audit/v1",
        "base": "864d979c",
        "verdict": "PASS" if not problems else "FAIL",
        "problems": problems,
        "debug_vs_release": {
            "smoke_builder": "tools/build.py: release differs from debug only by omitting -g/-l; "
                             "ROM bytes must be identical (CI cmp debug/release).",
            "dev_builder": "tools/build_rom.py: release differs from debug only by omitting -g for the "
                           "generated api_table.s; override objects keep -g in both modes for ld65 debug "
                           "info; ROM bytes must be identical.",
            "hooks": "Verification-only hooks (gg_verify/GG_Verify, block-write/stack-access/ST-write/IRQ-entry "
                     "guards) exist only in tests/integration/patches/*.patch and staged TOOLS_DIR copies; "
                     "no hook symbol is referenced by tools/build*.py or linked into build/ outputs.",
            "stop_points": "Stop points (pre-instruction, pre-write, pre-push/pop, decode-time TAM, IRQ deferral) "
                           "live in the debugger/probe harness, not in release ROM bytes; release has no stop "
                           "mechanism and claims none.",
            "failure_behavior": "Invalid mode fails before output; unimplemented slots trap in a nonreturning "
                                "loop; `make release` and --release-gate refuse with BLOCKED (exit 1/3), never 0.",
        },
        "unmeasured_UNKNOWN": {
            "free_run_equivalence": "UNKNOWN: no free-run (non-debugger) harness measurement; "
                                    "debugger stop semantics do not transfer.",
            "libretro_equivalence": "UNKNOWN: libretro core exposes no single-instruction stop API (#103); "
                                    "release behavior there is NOT_RUN.",
            "release_equivalence": "UNKNOWN until byte-identical debug/release ROMs are compared "
                                   "on the same checkout (this gate rebuilds+compares when the pinned cc65 "
                                   "is usable, else byte_identity is SKIPPED; CI cmp + rom_provenance "
                                   "--reproduce); a passing static gate is not execution proof.",
            "production_safety": "NOT PASS: release_eligible=false, ABI VERIFIED 0/81, DEV_CALLABLE 0; "
                                 "no mode change promotes any of these.",
        },
        "release_gates": {
            "release_eligible": False,
            "abi_verified": "0/81",
            "dev_callable": 0,
            "make_release": "BLOCKED (exit 1)",
            "dev_release_gate": "BLOCKED (exit 3)",
        },
        "release_artifact_scan": artifact_disposition,
        "byte_identity": {
            "verdict": byte_verdict,
            "detail": artifact_disposition,
            "debug_sha256": artifact_evidence["debug_sha256"],
            "release_sha256": artifact_evidence["release_sha256"],
            "source": artifact_evidence["source"],
            "cc65_revision": artifact_evidence.get("cc65_revision"),
            "rebuild_status": artifact_evidence.get("rebuild_status"),
        },
        "provenance": "synthetic fixtures/media only; pinned cc65; pinned Geargrafx b49ae82 reference only; "
                      "no private ./rom/ inputs used or required.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="store_true",
                        help="emit the JSON audit report instead of the one-line verdict")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        report = build_report(args.root)
    except ValueError as error:
        print(f"FAIL 724 build-diff audit: {error}", file=sys.stderr)
        return 1
    if args.report:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["verdict"] == "PASS" else 1
    if report["verdict"] == "PASS":
        print("PASS 724 build-diff audit: release inputs clean; mode only selects -g/-l; "
              "BLOCKED gates intact; unmeasured equivalence stays UNKNOWN "
              f"(byte-identity: {report['byte_identity']['verdict']} --"
              f" {report['release_artifact_scan']})")
        return 0
    for problem in report["problems"]:
        print(f"FAIL 724: {problem}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
