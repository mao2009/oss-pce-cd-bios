#!/usr/bin/env python3
"""Static guard for the ``make release`` rule graph (Issue #725 MAJOR-1).

The previous ``check_makefile_release`` inspected only the main ``Makefile``.
That misses rule-graph-changing inputs: an ``include extras.mk`` line can
pull in a second file that adds ``release: preflight`` prerequisites or a
second ``release`` rule. The ``preflight`` recipe then runs *before* the
pinned ``@exit 1`` fails, so a non-zero exit proves nothing.

This module inspects those inputs statically and fails closed. It never
executes any Makefile (no ``make``, no shell, no ``eval``); it only reads
text files under the given root.

``check_release_makefile(root)`` returns ``[]`` when the safe-refusal shape
is confirmed, otherwise a list of human-readable reasons. Any undecidable
input (missing file, expansion in an include path, recipe-prefix change,
dynamic ``$(eval)`` rule, conditional/``define`` graph change) is a reason,
never a pass.

Allowed dev-target expansions (``$(PYTHON)``/``$(MAKE)``/``$(MODE)`` and
friends) outside the ``release`` rule are *not* refused here; only inputs
that can alter the ``release`` deps or recipe are refused.

Evaluation-time side effects (parse-time ``:=``/``::=``/``?=``/``+=``/``!=``
assignments, rule prerequisites, directive arguments) are refused when they
can run commands or change state before the ``release`` stub is reached:
``$(shell ...)``, ``$(file ...)`` (may write files), ``$(eval ...)`` (rewrites
the rule graph), ``$(call ...)`` (indirect: the callee may expand to any of
the above, including via computed names), backticks, and the ``!=`` shell
assignment operator. Immediate and deferred (``=``) flavors are
distinguished in the diagnostics, but deferred definitions with
side-effecting expansions are refused as well: proving that no parse-time
position (another immediate assignment, a prerequisite list, a directive
argument) ever references the variable is undecidable once ``$(call)`` or
computed ``$($(NAME))`` references exist. ``$(info)``/``$(warning)`` output
is allowed (console text only, no execution or state change) while
``$(error)`` aborts the build (fail-closed outcome); both are still scanned
for nested refused tokens. ``$$`` (escaped dollar) is collapsed before
scanning so it cannot hide — or falsely trigger — a token match.
"""
from __future__ import annotations

import re
from pathlib import Path


def _is_comment_or_blank(line: str) -> bool:
    stripped = line.strip()
    return stripped == "" or line.lstrip().startswith("#")


def _check_global_inputs(all_lines: list[str]) -> list[str]:
    """Refuse rule-graph inputs that live outside the release stanza."""
    problems: list[str] = []
    for lineno, line in enumerate(all_lines, start=1):
        if _is_comment_or_blank(line):
            continue
        # Recipe lines (TAB-prefixed under the default prefix) can never be
        # make directives; the shell command `include foo` inside a dev
        # recipe must not trip the include guard.  Once .RECIPEPREFIX is set
        # we already fail closed, so TAB is the only prefix to exclude.
        if line.startswith("\t"):
            # Still refuse dynamic $(eval) even inside recipes: make expands
            # it before the shell ever runs, so it can rewrite the rule
            # graph from any line.
            if re.search(r"\$\(\s*eval\b", line) or re.search(
                r"\$\{\s*eval\b", line
            ):
                problems.append(
                    f"Makefile line {lineno}: '$(eval ...)' dynamic rule "
                    "refused (rule-graph rewrite; fail-closed)"
                )
            continue
        include_match = re.match(
            r"^[ ]*(-include|sinclude|include)\b(.*)$", line
        )
        if include_match is not None:
            directive, args = include_match.group(1), include_match.group(2)
            problems.append(
                f"Makefile line {lineno}: '{directive}{args}' external "
                "include refused (rule-graph input outside main Makefile; "
                "undecidable without execution; fail-closed; prove safe or "
                "remove)"
            )
            if "$" in args or "`" in args:
                problems.append(
                    f"Makefile line {lineno}: make/shell expansion in "
                    "include path refused (undecidable include set; "
                    "fail-closed)"
                )
            continue
        if re.search(r"\$\(\s*eval\b", line) or re.search(
            r"\$\{\s*eval\b", line
        ):
            problems.append(
                f"Makefile line {lineno}: '$(eval ...)' dynamic rule "
                "refused (rule-graph rewrite; fail-closed)"
            )
        if "RECIPEPREFIX" in line:
            problems.append(
                f"Makefile line {lineno}: '.RECIPEPREFIX' change refused "
                "(recipe-prefix change makes TAB-parsing undecidable; "
                "fail-closed)"
            )
        if re.match(
            r"^[ ]*(ifeq|ifneq|ifdef|ifndef|else|endif)\b", line
        ) is not None:
            problems.append(
                f"Makefile line {lineno}: make conditional refused "
                "(release rule graph becomes variable-dependent and "
                "undecidable without execution; fail-closed)"
            )
        if re.match(r"^[ ]*(define|endef|undefine)\b", line) is not None:
            problems.append(
                f"Makefile line {lineno}: 'define/endef' block refused "
                "(dynamic make content; undecidable without execution; "
                "fail-closed)"
            )
    return problems


