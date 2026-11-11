"""Public boundary for the slot-74 generation-diff research doc (Issue #84, boundary from #596).

The new Markdown document must not republish candidate entry values, candidate
names, or non-allowlisted dollar addresses. Prohibited strings are fetched
from the allowed ledger at runtime so this file carries no prohibited literal.
Host only. No ROM and no private image is read.
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET_SLOT = 74
NEW_DOCS = (
    "docs/research/slot4a-generation-diff.md",
)

DOLLAR_ADDR = re.compile(r"\$[0-9A-Fa-f]{4}\b")
HEX_ADDR = re.compile(r"0x[0-9A-Fa-f]{4}\b")
BARE_HEX = re.compile(r"\b[0-9A-Fa-f]{4}\b")


def _contracts():
    return json.loads((ROOT / "spec" / "api_contracts.json").read_text(encoding="utf-8"))


def _target():
    contracts = _contracts()
    for api in contracts["apis"]:
        if api.get("slot") == TARGET_SLOT:
            return api
    raise AssertionError("target slot missing")


def _allowed_dollar_addrs():
    """DEV-owned dollar addresses parsed from the DEV layout source.

    Only values already present in the DEV source are allowed. Anything else
    is treated as a non-allowlisted address (internal reconstruction).
    """
    text = (ROOT / "src" / "bank0.cfg").read_text(encoding="utf-8")
    allowed = {m.upper() for m in DOLLAR_ADDR.findall(text)}
    # CPU window bases from the memory geometry (computable, not copied values).
    for i in range(8):
        allowed.add(f"${i * 0x2000:04X}")
    # Stack range from the machine-readable contract (numeric, formatted here).
    rom = json.loads((ROOT / "spec" / "rom-contract.json").read_text(encoding="utf-8"))
    stack = next(c for c in rom["claims"] if c["id"] == "cpu_stack")
    lo = stack["value"]["logical_first"]
    hi = stack["value"]["logical_last"]
    for v in range(lo, hi + 1, 0x100):
        allowed.add(f"${v:04X}")
    return allowed


def scan_document(text, entry, name, allowed):
    """Return list of boundary hits in one document text."""
    hits = []
    upper = text.upper()
    # Entry values: prefixed forms and bare standalone hex (hashes excluded by \b).
    bare = entry[2:].upper() if entry.upper().startswith("0X") else entry.upper()
    for variant in (entry, "$" + bare, "0X" + bare):
        if variant.upper() in upper:
            hits.append(f"entry:{variant}")
    # Bare standalone entry digits (not inside a longer hash).
    for m in BARE_HEX.finditer(text):
        if m.group(0).upper() == bare:
            hits.append(f"entry-bare:{m.group(0)}")
    # Names: exact candidate name from the ledger (case-sensitive).
    if name in text:
        hits.append("candidate-name")
    # Dollar addresses: only allowlisted DEV/architecture values.
    for m in DOLLAR_ADDR.findall(text):
        if m.upper() not in allowed:
            hits.append(f"dollar:{m}")
    # Hex addresses in the CPU range: only allowlisted equivalents.
    # Non-CPU hex (e.g. character codes below the CPU window) is ignored.
    # The CPU window base is the DEV builder constant, not a copied value.
    from tools.build_rom import FIRST_ENTRY

    allowed_hex = {"0X" + v[1:].upper() for v in allowed if v.startswith("$")}
    for m in HEX_ADDR.findall(text):
        try:
            value = int(m[2:], 16)
        except ValueError:
            continue
        if FIRST_ENTRY <= value <= 0xFFFF and m.upper() not in allowed_hex:
            hits.append(f"hex:{m}")
    return hits


class Slot4aPublicBoundaryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entry = _target()["candidate_entry"]
        cls.name = _target()["candidate_name"]
        cls.allowed = _allowed_dollar_addrs()
        cls.docs = {name: (ROOT / name).read_text(encoding="utf-8") for name in NEW_DOCS}

    def test_new_docs_carry_no_prohibited_entry_or_name(self):
        for name, text in self.docs.items():
            with self.subTest(doc=name):
                self.assertEqual(scan_document(text, self.entry, self.name, self.allowed), [])

    def test_scan_flags_entry_prefixed_form(self):
        synthetic = "synthetic prefix " + ("$" + self.entry[2:])
        self.assertTrue(scan_document(synthetic, self.entry, self.name, self.allowed))

    def test_scan_flags_entry_hex_form(self):
        synthetic = "synthetic hex " + self.entry
        self.assertTrue(scan_document(synthetic, self.entry, self.name, self.allowed))

    def test_scan_flags_candidate_name(self):
        synthetic = "synthetic name " + self.name
        self.assertTrue(scan_document(synthetic, self.entry, self.name, self.allowed))

    def test_scan_flags_non_allowlisted_dollar_address(self):
        # Construct a generic non-allowlisted address without copying a real one.
        probe = None
        for delta in range(1, 256):
            cand = f"${0xF000 + delta:04X}"
            if cand.upper() not in self.allowed:
                probe = cand
                break
        self.assertIsNotNone(probe)
        self.assertTrue(scan_document("synthetic " + probe, self.entry, self.name, self.allowed))

    def test_scan_accepts_allowlisted_dollar_address(self):
        # Any allowlisted value must not be flagged by the dollar rule alone.
        # Pick one that is not also an entry value.
        entry_dollar = ("$" + self.entry[2:]).upper()
        pick = next((v for v in self.allowed if v.upper() != entry_dollar), None)
        if pick is None:
            self.skipTest("no allowlisted value distinct from entry")
        # The entry rule may still flag it if it equals an entry; choose one
        # that is not any slot entry.
        contracts = _contracts()
        all_entries = {"$" + a["candidate_entry"][2:].upper() for a in contracts["apis"]}
        candidates = [v for v in self.allowed if v.upper() not in all_entries]
        if not candidates:
            self.skipTest("no allowlisted non-entry value")
        self.assertEqual(
            scan_document("synthetic " + candidates[0], self.entry, self.name, self.allowed),
            [],
        )

    def test_scan_flags_non_allowlisted_hex_address(self):
        probe = None
        for delta in range(1, 256):
            cand = f"0x{0xF000 + delta:04X}"
            # HEX form of an allowlisted value is allowed; pick a delta outside it.
            allowed_hex = {"0X" + v[1:].upper() for v in self.allowed if v.startswith("$")}
            if cand.upper() not in allowed_hex:
                probe = cand
                break
        self.assertIsNotNone(probe)
        self.assertTrue(scan_document("synthetic " + probe, self.entry, self.name, self.allowed))

    def test_scan_accepts_clean_synthetic(self):
        self.assertEqual(
            scan_document("synthetic clean text without addresses", self.entry, self.name, self.allowed),
            [],
        )

    def test_target_in_ledger_follows_layout_rule(self):
        # The ledger value is checked by rule via the DEV builder, not copied here.
        from tools.build_rom import FIRST_ENTRY  # noqa: E402
        self.assertEqual(self.entry, f"0x{FIRST_ENTRY + 3 * TARGET_SLOT:04X}")


if __name__ == "__main__":
    unittest.main()
