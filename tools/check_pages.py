#!/usr/bin/env python3
"""Check the static bilingual website without external packages."""
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit
import sys
from xml.etree import ElementTree

SITE = (Path(__file__).resolve().parents[1] / "site").resolve()
PAGES = (SITE / "index.html", SITE / "ja" / "index.html")


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids, self.links = set(), []
        self.mains = self.h1s = self.scripts = 0
        self.lang = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.mains += tag == "main"
        self.h1s += tag == "h1"
        self.scripts += tag == "script"
        if tag == "html":
            self.lang = attrs.get("lang")
        if attrs.get("id"):
            if attrs["id"] in self.ids:
                raise ValueError("duplicate id: " + attrs["id"])
            self.ids.add(attrs["id"])
        if tag in ("a", "link") and attrs.get("href"):
            self.links.append(attrs["href"])
        if tag in ("img", "script") and attrs.get("src"):
            self.links.append(attrs["src"])


def main():
    parsed = {}
    for page in PAGES:
        source = page.read_text(encoding="utf-8")
        doc = Page()
        doc.feed(source)
        doc.close()
        lang = "ja" if page.parent.name == "ja" else "en"
        if doc.lang != lang or doc.mains != 1 or doc.h1s != 1 or doc.scripts:
            raise ValueError(str(page) + ": invalid lang, landmark, heading or script")
        if not {"content", "mission", "progress", "contribute"} <= doc.ids:
            raise ValueError(str(page) + ": missing required sections")
        for evidence in ("EX_GETVER", "765", "#174", "UNVERIFIED", "BLOCKED"):
            if evidence not in source:
                raise ValueError(str(page) + ": missing evidence: " + evidence)
        parsed[page.resolve()] = doc

    for page, doc in parsed.items():
        for ref in doc.links:
            url = urlsplit(ref)
            if url.scheme in ("https", "mailto"):
                continue
            if url.scheme or url.netloc or ref.startswith("//"):
                raise ValueError(str(page) + ": unsupported link: " + ref)
            target = (page.parent / unquote(url.path)).resolve() if url.path else page
            if not target.is_relative_to(SITE):
                raise ValueError(str(page) + ": link escapes site: " + ref)
            if target.is_dir():
                target /= "index.html"
            if not target.is_file():
                raise ValueError(str(page) + ": missing local file: " + ref)
            if url.fragment and target.suffix == ".html" and unquote(url.fragment) not in parsed[target].ids:
                raise ValueError(str(page) + ": missing anchor: " + ref)

    css = (SITE / "assets" / "styles.css").read_text(encoding="utf-8")
    for rule in ("@media (max-width:", "prefers-reduced-motion", ":focus-visible"):
        if rule not in css:
            raise ValueError("missing responsive or accessible CSS: " + rule)
    ElementTree.parse(SITE / "assets" / "mark.svg")
    print("PASS: bilingual Pages, local links, section IDs, evidence, CSS and SVG")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, ElementTree.ParseError, KeyError) as error:
        sys.exit("FAIL Pages: " + str(error))
