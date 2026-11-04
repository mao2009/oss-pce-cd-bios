# C0 addendum: CD image thread timing and CD state equality (C-R1, Issue #291)

Scope: synthetic CUE/BIN only (`<SYNTH_MEDIA>/synthetic_cd.cue`, unmodified). No reference BIOS, no `rom/`, no commercial medium. Source: pinned tree the pinned Geargrafx source tree (not published) (HEAD b49ae82a), read-only. File references are `file:line` in that tree unless marked `[test build]`.

This addendum closes review item M-6 ("audit omits CD image worker-thread timing"). It also corrects the review's statement that the synthetic CUE exercises the cuebin worker thread.

## 1. Build flags decide which CD threads exist

| Macro | Pinned build | Test build (`phase-c/build-det`) | Source |
|---|---|---|---|
| `GG_ENABLE_PHYSICAL_CDROM` | defined | defined | `build/build.ninja` DEFINES; `wrapper/CMakeLists.txt:71` |
| `GG_ENABLE_CDROM_CUEBIN_READAHEAD` | **not defined** | **not defined** | not in `build/`, `wrapper/`, `src/platforms/` build files; only `src/platforms/libretro/Makefile` mentions it |
| `GG_TESTING` | not defined | not defined | `build.ninja` DEFINES of both builds |

Consequence in this build: `CdRomCueBinImage::SetLoadOptions` forces `enable_read_ahead = false` and `read_ahead_chunks = 0` (`cdrom_cuebin_image.cpp:227-233`, `#else` branch). The cuebin worker code is compiled out: start `:1520-1528`, stop `:1530-1542`, thread body `:1544-1586`, and the start call `:190-192` are all under `#if defined(GG_ENABLE_CDROM_CUEBIN_READAHEAD)`.

So the review's M-6 statement ("the synthetic CUE uses the cuebin path, so this thread is exercised by the synthetic tests too") is **refuted for this build**. The cuebin thread does not exist in either binary.

The physical-drive worker does exist in the build (`GG_ENABLE_PHYSICAL_CDROM`), but it is started only by a physical-device load (`cdrom_physical_image.cpp:127`, inside the device-load function). `cdrom_media.cpp:97` sends a CUE to `m_cue_bin_image`, so the synthetic CUE never starts it.

## 2. Thread inventory

| Thread | Start | Stop | Body and waits | Reached by the synthetic CUE? |
|---|---|---|---|---|
| Media loader (`load_media_thread_func`) | `emu.cpp:192` (`emu_load_media_async`) | join at `emu.cpp:150-153`; state flag `loading_state` set at `emu.cpp:189` | runs `geargrafx->LoadMedia` (`emu.cpp:187`) | yes (this is the load, not a CD worker) |
| CUE read-ahead worker | `cdrom_cuebin_image.cpp:1520-1528` (called `:191-192`, guard `:190-193`) | `:1530-1542` (from dtor `:44-50` and `Reset` `:58-63`) | `:1544-1586`; `wait_for(100 ms)` at `:1558`; keep-alive every `GG_CDROM_CUEBIN_KEEPALIVE_SECONDS` = 5 (`cdrom_cuebin_image.h:33`, `:1580`) | **no** (compiled out) |
| Physical CD worker | `cdrom_physical_image.cpp:810-817` (called from device load `:127`) | `:820-829`; also `Reset` calls `StopWorker` `:64-66` (no-op when not started) | `:873-918`; `wait_for(250 ms)` at `:885` | **no** (not started by a CUE load) |

## 3. Timing relative to the load, reset, CD controller init, and CPU start (synthetic CUE path)

In order, for one run:

