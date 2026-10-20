"""Issue #75: run src/math/ma_mul16u.s on the real Geargrafx core (test-only HuCARD, not a BIOS).

The harness runs 64 operand pairs (20 named edge cases incl. 0, 1, max and carry chains, 16 single-bit
multipliers of 0xFFFF, 28 pairs from a fixed LCG seed) with D=1 on entry. The host regenerates the
pairs, requires every 32-bit product to equal Python a*b, requires D/I to be preserved, and requires
ROMs with a broken ldx count, a removed cld and a removed clc in the high-word add to be rejected.
Project-internal interface only: the reference MA_MUL16U ABI is UNVERIFIED and the jump table is untouched.

Not part of `make test`/`make integration` (Makefile is a shared file). Run directly from a clean clone on
Linux with TOOLS_DIR set and the pinned Geargrafx core built:
    TOOLS_DIR=<tools dir> python3 tests/integration/geargrafx_ma_mul16u.py [--output evidence.json]
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

EDGES = [(0, 0), (0, 0xFFFF), (0xFFFF, 0), (1, 1), (1, 0xFFFF), (0xFFFF, 1), (0xFFFF, 0xFFFF),
         (0x0100, 0x0100), (0x00FF, 0x00FF), (0x8000, 0x8000), (0x8000, 2), (0x0001, 0x0100),
         (0x0100, 0x0001), (0x1234, 0x5678), (0xFFFF, 0x8000), (0x0101, 0x0101), (0xAAAA, 0x5555),
         (0x0FFF, 0x0FFF), (0x7FFF, 0x0002), (0x00FF, 0xFF00)]
CASES = EDGES + [(0xFFFF, 1 << k) for k in range(16)]
seed = 0x75
while len(CASES) < 64:  # fixed-seed LCG: reproducible synthetic operands
    seed = (seed * 1103515245 + 12345) & 0xFFFFFFFF
    a = (seed >> 16) & 0xFFFF
    seed = (seed * 1103515245 + 12345) & 0xFFFFFFFF
    CASES.append((a, (seed >> 16) & 0xFFFF))
assert len(CASES) == 64


def cases_include():
    rows = []
    for a, b in CASES:  # per case: multiplicand lo/hi, multiplier lo/hi
        rows.append(f"    .byte ${a & 0xFF:02X}, ${a >> 8:02X}, ${b & 0xFF:02X}, ${b >> 8:02X}")
    return "case_tab:\n" + "\n".join(rows) + "\n"


def build(output):
    ca65, ld65 = executable("ca65"), executable("ld65")
    (output / "ma_mul16u_cases.inc").write_text(cases_include())
    objects = []
    for source in (ROOT / "tests/fixtures/ma_mul16u_harness.s", ROOT / "src/math/ma_mul16u.s"):
        objects.append(str(output / (source.stem + ".o")))
        subprocess.run([ca65, "--cpu", "huc6280", "-I", str(output), "-o", objects[-1], str(source)], check=True)
    rom, labels = output / "ma-mul16u-test-not-bios.pce", output / "ma_mul16u.lbl"
    subprocess.run([ld65, "-C", str(ROOT / "tests/fixtures/ma_mul16u.cfg"), "-o", str(rom),
                    "-Ln", str(labels), *objects], check=True)
    entry = int(re.search(r"^al ([0-9A-F]{6}) \.ma_mul16u$", labels.read_text(), re.M)[1], 16)
    data = rom.read_bytes()
    if len(data) != 8192:
        raise ValueError("ma_mul16u image size mismatch")
    start = entry - 0xE000
    # routine layout: php(0) cld(1) ldx #16(2-3) ... ; the high-word add is the first clc+adc zp (18 65)
    if data[start:start + 4] != b"\x08\xd8\xa2\x10":
        raise ValueError("ma_mul16u prologue differs from php/cld/ldx #16")
    clc = data.find(b"\x18\x65", start, start + 0x40)
    if clc < 0:
        raise ValueError("ma_mul16u high-word clc/adc not found")
    # (offset, expected byte, mutant byte): ldx #16->#15, cld->nop, clc->nop
    mutations = [(start + 3, 0x10, 0x0F), (start + 1, 0xD8, 0xEA), (clc, 0x18, 0xEA)]
    if any(data[o] != good for o, good, _ in mutations):
        raise ValueError("ma_mul16u mutation offsets do not match the image")
    return data, mutations


def execute(core_file, rom, max_frames=1200):
    """Run frames until the guest writes DONE; return the $2200 result block (0x200 bytes)."""
    core = C.CDLL(str(core_file))
    with tempfile.TemporaryDirectory(prefix="pce-ma-mul16u-") as profile:
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
            C.memset(ram + 0x200, 0xCC, 0x200)
            for frame in range(1, max_frames + 1):
                core.retro_run()
                block = C.string_at(ram + 0x200, 0x200)
                if block[:4] in (b"DONE", b"FAIL"):
                    break
            return {"core_version": info.version.decode(), "frames": frame, "block": block.hex()}
        finally:
            if loaded:
                core.retro_unload_game()
            core.retro_deinit()


def run_worker(core, rom):
    result = subprocess.run([sys.executable, __file__, "--worker", str(core), str(rom)],
                            capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise ValueError(f"Geargrafx worker failed: {result.stderr[-2000:]}")
    run = json.loads(result.stdout)
    if run["core_version"] != DEPENDENCIES["geargrafx"]["revision"]:
        raise ValueError("Geargrafx core is not the pinned revision")
    return run, bytes.fromhex(run["block"])


def check_block(block):
    """Return the list of mismatches between the guest block and the host a*b model."""
    problems = []
    if block[:4] != b"DONE":
        return [f"guest did not finish: {block[:4]!r}"]
    p = block[4]
    if p & 0x0C != 0x0C:
        problems.append(f"D or I not preserved: P={p:#04x} after calls")
    for index, (a, b) in enumerate(CASES):
        got = int.from_bytes(block[0x100 + 4 * index:0x104 + 4 * index], "little")
        if got != a * b:
            problems.append(f"{a:#06x}*{b:#06x}: guest {got:#010x} host {a * b:#010x}")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", nargs=2, type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(execute(*args.worker)))
        return
    core = core_path()
    with tempfile.TemporaryDirectory(prefix="pce-ma-mul16u-build-") as tmp:
        data, mutations = build(Path(tmp))
        rom = Path(tmp) / "ma-mul16u-test-not-bios.pce"
        run, block = run_worker(core, rom)
        problems = check_block(block)
        if problems:
            raise ValueError("real-core mismatches: " + "; ".join(problems[:8]))
        for offset, _, bad in mutations:
            mutated = Path(tmp) / "mutant.pce"
            mutated.write_bytes(data[:offset] + bytes([bad]) + data[offset + 1:])
            _, negative = run_worker(core, mutated)
            if not check_block(negative):
                raise ValueError(f"mutant at ROM offset {offset:#x} was not rejected")
    evidence = {"scope": "Issue #75 project-internal ma_mul16u only; reference MA_MUL16U ABI UNVERIFIED",
                "status": "PASS", "rom_sha256": hashlib.sha256(data).hexdigest(),
                "geargrafx_revision": run["core_version"], "frames": run["frames"], "cases_checked": len(CASES),
                "mutations_rejected": len(mutations), "jump_table": "NOT_CONNECTED", "release_eligible": False}
    if args.output:
        args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(json.dumps(evidence, sort_keys=True))
    print(f"PASS real Geargrafx ma_mul16u: {len(CASES)} cases, host a*b match, "
          f"{len(mutations)} mutants rejected")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        raise SystemExit(f"ma_mul16u integration FAIL/BLOCKED: {error}") from error
