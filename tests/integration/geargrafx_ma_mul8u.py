"""Issue #73: run src/math/ma_mul8u.s on the real Geargrafx core (test-only HuCARD, not a BIOS).

The guest checks all 65,536 pairs against a repeated-addition reference plus Y/C/I/D/SP
preservation; this script checks the guest's pair count and an edge table against Python
a*b, and requires a ROM with the loop count mutated to report FAIL. Project-internal
interface only: the reference MA_MUL8U ABI is UNVERIFIED and the jump table is untouched.
"""
import argparse
import ctypes as C
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from environment import DEPENDENCIES, core_path, executable  # noqa: E402
from geargrafx_smoke import GameInfo, SystemInfo  # noqa: E402

EDGES = [(0, 0), (0, 255), (255, 0), (1, 1), (1, 255), (255, 1), (255, 255),
         (2, 128), (128, 2), (16, 16), (15, 17), (170, 85)]  # same order as the harness table


def build(output):
    ca65, ld65 = executable("ca65"), executable("ld65")
    objects = []
    for source in (ROOT / "tests/fixtures/ma_mul8u_harness.s", ROOT / "src/math/ma_mul8u.s"):
        objects.append(str(output / (source.stem + ".o")))
        subprocess.run([ca65, "--cpu", "huc6280", "-o", objects[-1], str(source)], check=True)
    rom, labels = output / "ma-mul8u-test-not-bios.pce", output / "ma_mul8u.lbl"
    subprocess.run([ld65, "-C", str(ROOT / "tests/fixtures/ma_mul8u.cfg"), "-o", str(rom),
                    "-Ln", str(labels), *objects], check=True)
    entry = int(re.search(r"^al ([0-9A-F]{6}) \.ma_mul8u$", labels.read_text(), re.M)[1], 16)
    data = rom.read_bytes()
    loop = entry - 0xE000 + 8  # php cld sta stx lda# -> ldx #8
    if len(data) != 8192 or data[loop:loop + 2] != b"\xa2\x08":
        raise ValueError("ma_mul8u image size or ldx #8 placement mismatch")
    return data, loop + 1


def execute(core_file, rom, max_frames=1200):
    """Run frames until the guest writes DONE/FAIL; return the $2200 result block."""
    core = C.CDLL(str(core_file))
    with tempfile.TemporaryDirectory(prefix="pce-ma-mul8u-") as profile:
        profile_bytes = profile.encode()  # must outlive the core's use of the pointer

        @C.CFUNCTYPE(C.c_bool, C.c_uint, C.c_void_p)
        def environment(command, data):
            if command in (9, 31):  # system/save directory
                C.cast(data, C.POINTER(C.c_char_p))[0] = profile_bytes
            elif command in (17, 52):  # no variable update / legacy core options
                C.cast(data, C.POINTER(C.c_bool if command == 17 else C.c_uint))[0] = 0
            return command in (9, 31, 17, 52, 10, 16, 18)

        callbacks = {"environment": environment,
                     "video_refresh": C.CFUNCTYPE(None, C.c_void_p, C.c_uint, C.c_uint, C.c_size_t)(lambda *_: None),
                     "audio_sample": C.CFUNCTYPE(None, C.c_int16, C.c_int16)(lambda *_: None),
                     "audio_sample_batch": C.CFUNCTYPE(C.c_size_t, C.POINTER(C.c_int16), C.c_size_t)(lambda _, n: n),
                     "input_poll": C.CFUNCTYPE(None)(lambda: None),
                     "input_state": C.CFUNCTYPE(C.c_int16, C.c_uint, C.c_uint, C.c_uint, C.c_uint)(lambda *_: 0)}
        for name, callback in callbacks.items():
            getattr(core, "retro_set_" + name)(callback)
        info = SystemInfo()
        core.retro_get_system_info(C.byref(info))
        if info.name.decode() != "Geargrafx":
            raise ValueError("unexpected libretro core")
        core.retro_get_memory_data.restype = C.c_void_p
        core.retro_load_game.restype = C.c_bool
        core.retro_init()
        loaded = False
        try:
            loaded = core.retro_load_game(C.byref(GameInfo(str(rom.resolve()).encode(), None, 0, None)))
            ram = core.retro_get_memory_data(2)  # RETRO_MEMORY_SYSTEM_RAM, $2000 = +0
            if not loaded or not ram:
                raise ValueError("Geargrafx rejected the test image or exposed no work RAM")
            C.memset(ram + 0x200, 0xCC, 0x30)
            for frame in range(1, max_frames + 1):
                core.retro_run()
                block = C.string_at(ram + 0x200, 0x30)
                if block[:4] in (b"DONE", b"FAIL"):
                    break
            return {"core_version": info.version.decode(), "frames": frame, "block": block.hex()}
        finally:
            if loaded:
                core.retro_unload_game()
            core.retro_deinit()


def run_worker(core, rom):
    result = subprocess.run([sys.executable, __file__, "--worker", str(core), str(rom)],
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise ValueError(f"Geargrafx worker failed: {result.stderr[-2000:]}")
    run = json.loads(result.stdout)
    if run["core_version"] != DEPENDENCIES["geargrafx"]["revision"]:
        raise ValueError("Geargrafx core is not the pinned revision")
    return run, bytes.fromhex(run["block"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", nargs=2, type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(execute(*args.worker)))
        return
    core = core_path()
    with tempfile.TemporaryDirectory(prefix="pce-ma-mul8u-build-") as tmp:
        data, loop_operand = build(Path(tmp))
        rom = Path(tmp) / "ma-mul8u-test-not-bios.pce"
        run, block = run_worker(core, rom)
        pairs = int.from_bytes(block[4:7], "little")
        if block[:4] != b"DONE" or pairs != 65536:
            raise ValueError(f"guest did not pass all 65536 pairs: {run}")
        edges = [block[0x10 + i] | block[0x20 + i] << 8 for i in range(len(EDGES))]
        if edges != [a * b for a, b in EDGES]:
            raise ValueError(f"edge products differ from host a*b: {edges}")
        mutated = Path(tmp) / "ldx7.pce"
        mutated.write_bytes(data[:loop_operand] + b"\x07" + data[loop_operand + 1:])
        _, negative = run_worker(core, mutated)
        if negative[:4] != b"FAIL":
            raise ValueError("mutated ldx #7 image was not rejected by the guest")
    evidence = {"scope": "Issue #73 project-internal ma_mul8u only; reference MA_MUL8U ABI UNVERIFIED",
                "status": "PASS", "rom_sha256": hashlib.sha256(data).hexdigest(),
                "geargrafx_revision": run["core_version"], "frames": run["frames"], "pairs_checked": pairs,
                "edge_products": {f"{a}*{b}": p for (a, b), p in zip(EDGES, edges)},
                "mutation_ldx7": "PASS_EXPECTED_FAILURE", "mutation_fail_block": negative[:12].hex(),
                "jump_table": "NOT_CONNECTED", "release_eligible": False}
    if args.output:
        args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(json.dumps(evidence, sort_keys=True))
    print("PASS real Geargrafx ma_mul8u: 65536 pairs, edge table vs host a*b, ldx #7 mutation rejected")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        raise SystemExit(f"ma_mul8u integration FAIL/BLOCKED: {error}") from error
