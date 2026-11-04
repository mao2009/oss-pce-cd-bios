# Phase C0: environment and source audit (Issue #291)

Scope: source-confirmed audit of the pinned Geargrafx tree. No reference BIOS, no `rom/`, no reference JSR, no commercial medium, no GitHub access. All paths below are under the pinned tree the pinned Geargrafx source tree (not published) (commit b49ae82a012eade566d72e9d61c9d95a5caa4da3). Line numbers are from the pinned tree (unmodified).

## Environment record (taken at start of C0)

| Item | Value |
|---|---|
| Pinned HEAD | `b49ae82a012eade566d72e9d61c9d95a5caa4da3` (verified with `git rev-parse HEAD`) |
| Pinned tree status | `git status --short` in `src/` printed nothing (clean). Report only; not changed. |
| Pinned binary | the pinned binary (not published) |
| Pinned binary SHA-256 (before) | `4933885ca1cebd4a979dcd8da1a1324aadac95885d3403b7019fe5f7df664827` |
| Memory at start | 7,170,588 kB free of 16,641,588 kB physical (about 43 %) |
| Other build/emulator processes | none found at start (`tasklist` for ninja, cmake, cc1plus, geargrafx, make, g++, ld). Re-checked before the test build (see C1 record). |
| Toolchain (same as pinned) | gcc/g++ 16.2.0 (MinGW-W64 UCRT, WinLibs), CMake 4.4.4, Ninja 1.13.2, SDL3 3.2.28 (per `env-geargrafx/env-manifest.md`) |
| Synthetic inputs | `<SYNTH_MEDIA>/synthetic_cd.cue` and `.bin` (unmodified; `media-hashes.json` lists `.bin` SHA-256 `7ba14aad...ac64c`, `.cue` SHA-256 `a751990e...36dd2`) |

## Seed and RNG

### Where the seed comes from
- `geargrafx_core.cpp:107-108`: `m_random = new Random(); m_random->Seed((u32)time(NULL));` in `GeargrafxCore::Init` (line 100). This is the **only** seed in the core. It is wall-clock time in whole seconds. Two launches in the same second get the same seed.
- `random.h:29-32`: the constructor sets state `0x12345678`. The seed replaces it via `Seed` -> `SetState` (`random.h:34-37`, `84-87`). A seed of 0 maps to `0x12345678`.
- `random.h:51-58`: the generator is xorshift32 (shifts 13, 17, 5). `Next8Bit` and `Next16Bit` take the top 8 or 16 bits of the 32-bit output (`60-68`). `Next(limit)` uses one draw (`70-76`).
- No `srand`/`rand` exists in the core. A grep for `srand`, `rand(`, `random_device`, `mt19937` and `chrono` in `src/` found no calls. The one-off ` rand` identifiers are the xorshift class only.
- `GeargrafxCore::Reset` (`geargrafx_core.cpp:1205-1246`) does **not** reseed. Every reset draws from the continuing stream. Reset order: memory (1231), huc6202 (1232), huc6260 (1233), huc6270 x2 (1234-1235), huc6280 (1236), cdrom (1237), scsi (1238), cdrom_audio (1239), adpcm (1240), laseractive (1241), audio (1242), input (1243).
- Savestates: `geargrafx_core.cpp:774` writes the RNG state and `:1075` restores it, so a state load replaces the stream position.
- `geargrafx_core.cpp:794`: the savestate header timestamp is `time(NULL)`. This changes only the savestate file bytes, not emulated state.

### Where random values are consumed (default configuration)
Each consumer's behaviour depends on a reset value. The default is set in `Memory::Memory` (`memory.cpp:52-55`) and in the config defaults (`platforms/shared/desktop/config_definitions.inc.h:202-207`), which the GUI applies at startup (`gui.cpp:160-166`).

