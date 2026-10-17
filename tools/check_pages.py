#!/usr/bin/env python3
"""Validate the bilingual, search-readable GitHub Pages site using only stdlib."""
import json
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree

SITE = (Path(__file__).resolve().parents[1] / "site").resolve()
BASE = "https://mao2009.github.io/oss-pce-cd-bios/"
REPO = "https://github.com/mao2009/oss-pce-cd-bios"
URLS = {
    (SITE / "index.html").resolve(): (BASE, "en"),
    (SITE / "ja" / "index.html").resolve(): (BASE + "ja/", "ja"),
}
LOCALES = {"en": BASE, "ja": BASE + "ja/", "x-default": BASE}


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids, self.links, self.alternates = set(), [], {}
        self.meta, self.canonicals, self.jsonld = {}, [], []
        self.mains = self.h1s = self.script_count = 0
        self.lang = None
        self.in_script = False
        self.script_parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.mains += tag == "main"
        self.h1s += tag == "h1"
        if tag == "html":
            self.lang = attrs.get("lang")
        if attrs.get("id"):
            if attrs["id"] in self.ids:
                raise ValueError("duplicate ID: " + attrs["id"])
            self.ids.add(attrs["id"])
        if tag == "meta":
            key = attrs.get("name") or attrs.get("property")
            if key:
                if key in self.meta:
                    raise ValueError("duplicate meta: " + key)
                self.meta[key] = attrs.get("content")
        if tag == "link":
            if attrs.get("rel") == "canonical":
                self.canonicals.append(attrs.get("href"))
            if attrs.get("rel") == "alternate" and attrs.get("hreflang"):
                lang = attrs["hreflang"]
                if lang in self.alternates:
                    raise ValueError("duplicate hreflang: " + lang)
                self.alternates[lang] = attrs.get("href")
        if tag in ("a", "link") and attrs.get("href"):
            self.links.append(attrs["href"])
        if tag == "img" and attrs.get("src"):
            self.links.append(attrs["src"])
        if tag == "script":
            self.script_count += 1
            if attrs.get("type") != "application/ld+json" or attrs.get("src"):
                raise ValueError("only inline JSON-LD metadata is permitted")
            if self.in_script:
                raise ValueError("nested script")
            self.in_script, self.script_parts = True, []

    def handle_data(self, data):
        if self.in_script:
            self.script_parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script":
            if not self.in_script:
                raise ValueError("unexpected script end tag")
            self.jsonld.append(json.loads("".join(self.script_parts)))
            self.in_script = False


def validate_page(page, url, language):
    source = page.read_text(encoding="utf-8")
    doc = Page()
    doc.feed(source)
    doc.close()
    if doc.lang != language or doc.mains != 1 or doc.h1s != 1 or doc.in_script:
        raise ValueError(f"{page}: invalid language, landmarks, h1 or script")
    if not {"content", "mission", "progress", "contribute", "faq"} <= doc.ids:
        raise ValueError(f"{page}: a required section or skip target is missing")
    if doc.canonicals != [url] or doc.alternates != LOCALES:
        raise ValueError(f"{page}: canonical or reciprocal hreflang mismatch")
    if doc.meta.get("og:url") != url or doc.meta.get("og:type") != "website":
        raise ValueError(f"{page}: Open Graph URL/type mismatch")
    if doc.meta.get("robots") != "index, follow":
        raise ValueError(f"{page}: intended indexing directive missing")
    if not doc.meta.get("description") or not doc.meta.get("og:description"):
        raise ValueError(f"{page}: description metadata missing")
    for fact in ("EX_GETVER", "765", "#174", "UNVERIFIED", "BLOCKED"):
        if fact not in source:
            raise ValueError(f"{page}: missing dated evidence {fact}")
    if doc.script_count != 1 or len(doc.jsonld) != 1:
        raise ValueError(f"{page}: exactly one JSON-LD document required")
    data = doc.jsonld[0]
    if data.get("@context") != "https://schema.org":
        raise ValueError(f"{page}: invalid JSON-LD context")
    graph = data.get("@graph")
    if not isinstance(graph, list):
        raise ValueError(f"{page}: JSON-LD graph required")
    nodes = {item.get("@type"): item for item in graph if isinstance(item, dict)}
    if set(nodes) != {"WebSite", "WebPage", "SoftwareSourceCode"} or len(graph) != 3:
        raise ValueError(f"{page}: invalid JSON-LD node types")
    if nodes["WebSite"].get("url") != BASE:
        raise ValueError(f"{page}: WebSite URL mismatch")
    if nodes["WebPage"].get("url") != url or nodes["WebPage"].get("inLanguage") != language:
        raise ValueError(f"{page}: WebPage URL/language mismatch")
    code = nodes["SoftwareSourceCode"]
    if code.get("codeRepository") != REPO or code.get("url") != REPO:
        raise ValueError(f"{page}: SoftwareSourceCode repository URL mismatch")
    if code.get("license") != REPO + "/blob/main/LICENSE":
        raise ValueError(f"{page}: license link mismatch")
    if "unverified" not in code.get("description", "").lower():
        raise ValueError(f"{page}: structured data must not imply verified compatibility")
    return doc


