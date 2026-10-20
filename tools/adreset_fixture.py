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
    """Synthetic model: reset aborts an active transfer or playback, returns to IDLE, clears busy in one poll.
    A non-mapping case yields no observation; judge() then blocks it."""
    if not isinstance(case, dict):
        return {}
    return {"entry": case.get("entry"), "state_after": "IDLE", "busy_after": 0,
            "polls": 1, "aborted": case.get("state") != "IDLE"}


def judge(case, obs):
    """Judge one observation record against one case. Never returns a PASS-like verdict for a callee.

    Input shape is checked first (type mismatch -> BLOCKED, never FAIL), then scope (REJECTED),
    then the observation's values against the synthetic model (FAIL).
    """
    if not isinstance(case, dict):
        return BLOCKED  # malformed case record: fail closed without raising
    flags = [case[flag] for flag in FORBIDDEN_FLAGS if flag in case]
    if (type(case.get("entry")) is not int or type(case.get("op")) is not str
            or case.get("state") not in STATES or any(type(v) is not bool for v in flags)):
        return BLOCKED  # wrong-typed or missing case fields
    if case.get("entry") != ENTRY_CANDIDATE or case.get("op") != "reset":
        return REJECTED  # other slots or operations are out of scope for this fixture
    if any(flags):
        return REJECTED  # an execution, side-effect or real-JSR request is refused outright
    if not isinstance(obs, dict) or "entry" not in obs:
        return BLOCKED  # missing observation, or observation without its entry field
    entry, state_after = obs["entry"], obs.get("state_after")
    polls, busy, aborted = obs.get("polls"), obs.get("busy_after"), obs.get("aborted")
    if (type(entry) is not int or type(state_after) is not str or type(busy) is not int
            or type(polls) is not int or type(aborted) is not bool):
        return BLOCKED  # observation fields of the wrong type
    if entry != ENTRY_CANDIDATE:
        return FAIL  # the observation names another entry than the model expects
    ok = (state_after == "IDLE" and busy == 0 and 0 < polls <= POLL_BUDGET
          and aborted == (case["state"] != "IDLE"))
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
    print("[synthetic model, not a callee result]")
    for name, c in CASES.items():
        print(f"{name}: {judge(c, model(c))} (abi_status=BLOCKED_ABI, release_eligible=false)")
    # Always fail closed: the model is synthetic and the ABI is unresolved, so no run may exit 0.
    return 3


if __name__ == "__main__":
    sys.exit(main())
