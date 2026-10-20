"""Compatibility alias for the observation-kind rule of Issue #225.

The rule lives in contracts.adjudicate_claim() (the formal path called by validate_contracts)
and is not reimplemented here. adjudicate_observation() only delegates, once, so there is no
second validation pass. An OBSERVED claim must declare observation_kind covering its field; the
kind is a declaration, not a measurement (evidence is FORMAT_ONLY, see contracts.EVIDENCE_TRUST_LEVEL).

Reachability: validate_contracts calls adjudicate_claim for every claim, but the shipped ledger has
no OBSERVED claim and its verification gate forces executed_callee False, so no OBSERVED claim can
pass the ledger today. Enforcement is in place on the formal path; the ledger does not yet exercise it.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from contracts import OBSERVATION_KIND_FIELDS, adjudicate_claim  # noqa: E402,F401


def adjudicate_observation(claim, source_kinds, *, field, context, executed_callee=False):
    """Delegate to contracts.adjudicate_claim (the single enforcement point). Returns None or raises ValueError."""
    return adjudicate_claim(claim, source_kinds, field=field, context=context,
                            executed_callee=executed_callee)
