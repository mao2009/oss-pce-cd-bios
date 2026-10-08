# Licensed reuse and provenance policy

This project builds a redistributable PC Engine / TurboGrafx-CD System Card replacement. **Reuse proven OSS instead of reinventing it** when the code is technically suitable and its license permits the planned use. An original implementation, properly licensed upstream code, and properly licensed modified/ported code are all acceptable. The project must never distribute copied proprietary System Card firmware, commercial games, or their protected assets.

## Implementation preference (in order)

1. **Reuse unchanged:** select maintained, appropriate upstream OSS, including target-console runtimes and other OSS BIOS implementations, if its exact files and dependencies are cleared.
2. **Adapt or port:** change CPU syntax, ABI, registers, bank mapping, integration, or test harness as needed while preserving licensing and authorship requirements.
3. **Reference behavior and tests:** when direct inclusion is unsuitable, record the source and use public functional descriptions, independently validated contracts, and independently authored tests. A direct translation of protected code is not automatically an independent implementation.
4. **Implement from scratch:** when no suitable, permitted implementation exists, or an independent implementation is demonstrably simpler, safer, or more correct. Record the reason to avoid duplicate future effort.

Treat **technical suitability and legal clearance as separate gates**. A compatible license does not establish compatible functionality. Never claim any API works just because an upstream routine compiles.

## Non-negotiable exclusions

1. Do not commit/distribute proprietary NEC/Hudson/Konami or third-party BIOS dumps, extracted/translated firmware code, disassembly listings, copied tables/assets, commercial disc sectors, ROM graphics/audio, patches containing protected bytes, or private saves.
2. Do not silently incorporate proprietary-derived code from an emulator or article. Public visibility is not a license.
3. Keep private lawful reference firmware and commercial discs out of the repository, Issues, CI, release assets, and unsanitized logs.
4. Hardware facts and caller-observable interfaces may be investigated, but investigate whether a claimed contract is actually correct and whether its source is permissible. Do not claim a strict clean room where one did not exist.
5. No affiliation or endorsement by NEC, Hudson Soft, Konami, or their successors.

## License gate for each adopted file or substantial fragment

Before a file or substantial fragment is copied, translated, adapted, vendored, linked, embedded, or included in generated ROM output:

- Pin **repository URL, exact commit/tag, path and hash**, upstream author(s)/copyright holder(s), file-level license header, accompanying license text, and any third-party origins. A GitHub repository's summary license alone is insufficient.
- Identify all imported dependencies, templates, generated files, submodules and copied assets, each with their actual terms.
- Check permission for **copying, adaptation, compilation into a ROM, distribution of source and binary, and the project's current intended MIT-licensed distribution**. Keep any required copyright text, license copy, attribution, notices, source offers, and modification statements in source and release bundles.
- Record what was changed, what code was copied unchanged, build-generated content and whether the reused unit can actually satisfy the target HuC6280/System Card ABI.
- Do **not** relicense third-party material merely because this repository's own code is MIT. Keep license boundaries explicit.
- For copyleft material (GPL, LGPL, etc.), assess the **actual form of integration and conveyance**. GPL-only code should not be folded into a ROM represented as MIT-only. A change to the overall licensing strategy requires an explicit maintainer decision and complete obligation review. LGPL and exceptions are not automatic approval for static firmware integration.
- Lack of an explicit license is **not** permission to copy. Resolve ambiguity, obtain separate permission from an authorized rights holder, or use behavior-level reference and independently created code instead.
- Require human-reviewed evidence and disposition **APPROVED / PENDING / RESTRICTED**, with approver and decision date. PENDING/RESTRICTED materials cannot enter distributed firmware, CI binaries or releases. CI scanners cannot establish copyright ownership or licensing validity by themselves.

## Reference-only use

- Record public sources, exact revision, relevant contract/observations, confidence and any known disagreement; validate with synthetic fixtures and the real Geargrafx emulator.
- Do not treat an emulator's HLE shortcut as hardware proof or translate third-party code line-by-line to evade a license.
- Reverse-engineering-based descriptions may contain uncertain or restricted details; review provenance by claim. Preserve disagreement rather than turning guesses into normative requirements.
- A test-only dependency may remain external to the distributable ROM; still honor its license when the test harness, binary, or artifacts are redistributed.

## Required per-source audit record

- Component/API and planned usage: `DIRECT` / `ADAPTED` / `REFERENCE_ONLY` / `REJECTED`.
- Upstream repo URL, commit SHA/tag, file path and integrity hash; authors, copyright holders and origins.
- Exact file-level license and obligations; dependencies/embedded materials; original license files/NOTICE links.
- Fit assessment: CPU, assembler syntax, bank mapping, ABI, behavior, timing, memory, error cases.
- Legal/technical disposition: `APPROVED` / `PENDING` / `RESTRICTED`, rationale, reviewer and date.
- Modifications, attribution/NOTICE locations, release/source obligations and related Issue/PR.
- Validation evidence: host tests, Geargrafx real-core traces, negative tests, and known limitations.

See [reuse audit inventory](reuse-inventory.md) for initial candidates. An initial inventory entry is **not** clearance to integrate code.

## PR and release review checklist

- [ ] An existing-OSS search was performed for this capability; reuse, adaptation or new implementation choice is documented.
- [ ] Every incorporated upstream file/fragment has an APPROVED, pin-specific audit record; generated ROM content is included in the analysis.
- [ ] Source and release preserve license/copyright/NOTICE and modification records; original project MIT LICENSE remains accurate about **only the parts owned/licensed accordingly**.
- [ ] No proprietary firmware/game data, copied protected assets, or unlicensed third-party content appears in sources, diffs, generated binaries, tests or artifacts.
- [ ] External emulator/test dependencies are not silently linked into shipped ROM.
- [ ] The accepted behavior is actually measured; unverified behavior is labeled NOT_RUN/BLOCKED, not PASS.

A human reviewer must resolve uncertain origin or permission **before merging**. This policy is not a legal warranty or substitute for specialist advice in disputed cases.
