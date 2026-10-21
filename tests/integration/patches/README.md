# Verification-only patches for the pinned Geargrafx core

Upstream: https://github.com/drhelius/Geargrafx, pinned commit
`b49ae82a012eade566d72e9d61c9d95a5caa4da3` (archive sha256
`a80a3eae26a8efcd994350e84bbe73c975e406ced41f8f2404c88b00d4217f68`, see `tools/dependencies.json`).

## What is here

| File | Purpose | Issue |
| --- | --- | --- |
| `geargrafx-b49ae82-st-write-guard.patch` | Unified diff against `src/huc6280_opcodes.cpp` only. Adds a null-by-default guard hook that the ST0 ($03), ST1 ($13) and ST2 ($23) handlers call before `WriteFromCPU`. | #365 (parent #173) |

## License and distribution

- Geargrafx is GPL-3.0. The patch is a modification of one GPL-3.0 source file.
  The file's upstream license header is untouched; the patch adds no new third-party code.
- The patch is **not vendored**. The repository keeps only this diff. The core is fetched
  by `scripts/setup-geargrafx.sh` into the external tool cache (outside the repository), and
  `tests/integration/geargrafx_st_write_guard.py` applies the diff to a staged copy in a
  temporary directory, compiles only that one object, and links it into a local probe.
- Local probe executables and linked objects are never distributed or committed.
- The hook is for verification only. It must not be carried into a release build.

## Applying by hand (for review)

```sh
# from a clean copy of the pinned tree, e.g. the tool cache source
patch -p1 --dry-run -i tests/integration/patches/geargrafx-b49ae82-st-write-guard.patch
```

The patch was dry-run against the pinned upstream file and reproduces the patched file
byte-for-byte when applied to the pristine upstream copy.

## Status

NOT_RUN. The core build and the real-core probe need the Linux toolchain (see
`docs/emulator-geargrafx.md`). The patch and probe were syntax-checked only (MinGW
`g++ -fsyntax-only`). Nothing here changes `BLOCKED_ABI` or `release_eligible`.
