"""Preparation guard for the reference MA_SQRT contract (slot $44, Issue #78).

Checks only what the repository can prove without running reference callees:
- docs/78-ma-sqrt-reference-prep.md covers every open scope item (input format,
  rounding, 0, 1, perfect squares, maximum input, output placement, side effects).
- spec/api_contracts.json keeps the MA_SQRT reference contract UNKNOWN /
  HYPOTHESIS / NOT_RUN. Nothing is promoted to VERIFIED here.
- The DEV model (src/overrides/slot_44.s mirror in test_ma_sqrt_dev.py) agrees
  with math.isqrt at the edge inputs. This is DEV correctness only, NOT the
  reference spec.
"""
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "host"))
from test_ma_sqrt_dev import dev_sqrt  # noqa: E402

DOC = ROOT / "docs" / "78-ma-sqrt-reference-prep.md"
CONTRACTS = ROOT / "spec" / "api_contracts.json"

SCOPE_ITEMS = [
    "入力形式",
    "丸め",
    "0",
    "1",
    "完全平方数",
    "最大入力",
    "出力配置",
    "副作用",
    "UNKNOWN",
    "BLOCKED",
    "DEV",
]


def _ma_sqrt_entry():
    data = json.loads(CONTRACTS.read_text(encoding="utf-8"))
    matches = [e for e in _walk(data) if e.get("candidate_name") == "MA_SQRT" and "contract" in e]
    assert len(matches) == 1, f"expected one MA_SQRT contract entry, got {len(matches)}"
    return matches[0]


def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


class MaSqrtReferencePrepTests(unittest.TestCase):
    def test_doc_covers_every_scope_item(self):
        self.assertTrue(DOC.is_file(), "docs/78-ma-sqrt-reference-prep.md is missing")
        text = DOC.read_text(encoding="utf-8")
        for item in SCOPE_ITEMS:
            self.assertIn(item, text, f"scope item not covered: {item}")
        self.assertIn("Issue", text)
        self.assertIn("#78", text)

    def test_reference_contract_stays_unverified(self):
        entry = _ma_sqrt_entry()
        self.assertEqual(entry["slot"], 68)
        self.assertEqual(entry["mapping_status"], "HYPOTHESIS")
        self.assertEqual(entry["verification"]["status"], "NOT_RUN")
        self.assertFalse(entry["verification"]["executed_callee"])
        for field, obj in entry["contract"].items():
            self.assertEqual(obj["status"], "UNKNOWN", f"contract.{field} must stay UNKNOWN")
        self.assertNotIn("VERIFIED", json.dumps(entry))

    def test_dev_model_edge_values_match_isqrt(self):
        """DEV correctness only: 0, 1, perfect squares, non-squares, max input 65535."""
        edges = [0, 1, 2, 3, 4, 8, 9, 15, 16, 17, 255, 256, 257, 1024, 4095, 65024, 65025, 65535]
        for n in edges:
            self.assertEqual(dev_sqrt(n), math.isqrt(n), n)
        self.assertEqual(dev_sqrt(65535), 255)
        self.assertEqual(dev_sqrt(0), 0)
        self.assertEqual(dev_sqrt(1), 1)

    def test_dev_output_placement_is_a_and_x_zero(self):
        source = (ROOT / "src" / "overrides" / "slot_44.s").read_text(encoding="utf-8")
        self.assertIn("lda $FB             ; A = root", source)
        self.assertIn("ldx #0", source)
        self.assertIn("plp", source)


if __name__ == "__main__":
    unittest.main()
