"""Judge the Issue #231 CD_STAT device-layer observation (DEVELOPMENT-ONLY; not a BIOS callee, no CD_STAT ABI).

The drive-state classification is NOT re-implemented here: it is the Issue #144 judge
(tools/build_cd_media_state.judge / check_pair) applied to the same paired control. This module only adds
the #231 bounds: every run must end at reset_ready inside the step budget, and must carry the
PC/SP/P/MPR, step, reason and RAM fields. Fail-closed: a missing field or budget exhaustion raises ValueError.
"""
if __package__:
    from .build_cd_media_state import check_pair
else:
    from build_cd_media_state import check_pair

OBSERVED_FIELDS = ("pc", "sp", "p", "mpr", "step", "reason", "result_hex", "buffer_hex")


def judge_observation(with_disc, without_disc, budget):
    """Return the paired state {disc, no_disc} or raise ValueError (BLOCKED for missing evidence, FAIL otherwise)."""
    if not isinstance(budget, int) or budget <= 0:
        raise ValueError("BLOCKED: step budget must be a positive integer")
    for name, records in (("disc", with_disc), ("no disc", without_disc)):
        for run in records.get("RUN", []):
            where = f"{name} {run.get('cycle')}"
            if run.get("reason") == "budget" or not isinstance(run.get("step"), int) or run["step"] > budget:
                raise ValueError(f"{where}: not bounded by the step budget (reason {run.get('reason')}, step {run.get('step')})")
            missing = [field for field in OBSERVED_FIELDS if field not in run]
            if missing:
                raise ValueError(f"BLOCKED: {where}: missing observation fields {missing}")
    disc, no_disc = check_pair(with_disc, without_disc)
    return {"status": "OBSERVED", "disc": disc, "no_disc": no_disc, "budget": budget}
