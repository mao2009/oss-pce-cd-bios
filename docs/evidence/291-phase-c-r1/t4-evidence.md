# T4 evidence: historical run versus C-R1 re-run (review item M-4, and M-1 / M-5 for the T4 relation)

Scope: synthetic BIOS (`phase-c/synthetic/synthetic_bios.bin`) and synthetic CUE, deterministic test binary, seed A. No reference BIOS, no `rom/`, no commercial medium.

## 1. Two kinds of evidence, kept apart

| | Historical (C3, 17:52-17:54) | C-R1 re-run (this step) |
|---|---|---|
| Kind | probe run (`run_c3.py probe`, 17:52) and full-run runs `det_S1_r1` and `det_S1_up_r1` (17:53-17:54) | new re-run, `phase-c-r1/runs/t4r1_noinput` and `t4r1_up` |
| Expected values written before the run | `phase-c3-expected.json` (17:53:34) for T4 relation; the probe's expectation is not recorded in a separate file | `phase-c-r1/t4-expected-r1.json`, written 2026-11-02T09:18:47Z before the first R1 launch (sha256 `d23732586bdc9b9652128c128e7d780fda56626e10b9240c73a4f9013b239f48`) |
| Observed (SEL0 at 0x2010, SEL1 at 0x2011) | no input: 3F / 3F; Up: 3F / 3E (`det_S1_r1`, `det_S1_up_r1`, `phase-c3-results.json`) | no input: 3F / 3F; Up: 3F / 3E (`t4-rerun-r1.json`) |
| Result | full-run T4 PASS | T4 re-run PASS (`checks.result`) |

## 2. The historical probe output is not preserved (M-4)

- `run_c3.py` `probe` mode (`:243-250`) printed its JSON and trace samples to stdout only. No file was written from that output.
- The probe run directory `phase-c/runs/probe-det-S1` contains only `geargrafx.exe`, `SDL3.dll`, `config.ini` (17:52:42), `stderr.txt` (0 bytes) and `tmp/`. Searches of the Phase C folder for the probe's output keys (`trace_first`, `sysram_first`, `probe_samples`) found only `phase-c3-manifests.json` (a manifest listing) and `review-c1-c3.md`.
- `phase-c/c3-full-stdout.txt` contains only the five T1-T5 lines.
- **Not restored.** The statement in `phase-c3-tests.md` that the probe "showed the SEL=1 read changed instead" cannot be checked from any saved file. The -r1 document marks this (`phase-c3-tests-r1.md`, Model corrections item 1).

## 3. Why the relation is still trusted: source, and the re-run

The corrected relation (SEL=0 unchanged; SEL=1 bit0 cleared by Up) is derived from the standard-pad path, not from the probe:

- `src/input.cpp:47` idle pad value `0xFFFF` (active low).
- `src/input_inline.h:29` press clears the bit (`&= ~key`); `src/types.h:81` `GG_KEY_UP = 0x10`.
- `src/input_inline.h:103` `m_sel = bit 0 of the write`; `:123` `m_register = 0x30` on each port write (the register is rebuilt when the synthetic program writes `$1000`, not on the read). Both `:103` and `:123` are in the same port-write function as `:204-217` (its `TraceInputEvent(TRACE_INPUT_WRITE, ...)` is at `:220`).
- `src/input_inline.h:204-217` (standard pad, default controller type `input.cpp:48`): `m_sel ? (raw >> 4) & 0x0F : raw & 0x0F` (inside `if (!m_clr)`).

Derived values: no input, SEL=0: `0x30 | 0x0F = 0x3F`; no input, SEL=1: `0x3F`; Up, SEL=0: `0x3F`; Up, SEL=1: `0x30 | 0x0E = 0x3E`. These match the re-run.

Citation: the earlier T4 justification cited `src/input_inline.h:174-180`. Those lines are the mouse branch (`:148` onward, `:174-177` choose mouse or pad nibble with no high-nibble rule). They do not support the relation. The correct lines are `:204-217` (M-1, MAJOR, citation only; the content was re-checked in source and is correct).

## 4. C-R1 re-run: procedure and result

Procedure (identical to the C3 harness, by calling `run_c3.launch` unchanged; only the run directory is redirected to `phase-c-r1/runs/`):

1. `load_bios` (synthetic, syscard), `load_media` (synthetic CUE), wait PC = E000.
2. Breakpoints at E012 (input) and E028 (halt); `debug_continue`.
3. At E012: for the Up run, `controller_button` player 1, `up`, `press`. For the no-input run, no button call.
4. `debug_continue` to E028; read the trace (`get_trace_log`), CPU state, SYSTEM RAM (8192 bytes).
5. Read WRAM 0x10 (`$2010`, SEL=0 read) and WRAM 0x11 (`$2011`, SEL=1 read) from the SYSTEM RAM dump.

Executed: `timeout 600 py -3 phase-c-r1/scripts/r1_runs.py t4` (exit 0). Stdout `phase-c-r1/t4-rerun-stdout.txt`; stderr `phase-c-r1/t4-rerun-stderr.txt` (empty). Full record `phase-c-r1/t4-rerun-r1.json`.

| Launch | Input | Trace entries | Exit | SEL0 2010 | SEL1 2011 |
|---|---|---|---|---|---|
| `t4r1_noinput` | none | 16 | 0 | 3F | 3F |
| `t4r1_up` | controller_button up press at E012 (response `action` press, `button` up, `player` 1) | 16 | 0 | 3F | 3E |

Checks: no-input matches expected (3F/3F); Up matches expected (3F/3E). Result **PASS**.

## 5. Corrected manifest (M-5)

`synthetic/synthetic_bios_manifest.json` (17:48, original kept, sha256 `c498afa2e883354e37ff1776a7f95e97e958ce33cf17fd89ea9d603b39c3f129` as recorded in `phase-c3-tests.md`) contains the superseded field `joypad_expectation` ("Up clears bit0 on SEL=0 row"). The -r1 copy `phase-c-r1/synthetic_bios_manifest-r1.json` replaces that field with the current relation and states that the earlier draft was superseded. Diff: `phase-c-r1/diffs/synthetic_bios_manifest-r1.diff` (one line).

## 6. Status

| Item | Status |
|---|---|
| T4 relation (SEL=0 unchanged, SEL=1 bit0 cleared by Up) | **RESOLVED**: source-derived and re-run PASS (new evidence) |
| Historical probe output (M-4) | **UNKNOWN**: not preserved; cannot be restored; would be settled only by the original stdout, which does not exist |
| Citation (M-1, MAJOR) | **RESOLVED**: corrected to `input_inline.h:204-217` in the -r1 documents; the content is correct |
| Stale manifest field (M-5) | **RESOLVED**: -r1 copy corrected; original kept |
