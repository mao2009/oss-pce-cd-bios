"""Compare main HEAD to success-only artifacts; skip-only runs have no baseline."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

SCHEMA = "hucard-bootstrap-v1"


def valid_state(baseline):
    return (isinstance(baseline, dict) and baseline.get("schema") == SCHEMA
            and baseline.get("source_ref") == "main"
            and isinstance(baseline.get("source_sha"), str)
            and re.fullmatch(r"[0-9a-f]{40}", baseline["source_sha"]) is not None
            and isinstance(baseline.get("rom_sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", baseline["rom_sha256"]) is not None
            and baseline.get("status") == "smoke-fixture-not-bios")


def decide(current, baseline):
    if not re.fullmatch(r"[0-9a-f]{40}", current):
        raise ValueError("invalid current SHA")
    return not (valid_state(baseline) and baseline["source_sha"] == current)


def gh(*arguments):
    for attempt in range(3):
        try:
            return subprocess.run(["gh", *arguments], capture_output=True, text=True, check=True).stdout
        except subprocess.CalledProcessError as error:
            transient = re.search(r"HTTP (429|5\d\d)|timeout|timed out|connection reset", error.stderr or "", re.I)
            if arguments[0] != "api" or not transient or attempt == 2:
                raise
            time.sleep(attempt + 1)


def api(*arguments):
    try:
        return json.loads(gh("api", *arguments))
    except (subprocess.CalledProcessError, ValueError, OSError) as error:
        raise ValueError("GitHub API unavailable/invalid response; Nightly decision indeterminate") from error


def find_baseline(repository, current_run):
    # Paginate artifacts, not workflow runs: skipped successful runs have no marker.
    pages = api("--paginate", "--slurp",
                f"repos/{repository}/actions/artifacts?name=nightly-success-state&per_page=100")
    if (not isinstance(pages, list) or any(not isinstance(page, dict)
            or not isinstance(page.get("artifacts"), list) for page in pages)):
        raise ValueError("GitHub API invalid artifact inventory; Nightly decision indeterminate")
    artifacts = sorted((item for page in pages for item in page["artifacts"]
                        if not item["expired"]), key=lambda item: item["id"], reverse=True)
    seen_runs = set()
    for item in artifacts:
        run_id = item["workflow_run"]["id"]
        if str(run_id) == current_run or run_id in seen_runs:
            continue
        seen_runs.add(run_id)
        run = api(f"repos/{repository}/actions/runs/{run_id}")
        if not isinstance(run, dict) or not {"status", "conclusion", "path", "head_branch", "head_sha"} <= run.keys():
            raise ValueError("GitHub API invalid workflow metadata; Nightly decision indeterminate")
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
        if valid_state(baseline) and baseline["source_sha"] == run.get("head_sha"):
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
