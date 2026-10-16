# Agent instructions

These instructions apply to the entire repository. Follow [CONTRIBUTING.md](CONTRIBUTING.md), the provenance policy in `docs/provenance.md`, and the evidence boundaries in `docs/testing.md`. The project is **not yet a compatible System Card BIOS**; do not imply otherwise from diagnostic successes.

## Optional local ROM and CD media

- For tasks involving CD-media behavior, System Card reference comparisons, emulator diagnostics, or retail-game compatibility, check whether the repository-root `./rom/` directory contains relevant local inputs **before** reporting that real-media tests cannot be attempted.
- `./rom/` contains optional, developer-supplied **private reference inputs**, not redistributable project fixtures. It may contain game-disc images and/or separately obtained System Card firmware; do not conflate these, assume any particular file is available, or treat a game image as a BIOS.
- If a relevant diagnostic explicitly supports an input's format, use available local media for **applicable, non-destructive, opt-in local tests**. Inspect the diagnostic's arguments and supported formats first. The presence of `./rom/` alone does not mean there is an automatic real-ROM test command or a working boot path.
- Treat inputs as read-only. Keep any temporary derived files outside tracked paths and do not preserve or publish proprietary content. Never stage, commit, upload, attach to issues/PRs, or redistribute files under `./rom/` or extracted proprietary bytes. Do not copy their contents into an AI prompt, tool response, source fixture, or CI artifact.
- Do not require `./rom/` for ordinary builds, synthetic-fixture tests, CI, or release gates. All public tests must remain reproducible without private media.
- If media is absent, unsupported, or the necessary emulator/BIOS path is unimplemented, report `SKIP` or `BLOCKED` with the actual reason, **not** `PASS`. Never claim a test was run simply because a file was found.
- Share only appropriately sanitized diagnostic findings. Exclude copyrighted sector contents, audio, firmware bytes, game assets, user-private absolute paths, and saves from logs, PR descriptions, and artifacts. A fingerprint or hash is not permission to publish the input.

## Evidence reporting

Keep these categories distinct: host-model tests, synthetic-disc real-core diagnostics, local commercial-disc structural observations, callable System Card BIOS API execution, original homebrew-disc boot, and commercial-game compatibility. Specify which steps actually ran, their results and limitations, and the emulator/tool version when relevant. Geargrafx observations are not proof of physical hardware behavior.