# Function calls with parse/evaluation-time side effects. $(shell) runs
# external commands, $(file) can write files, $(eval) rewrites the rule
# graph, and $(call) invokes a user-defined function that may expand to any
# of those (possibly via computed names, so indirection cannot be resolved
# statically). $(info)/$(warning) only print console text (no execution or
# state change) and $(error) aborts the build (fail-closed outcome), so bare
# uses are tolerated -- but any nested refused token inside their arguments
# is still caught by the same scan.
_EVAL_SIDE_EFFECT_FUNCS = ("shell", "file", "call", "eval")

# Reference to the special prerequisite-injecting variable, either
# reference syntax. Checked before anything else: no legitimate use exists
# in this repository.
_EXTRA_PREREQS_REF = re.compile(r"\$[\(\{]\s*\.EXTRA_PREREQS\b")

# Bare mention of the exact special variable name (definition in any
# flavor/prefix/spacing, or a target line reusing the name). A longer name
# such as `.EXTRA_PREREQS_FOO` is a distinct ordinary variable with no
# special meaning and must not trip the check.
_EXTRA_PREREQS_MENTION = re.compile(r"\.EXTRA_PREREQS(?![A-Za-z0-9_.-])")

# Immediate flavors evaluate their right-hand side at parse time. '+=' is
# immediate when the variable is already simply expanded and deferred
# otherwise -- statically undecidable, so it is treated as immediate
# (fail-closed). Plain '=' defers expansion to the reference site, but
# references from parse-time positions cannot be ruled out once $(call)
# or computed names exist, so deferred definitions with side-effecting
# expansions are refused as well (with the flavor named in the reason).
_IMMEDIATE_OPS = (":=", "::=", "?=", "+=")


def _collapse_escaped_dollars(text: str) -> str:
    """Collapse '$$' (literal dollar) so it can neither hide nor fake a token."""
    return text.replace("$$", "")


def _find_side_effect_calls(text: str) -> list[str]:
    """Names of side-effecting $(func)/${func} calls in collapsed text."""
    found: list[str] = []
    for match in re.finditer(r"\$[\(\{]\s*([A-Za-z][A-Za-z0-9_-]*)", text):
        name = match.group(1)
        if name in _EVAL_SIDE_EFFECT_FUNCS and name not in found:
            found.append(name)
    return found


def _logical_lines(
    all_lines: list[str],
) -> list[tuple[str, int]]:
    """Join backslash continuations: [(logical text, start lineno)].

    Classification uses the first physical line (a TAB there means a
    recipe), so a variable value continued on a TAB-prefixed line is still
    scanned, while a recipe continued over several lines stays a recipe.
    """
    logical: list[tuple[str, int]] = []
    index = 0
    total = len(all_lines)
    while index < total:
        start = index
        parts = [all_lines[index]]
        while parts[-1].endswith("\\") and index + 1 < total:
            index += 1
            parts.append(all_lines[index])
        # A backslash-newline is removed by make (both characters gone);
        # model exactly that so a token split across the break still
        # matches (e.g. '$(ev\<newline>al ...)').
        text = "".join(
            part[:-1] if part.endswith("\\") else part for part in parts
        )
        logical.append((text, start + 1))
        index += 1
    return logical


