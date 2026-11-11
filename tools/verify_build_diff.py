"""Debug/release diff audit gate for Issue #724 (design-only, no production change).

Compares verification-only/debug behavior against release claims and refuses
GPL/verification-patch leakage into release inputs. Static only: needs no
cc65, no Geargrafx core, no private media. Real-core/free-run/libretro/release
equivalence stays UNKNOWN here; this gate never reports production safety PASS.

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
import json
import re
import sys
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
RELEASE_INPUT_GLOBS = (
    "src/boot.s",
    "src/bank0.cfg",
    "src/overrides/*.s",
    "tests/fixtures/smoke.s",
    "tests/fixtures/smoke.cfg",
    "tools/build.py",
    "tools/build_rom.py",
    "tools/environment.py",
    "tools/rom.py",
    "spec/api_slots.json",
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

# Lines using the `mode` name in build logic must match one of these
# (debug-info-only branches, plumbing, or the manifest echo). Any other
# mode-dependent branch is a release-behavior fork and fails closed.
ALLOWED_MODE_LINE = re.compile(
    r"(mode\s*=\s*['\"]debug['\"]"
    r"|if mode not in \(\s*['\"]debug['\"]\s*,\s*['\"]release['\"]\s*\)"
    r"|if mode ==\s*['\"]debug['\"]"
    r"|debug\s*=\s*mode ==\s*['\"]debug['\"]"
    r"|['\"]mode['\"]\s*:\s*mode"
    r"|--mode"
    r"|args\.mode"
    r"|os\.environ\.get\("
    r"|ROOT\s*/\s*['\"]build['\"]\s*/\s*args\.mode"
    r"|MODE must be debug or release)"
)


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


def scan_release_inputs(root: Path = ROOT) -> list[str]:
    """Fail-closed content scan of release inputs for hooks/GPL text."""
    problems: list[str] = []
    for path in _release_input_files(root):
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


def check_mode_branches(root: Path = ROOT) -> list[str]:
    """Mode may only select debug info (-g/-l), never ROM behavior."""
    problems: list[str] = []
    for rel in ("tools/build.py", "tools/build_rom.py"):
        path = root / rel
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as error:
            problems.append(f"{rel}: unparseable ({error})")
            continue
        names = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Name) and n.id == "mode"]
        lines = path.read_text(encoding="utf-8").splitlines()
        for node in names:
            lineno = getattr(node, "lineno", 0)
            line = lines[lineno - 1] if 1 <= lineno <= len(lines) else ""
            if not ALLOWED_MODE_LINE.search(line):
                problems.append(
                    f"{rel}:{lineno}: unexpected mode-dependent branch: {line.strip()[:160]!r}")
    return problems


def check_release_gates(root: Path = ROOT) -> list[str]:
    """The existing BLOCKED release gates must still refuse."""
    problems: list[str] = []
    # 1. Makefile `release` target stays a refusal (static text check).
    makefile = root / "Makefile"
    try:
        text = makefile.read_text(encoding="utf-8")
    except OSError as error:
        return [f"Makefile: unreadable ({error})"]
    release_block = "\n".join(
        line for line in text.splitlines()
        if "BLOCKED" in line or line.strip() in ("release:", "@exit 1"))
    if "BLOCKED" not in release_block or "@exit 1" not in release_block:
        problems.append("Makefile: `release` target no longer refuses with BLOCKED/exit 1")
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


def scan_release_artifacts(root: Path = ROOT) -> tuple[list[str], str]:
    """If release outputs exist, they must be byte-identical to debug and clean.

    Returns (problems, disposition) where disposition is CHECKED or NOT_RUN
    (absent outputs are not evidence either way).
    """
    debug_rom = root / "build/debug/smoke-test-not-bios.pce"
    release_rom = root / "build/release/smoke-test-not-bios.pce"
    if not debug_rom.is_file() or not release_rom.is_file():
        return [], "NOT_RUN (build/debug+build/release smoke ROMs absent; run make build MODE=debug/release)"
    problems: list[str] = []
    debug = debug_rom.read_bytes()
    release = release_rom.read_bytes()
    if debug != release:
        problems.append("build/release smoke ROM differs from build/debug (mode must only add -g/-l, never ROM bytes)")
    for label, blob in (("debug", debug), ("release", release)):
        for marker in FORBIDDEN_MARKERS:
            if marker.encode() in blob:
                problems.append(f"build/{label} smoke ROM contains {marker!r}")
    return problems, "CHECKED"


def build_report(root: Path = ROOT) -> dict:
    """JSON-serializable audit report. Unmeasured equivalence stays UNKNOWN."""
    input_problems = scan_release_inputs(root)
    import_problems = check_build_imports(root)
    mode_problems = check_mode_branches(root)
    gate_problems = check_release_gates(root)
    isolation_problems = check_patch_isolation(root)
    artifact_problems, artifact_disposition = scan_release_artifacts(root)
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
            "release_equivalence": "UNKNOWN until byte-identical debug/release ROMs are rebuilt and compared "
                                   "on the same checkout (CI cmp + rom_provenance --reproduce); a passing static "
                                   "gate is not execution proof.",
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
              f"(artifact scan: {report['release_artifact_scan']})")
        return 0
    for problem in report["problems"]:
        print(f"FAIL 724: {problem}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
