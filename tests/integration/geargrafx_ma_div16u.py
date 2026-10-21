"""Issue #77: run src/math/ma_div16u.s on the real Geargrafx core (test-only HuCARD, not a BIOS).

The guest checks 20 edge pairs (divide-by-zero, max value, exact, non-exact) and 65,536 LFSR-generated
pairs in-guest (q*d + r == n, r < d, d == 0 -> q $FFFF / r == n), plus Y/P/divisor/stack preservation.
This script checks the guest's pass count and the edge quotient/remainder tables against Python
n // d and n % d (zero divisor: quotient $FFFF, remainder n, the project-internal choice), and requires
ROMs with the ldx #16 count, the bcs overflow branch, sec before the subtract, and ora #1 mutated to
report FAIL. Project-internal interface only: the reference MA_DIV16U ABI is UNVERIFIED and the
jump table is untouched.

Not part of `make test`/`make integration`. Run directly from a clean clone on Linux with TOOLS_DIR set:
    TOOLS_DIR=<tools dir> python3 tests/integration/geargrafx_ma_div16u.py
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

# (n, d) in the same order as the harness edge tables
EDGES = [(0x0000, 0x0001), (0x0001, 0x0001), (0x0000, 0x0000), (0x0001, 0x0000), (0xFFFF, 0x0000),
         (0xFFFF, 0x0001), (0xFFFF, 0xFFFF), (0xFFFE, 0xFFFF), (0xFFFF, 0xFFFE), (0x8000, 0x0002),
         (0x7FFF, 0x0002), (0x1000, 0x0003), (0x1234, 0x0100), (0xFFFF, 0x0100), (0x0100, 0xFFFF),
         (0x00FF, 0x0100), (0xFFFF, 0x8001), (0x8000, 0x8000), (0xFF01, 0xFFFF), (0xABCD, 0x1234)]
RANDOM_PAIRS = 65536


def reference(n, d):
    """Project-internal reference: zero divisor -> (0xFFFF, n); otherwise Python floor division."""
    return (0xFFFF, n) if d == 0 else (n // d, n % d)


def build(output):
    ca65, ld65 = executable("ca65"), executable("ld65")
    objects = []
    for source in (ROOT / "tests/fixtures/ma_div16u_harness.s", ROOT / "src/math/ma_div16u.s"):
        objects.append(str(output / (source.stem + ".o")))
        subprocess.run([ca65, "--cpu", "huc6280", "-o", objects[-1], str(source)], check=True)
    rom, labels = output / "ma-div16u-test-not-bios.pce", output / "ma_div16u.lbl"
    subprocess.run([ld65, "-C", str(ROOT / "tests/fixtures/ma_div16u.cfg"), "-o", str(rom),
                    "-Ln", str(labels), *objects], check=True)
    entry = int(re.search(r"^al ([0-9A-F]{6}) \.ma_div16u$", labels.read_text(), re.M)[1], 16)
    data = rom.read_bytes()
    base = entry - 0xE000
    if len(data) != 8192:
        raise ValueError("ma_div16u image size mismatch")
    # byte offsets inside the routine (php cld stz stz ldx#16 | bit: asl rol rol rol bcs lda cmp lda sbc bcc |
    # sub: sec lda sbc sta lda sbc sta lda ora sta | next: dex bne plp rts)
    expect = {0: 0x08, 1: 0xD8, 2: 0x64, 4: 0x64, 6: 0xA2, 7: 0x10, 16: 0xB0, 28: 0x38, 43: 0x09, 44: 0x01}
    # offsets 2/4 are STZ zp (0x64) and 16 is BCS (0xB0); any layout change fails before the mutants run
    if any(data[base + o] != v for o, v in expect.items()):
        raise ValueError("ma_div16u opcode layout does not match the expected routine")
    # (offset, mutant byte): ldx #16 -> #15, bcs -> bcc (overflow path), sec -> clc, ora #1 -> ora #0
    mutations = [(base + 7, 0x0F), (base + 16, 0x90), (base + 28, 0x18), (base + 44, 0x00)]
    return data, mutations


def execute(core_file, rom, max_frames=3000):
    """Run frames until the guest writes DONE/FAIL; return the $2200 result block."""
    core = C.CDLL(str(core_file))
    with tempfile.TemporaryDirectory(prefix="pce-ma-div16u-") as profile:
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
            C.memset(ram + 0x200, 0xCC, 0x100)
            for frame in range(1, max_frames + 1):
                core.retro_run()
                block = C.string_at(ram + 0x200, 0x100)
                if block[:4] in (b"DONE", b"FAIL"):
                    break
            return {"core_version": info.version.decode(), "frames": frame, "block": block.hex()}
        finally:
            if loaded:
                core.retro_unload_game()
            core.retro_deinit()


def run_worker(core, rom):
    result = subprocess.run([sys.executable, __file__, "--worker", str(core), str(rom)],
                            capture_output=True, text=True, timeout=600)
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
    with tempfile.TemporaryDirectory(prefix="pce-ma-div16u-build-") as tmp:
        data, mutations = build(Path(tmp))
        rom = Path(tmp) / "ma-div16u-test-not-bios.pce"
        rom.write_bytes(data)
        run, block = run_worker(core, rom)
        if block[:4] != b"DONE":
            raise ValueError(f"guest did not pass: {run}; block={block[:16].hex()}")
        pairs = int.from_bytes(block[4:7], "little")
        if pairs != RANDOM_PAIRS:
            raise ValueError(f"guest pair count {pairs} != {RANDOM_PAIRS}")
        got = []
        for i in range(len(EDGES)):
            q = block[0x40 + i] | block[0x60 + i] << 8
            r = block[0x80 + i] | block[0xA0 + i] << 8
            got.append((q, r))
        expected = [reference(n, d) for n, d in EDGES]
        if got != expected:
            bad = [(EDGES[i], got[i], expected[i]) for i in range(len(EDGES)) if got[i] != expected[i]]
            raise ValueError(f"edge results differ from reference: {bad}")
        for offset, bad_byte in mutations:
            mutated = Path(tmp) / "mutant.pce"
            mutated.write_bytes(data[:offset] + bytes([bad_byte]) + data[offset + 1:])
            _, negative = run_worker(core, mutated)
            if negative[:4] != b"FAIL":
                raise ValueError(f"mutant at +{offset:#x} was not rejected by the guest")
    evidence = {"scope": "Issue #77 project-internal ma_div16u only; reference MA_DIV16U ABI UNVERIFIED",
                "status": "PASS", "rom_sha256": hashlib.sha256(data).hexdigest(),
                "geargrafx_revision": run["core_version"], "frames": run["frames"],
                "random_pairs_checked": pairs, "edge_cases_checked": len(EDGES),
                "divide_by_zero_policy": "project-internal: Q=$FFFF, R=dividend (not a reference contract)",
                "mutations_rejected": len(mutations), "jump_table": "NOT_CONNECTED", "release_eligible": False}
    if args.output:
        args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(json.dumps(evidence, sort_keys=True))
    print(f"PASS real Geargrafx ma_div16u: {len(EDGES)} edge cases + {pairs} pairs, {len(mutations)} mutants rejected")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        raise SystemExit(f"ma_div16u integration FAIL/BLOCKED: {error}") from error
