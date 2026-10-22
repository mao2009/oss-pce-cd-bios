# Verification-only patches for the pinned Geargrafx core

Upstream: https://github.com/drhelius/Geargrafx, pinned commit
`b49ae82a012eade566d72e9d61c9d95a5caa4da3` (archive sha256
`a80a3eae26a8efcd994350e84bbe73c975e406ced41f8f2404c88b00d4217f68`, see `tools/dependencies.json`).

## What is here

| File | Purpose | Issue |
| --- | --- | --- |
| `geargrafx-b49ae82-st-write-guard.patch` | Unified diff against `src/huc6280_opcodes.cpp` only. Adds a null-by-default guard hook that the ST0 ($03), ST1 ($13) and ST2 ($23) handlers call before `WriteFromCPU`. | #365 (parent #173) |
| `geargrafx-b49ae82-block-write-guard.patch` | Diff against `src/huc6280_opcodes.cpp` and `src/huc6280_opcodes_inline.h`. Adds null-by-default hooks: a pre-write guard for the TII/TIN/TIA/TDD/TAI byte stores, and a pre-push guard for the TransferStart A/X/Y stack pushes (pushes only). | #367 (parent #173), #459 |

## License and distribution

- **This directory is GPL-3.0, not MIT.** The patches modify GPL-3.0 source
  files (`src/huc6280_opcodes.cpp`, and `src/huc6280_opcodes_inline.h` for the block-write-guard patch), so the patch and any build of the patched core are GPL-3.0. The full GPL-3.0 text is in
  `LICENSE` in this directory (copied verbatim from the upstream tree, sha256
  `3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986`). The repository root
  `LICENSE` (MIT) does not cover this directory or the patched core.
- Upstream copyright is kept intact: `src/huc6280_opcodes.cpp` begins with the upstream header
  `Copyright (C) 2024  Ignacio Sanchez` under GPL-3.0. The patch hunks start at line 20, so the
  header lines (1-19) are not changed by the patch.
- Distribution conditions: whoever distributes this patch, or a binary built from the patched
  core, must license it under GPL-3.0, keep the upstream notices, and provide the corresponding
  source (the upstream commit above plus this patch). The patch adds no other third-party code.
- `geargrafx_st_write_guard.cpp` and `geargrafx_st_write_guard.py` contain no copied upstream
  source. The probe `#include`s upstream headers from the fetched core at build time. Neither the
  probe nor the driver is distributed as a binary, and no object built from them is tracked.
- The patch is **not vendored** in the MIT sources. The repository keeps only this diff. The core
  is fetched by `scripts/setup-geargrafx.sh` (`tools/setup.py`) into `TOOLS_DIR`, which must lie
  outside the repository (`tools/environment.py` refuses otherwise).
- `tests/integration/geargrafx_st_write_guard.py` applies the diff to a staged copy in a temporary
  directory, compiles only the patched `huc6280_opcodes` object, and links it into a local probe.
  Probe executables, objects and the patched copy are never committed, uploaded or published.
- The hook is for verification only. It must not be carried into a release build.
- The block-write-guard patch's TransferStart hook (#459) is **not** a production safety
  mechanism. A refused push leaves three never-written stack slots, and the core's unconditional
  TransferEnd pops read them: with the stack page mapped to $FF those reads hit VDC registers
  ($2102 = data read buffer LSB, $2103 = MSB, $2104 = status register, which the read clears),
  so A/X/Y are overwritten with the values read and the VDC status register is cleared. This was
  measured on the real core; see `docs/367-block-transfer-audit.md`. The hook prevents the VDC
  **write** side effect only and does not stop the instruction.

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

PASS on the pinned core under WSL was recorded at commit `8046ecf` (PR #430). That run used
earlier compile flags and working directory. Later commits `fc1d3fe` and `638baf0` changed both,
so **8046ecf does not cover the head driver**. The PASS for head `21e8b38` is pending (not run in
the authoring worktree, where WSL is refused). The cases recorded at 8046ecf were:

- Armed (refused inside the core before the side effect, 4 cases): `st0-armed`, `st1-armed`,
  `st2-armed`, `st0-after-tam-armed` (MPR0 changed to $FF by TAM first).
- Unguarded (patched build, null hook; the store lands, 4 cases): `st0-unguarded`,
  `st1-unguarded`, `st2-unguarded`, `st0-after-tam-unguarded`.
- Positive control: `pos-write-armed` (allowlisted RAM store lands).

This is a verification patch with a null-by-default hook. The unguarded runs use the same
patched build with the hook unset, not the stock object. Nothing here changes `BLOCKED_ABI`
or `release_eligible`.

No WSL run is recorded for `638baf0` or `21e8b38`. The head run and its EVIDENCE rows are
pending; this file will cite them only after they are run and committed.

The nine `EVIDENCE` rows (4 armed, 4 unguarded, 1 positive control) carry `"bios": "SYNTHETIC"`.
