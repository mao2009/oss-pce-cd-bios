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
