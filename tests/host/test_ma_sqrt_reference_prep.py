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
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "host"))
DOC = ROOT / "docs" / "78-ma-sqrt-reference-prep.md"
CONTRACTS = ROOT / "spec" / "api_contracts.json"
SOURCE = ROOT / "src" / "overrides" / "slot_44.s"

# Scope table rows in section 2 of the doc, in required order (exact first-column labels).
SCOPE_LABELS = ["入力形式", "丸め", "0", "1", "完全平方数", "最大入力", "出力配置", "副作用"]
ROW_RE = re.compile(r"^\| (入力形式|丸め|0|1|完全平方数|最大入力|出力配置|副作用) \|", re.M)


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
    def test_doc_scope_rows_exact_and_ordered(self):
        self.assertTrue(DOC.is_file(), "docs/78-ma-sqrt-reference-prep.md is missing")
        text = DOC.read_text(encoding="utf-8")
        labels = [m.group(1) for m in ROW_RE.finditer(text)]
        self.assertEqual(labels, SCOPE_LABELS)

    def test_doc_reports_exhaustive_check_as_python_mirror(self):
        text = DOC.read_text(encoding="utf-8")
        self.assertIn("Python mirror", text)
        # The exhaustive-range line must exist (non-vacuous) and name the mirror.
        exhaustive = [line for line in text.splitlines() if "0..65535" in line and "全数" in line]
        self.assertEqual(len(exhaustive), 1, "exhaustive-range statement missing or duplicated")
        self.assertIn("Python mirror", exhaustive[0])
        self.assertIn("slot_44.s` 自体の全数実行ではない", exhaustive[0])

    def test_reference_contract_stays_unverified(self):
        entry = _ma_sqrt_entry()
        self.assertEqual(entry["slot"], 68)
        self.assertEqual(entry["mapping_status"], "HYPOTHESIS")
        self.assertEqual(entry["verification"]["status"], "NOT_RUN")
        self.assertFalse(entry["verification"]["executed_callee"])
        for field, obj in entry["contract"].items():
            self.assertEqual(obj["status"], "UNKNOWN", f"contract.{field} must stay UNKNOWN")
        # Whole-word match: "UNVERIFIED" must not count as VERIFIED.
        self.assertIsNone(re.search(r"\bVERIFIED\b", json.dumps(entry)))

    def test_dev_output_placement_and_y_restore_ordering(self):
        lines = SOURCE.read_text(encoding="utf-8").splitlines()

        def only(pattern):
            hits = [i for i, line in enumerate(lines) if re.match(pattern, line)]
            self.assertEqual(len(hits), 1, f"expected exactly one instruction line: {pattern}")
            return hits[0]

        phy = only(r"^\s*phy\b")
        root_to_a = only(r"^\s*lda \$FB\s+; A = root \(floor\(sqrt\(n\)\)\)")
        x_zero = only(r"^\s*ldx #0\b")
        ply = only(r"^\s*ply\b")
        plp = only(r"^\s*plp\b")
        rts = only(r"^\s*rts\b")
        self.assertLess(phy, ply)            # Y saved on entry, restored before return
        self.assertLess(root_to_a, x_zero)   # A = root is set before X = 0
        self.assertLess(x_zero, ply)
        self.assertLess(ply, plp)
        self.assertLess(plp, rts)


if __name__ == "__main__":
    unittest.main()
