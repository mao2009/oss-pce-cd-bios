"""Run the original MA_MUL8S routine on the real Geargrafx core for all 65,536 pairs (Issue #74).

The guest test driver (tests/fixtures/ma_mul8s_driver.s) calls ma_mul8s; this host side only
writes requests and reads RAM between frames. Results are compared with the host model of the
project's own signed contract (docs/ma-mul8s.md), not with any reference-BIOS ABI.
"""
import argparse
import ctypes as C
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from environment import DEPENDENCIES, core_path, executable  # noqa: E402
from geargrafx_smoke import GameInfo, SystemInfo  # noqa: E402

EDGES = (-128, -1, 0, 1, 127)
ROW = 0x200            # driver result block at CPU $2200 (RAM offset $200), 6 pages
SIGN_FIX = bytes.fromhex("38 fd 02 21")  # sec; sbc $2102,x  (a<0 correction), negative control


def signed(value):
    return value - 256 if value & 0x80 else value


def expected(a, b):
    """Project contract: X:A = two's-complement 16-bit product of signed a and b."""
    return (signed(a) * signed(b)) & 0xFFFF


def expected_row(a):
    """Bytes the driver must leave at $2200-$27FF for request a (P pages are checked separately)."""
    products = [expected(a, b) for b in range(256)]
    return (bytes(p & 0xFF for p in products) + bytes(p >> 8 for p in products)
            + bytes(b ^ 0x5A for b in range(256)) + b"\xfd" * 256)


def check_row(a, data):
    """Mismatch strings for one row: product, Y, S, and P preserved (with the C/D inputs varied)."""
    errors = []
    want = expected_row(a)
    for b in range(256):
        got = [data[page * 256 + b] for page in range(6)]
        exp = [want[page * 256 + b] for page in range(4)]
        if got[:4] != exp:
            errors.append(f"a={a:02x} b={b:02x} lo/hi/Y/S={bytes(got[:4]).hex()} expected {bytes(exp).hex()}")
        if got[4] != got[5] or (got[5] & 0x09) != ((b & 1) | (b & 2) << 2):  # C=bit0, D=bit1 of b
            errors.append(f"a={a:02x} b={b:02x} P before {got[5]:02x} after {got[4]:02x}")
    return errors


def build(output):
    """Assemble routine + driver with the pinned cc65 into an 8 KiB HuCARD test image."""
    ca65, ld65 = executable("ca65"), executable("ld65")
    for tool in (ca65, ld65):
        version = subprocess.run([tool, "--version"], capture_output=True, text=True, check=True)
        if DEPENDENCIES["cc65"]["revision"][:9] not in version.stdout + version.stderr:
            raise ValueError(f"{tool} does not report the pinned cc65 build ID")
    output.mkdir(parents=True, exist_ok=True)
    objects = []
    for source in (ROOT / "src/math/ma_mul8s.s", ROOT / "tests/fixtures/ma_mul8s_driver.s"):
        obj = output / (source.stem + ".o")
        subprocess.run([ca65, "--cpu", "huc6280", "-o", str(obj), str(source)], check=True)
        objects.append(str(obj))
    rom = output / "ma-mul8s-test-not-bios.pce"
    subprocess.run([ld65, "-C", str(ROOT / "tests/fixtures/ma_mul8s.cfg"), "-o", str(rom),
                    "-Ln", str(output / "ma_mul8s.lbl"), *objects], check=True)
    if rom.stat().st_size != 8192:
        raise ValueError("MA_MUL8S test ROM is not one 8 KiB bank")
    return rom


