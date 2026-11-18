"""Keep the English canonical README and Japanese translation consistent.

These are structural and claim-parity checks, not evidence of System Card ABI
or commercial game compatibility.
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EN = ROOT / "README.md"
JP = ROOT / "readme.jp.md"
LEDGER = ROOT / "spec" / "dev_implementations.json"
IMAGES = (
    "assets/readme/hero.svg",
    "assets/readme/overview.svg",
    "assets/readme/boot-path.svg",
    "assets/readme/architecture.svg",
    "assets/readme/roadmap.svg",
)


def image_paths(body):
    return re.findall(r"!\[[^\]]*\]\((assets/readme/[^)]+)\)", body)


class ReadmeLanguageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.en = EN.read_text(encoding="utf-8")
        cls.jp = JP.read_text(encoding="utf-8")
        cls.ledger = json.loads(LEDGER.read_text(encoding="utf-8"))

    def test_english_is_canonical_and_japanese_is_linked(self):
        self.assertIn("**English** | [日本語](readme.jp.md)", self.en)
        self.assertIn("[English](README.md) | **日本語**", self.jp)
        self.assertIn("Canonical / SSOT", self.jp)
        self.assertIn("# oss-pce-cd-bios", self.en)
        self.assertIn("## At a glance", self.en)
        self.assertIn("## Quick start", self.en)
        self.assertIn("## 現在地", self.jp)
        self.assertIn("## 試してみる", self.jp)
        # Other than the language-switch label, the default README must
        # not accidentally become Japanese-language content again.
        text = self.en.replace("[日本語]", "")
        self.assertIsNone(re.search(r"[\u3040-\u30ff\u3400-\u9fff]", text))

    def test_five_shared_images_exist_and_have_accessible_svg_metadata(self):
        self.assertEqual(image_paths(self.en), list(IMAGES))
        self.assertEqual(image_paths(self.jp), list(IMAGES))
        for path in IMAGES:
            with self.subTest(path=path):
                data = (ROOT / path).read_text(encoding="utf-8")
                self.assertIn("<svg ", data)
                self.assertIn("<title ", data)
                self.assertIn("<desc ", data)
                self.assertIn("</svg>", data)

    def test_badges_are_shared(self):
        for badge in (
            "actions/workflows/ci.yml/badge.svg",
            "actions/workflows/core-harness.yml/badge.svg",
            "actions/workflows/api-source.yml/badge.svg",
            "license-MIT",
            "status-EXPERIMENTAL",
            "18%2F81",
            "verified%20ABI-0%2F81",
            "release-BLOCKED",
            "G5__OBSERVED-false",
        ):
            with self.subTest(badge=badge):
                self.assertIn(badge, self.en)
                self.assertIn(badge, self.jp)

    def test_status_metrics_are_consistent_with_ledger(self):
        slots = self.ledger["slots"]
        dev = sum(bool(x["implementation"]) for x in slots)
        stubs = len(slots) - dev
        for body in (self.en, self.jp):
            self.assertRegex(body, rf"\b{dev}\s*/\s*{len(slots)}\b")
            self.assertRegex(body, rf"\b{stubs}\s*/\s*{len(slots)}\b")
            self.assertIn("DEV_CALLABLE: 0", body)
            self.assertIn("ABI VERIFIED: 0/81", body)
            self.assertIn("JSR permitted: 0/81", body)
            self.assertIn("G5_OBSERVED=false", body)
            self.assertIn("fa694281", body)
            self.assertIn("f90c16b", body)

    def test_g5_scope_and_evidence_links_match(self):
        for body in (self.en, self.jp):
            self.assertIn("CUE[0]", body)
            self.assertIn("CUE[1]", body)
            self.assertIn("\u0024" + "4A", body)
            for link in (
                "docs/g5-commercial-reobservation-2026-11-18.md",
                "docs/status.md",
                "docs/README.md",
                "docs/api-inventory.md",
                "docs/roadmap.md",
                "docs/compatibility.md",
            ):
                self.assertIn(link, body)
                self.assertTrue((ROOT / link).is_file(), link)

    def test_project_guides_and_docs_hub_follow_language_links(self):
        hub = (ROOT / "docs/README.md").read_text(encoding="utf-8")
        eng = (ROOT / "docs/project-guide.md").read_text(encoding="utf-8")
        jpn = (ROOT / "docs/project-guide.jp.md").read_text(encoding="utf-8")
        self.assertIn("../README.md", hub)
        self.assertIn("../readme.jp.md", hub)
        self.assertIn("project-guide.jp.md", eng)
        self.assertIn("project-guide.md", jpn)
        self.assertIn("../readme.jp.md", jpn)
        self.assertIn("docs/project-guide.jp.md", self.jp)


if __name__ == "__main__":
    unittest.main()
