# Contributing

This repository has a working development bootstrap and original HuCARD fixture.
BIOS APIs remain unimplemented. Open an Issue before implementing BIOS APIs where
the format, license or behavior is uncertain; check existing API Issues first.

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

## Optional private ROM/CD inputs for local diagnostics

Contributors may place their own lawfully obtained **local reference** inputs in the repository-root `./rom/` directory:

```sh
mkdir -p rom
```

This directory can hold game-disc images and, if separately available, reference System Card firmware. They are **different kinds of inputs**: a CD game image is not a BIOS dump. Root `/rom/` is already excluded by `.gitignore`; never stage, commit, upload, share or redistribute these inputs or proprietary data extracted from them.

- Private inputs are **optional** and are neither downloaded by `make setup` nor required for the standard build, synthetic tests, CI or release checks.
- There is no universal filename or automatic `rom/` test runner. Check each diagnostic's documented options and actual format support before running it. CUE/BIN and CHD are examples of possible local disc-image formats, **not** a promise that every tool can read them. Preserve track/index/audio layout when investigating mixed-mode CDs.
- When a suitable local diagnostic exists, run it only as an explicit, non-destructive local test. If it does not support the input or the firmware path is not implemented, record `SKIP` or `BLOCKED`, not `PASS`; do not invent a test target.
- Distinguish private disc-structure observations from emulator guest READ(6) diagnostics, callable System Card API behavior and actual game boot. A recognized file or valid sector does not establish BIOS compatibility.
- Publish only sanitized test evidence: no ROM/sector/audio/firmware bytes, game assets, saves, proprietary binary extracts or local private absolute paths in issues, PRs, CI artifacts or logs.

See [AGENTS.md](AGENTS.md) for the local-media rules applied to development agents.

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

For API table or diagnostic layout changes, also run `make dev-test`,
`make dev-rom MODE=release`, compare diagnostic debug/release bytes, and verify
that `make dev-release-gate` exits nonzero. An override file alone does not
constitute an implemented, approved or release-eligible API.
