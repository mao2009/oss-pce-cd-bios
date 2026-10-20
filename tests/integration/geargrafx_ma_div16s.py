"""Run the original MA_DIV16S routine on the real Geargrafx core (Issue #76).

The guest driver (tests/fixtures/ma_div16s_driver.s) calls ma_div16s for 256 entries per batch; the host
writes the inputs into work RAM and reads the results between frames. Results are judged by the host
model of the project contract (tools/ma_div16s_model.py), not by any reference-BIOS ABI.
Cases: the 16x16 edge grid (includes divide-by-zero and -32768/-1) plus 4096 seeded random pairs.
"""
import argparse
import ctypes as C
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from environment import DEPENDENCIES, core_path, executable  # noqa: E402
from geargrafx_smoke import GameInfo, SystemInfo  # noqa: E402
from ma_div16s_model import check_case  # noqa: E402

EDGE = (-32768, -32767, -256, -128, -7, -2, -1, 0, 1, 2, 7, 128, 255, 16384, 32766, 32767)
RANDOM_BATCHES = 16
# CPU addresses of the driver tables; RAM offset = CPU address - $2000 (bank $F8 at MPR1).
TABLES = {"a_lo": 0x2200, "a_hi": 0x2300, "b_lo": 0x2400, "b_hi": 0x2500,
          "q_lo": 0x2600, "q_hi": 0x2700, "r_lo": 0x2800, "r_hi": 0x2900, "p": 0x2A00}
REQUEST = 0  # request id byte at $2000
DONE = 1  # done id byte at $2001


def cases():
    """Edge grid first (one batch), then seeded random pairs in whole batches of 256."""
    grid = [(a & 0xFFFF, b & 0xFFFF) for a in EDGE for b in EDGE]
    rng = random.Random(76)
    rest = [(rng.randrange(65536), rng.randrange(65536)) for _ in range(256 * RANDOM_BATCHES)]
    return grid + rest


def build(output):
    """Assemble routine + driver with the pinned cc65 into an 8 KiB HuCARD test image (cfg shared with #74)."""
    ca65, ld65 = executable("ca65"), executable("ld65")
    for tool in (ca65, ld65):
        version = subprocess.run([tool, "--version"], capture_output=True, text=True, check=True)
        if DEPENDENCIES["cc65"]["revision"][:9] not in version.stdout + version.stderr:
            raise ValueError(f"{tool} does not report the pinned cc65 build ID")
    output.mkdir(parents=True, exist_ok=True)
    objects = []
    for source in (ROOT / "src/math/ma_div16s.s", ROOT / "tests/fixtures/ma_div16s_driver.s"):
        obj = output / (source.stem + ".o")
        subprocess.run([ca65, "--cpu", "huc6280", "-o", str(obj), str(source)], check=True)
        objects.append(str(obj))
    rom = output / "ma-div16s-test-not-bios.pce"
    subprocess.run([ld65, "-C", str(ROOT / "tests/fixtures/ma_mul8s.cfg"), "-o", str(rom),
                    "-Ln", str(output / "ma_div16s.lbl"), *objects], check=True)
    if rom.stat().st_size != 8192:
        raise ValueError("MA_DIV16S test ROM is not one 8 KiB bank")
    return rom


def execute(core_file, rom, batches):
    """Worker: run every batch on the real core; returns raw result tables (no judgement here)."""
    core = C.CDLL(str(core_file))
    with tempfile.TemporaryDirectory(prefix="pce-geargrafx-div16s-") as profile:
        profile_bytes = profile.encode()

        @C.CFUNCTYPE(C.c_bool, C.c_uint, C.c_void_p)
        def environment(command, data):
            if command in (9, 31):
                C.cast(data, C.POINTER(C.c_char_p))[0] = profile_bytes
                return True
            if command in (17, 52):
                C.cast(data, C.POINTER(C.c_uint8 if command == 17 else C.c_uint))[0] = 0
                return True
            return command in (10, 16, 18)

        keep = [environment,
                C.CFUNCTYPE(None, C.c_void_p, C.c_uint, C.c_uint, C.c_size_t)(lambda *_: None),
                C.CFUNCTYPE(None, C.c_int16, C.c_int16)(lambda *_: None),
                C.CFUNCTYPE(C.c_size_t, C.POINTER(C.c_int16), C.c_size_t)(lambda _, n: n),
                C.CFUNCTYPE(None)(lambda: None),
                C.CFUNCTYPE(C.c_int16, C.c_uint, C.c_uint, C.c_uint, C.c_uint)(lambda *_: 0)]
        for name, callback in zip(("environment", "video_refresh", "audio_sample",
                                   "audio_sample_batch", "input_poll", "input_state"), keep):
            setter = getattr(core, "retro_set_" + name)
            setter.argtypes, setter.restype = [type(callback)], None
            setter(callback)
        core.retro_get_system_info.argtypes = [C.POINTER(SystemInfo)]
        info = SystemInfo()
        core.retro_get_system_info(C.byref(info))
        core.retro_load_game.argtypes, core.retro_load_game.restype = [C.POINTER(GameInfo)], C.c_bool
        core.retro_get_memory_data.argtypes, core.retro_get_memory_data.restype = [C.c_uint], C.c_void_p
        core.retro_init()
        if not core.retro_load_game(C.byref(GameInfo(str(rom.resolve()).encode(), None, 0, None))):
            raise ValueError("Geargrafx rejected the MA_DIV16S test ROM")
        try:
            ram = core.retro_get_memory_data(2)
            C.memset(ram + DONE, 0xCC, 1)
            core.retro_run()
            if C.string_at(ram + DONE, 1) != b"\0":
                raise ValueError("driver did not initialise its request/done ids")
            results, max_frames = [], 0
            for batch_no, batch in enumerate(batches, start=1):
                for name in ("a_lo", "a_hi", "b_lo", "b_hi"):
                    column = bytes(
                        (a if name[0] == "a" else b) >> (0 if name.endswith("lo") else 8) & 0xFF
                        for a, b in batch)
                    C.memmove(ram + TABLES[name] - 0x2000, column, 256)
                request = batch_no & 0xFF or 1
                C.memset(ram + REQUEST, request, 1)
                for frames in range(1, 33):
                    core.retro_run()
                    if C.string_at(ram + DONE, 1)[0] == request:
                        break
                else:
                    raise ValueError(f"batch {batch_no} not completed within 32 frames")
                max_frames = max(max_frames, frames)
                results.append({name: C.string_at(ram + TABLES[name] - 0x2000, 256).hex()
                                for name in ("q_lo", "q_hi", "r_lo", "r_hi", "p")})
            return {"core_name": info.name.decode(), "core_version": info.version.decode(),
                    "max_frames_per_batch": max_frames, "batches": results}
        finally:
            core.retro_unload_game()
            core.retro_deinit()


