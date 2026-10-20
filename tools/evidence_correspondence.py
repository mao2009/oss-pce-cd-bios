"""Compatibility alias for the observation-kind rule of Issue #225.

The rule lives in contracts.adjudicate_claim() (the formal path called by validate_contracts)
and is not reimplemented here. adjudicate_observation() only delegates, once, so there is no
second validation pass. An OBSERVED claim must declare observation_kind covering its field; the
kind is a declaration, not a measurement (evidence is FORMAT_ONLY, see contracts.EVIDENCE_TRUST_LEVEL).

The declared kind is also matched against the measurement-point registry (spec/measurement_points.json,
Issue #364): a kind must be the kind of a registry point covering the field, and the registry pin must have
its review marker. This binds the declaration to the registry, not to a measured log: the provenance of the
evidence is NOT verified (a caller can still declare a covered kind), and OBSERVED is refused until
producer authentication exists (#124).

Reachability: validate_contracts calls adjudicate_claim for every claim, but the shipped ledger has
no OBSERVED claim and its verification gate forces executed_callee False, so no OBSERVED claim can
pass the ledger today. Enforcement is in place on the formal path; the ledger does not yet exercise it.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from contracts import OBSERVATION_KIND_FIELDS, adjudicate_claim  # noqa: E402,F401


def adjudicate_observation(claim, source_kinds, *, field, context, executed_callee=False,
                          resolved_evidence_hashes=frozenset()):
    """Delegate to contracts.adjudicate_claim (the single enforcement point). Returns None or raises ValueError."""
    return adjudicate_claim(claim, source_kinds, field=field, context=context,
                            executed_callee=executed_callee,
                            resolved_evidence_hashes=resolved_evidence_hashes)
