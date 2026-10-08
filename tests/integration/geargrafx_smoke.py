"""Execute the official Geargrafx libretro core with a tiny headless frontend.

The frontend supplies callbacks; CPU/memory execution belongs to Geargrafx.
No emulator source is incorporated into the MIT ROM or this frontend.
"""
import argparse
import ctypes as C
import hashlib
import json
import re
from pathlib import Path
import subprocess
import sys
import struct
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from build import verify_fixture  # noqa: E402
from environment import DEPENDENCIES, core_path  # noqa: E402

EXPECTED = b"PCE!\xf8\x5a"


def missing_marker_rom(data, labels):
    """Locate the exported instruction and assert its opcode/operands before mutation."""
    symbols = {}
    for line in labels.splitlines():
        match = re.fullmatch(r"al ([0-9A-Fa-f]{6}) \.(\w+)", line)
        if match:
            address, name = int(match[1], 16), match[2]
            if name in symbols and symbols[name] != address:
                raise ValueError(f"conflicting fixture symbol: {name}")
            symbols[name] = address
    if not {"reset", "marker", "marker_transfer"} <= symbols.keys():
        raise ValueError("fixture transfer/marker/reset symbols missing")
    if symbols["reset"] != 0xe000 or len(data) != 8192:
        raise ValueError("fixture bank mapping mismatch")
    offset = symbols["marker_transfer"] - symbols["reset"]
    if not 0 <= offset <= len(data) - 7 or not 0xe000 <= symbols["marker"] <= 0xffff:
        raise ValueError("fixture transfer symbol outside mapped ROM")
    instruction = b"\x73" + struct.pack("<HHH", symbols["marker"], 0x2200, 4)
    if data[offset:offset + len(instruction)] != instruction:
        raise ValueError("symbol does not point to the expected TII marker,$2200,4 instruction")
    mutated = bytearray(data)
    mutated[offset:offset + len(instruction)] = b"\xea" * len(instruction)
    return mutated


class GameInfo(C.Structure):
    _fields_ = [("path", C.c_char_p), ("data", C.c_void_p),
                ("size", C.c_size_t), ("meta", C.c_char_p)]


class SystemInfo(C.Structure):
    _fields_ = [("name", C.c_char_p), ("version", C.c_char_p),
                ("extensions", C.c_char_p), ("need_fullpath", C.c_bool),
                ("block_extract", C.c_bool)]


def execute(core_file, rom):
    core = C.CDLL(str(core_file))
    with tempfile.TemporaryDirectory(prefix="pce-geargrafx-profile-") as profile:
        profile_bytes = profile.encode()
        env_type = C.CFUNCTYPE(C.c_bool, C.c_uint, C.c_void_p)

        @env_type
        def environment(command, data):
            if command in (9, 31):  # GET_SYSTEM_DIRECTORY / GET_SAVE_DIRECTORY
                C.cast(data, C.POINTER(C.c_char_p))[0] = profile_bytes
                return True
            if command == 52:  # GET_CORE_OPTIONS_VERSION: legacy variables
                C.cast(data, C.POINTER(C.c_uint))[0] = 0
                return True
            if command == 17:  # GET_VARIABLE_UPDATE: no option changes
                C.cast(data, C.POINTER(C.c_bool))[0] = False
                return True
            return command in (10, 16, 18)  # pixel format, variables, support-no-game

        video_type = C.CFUNCTYPE(None, C.c_void_p, C.c_uint, C.c_uint, C.c_size_t)
        audio_type = C.CFUNCTYPE(None, C.c_int16, C.c_int16)
        batch_type = C.CFUNCTYPE(C.c_size_t, C.POINTER(C.c_int16), C.c_size_t)
        poll_type = C.CFUNCTYPE(None)
        input_type = C.CFUNCTYPE(C.c_int16, C.c_uint, C.c_uint, C.c_uint, C.c_uint)
        callbacks = [("environment", env_type, environment),
                     ("video_refresh", video_type, video_type(lambda *_: None)),
                     ("audio_sample", audio_type, audio_type(lambda *_: None)),
                     ("audio_sample_batch", batch_type, batch_type(lambda _, count: count)),
                     ("input_poll", poll_type, poll_type(lambda: None)),
                     ("input_state", input_type, input_type(lambda *_: 0))]
        # Keep every CFUNCTYPE object alive until retro_deinit.
        for name, callback_type, callback in callbacks:
            setter = getattr(core, "retro_set_" + name)
            setter.argtypes = [callback_type]
            setter.restype = None
            setter(callback)
        core.retro_get_system_info.argtypes = [C.POINTER(SystemInfo)]
        info = SystemInfo()
        core.retro_get_system_info(C.byref(info))
        name, version = info.name.decode(), info.version.decode()
        if name != "Geargrafx":
            raise ValueError(f"unexpected core: {name}")
        core.retro_load_game.argtypes = [C.POINTER(GameInfo)]
        core.retro_load_game.restype = C.c_bool
        core.retro_get_memory_data.argtypes = [C.c_uint]
        core.retro_get_memory_data.restype = C.c_void_p
        core.retro_get_memory_size.argtypes = [C.c_uint]
        core.retro_get_memory_size.restype = C.c_size_t
        core.retro_init()
        loaded = False
        try:
            game = GameInfo(str(rom.resolve()).encode(), None, 0, None)
            loaded = core.retro_load_game(C.byref(game))
            if not loaded:
                raise ValueError("Geargrafx rejected the fixture")
            observations = []
            for reset in (False, True):
                if reset:
                    core.retro_reset()
                pointer = core.retro_get_memory_data(2)  # RETRO_MEMORY_SYSTEM_RAM
                size = core.retro_get_memory_size(2)
                if not pointer or size < 0x206:
                    raise ValueError("Geargrafx did not expose working RAM")
                C.memset(pointer + 0x200, 0xcc, len(EXPECTED))
                for _ in range(2):
                    core.retro_run()
                observed = C.string_at(pointer + 0x200, len(EXPECTED))
                observations.append(observed.hex())
                if observed != EXPECTED:
                    raise ValueError(f"actual guest marker mismatch: {observed.hex()}")
            return {"core_name": name, "core_version": version,
                    "cold_and_warm_reset_ram": observations, "frames_per_reset": 2}
        finally:
            if loaded:
                core.retro_unload_game()
            core.retro_deinit()


