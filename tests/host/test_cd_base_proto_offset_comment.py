"""Host lint: src/cd_base_proto.s offset-state comment must agree with the code.

EXPERIMENTAL CD_BASE proto V1 only. The state comment for $2405/$2406/$2407
must say the offset is preserved on error, because every error path updates only
`last` (ST_LAST) and leaves ST_OFF_* untouched (MAJOR-1). A stale
"cleared to 0 on error" comment contradicts the implementation.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "cd_base_proto.s"


class CdBaseProtoOffsetCommentLint(unittest.TestCase):
    def test_offset_comment_says_preserved_on_error(self):
        text = SRC.read_text(encoding="utf-8")
        self.assertIn("preserved on error).", text,
                      "offset comment must state preservation on error")
        self.assertNotIn("cleared to 0 on error", text,
                         "stale zero-clear wording contradicts MAJOR-1")

    def test_no_offset_store_on_error_paths(self):
        # Error epilogues must not touch ST_OFF_*; only ST_LAST is updated there.
        text = SRC.read_text(encoding="utf-8")
        # Count ST_OFF stores overall: they may only appear on the @data success path.
        self.assertEqual(text.count("sta ST_OFF_HI"), 1)
        self.assertEqual(text.count("sta ST_OFF_MID"), 1)
        self.assertEqual(text.count("sta ST_OFF_LO"), 1)


if __name__ == "__main__":
    unittest.main()
