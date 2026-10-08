"""Compare main HEAD to success-only artifacts; skip-only runs have no baseline."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

SCHEMA = "hucard-bootstrap-v1"


def decide(current, baseline):
    if not re.fullmatch(r"[0-9a-f]{40}", current):
        raise ValueError("invalid current SHA")
    return not (baseline and baseline.get("schema") == SCHEMA
                and baseline.get("source_ref") == "main"
                and baseline.get("source_sha") == current)


def gh(*arguments):
    return subprocess.run(["gh", *arguments], capture_output=True, text=True, check=True).stdout


def find_baseline(repository, current_run):
    # Paginate artifacts, not workflow runs: skipped successful runs have no marker.
    pages = json.loads(gh("api", "--paginate", "--slurp",
                         f"repos/{repository}/actions/artifacts?name=nightly-success-state&per_page=100"))
    artifacts = sorted((item for page in pages for item in page["artifacts"]
                        if not item["expired"]), key=lambda item: item["id"], reverse=True)
    for item in artifacts:
        run_id = item["workflow_run"]["id"]
        if str(run_id) == current_run:
            continue
        run = json.loads(gh("api", f"repos/{repository}/actions/runs/{run_id}"))
        # A marker uploaded before a later job failure must never count as success.
        if (run["status"] != "completed" or run["conclusion"] != "success"
                or run["path"] != ".github/workflows/nightly.yml"
                or run["head_branch"] != "main"):
            continue
        with tempfile.TemporaryDirectory(prefix="pce-nightly-state-") as directory:
            try:
                gh("run", "download", str(run_id), "--repo", repository,
                   "--name", "nightly-success-state", "--dir", directory)
                baseline = json.loads((Path(directory) / "nightly-state.json").read_text())
            except (subprocess.CalledProcessError, OSError, json.JSONDecodeError):
                continue  # expired/racing/legacy state -> conservatively rebuild
        if isinstance(baseline, dict) and baseline.get("schema") == SCHEMA:
            return baseline
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "record"])
    args = parser.parse_args()
    current = os.environ["SOURCE_SHA"]
    if args.command == "plan":
        baseline = find_baseline(os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_RUN_ID"])
        build = decide(current, baseline)
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write(f"build={str(build).lower()}\nsource_sha={current}\n")
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write(f"Main: `{current}`\n\nBuild: `{build}`. Baseline: "
                          f"`{baseline.get('source_sha') if baseline else 'none'}`\n")
    else:
        decide(current, None)  # validate SHA
        manifest = json.loads(Path("build/debug/manifest.json").read_text())
        if manifest["source_sha"] != current or manifest["working_tree_dirty"]:
            raise ValueError("artifact source does not match clean selected main commit")
        state = {"schema": SCHEMA, "source_ref": "main", "source_sha": current,
                 "rom_sha256": manifest["rom_sha256"], "status": "smoke-fixture-not-bios"}
        Path("build/nightly-state.json").write_text(json.dumps(state, indent=2) + "\n")


if __name__ == "__main__":
    try:
        main()
    except (KeyError, ValueError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"nightly: {error}") from error