1. **Process start and core construction.** `GeargrafxCore::Init` (`geargrafx_core.cpp:100`). Seed at `:108` (pinned, time). Creates `CdRomMedia`, which creates the cue, chd, mmi and physical images (`cdrom_media.cpp:56-66`). No thread is started in construction.
2. **Init-time resets (no media loaded yet).**
   - `:124` `m_cdrom_media->Init` leads to `CdRomCueBinImage::Init` → `Reset` (`cdrom_cuebin_image.cpp:52-68`). No worker code is compiled in, so no thread is stopped or started here.
   - `:125` `m_cdrom->Init` → `CdRom::Init` → `CdRom::Reset` (`cdrom.cpp:64-70`, `:113`).
   - `:126` `m_scsi_controller->Init` → `ScsiController::Reset` (`scsi_controller.cpp:79-83`). Draws one RNG value (`:190-192`).
   - `:127` `m_media->Init` → `Media::Reset` → `m_cdrom_media->Reset` (`media.cpp:103-105`, `:144`) → `CdRomMedia::Reset` (`cdrom_media.cpp:73-87`) → image resets.
   - `:128` `Memory::Init` → `Memory::Reset` (RNG draws, see `rng-521-520.md`). `:129` `HuC6260::Init` → `Reset` (512 RNG draws).
3. **load_bios** (`mcp_debug_adapter.cpp:2052-2068` → `GeargrafxCore::LoadBios` `geargrafx_core.cpp:246-249`). No CD thread, no reset.
4. **load_media** (MCP `StartLoadMedia`, `mcp_debug_adapter.cpp:1994-2015`):
   - `gui_load_rom` (`gui.cpp:497-510`): `config_push_recent_media` (`:505`, see `recentrom-isolation.md`), `emu_resume` (`:506`). The loader thread is started by `emu_load_media_async` (`emu.cpp:192`); how `gui_load_rom` reaches that call was not traced in detail.
   - Loader thread (`load_media_thread_func`, `emu.cpp:176-190`): `GeargrafxCore::LoadMedia` (`geargrafx_core.cpp:208-215`) → `Media::LoadMedia` (`media.cpp:169`) → `Media::Reset` (`:178`) → `CdRomMedia::LoadCueFromFile` (`media.cpp:345`, `cdrom_media.cpp:89-97`) → `CdRomCueBinImage::LoadFromFile` → `LoadFromCueData` (`cdrom_cuebin_image.cpp:155-196`; CRC at `:188`; worker start at `:190-192` is compiled out).
   - Then the core reset: `GeargrafxCore::Reset` (`geargrafx_core.cpp:213`, body `:1205-1246`). This includes `m_cdrom->Reset` (`:1237`) and `m_scsi_controller->Reset` (`:1238`).
   - `FinishLoadMedia` (`mcp_debug_adapter.cpp:2022-2050`) reads the result after `loading_state` is finished.
5. **Pause and snapshot.** The harness waits for PC = E000 (`run_c3.py` `wait_paused_at`). The CPU has not executed an instruction.
6. **CPU start.** `debug_continue` after the breakpoints (`run_c3.py:168-172`; R1 `r1_runs.py` uses `debug_step_into` for the 16-step measurement).

Synchronisation: the loader uses `loading_state` (atomic) and `join` (`emu.cpp:150-153, 189`). The cuebin worker (compiled out) would use `m_queue_mutex` / `m_queue_condition` (`:1556-1558`) and `m_chunk_mutex` (`KeepAliveFile`, `:1590`). The physical worker uses `m_queue_condition` and `TryLockDriveForReadAhead` (`cdrom_physical_image.cpp:936`), which the CUE path never reaches.

State snapshots through MCP (`get_cdrom_status`, `read_memory`) are read while the emulator is paused (paused at E000 or after the 16-step measurement). I did not verify whether these calls take locks against the emulation thread.

## 4. Measurement (C-R1)

Four fresh portable runs (`phase-c-r1/runs/cdA_r1`, `cdA_r2`, `cdP_r1`, `cdP_r2`), each with the synthetic BIOS, the synthetic CUE, and `load_media`. `cdA_*` use the test binary with seed `0x00C0FFEE`; `cdP_*` use the pinned binary (time seed, 1.5 s apart). Script: `phase-c-r1/scripts/r1_runs.py cd`. Output: `cd-measure-r1.json`, `cd-measure-stdout.txt`, `cd-measure-stderr.txt` (empty). All four exit 0 with empty stderr.

