# Contributing

This repository develops a redistributable replacement BIOS. Open an Issue before implementing BIOS APIs where format, license or behavior is uncertain.

## Reuse first

Avoid reinventing existing OSS. Before new implementations, search existing OSS BIOS implementations, PC Engine runtime libraries, emulators/HLE, and cross-console helpers. Prefer in order:
1. Reuse permitted code unchanged.
2. Adapt or port permitted code to HuC6280/System Card ABI.
3. Reuse researched contracts and independent tests as reference where copying is not appropriate.
4. Implement original code where the above are unsuitable; record why.

**Every direct or modified code inclusion requires a pin-specific, file-level license and provenance audit approved by a human before merging.** Record all copyrights, license/NOTICE obligations, dependencies, modifications and distribution requirements. GPL-licensed code cannot silently become MIT code; LGPL and other licenses demand an integration-specific review. The lack of a license is not permission to use code.

See [provenance and reuse policy](docs/provenance.md) and [initial candidate inventory](docs/reuse-inventory.md). A candidate in that list is not yet cleared.

## Expectations

- Only original and license-cleared code/assets/fixtures may enter a distributable artifact. No proprietary firmware ROM uploads, game discs, copied/disassembled proprietary code, artwork, or derived binary patches.
- Every hardware/API claim needs a source, confidence status and provenance assessment.
- Prefer narrow pull requests. Report what was actually run: assembler build, structure check, real Geargrafx core, independent emulator, synthetic disc or commercial game.
- Separate PASS, FAIL, SKIP and BLOCKED. Missing binaries or private fixtures are not passing tests.
- Document supported region and target environment for every new contract.
- Never claim a software ROM is working solely because a CI docs check passes.
- Keep proprietary paths, copyrighted sectors, private firmware, saves and local user data out of submitted logs.

## Suggested workflow

1. Pick or create a scoped Issue; identify usable OSS candidates and assess technical fit.
2. Audit exact upstream file revisions, rights, permissions, dependencies and notices; obtain human disposition for inclusion.
3. Establish the observable API contract, or mark as a research hypothesis.
4. Use an isolated branch, reuse/adapt approved code or write the smallest necessary new code; retain provenance and modification markers.
5. Create synthetic fixtures and negative/boundary tests.
6. Run pinned builds, supported emulator gate(s), and license/provenance checks.
7. Submit PR with source revisions, licensing, limitations, change history, exact ROM hashes and test evidence.
8. Don't merge if license or provenance is unresolved.

Reference material: [architecture](docs/architecture.md), [spec inventory](docs/specification.md), [provenance](docs/provenance.md), [compatibility](docs/compatibility.md).