def run_worker(core, rom):
    return subprocess.run([sys.executable, str(Path(__file__).resolve()),
                           "--worker", "--core", str(core), "--rom", str(rom)],
                          capture_output=True, text=True, timeout=30)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--core", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    core = args.core or core_path()
    if args.worker:
        print(json.dumps(execute(core, args.rom)))
        return
    digest = verify_fixture(args.rom.read_bytes())
    labels = args.labels or args.rom.with_name("smoke.lbl")
    mutated = missing_marker_rom(args.rom.read_bytes(), labels.read_text())
    observations = []
    for _ in range(2):
        result = run_worker(core, args.rom)
        if result.returncode:
            raise ValueError(f"Geargrafx execution failed ({result.returncode}): {result.stderr[-3000:]}")
        observations.append(json.loads(result.stdout))
    if any(item["core_version"] != DEPENDENCIES["geargrafx"]["revision"] for item in observations):
        raise ValueError("Geargrafx core does not report the pinned revision; rebuild with make setup")
    if observations[0] != observations[1]:
        raise ValueError("normalized cold/warm observations are not repeatable")
    # The real-core assertion must reject a ROM that never writes PCE!.
    with tempfile.TemporaryDirectory(prefix="pce-geargrafx-negative-") as directory:
        negative = Path(directory) / "missing-marker.pce"
        negative.write_bytes(mutated)
        result = run_worker(core, negative)
        if result.returncode != 1 or "actual guest marker mismatch" not in result.stderr:
            raise ValueError("negative execution did not fail at the guest marker assertion")
    evidence = {"schema_version": 1, "status": "PASS", "scope": "hucard-smoke-only",
                "rom_sha256": digest, "geargrafx_revision": DEPENDENCIES["geargrafx"]["revision"],
                "core_sha256": hashlib.sha256(core.read_bytes()).hexdigest(),
                "core_revision_source": "pinned setup; override core must be independently reviewed",
                "observations": observations, "missing_marker_negative": "PASS (expected failure)",
                "system_card_boot": "NOT_TESTED", "cd_read": "NOT_TESTED",
                "bios_api": "NOT_IMPLEMENTED", "cpu_pc_trace": "NOT_CAPTURED (libretro interface)"}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print("PASS real Geargrafx core: two cold starts + warm resets, RAM marker/TAM/TMA/TII, negative execution")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(f"FAIL/BLOCKED Geargrafx: {error}", file=sys.stderr)
        raise SystemExit(1) from error
