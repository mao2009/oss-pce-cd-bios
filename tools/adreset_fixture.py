#!/usr/bin/env python3
"""Synthetic, non-executing AD_RESET (slot $10, candidate $E030) controller model and judge (Issue #293).

The model encodes OUR assumption of ADPCM reset behaviour. It is not a measurement of the System
Card callee and it is not ABI evidence (docs/research/ad-reset-contract.md: ABI BLOCKED_ABI).
Nothing here launches an emulator, reads a ROM or runs a reference JSR. The best verdict is
``MODEL_OK``, which is never a callee PASS. Out-of-scope or unauthorized requests are ``REJECTED``
and malformed input is ``BLOCKED``.
"""
import sys

ENTRY_CANDIDATE = 0xE030  # docs/research/ad-reset-contract.md section 2.1 H1; caller-side SOURCE_FACT only
POLL_BUDGET = 4           # synthetic wait budget chosen by this fixture; not a hardware value
STATES = ("IDLE", "TRANSFER", "PLAYING")
MODEL_OK, FAIL, BLOCKED, REJECTED = "MODEL_OK", "FAIL", "BLOCKED", "REJECTED"
# Any of these set true means the request asks for execution or side effects: refuse it.
FORBIDDEN_FLAGS = ("execution_allowed", "side_effects_allowed", "unsafe_io_allow", "real_bios_jsr")


def model(case):
    """Synthetic model: reset aborts an active transfer or playback, returns to IDLE, clears busy in one poll."""
    return {"entry": case["entry"], "state_after": "IDLE", "busy_after": 0,
            "polls": 1, "aborted": case["state"] != "IDLE"}


def judge(case, obs):
    """Judge one observation record against one case. Never returns a PASS-like verdict for a callee."""
    if case.get("entry") != ENTRY_CANDIDATE or case.get("op") != "reset":
        return REJECTED  # other slots or operations are out of scope for this fixture
    if any(case.get(flag) for flag in FORBIDDEN_FLAGS):
        return REJECTED
    if case.get("state") not in STATES or not isinstance(obs, dict):
        return BLOCKED  # malformed case or missing observation: fail closed
    if obs.get("entry") != ENTRY_CANDIDATE:
        return REJECTED
    polls = obs.get("polls")
    ok = (obs.get("state_after") == "IDLE" and obs.get("busy_after") == 0
          and type(polls) is int and 0 < polls <= POLL_BUDGET
          and obs.get("aborted") == (case["state"] != "IDLE"))
    return MODEL_OK if ok else FAIL


def case(state="IDLE", **extra):
    return {"entry": ENTRY_CANDIDATE, "op": "reset", "state": state, **extra}


CASES = {
    "idle_reset": case("IDLE"),                       # positive: known registers and wait condition
    "transfer_boundary": case("TRANSFER"),            # reset mid-transfer: must abort and go idle
    "playing_boundary": case("PLAYING"),              # reset mid-playback: must abort and go idle
    "out_of_scope_entry": {**case("IDLE"), "entry": 0xE00C},
    "unauthorized_exec": case("IDLE", execution_allowed=True),
    "unsafe_io": case("IDLE", side_effects_allowed=True),
    "malformed_state": case("HALF"),
}


def main():
    for name, c in CASES.items():
        print(f"{name}: {judge(c, model(c))} (abi_status=BLOCKED_ABI, release_eligible=false)")
    # Always fail closed: the model is synthetic and the ABI is unresolved, so no run may exit 0.
    return 3


if __name__ == "__main__":
    sys.exit(main())
