# README visual design / ビジュアル更新ルール

[← README](../README.md) · [Docs hub](README.md)

## Design goals

- The README is an **entry page**, not an evidence ledger. Users should understand scope, current status and where to go within one screen.
- SVG visuals are text-based, source-controlled, accessible diagrams—not executable code or measured screenshots.
- Keep project images copyright-clean: original abstract 8-bit inspired shapes; avoid proprietary brand art, logos, game screenshots and commercial media assets.
- All SVGs use dark navy, cyan, violet and muted coral for consistent contrast. Emoji are optional; meaning must also be legible as text.

## Asset ledger

| Asset | Purpose | Data sensitivity |
| --- | --- | --- |
| `assets/readme/hero.svg` | Brand/intent illustration | Evergreen |
| `assets/readme/overview.svg` | 18/81 DEV vs 0/81 ABI, blocked release | **Dated snapshot 2026-11-18** |
| `assets/readme/boot-path.svg` | CUE[0]/CUE[1] slot sequence and $4A halt | **Dated anonymized observation** |
| `assets/readme/architecture.svg` | Synthetic/public loader/real-core trust boundaries | Conceptual |
| `assets/readme/roadmap.svg` | Stage progression and current $4A priority | **Dated milestone** |

## Maintenance checklist

1. If the ledger changes, derive current DEV count/stub count from `spec/dev_implementations.json`; ABI from the contract ledger; release from actual gate data. Update the canonical English `README.md`, its Japanese translation `readme.jp.md`, `docs/status.md`, and `overview.svg` together.
2. If new commercial observations occur, cite their sanitized Issue entry and commit; update `boot-path.svg` and `g5-commercial-reobservation-2026-11-18.md` by **new dated addendum**, not by overwriting historic evidence.
3. Never use colors or badges alone to claim PASS: accompanying text must spell out the condition and limitation.
4. Dynamic CI shields must link to the matching workflow. Static experimental/ABI counters are not CI badges.
5. Verify each SVG loads from **both** README files in GitHub markdown, remains readable on mobile, and has accessible alt/title/description.
6. Keep English `README.md` the canonical/SSOT, `readme.jp.md` a Japanese translation with visible language-switch links on both. Documentation indices and project guides have the same English-primary arrangement; avoid diverging metrics and use shared image assets.
7. Public documentation changes must pass `tests/host/test_public_docs_current.py`, `tools/check_pages.py` and `make check` before merge; if CI cannot be run, state NOT_RUN.

**Graphic art generated for brainstorming outside the repository is not technical evidence.** Versioned SVGs are the canonical diagrams; private or commercial assets are never embedded.
