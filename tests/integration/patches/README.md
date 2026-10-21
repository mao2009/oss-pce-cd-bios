# Verification-only patches for the pinned Geargrafx core

Upstream: https://github.com/drhelius/Geargrafx, pinned commit
`b49ae82a012eade566d72e9d61c9d95a5caa4da3` (archive sha256
`a80a3eae26a8efcd994350e84bbe73c975e406ced41f8f2404c88b00d4217f68`, see `tools/dependencies.json`).

## What is here

| File | Purpose | Issue |
| --- | --- | --- |
| `geargrafx-b49ae82-st-write-guard.patch` | Unified diff against `src/huc6280_opcodes.cpp` only. Adds a null-by-default guard hook that the ST0 ($03), ST1 ($13) and ST2 ($23) handlers call before `WriteFromCPU`. | #365 (parent #173) |

## License and distribution

- **This directory is GPL-3.0, not MIT.** The patch is a modification of one GPL-3.0 source
  file, so the patch and any build of the patched core are GPL-3.0. The full GPL-3.0 text is in
  `LICENSE` in this directory (copied verbatim from the upstream tree, sha256
  `3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986`). The repository root
  `LICENSE` (MIT) does not cover this directory or the patched core.
- Upstream copyright is kept intact: `src/huc6280_opcodes.cpp` begins with the upstream header
  `Copyright (C) 2024  Ignacio Sanchez` under GPL-3.0. The patch hunks start at line 20, so the
  header lines (1-19) are not changed by the patch.
- Distribution conditions: whoever distributes this patch, or a binary built from the patched
  core, must license it under GPL-3.0, keep the upstream notices, and provide the corresponding
  source (the upstream commit above plus this patch). The patch adds no other third-party code.
- The patch is **not vendored** in the MIT sources. The repository keeps only this diff. The core
  is fetched by `scripts/setup-geargrafx.sh` (`tools/setup.py`) into `TOOLS_DIR`, which must lie
  outside the repository (`tools/environment.py` refuses otherwise).
- `tests/integration/geargrafx_st_write_guard.py` applies the diff to a staged copy in a temporary
  directory, compiles only the patched `huc6280_opcodes` object, and links it into a local probe.
  Probe executables, objects and the patched copy are never committed, uploaded or published.
- The hook is for verification only. It must not be carried into a release build.

## How this stays out of MIT artifacts

- Repository: only the diff and this `LICENSE` are tracked. No upstream source or built object
  is written into the tree (the core lives in `TOOLS_DIR`, and the probe in a temporary directory).
- Artifacts: `tools/artifact_guard.py` refuses object and executable suffixes and ELF/Mach-O/PE/ar
  signatures in every upload path. It is a binary tripwire only: it does **not** detect GPL source.
  The upload paths in `.github/workflows/*.yml` cover `build/debug` outputs (ROM, map, label,
  listing and JSON files) and two logs under `.intake/.artifact-stage/`. None names the fetched
  core, the probe executable or this directory, so the GPL-3.0 patch is not published from there.
- Provenance: `tools/provenance_guard.py` checks license markers only in code extensions
  (`.s .c .h .cpp .py .sh .yml` ...). The `.patch` and `LICENSE` files here are outside that set.
- Pages: `site/` is the only Pages source; it does not include `tests/integration/`.

## Applying by hand (for review)

```sh
# from a clean copy of the pinned tree, e.g. the tool cache source
patch -p1 --dry-run -i tests/integration/patches/geargrafx-b49ae82-st-write-guard.patch
```

## Status

PASS on the pinned core under WSL, as recorded in commit `8046ecf` (PR #430):

- Armed (refused inside the core before the side effect, 4 cases): `st0-armed`, `st1-armed`,
  `st2-armed`, `st0-after-tam-armed` (MPR0 changed to $FF by TAM first).
- Unguarded (patched build, null hook; the store lands, 4 cases): `st0-unguarded`,
  `st1-unguarded`, `st2-unguarded`, `st0-after-tam-unguarded`.
- Positive control: `pos-write-armed` (allowlisted RAM store lands).

This is a verification patch with a null-by-default hook. The unguarded runs use the same
patched build with the hook unset, not the stock object. Nothing here changes `BLOCKED_ABI`
or `release_eligible`.

Re-run on 2026-10-21 under WSL (Ubuntu) from a clean clone of this branch at commit `e42f67f`
(`TOOLS_DIR` set to the existing external tool cache; `python3 tools/setup.py geargrafx --debugger`
then `python3 tests/integration/geargrafx_st_write_guard.py`). Synthetic BIOS and CD only. Output:

```
PASS issue-365/173 ST0/ST1/ST2 free-running pre-write refusal (synthetic, pinned core + verification patch)
  armed: 4 cases refused inside the core before side effect
  unguarded mutation: 4 cases land without the hook
```

The nine `EVIDENCE` rows (4 armed, 4 unguarded, 1 positive control) carry `"bios": "SYNTHETIC"`.
