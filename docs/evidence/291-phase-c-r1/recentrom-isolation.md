# RecentROM0 and portable isolation (C-R1, review item M-7)

Scope: the test-only configuration of the synthetic runs. Pinned source `scratchpad/env-geargrafx/src` (HEAD b49ae82a), read-only. Not excluded from the record: `RecentROM0` is audited below.

## 1. What is stored

- Key: `[Emulator] RecentROM0` (`platforms/shared/desktop/config_definitions.inc.h:269`, `CONFIG_STRING_ARRAY("Emulator", "RecentROM%d", config_emulator.recent_roms, config_max_recent_roms, "")`; in memory `config_data.h:68`, 15 slots).
- Value in every run directory: the path string as passed to `load_media`, which is the synthetic CUE under `scratchpad/<SYNTH_MEDIA>/` (absolute, forward slashes). This path is outside the run directory.
- Other path keys in the run `config.ini` (`SysCardBiosPath`, `GameExpressBiosPath`, `PacJapanBiosPath`, `PacUsBiosPath`, `LastOpenPath`, `SaveFilesPath`, `SaveStatesPath`, `BackupRAMPath`, `ScreenshotPath`, `TraceDiskPath`, `MB128Path`, `VideoRecordingPath`) are empty or resolve inside the run directory. Checked for all R1 run directories and four historical ones with `phase-c-r1/scripts/r1_manifest.py` (`run-manifest.json`): every R1 run has `SysCardBiosPath = ''` and `RecentROM0` set to the synthetic CUE.

## 2. Where it is written

- `config_push_recent_media(path)` (`platforms/shared/desktop/config.cpp:129-150`) moves the path to slot 0.
- Called only from `gui_load_rom` (`gui.cpp:505`). `gui_load_rom` is called by the MCP `StartLoadMedia` (`mcp/mcp_debug_adapter.cpp:2005`), by the GUI, and by the command-line `rom_file` argument (`application_headless.cpp:107`; not used by the harness).
- The in-memory value is saved to `config.ini` by `config_write` (`config.cpp:179`). Calls in the harness path: `main.cpp:258` (normal exit) and `main.cpp:239` (error exit). `config_read` (`config.cpp:152`) may also write when the file version is outdated (`:165`), which does not apply to a new run directory.

## 3. Where it is read

- At start, `config_read` (`main.cpp:212`) loads the slot into memory.
- Used by: GUI recent-media menu (`gui_menus.cpp:237-245`, builds menu items; selecting one would call `gui_load_rom`, which the harness never does), and MCP `list_recent_media` (`mcp/mcp_debug_adapter.cpp:1005-1028`, returns the path and file name as strings).
- Only these three places reference `recent_roms` (grep over `platforms/shared/desktop`). Neither the menu build nor `list_recent_media` opens, stats or reads the file.

## 4. Can it cause access outside the run directory?

- In the harness, each run starts with a fresh directory that contains only `geargrafx.exe` and `SDL3.dll` (`synthetic/run_c3.py:137-138`, `r1_runs.py` `cd_run`). No `config.ini` exists before launch, so `RecentROM0` is never read back by a run in this experiment. It is written at exit and stays in that run's `config.ini`.
- The only external file access during these runs is the synthetic CUE and BIN, read by `load_media` (`cdrom_media.cpp:89-97`, `cdrom_cuebin_image.cpp`). That read is the intended media load, not a consequence of `RecentROM0`.
- No file operation is triggered by `RecentROM0` in the source. The only file write that `RecentROM0` touches is the run-directory `config.ini` at exit.
- The T5 write check (`phase-c3-manifests.json`; R1 `run-manifest.json`) confirms that new files are only `config.ini`, `stderr.txt` and `tmp/` in the run directory, and that media, BIOS, pinned binary and pinned config are unchanged.

## 5. Effect on the experiment

- None on CPU, RNG or CD state: the value is a string, used only for the menu and the recent-media listing.
- It does not change the config used by the emulator. The config is created fresh in each run directory with defaults; `RecentROM0` is written after the run.

## 6. Test-only config change

**None made.** No external access was attributed to `RecentROM0`, so the rule ("change only the test-only config if it leads to access outside the run directory") was not triggered. The pinned binary and the pinned tree config were not touched.

## 7. Status

| Item | Status | Basis |
|---|---|---|
| RecentROM0 external path accounted for | **RESOLVED** | sections 1-4: stored, written at exit, listed only |
| External file access caused by RecentROM0 | **RESOLVED: none** | source (sections 2-4) |
| Isolation claim for file writes (T5) | **RESOLVED: PASS** | `phase-c3-manifests.json` (C3) and `run-manifest.json` (R1) |
| Isolation claim including config references | **RESOLVED with stated scope** | The config reference is outside the run directory and is recorded here. The claim "portable isolation PASS" covers file writes and no config change; it does not cover the config's string value |
| Read tracing of the whole process (independent check) | **not performed** | no file-access tracer was run; the conclusion rests on source reading and the write manifests. Would be settled by a process file-access trace of one run |

Limits: source reading, not a process trace. Synthetic media only.
