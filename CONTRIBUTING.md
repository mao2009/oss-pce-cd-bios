# Contributing

This repository has a working development bootstrap and original HuCARD fixture.
BIOS APIs remain unimplemented. Open an Issue before implementing BIOS APIs where
the format, license or behavior is uncertain; check existing API Issues first.

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

## Local development and pull requests

Create a feature branch from current `main`; preserve other contributors' changes.
Run `make setup`, `make build`, `make test`, `make check`, `make clean`, then
`make build`. Also compare `make build MODE=release` with the debug ROM for fixture
changes. Generated `build/` contents must remain untracked. External source trees
and their licenses live in the tool cache, never in BIOS source or artifacts.

Submit a PR targeting `main` with linked Issues, actual command exit codes,
ROM size/hash, Geargrafx revision and evidence scope, and explicit remaining work.
CI repeats the same commands on Ubuntu 24.04. Do not automatically merge PRs or
present a successful HuCARD smoke test as a System Card or commercial-game result.
Details: [building](docs/building.md), [testing](docs/testing.md),
[Geargrafx](docs/emulator-geargrafx.md), [toolchain](docs/toolchain.md).
