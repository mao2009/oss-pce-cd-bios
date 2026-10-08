"""Static checks using actual pinned actionlint and assembler."""
import ast
import subprocess

from environment import ROOT, executable, core_path


def main():
    for path in sorted(ROOT.rglob("*.py")):
        if not any(part.startswith(".") for part in path.relative_to(ROOT).parts):
            ast.parse(path.read_text(), filename=str(path))
    for path in [*ROOT.glob("scripts/*.sh"), *ROOT.glob("tools/*.sh")]:
        subprocess.run(["bash", "-n", str(path)], check=True)
    # actionlint parses YAML and checks expressions, workflow schema and job wiring.
    subprocess.run([executable("actionlint"), "-shellcheck=", "-pyflakes=",
                    *map(str, sorted(ROOT.glob(".github/workflows/*.yml")))], check=True)
    core_path()
    output = ROOT / "build/check"
    output.mkdir(parents=True, exist_ok=True)
    subprocess.run([executable("ca65"), "--cpu", "huc6280", "-o", str(output / "opcodes.o"),
                    str(ROOT / "tests/fixtures/opcodes.s")], check=True)
    subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)
    print("PASS Python/shell syntax, Actions schema/expressions, HuC6280 syntax, tool detection, whitespace")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, SyntaxError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"check: {error}") from error