def _definition_flavor_and_value(
    line: str,
) -> tuple[str, str] | None:
    """Parse '[export/override/private ...] NAME op value' (op winds shown).

    Returns (flavor, value) with flavor in {'immediate', 'deferred',
    'shell'} or None when the line is not a variable definition.
    """
    match = re.match(
        r"^[ ]*(?:(?:export|override|private)[ ]+)*"
        r"([^:=\s#][^:=\s#]*)\s*(::=|:=|\+=|\?=|!=|=)(.*)$",
        line,
    )
    if match is None:
        return None
    _name, op, value = match.group(1), match.group(2), match.group(3)
    if op == "!=":
        return ("shell", value)
    if op in _IMMEDIATE_OPS:
        return ("immediate", value)
    return ("deferred", value)


def _check_evaluation_time_side_effects(
    all_lines: list[str],
) -> list[str]:
    """Refuse parse/evaluation-time command execution outside recipes.

    Recipe lines (TAB-prefixed logical lines) run only when their target
    builds, so plain $(shell)/$(file) there cannot fire before the release
    refusal -- except $(eval), which make expands at parse time even inside
    recipes and is scanned on joined logical text (closing the
    backslash-split evasion). Everywhere else (assignments, prerequisites,
    directives, export values) expansions fire at parse time, so any
    side-effecting call, backtick, or '!=' operator is refused.

    GNU Make also honors the special variable .EXTRA_PREREQS: any value
    assigned to it is appended to the prerequisites of every target,
    including `release:`. A stub recipe with no direct prerequisites would
    then run the injected prerequisite first, so any mention of
    .EXTRA_PREREQS outside comments -- in any assignment flavor,
    override/export form, continuation, or spacing -- is refused, as is
    any $(.EXTRA_PREREQS)/${.EXTRA_PREREQS} reference anywhere.
    """
    problems: list[str] = []
    for text, lineno in _logical_lines(all_lines):
        if text.strip() == "" or text.lstrip().startswith("#"):
            continue
        collapsed = _collapse_escaped_dollars(text)
        is_recipe = text.startswith("\t")
        if _EXTRA_PREREQS_REF.search(collapsed) is not None:
            problems.append(
                f"Makefile line {lineno}: '$(.EXTRA_PREREQS)' reference "
                "refused (indirect prerequisite injection vector; "
                "fail-closed)"
            )
            continue
        if not is_recipe and _EXTRA_PREREQS_MENTION.search(collapsed) is not None:
            problems.append(
                f"Makefile line {lineno}: '.EXTRA_PREREQS' definition or "
                "mention outside any recipe refused (appends prerequisites "
                "to every target including 'release'; fail-closed)"
            )
            continue
        if is_recipe:
            # Only $(eval) matters at parse time inside recipes; it is
            # refused here on joined text (the physical-line scan could
            # miss a backslash-split '$(eval ...)').
            for func in _find_side_effect_calls(collapsed):
                if func == "eval":
                    problems.append(
                        f"Makefile line {lineno}: '$(eval ...)' dynamic "
                        "rule refused (rule-graph rewrite; fail-closed)"
                    )
                    break
            continue
        # Shell-assignment operator executes at parse time, always.
        flavored = _definition_flavor_and_value(collapsed)
        if flavored is not None and flavored[0] == "shell":
            problems.append(
                f"Makefile line {lineno}: '!=' shell assignment executes "
                "at parse time (refused; fail-closed)"
            )
            continue
        for func in _find_side_effect_calls(collapsed):
            if flavored is not None:
                flavor, _value = flavored
                timing = (
                    "parse-time"
                    if flavor != "deferred"
                    else "reference scope undecidable under "
                    "$(call)/computed names"
                )
                problems.append(
                    f"Makefile line {lineno}: '$({func} ...)' in a "
                    f"{flavor} variable definition evaluates before the "
                    f"release refusal ({timing}; fail-closed)"
                )
            else:
                problems.append(
                    f"Makefile line {lineno}: '$({func} ...)' outside any "
                    "recipe evaluates at parse time (prerequisite, "
                    "directive, or export context; fail-closed)"
                )
        if "`" in collapsed:
            problems.append(
                f"Makefile line {lineno}: backtick command substitution "
                "outside any recipe refused (fail-closed)"
            )
    # .SECONDEXPANSION changes the evaluation-timing model the static
    # reasoning assumes (prerequisites expanded a second time when the
    # target is considered); refuse it as undecidable.
    for lineno, line in enumerate(all_lines, start=1):
        if _is_comment_or_blank(line) or line.startswith("\t"):
            continue
        if re.match(r"^[ ]*\.SECONDEXPANSION\s*:", line) is not None:
            problems.append(
                f"Makefile line {lineno}: '.SECONDEXPANSION' changes the "
                "prerequisite evaluation-timing model (undecidable; "
                "fail-closed)"
            )
    return problems


