"""A/B comparison: CD read-ahead ON vs OFF on the pinned Geargrafx core (exp/cd-readahead-compare, diagnostic).

Variant A (ON)  : core + probe compiled with -DGG_ENABLE_CDROM_CUEBIN_READAHEAD (same flag set as the tools/setup.py debugger build, define removed for B).
Variant B (OFF): the same pinned source and flags with that one define removed.
Both load the same synthetic CUE/BIN through `cdrom://` (the only path that selects streaming load options),
with the same guest ROM, the same seed (CDRA_SEED pins the core RNG via a time() override), the same step budget.
Guest-visible records (HIT/PHASE/RUN) are compared record by record; VFS reads are logged by cdra_vfs_shim.cpp,
with thread "worker" = read-ahead prefetch and "main" = demand.

Outputs go to a mktemp directory and never into the repository. No ROM/BIOS bytes are printed; only counts,
CD diagnostic hit names and first-divergence positions. This is NOT a boot test: the guest is the CD read
diagnostic ROM, and its HIT names are not boot milestones. Run under WSL only (cc65/g++/make, POSIX paths).
"""
import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(HERE))
from build_cdread import SECTOR_BYTES, with_request  # noqa: E402
from cdread_verify import check_success  # noqa: E402
from environment import DEPENDENCIES, dependency_dir  # noqa: E402
from make_cd_image import CUE_NAME, build_image, sector_payload  # noqa: E402
from geargrafx_cdread import DEFAULT_REQUEST, STEP_BUDGET  # noqa: E402

DEFINE = "-DGG_ENABLE_CDROM_CUEBIN_READAHEAD"
REPS = 5
# (5,1): inside the first 64 sectors, which the load-time pass caches in both variants (hit control).
# (100,3) and (200,3): beyond the cache window; ON must demand-read and prefetch, OFF reads its own chunk layout.
SCENARIOS = (DEFAULT_REQUEST, (100, 3), (200, 3))
PHASE_DATA_IN = 4
# Larger than the streaming cache (64 one-sector chunks): the load-time pass caches only the first 64 sectors.
DISC_SECTORS = 256