def check_links(pages):
    for page, doc in pages.items():
        for ref in doc.links:
            parts = urlsplit(ref)
            if parts.scheme in ("https", "mailto"):
                continue
            if parts.scheme or parts.netloc or ref.startswith("//"):
                raise ValueError(f"{page}: unsupported link scheme {ref}")
            target = (page.parent / unquote(parts.path)).resolve() if parts.path else page
            if not target.is_relative_to(SITE):
                raise ValueError(f"{page}: link escapes site: {ref}")
            if target.is_dir():
                target /= "index.html"
            if not target.is_file():
                raise ValueError(f"{page}: missing linked file: {ref}")
            if parts.fragment and target.suffix == ".html":
                if target not in pages or unquote(parts.fragment) not in pages[target].ids:
                    raise ValueError(f"{page}: missing linked anchor: {ref}")


def check_sitemap():
    xml = ElementTree.parse(SITE / "sitemap.xml").getroot()
    ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
    alt_ns = "{http://www.w3.org/1999/xhtml}"
    if xml.tag != ns + "urlset":
        raise ValueError("invalid sitemap namespace/root")
    entries = xml.findall(ns + "url")
    if len(entries) != len(URLS):
        raise ValueError("sitemap must contain both locales only")
    urls = set()
    for entry in entries:
        loc = entry.findtext(ns + "loc")
        urls.add(loc)
        alts = {}
        for link in entry.findall(alt_ns + "link"):
            if link.get("rel") != "alternate":
                raise ValueError("invalid sitemap alternate relation")
            language = link.get("hreflang")
            if language in alts:
                raise ValueError("duplicate sitemap language")
            alts[language] = link.get("href")
        if alts != LOCALES:
            raise ValueError("sitemap hreflang links mismatch")
    if urls != {url for url, _ in URLS.values()}:
        raise ValueError("sitemap loc URLs do not match canonicals")


def main():
    pages = {page: validate_page(page, *values) for page, values in URLS.items()}
    check_links(pages)
    check_sitemap()
    llms = (SITE / "llms.txt").read_text(encoding="utf-8")
    for fact in ("# oss-pce-cd-bios", BASE, REPO, "EX_GETVER", "UNVERIFIED", "765", "not a commercially compatible BIOS"):
        if fact not in llms:
            raise ValueError("optional AI summary missing: " + fact)
    css = (SITE / "assets" / "styles.css").read_text(encoding="utf-8")
    for rule in ("@media (max-width:", "prefers-reduced-motion", ":focus-visible"):
        if rule not in css:
            raise ValueError("missing responsive/accessibility CSS: " + rule)
    ElementTree.parse(SITE / "assets" / "mark.svg")
    print("PASS Pages: 2 languages, canonical/hreflang, JSON-LD, XML sitemap, AI summary, links, sections and CSS")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError, ElementTree.ParseError) as error:
        sys.exit("FAIL Pages: " + str(error))