def _resolve_include_args(args: str) -> list[str]:
    """Split include args into literal tokens (static-only, no expansion)."""
    # Strip trailing make comments (` # ...`) conservatively: a `#` inside
    # a filename is vanishingly rare and failing closed on it is acceptable.
    cut = args.find(" #")
    if cut != -1:
        args = args[:cut]
    else:
        # A bare `#` after whitespace also starts a comment.
        parts = args.split("#", 1)
        if len(parts) == 2 and (parts[0] == "" or parts[0][-1] in " \t"):
            args = parts[0]
    return [tok for tok in args.split() if tok]


def _check_included_files(
    root: Path, all_lines: list[str]
) -> list[str]:
    """Best-effort static scan of literal include targets (never executes)."""
    problems: list[str] = []
    for lineno, line in enumerate(all_lines, start=1):
        if _is_comment_or_blank(line) or line.startswith("\t"):
            continue
        include_match = re.match(
            r"^[ ]*(-include|sinclude|include)\b(.*)$", line
        )
        if include_match is None:
            continue
        directive = include_match.group(1)
        for token in _resolve_include_args(include_match.group(2)):
            if "$" in token or "`" in token or "*" in token or "?" in token:
                problems.append(
                    f"Makefile line {lineno}: {directive} target {token!r} "
                    "has expansion/wildcard (undecidable file set; "
                    "fail-closed)"
                )
                continue
            candidate = root / token
            try:
                if not candidate.is_file():
                    # -include/sinclude tolerate absence at build time, but
                    # absence here is undecidable (file may be generated), so
                    # the bare include refusal above already fails closed;
                    # note it for diagnostics without executing anything.
                    problems.append(
                        f"Makefile line {lineno}: {directive} target "
                        f"{token!r} not statically resolvable "
                        "(missing or generated; fail-closed)"
                    )
                    continue
                text = candidate.read_text(encoding="utf-8")
            except OSError as exc:
                problems.append(
                    f"Makefile line {lineno}: {directive} target {token!r} "
                    f"unreadable ({exc}; fail-closed)"
                )
                continue
            for sub_no, sub_line in enumerate(text.splitlines(), start=1):
                if sub_line.startswith("\t"):
                    continue
                sub_match = re.match(
                    r"^[ ]*release\s*(::|:)(.*)$", sub_line
                )
                if sub_match is not None:
                    problems.append(
                        f"Makefile line {lineno}: {directive} target "
                        f"{token!r} line {sub_no} defines release "
                        f"'{sub_line.strip()}' (external release rule; "
                        "refused)"
                    )
                if re.search(r"\$\(\s*eval\b", sub_line) or re.search(
                    r"\$\{\s*eval\b", sub_line
                ):
                    problems.append(
                        f"Makefile line {lineno}: {directive} target "
                        f"{token!r} line {sub_no} contains '$(eval ...)' "
                        "(dynamic rule; refused)"
                    )
    return problems


