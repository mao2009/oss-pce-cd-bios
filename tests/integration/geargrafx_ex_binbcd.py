"""Run the project-own EX_BINBCD probe on the real Geargrafx core (Issue #70, opt-in).

Evidence scope: this project's own routine only. No System Card code runs and nothing here
confirms the reference $3C/$E0B4 ABI.
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
from environment import DEPENDENCIES, core_path  # noqa: E402
from ex_binbcd import binbcd_packed, build_probe  # noqa: E402
from geargrafx_smoke import GameInfo, SystemInfo  # noqa: E402

FRAMES = 10


def execute(core_file, rom):
    """Load the ROM, seed result RAM with $CC, run FRAMES frames, return RAM $0200-$05FF."""
    core = C.CDLL(str(core_file))
    with tempfile.TemporaryDirectory(prefix="pce-ex-binbcd-") as profile:
        profile_bytes = profile.encode()

        @C.CFUNCTYPE(C.c_bool, C.c_uint, C.c_void_p)
        def environment(command, data):
            if command in (9, 31):
                C.cast(data, C.POINTER(C.c_char_p))[0] = profile_bytes
                return True
            if command in (52, 17):
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
            getattr(core, "retro_set_" + name)(callback)
        info = SystemInfo()
        core.retro_get_system_info(C.byref(info))
        core.retro_get_memory_data.restype = C.c_void_p
        core.retro_get_memory_size.restype = C.c_size_t
        core.retro_load_game.restype = C.c_bool
        core.retro_init()
        game = GameInfo(str(rom.resolve()).encode(), None, 0, None)
        if not core.retro_load_game(C.byref(game)):
            raise ValueError("Geargrafx rejected the probe ROM")
        try:
            pointer, size = core.retro_get_memory_data(2), core.retro_get_memory_size(2)
            if not pointer or size < 0x600:
                raise ValueError("Geargrafx did not expose working RAM")
            C.memset(pointer + 0x200, 0xCC, 0x400)
            for _ in range(FRAMES):
                core.retro_run()
            ram = C.string_at(pointer + 0x200, 0x400)
        finally:
            core.retro_unload_game()
            core.retro_deinit()
    return {"core_version": info.version.decode(), "ram_hex": ram.hex()}


def mismatches(ram):
    """Compare guest results with the host model; return a list of human-readable failures."""
    if ram[:4] != b"BCD!":
        return [f"completion marker missing: {ram[:4].hex()}"]
    bad = []
    for value in range(256):
        a, p, x = ram[0x100 + value], ram[0x200 + value], ram[0x300 + value]
        want_a, want_c = binbcd_packed(value)
        if (a, p & 1, p & 8, x) != (want_a, want_c, 0, 0xA5):
            bad.append(f"in={value} A={a:02x} P={p:02x} X={x:02x}; want A={want_a:02x} C={want_c} D=0 X=a5")
    return bad


def run_worker(core, rom):
    result = subprocess.run([sys.executable, __file__, "--worker", "--core", str(core), "--rom", str(rom)],
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError(f"Geargrafx worker failed ({result.returncode}): {result.stderr[-2000:]}")
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("debug", "release"), default="debug")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--core", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--rom", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(execute(args.core, args.rom)))
        return
    core = core_path()
    out = ROOT / "build" / args.mode / "ex-binbcd"
    rom, symbols = build_probe(out)
    observed = run_worker(core, rom)
    if observed["core_version"] != DEPENDENCIES["geargrafx"]["revision"]:
        raise ValueError("Geargrafx core does not report the pinned revision")
    bad = mismatches(bytes.fromhex(observed["ram_hex"]))
    if bad:
        raise ValueError("guest/model mismatch: " + "; ".join(bad[:8]))
    # Negative: ADC #$10 -> ADC #$20 must be caught by the same real-core comparison.
    data = bytearray(rom.read_bytes())
    offset = symbols["ex_binbcd_tens_step"] - 0xE000
    if data[offset:offset + 2] != b"\x69\x10":
        raise ValueError("mutation point is not ADC #$10")
    data[offset + 1] = 0x20
    negative = out / "mutated-tens-step.pce"
    negative.write_bytes(data)
    if not mismatches(bytes.fromhex(run_worker(core, negative)["ram_hex"])):
        raise ValueError("mutated routine passed on the real core")
    ram = bytes.fromhex(observed["ram_hex"])
    (out / "ex-binbcd-evidence.json").write_text(json.dumps({
        "scope": "project-own EX_BINBCD packed routine only; System Card $3C ABI UNCONFIRMED",
        "status": "PASS", "inputs": "0..255", "frames": FRAMES,
        "rom_sha256": hashlib.sha256(rom.read_bytes()).hexdigest(),
        "core_sha256": hashlib.sha256(core.read_bytes()).hexdigest(),
        "geargrafx_revision": observed["core_version"], "mutation_negative": "PASS_EXPECTED_FAILURE",
        "samples": {str(v): {"a": ram[0x100 + v], "p": ram[0x200 + v]} for v in (0, 9, 10, 99, 100, 255)},
    }, indent=2, sort_keys=True) + "\n")
    print("PASS real Geargrafx EX_BINBCD (project-own packed spec): inputs 0..255 match host model; mutation rejected")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit(f"EX_BINBCD integration FAIL/BLOCKED: {error}") from error