| Component | Source line | Random under default config? | Default |
|---|---|---|---|
| CPU A, X, Y, S, P at reset | `huc6280.cpp:214-222` (`m_reset_value = -1`, `huc6280.cpp:53`) | **yes**: `rnd = Next()` gives A, X, Y, S bytes; `P = Next8Bit()` | InitRegisters=0 (Random) |
| MPR0-MPR6 at reset | `memory.cpp:106-118` (`m_mpr_reset_value = -1`, `memory.cpp:52`) | **yes**: `Next8Bit` with zero rejection | InitMPR=0 (Random). MPR7 is fixed to 0 (`memory.cpp:104`). |
| HuC6260 colour table | `huc6260.cpp:180-192` (`m_reset_value = -1`, `huc6260.cpp:48`) | **yes**: 256 x (Next8Bit, Next16Bit) = 512 draws | InitColorTable=0 (Random) |
| WRAM (main RAM) | `memory.cpp:120-126` (`m_wram_reset_value = 0`, `memory.cpp:53`) | no (fixed 0x00) | InitRam=1 (0x00) |
| CD-ROM RAM | `memory.cpp:146-154` (uses `m_wram_reset_value`) | no (fixed 0x00) | InitRam=1 |
| Card RAM | `memory.cpp:174-180` (`memory.cpp:54`) | no (fixed 0x00) | InitCardRam=1 (0x00) |
| Arcade Card RAM (2 MB) | `memory.cpp:182-190` (`memory.cpp:55`) | no (fixed 0x00) | InitArcadeCard=1 (0x00) |
| SCSI initial read-phase delay | `scsi_controller.cpp:189-193`: `m_initial_read_phase_cycles = m_random->Next(k_max+1)` when `!keep_rst_signal` | **yes** (drawn on every SCSI reset, not gated by a reset value) | not configurable |

Notes on the table:
- The combo labels `"Random\0 0x00\0 0xFF"` (`gui_menus.cpp:2171-2242`) imply index 0 = random and index 1 = 0x00, but the body of `get_reset_value` was not inspected in the original audit. The mapping is inferred from the labels and the defaults. Correction (C-R1 review): the body was inspected later (`platforms/shared/desktop/utils.h:71-84`); index 1 returns `0x0000`, which agrees with this inference.
- The default `InitRam=1` means WRAM is **not** random. The only seed-dependent CPU-visible state under defaults is the CPU registers, MPR0-6, the colour table and the SCSI delay.
- Save RAM (BRAM): `memory.cpp:192-194` fills 0x2000 with 0xFF, clears 0x800 and writes the 8-byte init string (deterministic). `Memory::SaveRam` (`memory.cpp:444`) writes the file. Persistent data is loaded from disk by `load_ram()` (emu.cpp, `emu_reset`, line 567-584).
- Arcade RAM (`arcade_card_mapper.cpp:28`) is allocated uninitialised but is always filled by `Memory::Reset` (`memory.cpp:182-190`), so it is deterministic.

### Consequence for C1
A single fixed seed at `Init` fixes the whole random stream. Because `Reset` does not reseed, determinism holds for a fresh process with the same sequence of operations (the same number of draws). `srand` is not used, so there is no libc RNG to fix. The seed is 32-bit with 0 mapping to `0x12345678`.

## CPU, MPR, RAM, CD, input and trace initialisation

| Item | Source | Randomness | Time-dependent |
|---|---|---|---|
| CPU PC | `huc6280.cpp:209-210` reads reset vector via memory | none (ROM) | no |
| CPU A/X/Y/S/P | `huc6280.cpp:207-230` | seeded random (above) | no (seed only) |
| MPR init | `memory.cpp:100-118` | seeded random (above) | no |
| RAM init | `memory.cpp:120-190` | fixed 0x00 under defaults | no |
| Backup RAM | `memory.cpp:192-194` | deterministic | no; file load is from disk |
| CD controller | `cdrom.cpp:113-127` (`CdRom::Reset`) | none; state is deterministic | no (cycle counts only) |
| SCSI controller | `scsi_controller.cpp:176-204` | seeded random delay (line 192) | no |
| CD audio / ADPCM reset | `cdrom_audio.cpp:115`, `adpcm.cpp:142` | none | no |
| Controller input | `input.cpp:129-154` (`Input::Reset`): pads 0xFFFF, select/clear true, mouse zero | none | no (values come from the pump or the MCP `controller_button` API) |
| Debug reset | `mcp_debug_adapter.cpp:236-239` -> `emu.cpp:567-584` (`emu_reset`) | none in the reset itself; it calls `gui_debug_trace_logger_reset`, `reset_buffers`, `reset_rewind_timing`, `emu_audio_reset`, `emu_save_persistent_data`, `ResetMedia(false)`, `load_ram`, `load_mb128`, `rewind_reset` | `reset_rewind_timing` and rewind use wall-clock (see below) |
| Trace logger reset | `trace_logger.cpp:49-54` (counters only); `SetCapacity` (`:56-77`) calls `Reset()` | buffer from `new GG_Trace_Entry[capacity]` is not zeroed, but only entries below `count` are read | no |
| set_trace_log (MCP) | tool `set_trace_log` in `mcp_server.cpp`; `mcp_debug_adapter.cpp:4019` calls `SetEnabledFlags(0)` (dispatch body not traced in full) | none | no |

## Non-determinism sources besides the seed

