# Independent implementation and provenance rules

The repository may distribute **only independently written source, original assets and tests** with demonstrably permissible provenance. It is not a store for proprietary System Card firmware, software or sample game data.

1. No NEC/Hudson/Konami or other proprietary BIOS dumps, patches containing their bytes, ROM graphics, audio, disassembly or copied source in commits, Issues, CI and release assets.
2. Prefer vendor-published CPU information, independent hardware experiments, original ROM/CD fixtures, and public high-level documentation with identified provenance.
3. Publicly visible technical notes may be derived from proprietary disassembly; assess each *claim* before using it as normative implementation detail.
4. Emulator code is for external verification unless its exact version/license and origin are independently approved for any reuse. Do not blindly copy it into the MIT BIOS. In particular do not presume Geargrafx's code is MIT-compatible.
5. Keep reference firmware and commercial discs out of the repo, CI and logs. Private lawful use does not grant redistribution rights.
6. Document the external **behavioral contract** (inputs, outputs, side effects, timing), not a proprietary ROM's internal expression. For any contributors who have privately studied firmware, don't claim a strict clean-room separation that did not happen.
7. A human must review origin, permissions and independent authorship for external contributions or implementation details of uncertain origin; block merging until resolved.
8. Research or publisher responses, if ever referenced, must not be framed as legal authorization or proof of legality. Include scope, assumptions, limitations and publication permissions.
9. The project is not affiliated with or endorsed by NEC, Hudson Soft, Konami or their successors.

## Per-source fields

- URL, author, exact revision/date, public/license conditions
- Claim taken, derivation method if known, rights/permission analysis
- Independent reproduction or fixture, technical confidence
- Reviewer, disposition APPROVED / PENDING / RESTRICTED
- Whether proprietary content was inspected and why adoption does not reproduce expression

## Implementation review checklist

- Does the ROM contain any bytes, tables, assets or program expressions copied from proprietary firmware?
- Is every API address/selector evidenced or explicitly hypothetical?
- Can the behavior be justified through approved references or independent tests?
- Could logs disclose proprietary game sectors, private firmware, saves or third-party copyrighted data?
- Are any external emulator dependencies kept out of the distributable firmware?
- Is an uncertain source being silently treated as confirmed?

This is a project policy, not a legal warranty.
