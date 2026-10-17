# GitHub Pages community site

Sources: [English](../site/index.html), [Japanese](../site/ja/index.html), [shared CSS](../site/assets/styles.css).

The site is static HTML/CSS and an original SVG mark. It includes no client-side JavaScript, tracking, external fonts or proprietary firmware/CD content.

## Publish

1. Review and merge the dedicated Pages PR into main, once checks pass. The Pages workflow validates pull requests; it does not deploy them.
2. In repository **Settings → Pages → Build and deployment**, a maintainer must choose **GitHub Actions** as the Source. This is repository state and cannot be assumed to be enabled by the PR.
3. The workflow in .github/workflows/pages.yml publishes only the site directory, following a relevant main push or manual workflow_dispatch on main. It gives deployment permissions only to the main-only deploy job.
4. Restrict the github-pages environment to the protected main branch and verify the initial deployment run.

Expected address **after deployment succeeds**, not a claim that it is live now: https://mao2009.github.io/oss-pce-cd-bios/ (Japanese: https://mao2009.github.io/oss-pce-cd-bios/ja/).

## Check locally

Run:

    python3 tools/check_pages.py

This checks both languages, section anchors, local assets, canonical URLs, reciprocal hreflang, Open Graph metadata, JSON-LD, sitemap entries, factual AI summary and evidence disclaimers using only Python's standard library. In addition, open both pages in a browser and inspect desktop and narrow mobile layouts, keyboard focus, contrast, reduced-motion mode and screen-reader reading order. Automated checks cannot replace a visual accessibility review.

## Editorial conventions

- The figures on the pages are a **dated historical snapshot**, based on PR #174 and 2026-10-17: 1 of 81 candidate API slots with a callable development-only EX_GETVER implementation, 80 remaining nonreturning stubs and 765 host tests passing as reported for that merge.
- These are not percentages of commercial-game compatibility, current test counts, approved reference-BIOS ABI or firmware release readiness. Neither signed diagnostic evidence nor a CD sector-read experiment is a retail compatibility test.
- When implementation changes, update both pages together with the relevant PR, date and evidence. Keep the main README and docs/status.md aligned. PR #175 is a separate documentation effort.
- Do not insert unverified contributor identities, private ROMs, game data, disassembly, local paths, uploaded media, or other copyrighted/proprietary source material. Use actual Issues and the existing CONTRIBUTING.md and provenance policies as the route into collaboration.

## Search and AI discoverability

- English canonical: https://mao2009.github.io/oss-pce-cd-bios/
- Japanese canonical: https://mao2009.github.io/oss-pce-cd-bios/ja/
- Both pages include reciprocal en/ja/x-default hreflang, descriptive titles/meta descriptions, Open Graph metadata and a strictly factual SoftwareSourceCode/WebSite/WebPage JSON-LD graph. The structured data never claims a completed release or verified commercial compatibility.
- site/sitemap.xml lists **both** canonical language URLs and reciprocal language alternates. After successful deployment, add the **URL-prefix property** for the deployed project URL to Google Search Console (complete verification as instructed by the service), and submit https://mao2009.github.io/oss-pce-cd-bios/sitemap.xml there. Optionally register with Bing Webmaster Tools. Registration and indexing are NOT automatically done by a PR.
- site/llms.txt is a **voluntary, dated context document** linking to source-of-truth GitHub references for LLM tools that choose to fetch it. There is no broadly guaranteed llms.txt crawler behavior, and neither Google Search nor AI Overviews requires such a file.
- The public bilingual FAQ contains user-visible, evidence-linked explanations of what is implemented and what remains untested. Human-readable HTML remains primary; keep structured data aligned with those visible facts.
- Do not add a project-root robots.txt while hosted below a GitHub Pages project path and claim it controls crawlers for that project: the Robots Exclusion Protocol is scoped to the **origin root** (https://mao2009.github.io/robots.txt), not the /oss-pce-cd-bios/ subdirectory. Standard public GitHub Pages crawl policy applies. This PR does not control the origin-root robots settings.
- Update both languages, sitemap URLs, JSON-LD and optional AI summary together if the canonical domain, actual release eligibility, target scope or tested progress changes. Avoid any private ROM/ISO data in the site or metadata.
- Search ranking, search indexing, AI retrieval, answer citations and when they happen cannot be guaranteed by SEO markup. Measure real visibility only after deployment and verified Search Console access.

Primary guidance: [Google Search Central — AI features and your website](https://developers.google.com/search/docs/appearance/ai-features), [multilingual pages](https://developers.google.com/search/docs/specialty/international/localized-versions), [canonicalization](https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls).