Time and host sources found (all are outside the emulated CPU state, unless marked "may affect"):

1. **Wall-clock seed** (see above). Only the seed is time-dependent in the core.
2. **Frame pacing (headless)**: `application_headless.cpp:150-173` uses `SDL_GetPerformanceCounter` and `SDL_Delay` to pace the loop to 16.666 ms. The emulated state advances per `emu_update()`, so the state is deterministic for a fixed sequence of frames, but the number of frames executed before an MCP command is processed depends on wall-clock time (may affect test timing). Tests must pause and step instead of timing.
3. **Frame pacing (GUI)**: `display.cpp:52-100` (`SDL_GetPerformanceCounter`, `SDL_Delay`).
4. **Rewind**: `emu.cpp:451-479` (`get_rewind_pop_budget`) uses `SDL_GetPerformanceCounter` to decide how many snapshots to pop per frame. This affects state only if rewind is used.
5. **GUI timers**: `gui.cpp:586` and `:853` (status message timing, `SDL_GetTicks`); `application.cpp:277`, `:476`, `:620` (mouse idle timing).
6. **Savestate timestamp**: `geargrafx_core.cpp:794` (`time(NULL)`). Writes time into savestate files only.
7. **Host input pump**: `events.cpp:325` (`SDL_GetKeyboardState`) and `events.cpp:420-433` (`SDL_GetGamepadButton`, `SDL_GetGamepadAxis`) read host keyboard and gamepad state. This is a potential source of input differences if a host controller is attached. Not verified whether the headless path invokes this pump; the test harness must not use a host pad and should use the MCP `controller_button` API only.
8. **MCP thread**: commands run on a separate thread and are queued. The frame at which a command is applied depends on timing unless the emulator is paused (`debug_pause`), so tests use breakpoints and pause/continue.
9. **File system**: `config.ini` is read at start (`config.cpp:81-100`), save RAM is loaded from disk at reset (`emu.cpp:580`, inside `emu_reset`), and media is read from disk. Portable mode (`--portable`, `config.cpp:420-456`) puts `config.ini` and `tmp/` in the executable directory.
10. **Audio**: the audio callback and buffer fill are not verified to drive frame timing in headless mode. Not tested here.
11. **Uninitialised allocations**: `trace_logger.cpp` buffer (not zeroed; only the first `count` entries are read); `media.cpp:333` `new u8[m_rom_size]` (ROM buffer, filled by load). No uninitialised read of CPU-visible RAM was found because WRAM, CD RAM, card RAM and Arcade RAM are all written in `Memory::Reset`.
12. **BIOS load**: `media.cpp:662-702` (`LoadBiosData`) accepts exactly 0x40000 bytes (or 0x40200 with a 512-byte header, removed). There is no CRC gate: a synthetic 256 KB image loads (`valid_crc: false` is reported only as a warning).
13. **BIOS mapping**: for CD media with a 32-bank (256 KB) image the mapping is `bank = x % rom_bank_count` (`media.cpp:1173`). Bank 0 therefore maps file offset 0x0000-0x1FFF to $E000-$FFFF at reset (MPR7 = 0). The reset vector is at file offset 0x1FFE. `memory.cpp:425-436` (`GetBankType`) gives BIOS for banks below 0x80 when CD hardware is enabled.

## Source citations used for the design
- `geargrafx_core.cpp:100-110` (Init, seed), `:1205-1246` (Reset), `:774` and `:1075` (RNG in savestates), `:794` (timestamp).
- `random.h:26-93` (RNG class).
- `huc6280.cpp:207-230` (CPU reset), `:277-280` (SetResetValue).
- `memory.cpp:35-55` (constructor and reset values), `:100-199` (Reset), `:280-286` (SetResetValues), `:444` (SaveRam).
- `huc6260.cpp:154-197` (palette reset), `huc6260_inline.h:522-525` (SetResetValue).
- `scsi_controller.cpp:176-204`.
- `cdrom.cpp:113-127`, `input.cpp:129-154`, `trace_logger.cpp:49-77`.
- `emu.cpp:963-976` (reset-value setters), `:567-584` (emu_reset).
- `gui.cpp:160-166`, `config_definitions.inc.h:202-207` (defaults).
- `mcp_debug_adapter.cpp:236-239`, `mcp_server.cpp:2650-2654` (debug_reset).
- `main.cpp:87-89`, `config.cpp:81-100`, `config.cpp:420-456` (portable path).
- `media.cpp:662-702` (BIOS load), `media.cpp:1095-1175` (ROM map), `memory.cpp:425-436` (bank type).
