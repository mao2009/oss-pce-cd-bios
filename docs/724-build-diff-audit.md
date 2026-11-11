# Issue #724: debug/release path diff and verification-only non-contamination audit

Status: **design-only audit + static gate**. No production mechanism, no gate change.
`release_eligible=false`, ABI VERIFIED 0/81, DEV_CALLABLE 0, #173/#367/#459 safety gates stay BLOCKED.
This project is not a compatible System Card BIOS.

Scope (plan P09, parent #473): keep the apparent safety of the real-core
verification-only patch/probe path from being misread as a release guarantee.
PR #714 is **design only**; production implementation needs a separate Owner decision.

Refs #473, #714, `docs/473-selection-supplement-s7s8.md` §8. Depends #716, #173, #103.

## 1. What was compared (base 864d979c)

Builders: `tools/build.py` (smoke fixture) and `tools/build_rom.py` (DEV diagnostic ROM),
`MODE=debug|release`. Verification-only hooks: `tests/integration/patches/*.patch`
(block-write-guard, stack-access-guard, ST-write-guard, IRQ-entry-guard) applied only to
staged `TOOLS_DIR` copies outside the repository and linked into local probes.
Pinned Geargrafx `b49ae82a012eade566d72e9d61c9d95a5caa4da3` (GPL-3.0) is fetched to
`TOOLS_DIR`, never vendored into MIT sources. Synthetic fixtures/media only; no private
`./rom/` inputs used, read, or required.

| Axis | debug (`MODE=debug`) | release (`MODE=release`) | Verdict |
|---|---|---|---|
| Smoke ROM bytes (`tools/build.py`) | `ca65 -g -l` + `ld65`; `manifest.mode=debug` | same sources/config/link; `-g/-l` omitted; `manifest.mode=release` | ROM bytes must be byte-identical (CI `cmp` debug/release). Only listing/debug flags differ. |
| DEV ROM bytes (`tools/build_rom.py`) | `-g` for generated `api_table.s` and every override object (ld65 debug info for verification) | `-g` omitted for the generated table only; override objects keep `-g` in both modes; `manifest.mode=release` | ROM bytes must be byte-identical. `-g` never changes ROM bytes (comment + `test_build` reproducibility). |
| Verification-only hook effectiveness | Hooks active only inside patched-probe builds (`patch -p1` staged copy, patched `huc6280_opcodes` object substituted at link). 19 CPU byte-write paths stoppable; TransferStart push stoppable with dirty `transfer_state=2`; TransferEnd pop stop is a subset with BLOCKED post-stop state. | No hook symbol (`gg_verify*`/`GG_Verify*`) is referenced by `tools/build*.py`, imported, or linked into `build/` outputs. Release ROM has no refusal hook and claims none. | Hooks do not cross into release inputs (gate §3). Any hook text in release inputs FAILs closed. |
| Stop points | Debugger/probe harness: pre-instruction (`gg_verify_pre_instruction`), pre-write/pre-push/pre-pop, decode-time TAM refusal (D), IRQ deferral (E), per-byte transfer preflight (T). Post-instruction stop via debugger run loop. | No stop mechanism in ROM bytes. Release manifest records `mode` only. | Stop points are harness properties, not ROM properties. Release equivalence without a stop-capable harness is UNKNOWN. |
| Failure behavior | Invalid `MODE` fails before output. Unimplemented slots trap in a nonreturning loop (`api_unimplemented: bra @halt`). | Same traps. `make release` refuses (`BLOCKED`, exit 1). `tools/build_rom.py --release-gate` refuses even for a fully overridden slot set (`BLOCKED`, exit 3 via `tools/expect_exit.py`). | Refusal paths unchanged by mode. `release_eligible` stays `false` in every ledger. |

## 2. Static/build gate (what prevents leakage)

New gate: `tools/verify_build_diff.py` (runs in `make check`, hence in CI `make check`).

1. **Release-input scan** — every file whose bytes or assembling logic can reach a release ROM
   (`src/boot.s`, `src/bank0.cfg`, `src/overrides/*.s`, `tests/fixtures/smoke.s/.cfg`,
   `tools/build.py`, `tools/build_rom.py`, `tools/environment.py`, `tools/rom.py`,
   `spec/api_slots.json`) must not contain `gg_verify*`/`GG_Verify*` hook symbols, pinned-patch
   filenames, upstream-copyright strings, or GPL license text. Any hit FAILs closed.
2. **Build-logic import check** — `tools/build*.py`, `tools/environment.py`, `tools/rom.py` must not
   import probe modules (`compile_probe`, `reference_jsr_gate`, `geargrafx_api`, guard drivers) and
   must not reference `tests/integration/patches/`.
3. **Mode-branch check (AST)** — every use of the `mode` name in `tools/build.py`/`tools/build_rom.py`
   must match the debug-info-only allowlist (`-g`/`-l` selection, `MODE` plumbing, manifest echo).
   A new `mode`-dependent source/link/padding branch FAILs closed, so a future functional fork
   cannot silently split debug and release behavior.
4. **Release-gate check** — `Makefile: release` still refuses with `BLOCKED`/exit 1;
   `dev-release-gate` still routes to `tools/build_rom.py --release-gate`; `release_gate()` still
   raises `ReleaseBlocked` for both the empty and the fully-overridden slot set; spec ledgers keep
   `release_eligible=false` (top-level where present, nested slots otherwise; none may be true).
5. **Patch-isolation check** — `tests/integration/patches/` exists with its GPL `LICENSE` and
   `README.md` isolation note; no workflow upload `path:` stages it, the core `.so`, or `TOOLS_DIR`
   objects; `tools/environment.py` keeps the `TOOLS_DIR`-outside-repo refusal.
6. **Release-artifact scan (when outputs exist)** — if `build/debug` and `build/release` smoke ROMs
   are present they must be byte-identical and marker-free; when absent the disposition is
   `NOT_RUN`, never PASS-by-absence. Content signatures stay with `tools/artifact_guard.py`; license
   markers stay with `tools/provenance_guard.py`; reproducibility stays with
   `tools/rom_provenance.py`. This gate adds the hook/GPL-text and mode-fork tripwires those do not state.

Regression test: `tests/host/test_724_build_diff_audit.py` (10 tests, static only). Positive: HEAD scans
clean, gates intact, report verdict PASS with UNKNOWNs preserved. Negative (mutation): injected
`gg_verify` marker, injected GPL text, new `mode=="release"` behavior branch, and a probe import each
FAIL closed. `release_gate` refusal is exercised for `set()` and `set(range(81))`.

## 3. What stays UNKNOWN (explicitly not PASS)

- **free-run equivalence: UNKNOWN.** Stop semantics were measured only under the instruction-debugger
  harness (`RunToVBlank`, post-instruction stop, step counting). A harness-free free-run has no stop
  mechanism measured here: NOT_RUN.
- **libretro equivalence: UNKNOWN (#103).** The libretro core exposes no single-instruction stop API;
  transfer-safety behavior there is NOT_RUN.
- **release equivalence: UNKNOWN until re-measured.** Static byte-identity (`cmp` + `rom_provenance
  --reproduce`) on the same checkout is the minimum; this audit ran `cmp`-equivalent bytes only for
  the smoke ROMs present in the authoring checkout (see §4), not a full CI matrix. A passing static
  gate is not execution proof.
- **Production safety: NOT PASS.** `release_eligible=false`, ABI VERIFIED 0/81, DEV_CALLABLE 0,
  reference JSR permitted 0/81. No mode change promotes any of these. Release safety must not be
  claimed until release bytes are measured with the same rigor as debug — which they are not here.

## 4. Evidence (this change; independent review + CI pending)

Host-model, static only. No emulator run, no real-core diagnostic, no commercial media, no private
`./rom/` input. Geargrafx observations cited from prior docs are not proof of hardware behavior.

- `python3 tools/verify_build_diff.py` → `PASS 724 build-diff audit` (exit 0) on HEAD `864d979c`
  + this change; `--report` emits `verdict=PASS`, `release_artifact_scan=CHECKED` (authoring-checkout
  `build/debug` vs `build/release` smoke ROMs byte-identical, sha256
  `8d659d6f…fa25` both), `unmeasured_UNKNOWN` as above.
- `python3 -m unittest discover -s tests/host -p 'test_724*' -v` → 10 tests OK.
- `make check` (adds the new gate) and full `make test` / CI: to be recorded by review/CI, not claimed here.
- Real-core diagnostics: NOT_RUN in this change. Prior verification-only measurements (#498/#499/#645
  series, #653/#654) are referenced, not re-executed or re-adjudicated.

## 5. Residual risks / non-goals

- The gate is a text/AST tripwire over release inputs and build logic. It does not detect paraphrased,
  translated, or re-encoded third-party code (same limit as `docs/provenance.md`), does not authenticate
  artifact origin (same limit as `tools/rom_provenance.py`), and does not close `curl`/`git push`/`gh`
  publication routes outside the artifact guard (same limit as `tools/artifact_guard.py`).
- GPL-license suitability of the local probe link itself stays unconfirmed per
  `docs/173-safety-monitor-limits.md`; this gate only proves the hooks do not ship in release inputs.
- `build/` outputs are untracked and were present in the authoring checkout; the CHECKED scan there is
  checkout-local, not a CI attestation.