Snapshot 1 ("reset"): paused at E000 after the load-time reset. Snapshot 2 ("step16"): after 16 `debug_step_into` calls (the synthetic program is 16 instructions, so PC = E028).

Snapshot fields: `get_cdrom_status` (full), `list_cdrom_tracks`, `get_cdrom_audio_status`, BRAM 8192 bytes, SYSTEM RAM 8192 bytes, CPU state.

| Field | cdA_r1 | cdA_r2 | cdP_r1 | cdP_r2 |
|---|---|---|---|---|
| cdrom_status SHA-256 (reset / step16) | `df8927e7ff06b55e` / same | same | same | same |
| cdrom_tracks SHA-256 (reset / step16) | `51f88765ffb7589d` | same | same | same |
| cdrom_audio_status SHA-256 (reset / step16) | `1e265432c7b0b5ab` | same | same | same |
| BRAM SHA-256 (reset / step16) | `92928df1a2b74d23` | same | same | same |
| SYSTEM RAM SHA-256 (reset / step16) | `9f1dcbc3...` / `133b6ba7...` | same | `9f1dcbc3...` / `133b6ba7...` | same |
| CPU snapshot SHA-256 (reset / step16) | `81e94030...` / `813807ae...` | same as cdA_r1 | `7e447168...` / `2d6e04c0...` | `30e34869...` / `233a764f...` |

Full snapshot SHA-256 values are in `cd-measure-r1.json` (`snap_reset_sha256`, `snap_step_sha256`).

Key CD status values (identical in all four runs, reset and step16): `media_type` cue, `length` 00:02:16, `reset` 0, `next_event` NONE, `cycles_to_event` 0, `scsi_bsy` false, `bram_enabled` true.

Comparison (`cd-compare` output and `cd-measure-r1.json`):

- **cdA_r1 vs cdA_r2 (det seed A):** every section identical at reset and at step16. Result: **PASS (identical)**.
- **cdP_r1 vs cdP_r2 (pinned, time seed):** CD sections identical (controller status, tracks, audio status, BRAM, SYSTEM RAM). CPU differs at reset (A, X, Y, S, P and MPR[0..6]) and at step16 (X, Y, S, P, MPR). Result for the CD sections: **PASS (identical)**. Result for the whole snapshot: **FAIL (differs in CPU/MPR only)**, which is the expected effect of the time seed and is not a CD difference.

## 5. Gate

| Item | Status | Basis |
|---|---|---|
| M-6 (thread timing for the synthetic CUE) | **RESOLVED** (for these builds) | No cuebin worker in either build (section 1); no physical worker on the CUE path. Section 3 is the timing. |
| M-6 claim that the synthetic CUE exercises the cuebin thread | **REFUTED** | `cdrom_cuebin_image.cpp:227-233` and the build flags |
| CD controller state equality, exposed fields, det seed A | **RESOLVED: PASS** | section 4 |
| CD controller state equality, exposed fields, pinned time seed | **RESOLVED: PASS** (CD sections only) | section 4 |
| CD state equality over the whole snapshot (pinned) | **FAIL** (CPU/MPR only) | section 4; expected time-seed effect |
| CD RAM (64 KB) equality | **UNKNOWN** | no CD RAM memory area in this build (area list: SYSTEM RAM, ZP, VRAM, SAT, PALETTES, BRAM, LOGICAL, PHYSICAL). Would be settled by a memory read of CD RAM through a tool that exposes it, or a source-level hook. |
| CD timing differences under a program that uses the CD | **UNKNOWN** | the synthetic program does not touch the CD (`cycles_to_event` 0, `next_event` NONE). The seeded SCSI initial-phase draw (`scsi_controller.cpp:190-192`) would matter only once the CD is used. Would be settled by a synthetic program that reads the CD sector and snapshots after the first event. |

## 6. Limits

- Synthetic media only; no System Card or commercial-game claim.
- The reference-BIOS A/B step must not assume "no CD thread" unless the same build flags are used. The flags above are the evidence.
- Geargrafx output is not hardware evidence.
