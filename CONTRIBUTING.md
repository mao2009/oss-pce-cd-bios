# Contributing

This repository is in its planning phase. Open an Issue before implementing BIOS APIs where the format, license or behavior is uncertain.

## Expectations

- Independently authored code and fixtures only. No firmware ROM uploads, game discs, copied/disassembled proprietary code, assets or derived binary patches.
- Every hardware/API claim needs a source, confidence status and provenance assessment.
- Prefer narrow pull requests. Report what was actually run: assembler build, structure check, real Geargrafx core, independent emulator, synthetic disc or commercial game.
- Separate PASS, FAIL, SKIP and BLOCKED. Missing binaries or private fixtures are not passing tests.
- Document supported region and target environment for every new contract.
- Never claim a software ROM is working solely because a CI docs check passes.
- Any external code incorporation requires explicit license and authorship review.
- Keep proprietary paths, ROM hashes that should remain private, copyrighted sectors and local user data out of submitted logs.

## Suggested workflow

1. Pick or create a scoped Issue.
2. Establish source and contract, or mark as a research hypothesis.
3. Use an isolated branch; implement the smallest independent functionality.
4. Create synthetic fixture and negative/boundary tests.
5. Run pinned build and supported emulator gate(s).
6. Submit PR with limitations, provenance notes, exact version/hashes of distributable artifacts, and evidence.
7. Don't merge until disputed source provenance is resolved.

Reference material: [architecture](docs/architecture.md), [spec inventory](docs/specification.md), [provenance](docs/provenance.md), [compatibility](docs/compatibility.md).