def check_release_makefile(root: Path) -> list[str]:
    """Static contract of the ``release:`` target and its rule-graph inputs.

    Never executes any Makefile. Returns ``[]`` only when the safe-refusal
    shape is confirmed (exactly the two-line BLOCKED stub, no external
    inputs); otherwise returns reasons. Undecidable inputs fail closed.
    """
    problems: list[str] = []
    makefile = Path(root) / "Makefile"
    try:
        text = makefile.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"Makefile unreadable ({exc}; fail-closed)"]
    except ValueError as exc:
        return [f"Makefile undecodable ({exc}; fail-closed)"]
    all_lines = text.splitlines()

    # 1. Rule-graph-changing inputs outside the release stanza.
    problems.extend(_check_global_inputs(all_lines))
    problems.extend(_check_included_files(Path(root), all_lines))
    # 1b. Parse/evaluation-time side effects (immediate assignments,
    # prerequisites, directive arguments) that fire before the stub.
    problems.extend(_check_evaluation_time_side_effects(all_lines))

    # 2. Release target shape (main Makefile only; includes already refused).
    # Recipe lines start with TAB and can never define a target, so they are
    # skipped here.
    definitions: list[tuple[int, str, str, str]] = []
    for lineno, line in enumerate(all_lines, start=1):
        if line.startswith("\t"):
            continue
        match = re.match(r"^[ ]*release\s*(::|:)(.*)$", line)
        if match is not None:
            definitions.append(
                (lineno, line, match.group(1), match.group(2))
            )
    if not definitions:
        return problems + ["Makefile has no 'release:' target"]
    if len(definitions) != 1:
        problems.append(
            "Makefile release target must be defined exactly once "
            f"(found {len(definitions)} definitions at lines "
            + ", ".join(str(n) for n, _, _, _ in definitions)
            + "; duplicate/alternate release rules refused)"
        )
    lineno, target_line, colon, rest = definitions[0]
    if colon == "::":
        problems.append(
            f"Makefile release line {lineno} must use a single ':' "
            "(double-colon 'release::' rules run alongside the pinned "
            "recipe; refused)"
        )
    if rest.strip() != "":
        problems.append(
            f"Makefile release line {lineno} must be exactly 'release:' "
            f"with no prerequisites or inline recipe (got {target_line.strip()!r}; "
            "target deps refused)"
        )
        if "$" in rest:
            problems.append(
                f"Makefile release line {lineno}: make expansion in "
                "prerequisites refused (var/func expansion altering "
                "release deps; fail-closed)"
            )
        if "=" in rest or "export" in rest:
            problems.append(
                f"Makefile release line {lineno}: target-specific "
                "variable assignment refused "
                f"(got {target_line.strip()!r}; fail-closed)"
            )
        if ";" in rest:
            problems.append(
                f"Makefile release line {lineno}: inline ';' recipe "
                "refused (must be the pinned 2-line TAB recipe)"
            )
    elif target_line != "release:":
        problems.append(
            f"Makefile release line {lineno} must be exactly 'release:' "
            f"(got {target_line!r})"
        )
    # Recipe: the contiguous TAB-prefixed block after the target. Blank and
    # `#`-comment lines do not terminate a make recipe scan, so the first
    # significant line after the stub must not be TAB-prefixed either.
    target_index = definitions[0][0] - 1
    recipe_rows: list[tuple[int, str]] = []
    cursor = target_index + 1
    while cursor < len(all_lines) and all_lines[cursor].startswith("\t"):
        recipe_rows.append((cursor + 1, all_lines[cursor]))
        cursor += 1
    probe = cursor
    while probe < len(all_lines) and (
        all_lines[probe].strip() == ""
        or all_lines[probe].lstrip().startswith("#")
    ):
        probe += 1
    if probe < len(all_lines) and all_lines[probe].startswith("\t"):
        problems.append(
            "Makefile release recipe must be exactly 2 lines "
            f"(@echo BLOCKED-stub + @exit 1; extra recipe line at line {probe + 1} "
            "refused: recipe-append bypass)"
        )
    lines = [line for _, line in recipe_rows]
    recipe = "".join(line + "\n" for line in lines)
    # Strict allowlist: exactly the echo-BLOCKED + exit-1 stub, nothing more.
    if len(lines) != 2:
        problems.append(
            "Makefile release recipe must be exactly 2 lines "
            f"(@echo BLOCKED-stub + @exit 1), found {len(lines)}"
        )
    for index, line in enumerate(lines, start=1):
        if not line.startswith("\t"):
            problems.append(
                f"Makefile release line {index}: must start with TAB"
            )
            continue
        body = line[1:]
        if body.startswith("-"):
            problems.append(
                f"Makefile release line {index}: '-' (ignore-error) prefix "
                "forbidden (would neutralise 'exit 1'; '-@exit 1' refused)"
            )
        if not body.startswith("@"):
            problems.append(
                f"Makefile release line {index}: must use '@' prefix "
                f"(got {body[:12]!r}; conditional/silent bypass refused)"
            )
    if lines:
        body1 = lines[0][1:] if lines[0].startswith("\t") else lines[0]
        # The message charset is deliberately narrow: no '$' (GNU Make
        # expands $(...)/${...}/$X even inside single quotes), no backtick,
        # backslash, double-quote, or shell/make metacharacter. ';' inside
        # the quotes is permitted: it is safe for both make (no '$') and the
        # shell (single-quoted). Rewording the message needs a gate update.
        if not re.fullmatch(
            r"@echo 'BLOCKED: [A-Za-z0-9 .,;:\-_/]+' >&2", body1
        ):
            problems.append(
                "Makefile release line 1 must be \"@echo 'BLOCKED: ...' >&2\" "
                "with a make-expansion-free message "
                f"(got {body1!r}; preceding build commands and '$(...)'/"
                "backtick expansion refused, even inside quotes)"
            )
    if len(lines) >= 2:
        body2 = lines[1][1:] if lines[1].startswith("\t") else lines[1]
        if body2.strip() != "@exit 1":
            if "exit 1" not in body2:
                problems.append(
                    "Makefile release recipe must end with '@exit 1'"
                )
            else:
                problems.append(
                    f"Makefile release line 2 must be exactly '@exit 1' "
                    f"(got {body2!r}; conditional/chained success refused)"
                )
        if ";" in body2 or "||" in body2 or "&&" in body2 or "|" in body2:
            problems.append(
                "Makefile release line 2: shell chaining "
                f"(';'/||/&&/|) forbidden (got {body2!r})"
            )
    if "BLOCKED" not in recipe:
        problems.append("Makefile release recipe lacks BLOCKED")
    # Unconditional success anywhere is a promotion bypass.
    if re.search(r"exit\s+0\b", recipe):
        problems.append(
            "Makefile release recipe must not contain 'exit 0' "
            "(conditional success refused)"
        )
    # GNU Make expansion and command substitution are refused *everywhere*,
    # including inside the single-quoted message: make expands '$' before
    # the shell runs, so quotes protect nothing against $(shell ...).
    # Backslash (line continuation/escape) and double-quotes (which would
    # change the shell quoting contract) are refused as well.
    for token, reason in (
        ("$", "make variable/function/command expansion"),
        ("`", "command substitution"),
        ("\\", "line continuation/escape"),
        ('"', "double-quote (quoting-contract change)"),
    ):
        if token in recipe:
            problems.append(
                f"Makefile release recipe must not contain {token!r} "
                f"({reason} refused, even inside quotes)"
            )
    # Shell metacharacters outside the quoted BLOCKED message, excluding the
    # one allowed '>&2' redirection. The ';' inside the quoted message is
    # stripped with the quotes, so this does not false-positive on legit.
    code_outside_quotes = re.sub(r"'[^']*'", "''", recipe)
    code_no_redir = code_outside_quotes.replace(">&2", "")
    for token in (";", "||", "&&", "|", ">", "<", "#"):
        if token in code_no_redir:
            problems.append(
                f"Makefile release recipe: {token!r} outside quoted message "
                "forbidden (chaining/substitution/redirection refused)"
            )
            break
    if re.search(r"\btrue\b", code_no_redir) or re.search(
        r"\bfalse\b", code_no_redir
    ):
        problems.append(
            "Makefile release recipe must not contain 'true'/'false' "
            "(success-masking refused)"
        )
    for forbidden in (
        "build_rom.py",
        "--mode release",
        "release_eligible=true",
        "ALLOWED",
        "release_eligible = True",
        "build_rom",
        "$(PYTHON)",
        "${",
        "rm ",
        "rm\t",
        "cp ",
        "mv ",
        "chmod",
        "chown",
        "curl",
        "wget",
        "python",
    ):
        if forbidden in recipe:
            problems.append(
                f"Makefile release recipe must not contain {forbidden!r} "
                "(preceding build / dangerous statement refused)"
            )
            break
    return problems
