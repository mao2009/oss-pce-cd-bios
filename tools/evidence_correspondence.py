"""Field/evidence correspondence for OBSERVED claims (Issue #225).

adjudicate_claim() checks the shape of an OBSERVED claim, but it cannot tell which
observation a piece of evidence is about: a $1804 bus readback passes for `stack` as
well as for `hw_bus_reset`. This module makes the observation kind explicit: an OBSERVED
claim must declare `observation_kind`, and the declared kind must cover the claim's field.
All other rules are delegated to contracts.adjudicate_claim(), not reimplemented here.

The declared kind is a declaration, not a measurement. Nothing here authenticates the
evidence or checks that its content matches the kind (FORMAT_ONLY, see
contracts.EVIDENCE_TRUST_LEVEL). Not wired into validate_contracts: no OBSERVED claim
exists in spec/api_contracts.json yet.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from contracts import (  # noqa: E402
    CALLEE_BEHAVIOR_FIELDS, HARDWARE_OBSERVATION_FIELDS, adjudicate_claim,
)

OBSERVATION_KIND_KEY = 'observation_kind'
# Each kind lists the only contract fields its evidence may support.
# HW_BUS_READBACK: a low-level bus/register fact (e.g. $1804 readback); never callee ABI.
# CALLEE_EXECUTION: an executed callee's behavior; requires executed_callee=True via adjudicate_claim.
OBSERVATION_KIND_FIELDS = {
    'HW_BUS_READBACK': HARDWARE_OBSERVATION_FIELDS,
    'CALLEE_EXECUTION': CALLEE_BEHAVIOR_FIELDS,
}


def adjudicate_observation(claim, source_kinds, *, field, context, executed_callee=False):
    """Adjudicate one claim, then check that an OBSERVED claim's kind covers its field.

    Raises ValueError (fail closed) on any rule violation from adjudicate_claim(), on a
    missing/unknown/non-string observation_kind for an OBSERVED claim, and on a kind that
    does not cover the field. A non-OBSERVED claim must not declare a kind. The input
    claim is not modified. Returns None when consistent.
    """
    # Structural rules first: a non-dict claim fails inside adjudicate_claim with its own message.
    if not isinstance(claim, dict):
        adjudicate_claim(claim, source_kinds, field=field, context=context, executed_callee=executed_callee)
    core = {key: value for key, value in claim.items() if key != OBSERVATION_KIND_KEY}
    adjudicate_claim(core, source_kinds, field=field, context=context, executed_callee=executed_callee)
    if claim.get('status') != 'OBSERVED':
        if OBSERVATION_KIND_KEY in claim:
            raise ValueError(f'{context}: only an OBSERVED claim may declare {OBSERVATION_KIND_KEY}')
        return None
    kind = claim.get(OBSERVATION_KIND_KEY)
    if not isinstance(kind, str) or kind not in OBSERVATION_KIND_FIELDS:
        raise ValueError(f'{context}: OBSERVED requires {OBSERVATION_KIND_KEY} in '
                         f'{sorted(OBSERVATION_KIND_FIELDS)}')
    if field not in OBSERVATION_KIND_FIELDS[kind]:
        raise ValueError(f'{context}: observation kind {kind} does not support field {field!r}')
    return None
