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

## Automated provenance guard (Issue #8)

`tools/provenance_guard.py` scans the **changed content** (tracked files differing from the PR base, or all tracked files without a base), not just file extensions. It runs as the first step of the `verify` job in `.github/workflows/ci.yml`.

- **Always rejected:** content whose SHA-256 is listed in `provenance/denylist.sha256`; ISO9660 volume-descriptor and CD-ROM raw-sector-sync signatures, whatever the file name.
- **Rejected unless hash-pinned in `provenance/allowlist.tsv`:** prohibited extensions (`.pce .bin .cue .iso .img .chd .wav` and similar), unlabelled binaries, third-party paths (`vendor/`, `third_party/`, `.gitmodules`, ...), and third-party license markers in code files. This is default-deny for unclear third-party code.
- **Approval gate:** changing `provenance/allowlist.tsv`, `provenance/denylist.sha256` or `provenance/approvers.txt` requires a verified maintainer approval. The label alone is not enough. The guard queries the PR's issue timeline (`gh api`) and grants approval only when the newest `provenance-approved` label event was applied (not removed afterwards) by a login listed in `provenance/approvers.txt` at the **PR base**, that login is not the PR author, and its repository role is `admin` or `maintain`. An empty list at the base is closed (fail closed). Deleting a gated file, or renaming one away from its path (renames are detected as delete plus add), counts as a change to it and needs the same approval. Deleted files are never opened; a rename into a gated path is gated as an addition. The approval unlocks list edits only; it never overrides the denylist or signature rules and is not a legal clearance.
- **Bootstrap (no approver list at the base yet):** the list added by the PR is trusted instead, but only non-author repository admins listed in it count, and the `provenance-approved` label must come from one of them. The PR author cannot add itself. Once that list is merged, the base list governs and the bootstrap path is closed. Approvers are never hard-coded in the guard. **Status: BLOCKED.** The initial approver logins are a repository-owner decision, and the `provenance-approved` label does not exist yet. Until a second admin or maintainer is added, no PR can satisfy the bootstrap rule, so list edits stay blocked.
- **Residual risk:** the approval check runs from the PR merge commit, so a PR that edits the guard or `ci.yml` is controlled only by human review. Approval is not re-bound to later commits; re-check the label and list diff after any new push.
- Tests: `python3 -m unittest discover -s tests/host -p 'test_provenance_guard.py' -v`. Prohibited samples are generated in memory from short synthetic markers; no firmware, disc or ROM data is committed.

**Limitations.** A signature scanner is a tripwire, not a guarantee against infringement. It cannot detect paraphrased, translated, or re-encoded material; it cannot establish ownership or license validity; and a PR that edits the guard itself is controlled only by human review. Human review of the audit record above remains mandatory before merge.

## Artifact upload guard (Issue #227, PR #286)

`tools/artifact_guard.py` checks the files that `actions/upload-artifact` would publish from CI. It is a separate layer from the committed-content guard above.