def execute(core_file, rom):
    """Worker: drive all 256 rows on the real core; returns raw RAM rows (no judgement here)."""
    core = C.CDLL(str(core_file))
    with tempfile.TemporaryDirectory(prefix="pce-geargrafx-mul8s-") as profile:
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
            raise ValueError("Geargrafx rejected the MA_MUL8S test ROM")
        try:
            ram = core.retro_get_memory_data(2)
            C.memset(ram + 1, 0xCC, 2)                 # init must overwrite both ids with 0
            core.retro_run()
            if C.string_at(ram + 1, 2) != b"\0\0":
                raise ValueError("driver did not initialise its request/done ids")
            rows, max_frames = [], 0
            for a in range(256):
                stale = bytes(x ^ 0xFF for x in expected_row(a)) + b"\x00" * 512
                C.memmove(ram + ROW, stale, len(stale))
                request = (a + 1) & 0xFF
                C.memset(ram, a, 1)
                C.memset(ram + 1, request, 1)
                for frames in range(1, 9):
                    core.retro_run()
                    if C.string_at(ram + 2, 1)[0] == request:
                        break
                else:
                    raise ValueError(f"row a={a:02x} not completed within 8 frames")
                max_frames = max(max_frames, frames)
                rows.append(C.string_at(ram + ROW, 6 * 256).hex())
            return {"core_name": info.name.decode(), "core_version": info.version.decode(),
                    "max_frames_per_row": max_frames, "rows": rows}
        finally:
            core.retro_unload_game()
            core.retro_deinit()


def run_worker(core, rom):
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker",
                             "--core", str(core), "--rom", str(rom)],
                            capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise ValueError(f"Geargrafx worker failed ({result.returncode}): {result.stderr[-3000:]}")
    return json.loads(result.stdout)


def judge(observation):
    errors = []
    for a, row in enumerate(observation["rows"]):
        errors += check_row(a, bytes.fromhex(row))
    return errors


def run(output):
    """Build, execute positive and negative ROMs, and return the evidence dict (PASS or raise)."""
    core = core_path()
    rom = build(output)
    observed = run_worker(core, rom)
    if observed["core_version"] != DEPENDENCIES["geargrafx"]["revision"]:
        raise ValueError("Geargrafx core does not report the pinned revision")
    errors = judge(observed)
    if errors:
        raise ValueError(f"{len(errors)} MA_MUL8S mismatches on real core, first: {errors[:5]}")
    data = rom.read_bytes()
    if data.count(SIGN_FIX) != 1:
        raise ValueError("negative control: sign-correction pattern not unique")
    negative = output / "ma-mul8s-no-sign-fix.pce"
    negative.write_bytes(data.replace(SIGN_FIX, b"\xea" * len(SIGN_FIX)))
    negative_errors = judge(run_worker(core, negative))
    if not negative_errors:
        raise ValueError("negative control ROM was not rejected")
    rows = [bytes.fromhex(row) for row in observed["rows"]]
    edges = {f"{a},{b}": f"{rows[a & 0xFF][b & 0xFF] | rows[a & 0xFF][256 + (b & 0xFF)] << 8:04x}"
             for a in EDGES for b in EDGES}
    return {"status": "PASS", "scope": "original MA_MUL8S routine on real Geargrafx core; project "
            "contract only, reference BIOS ABI UNVERIFIED", "pairs_checked": 65536, "mismatches": 0,
            "rom_sha256": hashlib.sha256(data).hexdigest(),
            "core_sha256": hashlib.sha256(core.read_bytes()).hexdigest(),
            "geargrafx_revision": observed["core_version"],
            "cc65_revision": DEPENDENCIES["cc65"]["revision"],
            "max_frames_per_row": observed["max_frames_per_row"], "edge_products_hex": edges,
            "negative_control": f"FAIL as expected ({len(negative_errors)} mismatches without a<0 fix)",
            "jump_table": "NOT_CONNECTED", "release_eligible": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "build/ma-mul8s")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--core", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--rom", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(execute(args.core, args.rom)))
        return
    evidence = run(args.output)
    (args.output / "evidence.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in evidence.items() if k != "edge_products_hex"}, sort_keys=True))
    print(f"PASS MA_MUL8S: 65536 pairs on real Geargrafx; evidence {args.output / 'evidence.json'}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"FAIL/BLOCKED MA_MUL8S: {error}", file=sys.stderr)
        raise SystemExit(1) from error