def run_worker(core, rom, batches):
    payload = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(batches, payload)
    payload.close()
    try:
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker",
                                 "--core", str(core), "--rom", str(rom), "--cases", payload.name],
                                capture_output=True, text=True, timeout=600)
    finally:
        Path(payload.name).unlink(missing_ok=True)
    if result.returncode:
        raise ValueError(f"Geargrafx worker failed ({result.returncode}): {result.stderr[-3000:]}")
    return json.loads(result.stdout)


def judge(batches, observed):
    """Mismatch strings over every case; each batch row i pairs with case i of that batch."""
    errors = []
    for batch, rows in zip(batches, observed["batches"]):
        for i, (a, b) in enumerate(batch):
            got = (int(rows["q_lo"][2 * i:2 * i + 2], 16) | int(rows["q_hi"][2 * i:2 * i + 2], 16) << 8,
                   int(rows["r_lo"][2 * i:2 * i + 2], 16) | int(rows["r_hi"][2 * i:2 * i + 2], 16) << 8,)
            p = int(rows["p"][2 * i:2 * i + 2], 16)
            flags = (p & 1, p >> 6 & 1, p >> 7 & 1, p >> 1 & 1)  # C, V, N, Z
            for message in check_case(a, b, got + flags):
                errors.append(f"a={a:04x} b={b:04x} {message}")
    return errors


def run(output):
    """Build, execute on the real core, judge all cases, and return the evidence dict (PASS or raise)."""
    core = core_path()
    rom = build(output)
    cases_ = cases()
    batches = [cases_[i:i + 256] for i in range(0, len(cases_), 256)]
    observed = run_worker(core, rom, batches)
    if observed["core_version"] != DEPENDENCIES["geargrafx"]["revision"]:
        raise ValueError("Geargrafx core does not report the pinned revision")
    errors = judge(batches, observed)
    if errors:
        raise ValueError(f"{len(errors)} MA_DIV16S mismatches on real core, first: {errors[:5]}")
    data = rom.read_bytes()
    return {"status": "PASS", "scope": "original MA_DIV16S routine on real Geargrafx core; project "
            "contract only, reference BIOS ABI UNVERIFIED", "cases_checked": len(cases_),
            "edge_grid_cases": 256, "mismatches": 0,
            "divide_by_zero_cases": sum(1 for a, b in cases_ if b == 0),
            "overflow_cases": sum(1 for a, b in cases_ if (a, b) == (0x8000, 0xFFFF)),
            "rom_sha256": hashlib.sha256(data).hexdigest(),
            "core_sha256": hashlib.sha256(core.read_bytes()).hexdigest(),
            "geargrafx_revision": observed["core_version"],
            "cc65_revision": DEPENDENCIES["cc65"]["revision"],
            "max_frames_per_batch": observed["max_frames_per_batch"],
            "jump_table": "NOT_CONNECTED", "release_eligible": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "build/ma-div16s")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--core", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--rom", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--cases", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(execute(args.core, args.rom, json.loads(args.cases.read_text()))))
        return
    evidence = run(args.output)
    (args.output / "evidence.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(f"PASS MA_DIV16S: {evidence['cases_checked']} cases on real Geargrafx; "
          f"evidence {args.output / 'evidence.json'}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"FAIL/BLOCKED MA_DIV16S: {error}", file=sys.stderr)
        raise SystemExit(1) from error