- **Design decision: `.pce` is not refused by extension.** Original HuCARD images and DEV diagnostic ROMs generated by this project are legitimate artifacts. The artifact guard applies the content signature checks (ELF, Mach-O, PE/NE/LE, ar, archive signatures) to `.pce` files as well.
- **A passing `.pce` is not proof of provenance.** Signature checks do not establish that a `.pce` contains no GPL-derived code, that it was built from the stated source, or that a rebuild reproduces it. Origin, build procedure and reproducibility are **unresolved** and tracked in Issue #335. Until that is closed, every uploaded `.pce` needs human review of how it was produced.
- **Committed `.pce` files are unaffected.** The committed-content guard above still rejects prohibited extensions unless hash-pinned. The artifact guard does not change that rule.
- **Two jobs.** Build code and publication are in different jobs. A build job (it runs pull-request code) hands its outputs over as an `intermediate-*` artifact and never runs the guard. A guard job `needs:` that build job, downloads the intermediate artifact into `.intake` (inside the workspace), and runs only trusted steps: the checkout of `main` into `.trusted-main` (inside the workspace) (sparse: `tools/artifact_guard.py` only), the `python3` resolve, a checkout of the workflow files for the placement check, the download, stage, verify, and the upload. No build step runs in a guard job. The guard runs from the `main` copy; a change to the guard takes effect only after it is merged. The placement check rejects a job that mixes a build with a publication, a guard job that runs any other step, a guard job without `needs:`, and an intermediate artifact nothing consumes.
- **Staging.** The uploaded bytes are copies in `.artifact-stage/` made by the stage step, with sha256 recorded in a manifest. The verify step, immediately before the upload, recomputes the hashes and refuses a change, an extra file, a missing file, or a symlink. Upload paths must begin with `.artifact-stage/`.
- **Residual limits.** The hand-off is hash-checked by the guard job only after the artifact is downloaded: the guard job does not trust the build job's copy, and what it checks is what it publishes. The guard job runs on its own runner, and its checkouts are inside the workspace: `.trusted-main` holds only `tools/artifact_guard.py` from `main`, and it is created after the PR workflow checkout (a later checkout cleans the workspace, which would remove it). `.intake` holds only the downloaded intermediate artifact, never PR build output that was not handed over. This relies on GitHub-hosted runners starting each job on a fresh VM (a self-hosted runner would break it), so a build cannot alter the guard's files or the `python3` resolved in the guard job. Still open: the intermediate artifact is readable by repository readers until its retention ends (one day), and an unverified intermediate is visible while the workflow runs. The publication itself is attested only by the content checks; provenance is Issue #335.
- **Wildcards and broad patterns.** Current workflows use specific paths only, and no upload pattern is `*` or `**`. A new pattern that matches a whole directory, or that uses a wildcard at the root, uploads whatever it matches; the content check is signature-only and does not judge what kind of data a file contains. **Adding such a pattern needs an explicit review** of what it can match. A test (`PathPolicyTests`) fails if a repository upload uses a root wildcard, so the addition is at least visible.
- **Unlisted routes are refused (fail closed, Issue #337).** Every `uses:` key in a workflow or composite action must name an action in `KNOWN_ACTIONS` (`tools/artifact_guard.py`) with a ref, in any spelling (quoted key, flow mapping, folded or next-line value). Any other action, including a release action that is not an upload-artifact step, is refused by both the upload layer and `--check-placement`. `run:` text that calls GitHub Releases or uploads endpoints (`gh api .../releases`, `uploads.github.com`, a `/releases` path segment even on a shell continuation line) is refused as well. A complex key (`? uses`) or a double-quoted key with an escape (`"uses"`) is refused too, because the line reader cannot see its text. Each allowlisted action must be pinned to one full 40-digit commit SHA; a tag or a short SHA is refused, in both layers, including on an upload-artifact step (Issue #349). The SHA is trusted as the code at that commit; the guard does not verify its content. A publishing action (`deploy-pages`, `upload-pages-artifact`, `attest-build-provenance`) in a file with no upload-artifact step is refused by `--check-placement` (Issue #349). The cost: a build-only action that a workflow newly needs is refused until a reviewer adds it to the list with a SHA.
- **Outside the guarantee (not detected; not claimed as blocked).** The guard recognises the named routes above and nothing else. It does not see: `curl`/`wget` or any other network tool that sends a file to a host (including a hand-written `curl -F` upload); `git push` of a tag or branch; `gh gist` create; `gh api` writes (`-X PUT`, `-X POST`, `-X PATCH`) to an endpoint that is not a releases path; GraphQL mutations such as `createRelease` sent through `gh api graphql`; and any publication through a command whose text does not match a name in this list. Those routes need a human review of the `run:` text, not a guarantee from this guard.
- **Attestation publication scope** (Issue #349, owner decision). The `attest-cdread` job in `.github/workflows/api-source.yml` attests exactly two files, and no others:
  - `evidence/cdread-probe/cdread-evidence.json`: the synthetic diagnostic result JSON.
  - `evidence/cdread-probe/cdread-probe-not-bios.pce`: the synthetic diagnostic ROM.

  Generation and rights, as checked from the repository sources (2026-10-20):
  - The ROM is assembled by cc65 (`ca65`/`ld65`, pinned) only from `src/` and `src/bank0.cfg`, on top of the dev-only API build that `build_rom.py` derives from repository sources. It has no external BIOS, commercial ROM, or disc input, and no cc65 runtime library is linked.
  - The disc used by the run is generated by `tools/make_cd_image.py` (`OSSPCE-CD-TEST-V1` payload, deterministic SHA-256 stream). The JSON records hashes, phase/outcome statuses and the self-declared `"proprietary_content": "NONE"`.
  - Geargrafx is a third-party emulator used only to run the test. Its source is not in these files; only its revision and archive hash are recorded.
  - **Limit:** `"proprietary_content": "NONE"` is a self-declared field of the test script and is not machine-verified. The workflow runs no repository code, so the attest job cannot re-check the generation source.

  Residual risk, stated plainly: attestation publishes the **file names and sha256 digests** of both files. The ROM name reveals the "not-bios" status, and the digests let anyone confirm a copy of these exact files. This is accepted for the two synthetic files only. If any file in the list lacks a verified origin and rights, it must be removed from `subject-path`; that is a change under review, not something the attest job decides.
  - Out of this two-file scope: `attest-cdread-verdict` (`verdict/cdread-verdict.json`, push to main only) also attests a file name and digest. It is not covered by this decision and needs its own owner decision.
  - The attestation does not make the files distributable under any licence; distribution conditions stay with Issue #335 and the policy above.
- **Pages action pins (Issue #349).** `pages.yml` pins `configure-pages`, `upload-pages-artifact` and `deploy-pages` to the commit SHAs resolved with `gh api repos/actions/<name>/commits/<tag>` on 2026-10-20. Updating one needs a new resolution and review; a SHA is not re-checked against its tag.
- **Workflow changes are mandatory review.** Changes under `.github/workflows/` change what runs and must be reviewed by a maintainer. The guard cannot detect a workflow edit that removes its own steps; branch protection (set by the owner) is required for that.
- Tests: `python3 -B -m unittest discover -s tests/host -p 'test_artifact_guard.py' -v`.