def _setup_module():
    spec = importlib.util.spec_from_file_location("oss_pce_setup", ROOT / "tools" / "setup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(cmd, **kwargs):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kwargs)


def extract_pinned_core(destination: Path) -> Path:
    """Verify the cached pinned archive by SHA-256, then extract it into a fresh directory."""
    rev = DEPENDENCIES["geargrafx"]["revision"]
    archive = dependency_dir("geargrafx").parent / f"geargrafx-{rev}.tar.gz"
    if not archive.is_file():
        raise ValueError(f"BLOCKED: pinned Geargrafx archive missing at {archive}; run make setup")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != DEPENDENCIES["geargrafx"]["sha256"]:
        raise ValueError("BLOCKED: pinned Geargrafx archive checksum mismatch")
    tree = destination / "geargrafx"
    tree.mkdir()
    with tarfile.open(archive, "r:gz") as tar:
        members = [m for m in tar.getmembers() if m.isfile() or m.isdir()]
        for member in members:
            parts = Path(member.name).parts
            if member.name.startswith("/") or ".." in parts:
                raise ValueError("unsafe archive member")
        strip = [m for m in members if m.name.count("/") > 0]
        for member in strip:
            member.name = "/".join(Path(member.name).parts[1:])
        tar.extractall(tree, members=[m for m in strip if m.name])
    return tree


def build_variant(tree: Path, readahead: bool, setup_module) -> Path:
    platform = tree / "platforms" / "libretro"
    flags = setup_module.debugger_overrides(platform)
    if readahead:
        if not all(DEFINE in flag for flag in flags):
            raise ValueError("define missing from standard flag set")
    else:
        flags = [flag.replace(" " + DEFINE, "") for flag in flags]
        if any(DEFINE in flag for flag in flags):
            raise ValueError("define still present in OFF flag set")
    rev = DEPENDENCIES["geargrafx"]["revision"]
    _run(["make", "-C", str(platform), f"-j{os.cpu_count() or 2}", f"GIT_VERSION={rev}", *flags])
    return platform


def compile_probe(tree: Path, platform: Path, readahead: bool, output: Path) -> Path:
    make_dir = platform
    objects = _run(["make", "-s", "--no-print-directory", "-C", str(make_dir),
                    "--eval", "probe-objects:;@echo $(OBJECTS)", "probe-objects"]).stdout.split()
    objects = [(make_dir / p).resolve() for p in objects]
    if not objects or not all(p.is_file() for p in objects):
        raise ValueError("BLOCKED: core objects missing after build")
    define = [DEFINE] if readahead else []
    target = output / ("probe-on" if readahead else "probe-off")
    _run(["g++", "-std=c++11", "-O0", "-fno-rtti", "-D__LIBRETRO__", "-DGG_DISABLE_VGMRECORDER", "-DCDRA_VFS_MARK", *define,
          "-I" + str(tree / "src"), "-I" + str(make_dir),
          "-I" + str(tree / "platforms/shared/dependencies/miniz"),
          "-I" + str(tree / "platforms/shared/dependencies/json"),
          str(HERE / "geargrafx_cdread_probe.cpp"), str(HERE / "cdra_vfs_shim.cpp"),
          *map(str, objects), "-pthread", "-lm", "-o", str(target)])
    return target


def run_probe(probe: Path, rom: Path, cue_uri: str, labels: Path, seed: int, vfs_log: Path):
    env = dict(os.environ, CDRA_SEED=str(seed), CDRA_VFS_LOG=str(vfs_log))
    result = subprocess.run([str(probe), str(rom), cue_uri, str(labels), str(STEP_BUDGET)],
                            env=env, capture_output=True, text=True, timeout=900)
    if result.returncode != 0:
        raise ValueError(f"probe exited {result.returncode}")
    records = {"CAP": [], "HIT": [], "PHASE": [], "RUN": [], "BLOCK": []}
    for line in result.stdout.splitlines():
        tag, _, body = line.partition(" ")
        if tag in records:
            records[tag].append(json.loads(body))
    if len(records["RUN"]) != 2 or len(records["CAP"]) != 1:
        raise ValueError("probe did not complete cold and warm runs")
    lines = vfs_log.read_text().splitlines() if vfs_log.exists() else []
    if "MARK {\"name\":\"guest_start\"}" not in lines:
        raise ValueError("VFS log lacks the guest_start mark")
    split = lines.index("MARK {\"name\":\"guest_start\"}")
    load = [json.loads(line[4:]) for line in lines[:split] if line.startswith("VFS ")]
    guest = [json.loads(line[4:]) for line in lines[split:] if line.startswith("VFS ")]
    return records, load, guest


def guest_view(records):
    """Guest-visible, timing-normalised sequence: no VFS data, no host timing."""
    seq = []
    for tag in ("HIT", "PHASE", "RUN"):
        for row in records[tag]:
            keep = {k: v for k, v in row.items() if k not in ("buffer_head_hex", "buffer_hex", "result_hex")}
            seq.append([tag, keep])
    return seq


def metrics(records, vfs, lba, count, disc, load_reads):
    phases = [(r["scsi_phase"], r["cd_irq_active"], r["cd_irq_enabled"], r["step"]) for r in records["PHASE"]]
    return {
        "read_requests_data_in": sum(1 for r in records["PHASE"] if r["scsi_phase"] == PHASE_DATA_IN),
        "sectors_requested": count * len(records["RUN"]),
        "sectors_delivered": sum(delivered_sectors(run, disc, lba, count) for run in records["RUN"]),
        "run_reasons": [r["reason"] for r in records["RUN"]],
        "halt_steps": [r["halted_after_step"] for r in records["RUN"]],
        "run_steps": [r["step"] for r in records["RUN"]],
        "phase_sequence": [p[:3] for p in phases],
        "cd_diagnostic_hits": sorted({r["name"] for r in records["HIT"]}),
        "mpr_at_run": [r["mpr"] for r in records["RUN"]],
        "mpr_first_checkpoint": records["HIT"][0]["mpr"] if records["HIT"] else None,
        "vfs_load_reads_before_guest": load_reads,
        "vfs_demand_reads": sum(1 for e in vfs if e["thread"] == "main"),
        "vfs_prefetch_reads": sum(1 for e in vfs if e["thread"] == "worker"),
        "vfs_demand_bytes": sum(e["bytes"] for e in vfs if e["thread"] == "main"),
        "vfs_prefetch_bytes": sum(e["bytes"] for e in vfs if e["thread"] == "worker"),
    }


def delivered_sectors(run, disc, lba, count):
    """Sectors whose bytes in guest RAM equal the expected disc sector (measured, not assumed)."""
    buf = bytes.fromhex(run["buffer_hex"])
    return sum(1 for i in range(count) if buf[i * SECTOR_BYTES:(i + 1) * SECTOR_BYTES] == disc[lba + i])


def first_divergence(a, b):
    for index, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return {"index": index, "tag": x[0], "fields_differ": sorted(k for k in x[1] if x[1].get(k) != y[1].get(k))}
    if len(a) != len(b):
        return {"index": min(len(a), len(b)), "tag": "LENGTH", "fields_differ": []}
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("debug", "release"), default="debug")
    parser.add_argument("--work", help="empty or new directory for outputs (default: mktemp -d)")
    args = parser.parse_args()
    if sys.platform.startswith("win"):
        raise ValueError("BLOCKED: run inside WSL; cc65/g++/make paths are POSIX")
    cdread = ROOT / "build" / args.mode / "cdread-probe"
    rom_path = cdread / "cdread-probe-not-bios.pce"
    if not rom_path.is_file():
        raise ValueError("BLOCKED: run `make dev-cdread MODE=%s` first" % args.mode)
    payload = rom_path.read_bytes()
    manifest = json.loads((cdread / "manifest.json").read_text())
    symbols, labels = manifest["symbols"], cdread / "trace-labels.txt"
    disc = [sector_payload(i) for i in range(DISC_SECTORS)]
    work = Path(args.work) if args.work else Path(tempfile.mkdtemp(prefix="cdra-compare-"))
    if args.work and work.exists() and any(work.iterdir()):
        raise ValueError("--work must be a new or empty directory")
    work.mkdir(parents=True, exist_ok=True)
    setup_module = _setup_module()

    build_dir = Path(tempfile.mkdtemp(prefix="cdra-build-", dir=work))
    probes = {}
    for name, readahead in (("on", True), ("off", False)):
        vtree = build_dir / name
        vtree.mkdir()
        variant_tree = extract_pinned_core(vtree)  # each variant: its own pristine extraction and objects
        platform = build_variant(variant_tree, readahead, setup_module)
        probes[name] = compile_probe(variant_tree, platform, readahead, build_dir)

    disc_dir = work / "disc"
    build_image(disc_dir, DISC_SECTORS)
    cue_uri = "cdrom://" + str((disc_dir / CUE_NAME).resolve())

    results = {"on": {}, "off": {}}
    divergences = {}
    for lba, count in SCENARIOS:
        rom = payload if (lba, count) == DEFAULT_REQUEST else with_request(payload, symbols, lba, count)
        key = f"lba{lba}x{count}"
        for rep in range(REPS):
            for name in ("on", "off"):  # interleave variants per repetition
                log = work / f"vfs-{name}-{key}-{rep}.jsonl"
                log.write_text("")
                rom_file = work / f"rom-{name}-{key}-{rep}.pce"
                rom_file.write_bytes(rom)
                records, load, vfs = run_probe(probes[name], rom_file, cue_uri, labels, 1, log)
                check_success(records, symbols, disc, lba, count)
                results[name].setdefault(key, []).append({
                    "guest": guest_view(records), "metrics": metrics(records, vfs, lba, count, disc, len(load)),
                })
        a, b = results["on"][key], results["off"][key]
        divergences[key] = first_divergence(a[0]["guest"], b[0]["guest"])
        for name, runs in (("on", a), ("off", b)):
            if any(r != runs[0] for r in runs):
                raise ValueError(f"FAIL: {name} {key} is not reproducible across {REPS} repetitions")

    on_prefetch = sum(r["metrics"]["vfs_prefetch_reads"] for v in results["on"].values() for r in v)
    off_prefetch = sum(r["metrics"]["vfs_prefetch_reads"] for v in results["off"].values() for r in v)
    if on_prefetch == 0:
        raise ValueError("FAIL: read-ahead ON never prefetched; the experiment did not exercise read-ahead")
    if off_prefetch != 0:
        raise ValueError("FAIL: read-ahead OFF performed worker-thread reads")
    summary = {"reps": REPS, "scenarios": [f"lba{l}x{c}" for l, c in SCENARIOS], "divergence": divergences,
               "results": {n: {k: [r["metrics"] for r in v] for k, v in results[n].items()} for n in results}}
    (work / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    for key in summary["scenarios"]:
        print(f"{key}: first_guest_divergence={divergences[key]} ON_prefetch={[r['metrics']['vfs_prefetch_reads'] for r in results['on'][key]]} "
              f"OFF_prefetch={[r['metrics']['vfs_prefetch_reads'] for r in results['off'][key]]}")
    print(f"COMPLETE (diagnostic, not boot): reproducible in all repetitions; summary: {work / 'summary.json'}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise SystemExit(f"cdra compare FAIL/BLOCKED: {error}") from error
