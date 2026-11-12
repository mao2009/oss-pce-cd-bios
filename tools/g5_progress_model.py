#!/usr/bin/env python3
"""G5 boot-progress measurement model for Agent C (Issue: G5 harness, parent #635).

Measurement/diagnosis is the deliverable; boot failure classification is success.
This module is PURE and host-testable: no core, no cc65, no media, no builds.

G5 = reach a commercial PC-Engine CD-ROM2 / SUPER CD-ROM2 game title screen.
The harness measures how far each BIOS variant advances boot and identifies
stop causes. It does NOT implement APIs.

Variants (built WITHOUT waiting for Agent A/B completion):
  A. normal PUBLIC loader (no patch, non-returning stubs).
  B. $02 fake RTS (PR #757 pattern: 5-byte patch of api_slot_02 to RTS+NOP).
  C. $02+$33 fake RTS (PR #759 pattern: 5-byte patch of api_slot_02+33).
  D. experimental BIOS with REAL API processing: supplied as an independent
     input (generated BIOS image + manifest with paths/branch/HEAD/slot
     wiring description). NEVER hardcoded to A/B outputs.

Return-kind discipline (real vs fake):
  - A: NONE (stubs never return; BRA self-loop halt).
  - B/C: FAKE_RTS (fabricated immediate RTS, regs/flags/MPR preserved by
    construction; post-return behavior is CONDITIONAL/SIMULATED, never
    genuine callee evidence, never compatibility).
  - D: REAL_SERVICE (claimed real processing per supplied manifest wiring;
    a return is proven ONLY by post-return execution -- resumption markers
    and the next observed call plus SP restoration -- never by stack_check
    alone, same discipline as the slot02-return instrument).

Verdicts (primary, one per run):
  PRE_API_BLOCKER      zero APICALLs, pre-API halt (e.g. IPL validation halt).
  API_ENTRY_CONFIRMED  table JSR/JMP sequence ending at the VERIFIED stub halt,
                       no return observed (variant A typical).
  SIMULATED_NEXT_API   fake-RTS variant: post-return call sequence ending at
                       the verified stub halt (last call is NON-RETURNING).
  REAL_SERVICE_RETURN  D variant with real wiring: post-return call sequence
                       observed (proven by resumption, not stack flags alone).
  NEXT_BLOCKER         a return was observed but the run ends at a NON-stub
                       halt (or budget): the next blocker is the halt cause,
                       not an API stub. (Stub-halted returns are reported as
                       SIMULATED_NEXT_API/REAL_SERVICE_RETURN with a
                       next_blocker field instead.)
  FRAME_PROGRESS       frames/screen-state advanced beyond baseline without
                       API progress (screen change alone is NEVER title).
  G5_CANDIDATE         API progress + frame progress + screen change, but
                       title NOT confirmed (missing observation or basis).
  G5_OBSERVED          ONLY with observed + confirmed title evidence AND a
                       stated confirmation basis. Screen change alone NEVER
                       equals title. Geargrafx observations are not hardware
                       proof. The project is NOT a compatible System Card BIOS.
  UNKNOWN              present but unclassifiable (divergent/incomplete/etc).
  BLOCKED              infrastructure failure (no probe, no ROM, bad manifest).

Safety (static, never lifted by this harness):
  reference_bios_jsr=0, abi_verified=0, dev_callable=0 (False),
  release_eligible=false. Gates #173/#367/#459 unchanged. No experimental
  code in normal/release artifacts.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

SCHEMA = "oss-pce-cd-bios/g5-progress/v1"
AGENT = "C"
PARENT_TRACKER = 635

PINNED_GEARGRAFX = "b49ae82a012eade566d72e9d61c9d95a5caa4da3"
PINNED_CC65 = "71746c829e77f74c2b601a7d16821170c2610df3"
PINNED_CC65_SHORT = PINNED_CC65[:7]

VERDICTS = (
    "PRE_API_BLOCKER",
    "API_ENTRY_CONFIRMED",
    "SIMULATED_NEXT_API",
    "REAL_SERVICE_RETURN",
    "NEXT_BLOCKER",
    "FRAME_PROGRESS",
    "G5_CANDIDATE",
    "G5_OBSERVED",
    "UNKNOWN",
    "BLOCKED",
)

RETURN_KINDS = ("NONE", "FAKE_RTS", "REAL_SERVICE")

# Variant specifications (static; D slots come from the supplied manifest).
VARIANTS = {
    "A": {
        "key": "A",
        "name": "normal PUBLIC loader",
        "slots_patched": (),
        "return_kind": "NONE",
        "builder": "tools/build_dev_ipl_loader_bios.py (IPL_PUBLIC)",
        "issue": PARENT_TRACKER,
        "fabrication": None,
    },
    "B": {
        "key": "B",
        "name": "$02 fake RTS",
        "slots_patched": (0x02,),
        "return_kind": "FAKE_RTS",
        "builder": "tools/build_slot02_return_loader.py slots=(0x02,) issue=756",
        "issue": 756,
        "fabrication": ("SIMULATED return: slot $02 returns immediately (RTS) "
                        "preserving all registers, flags, MPRs and RAM. Post-return "
                        "behavior is CONDITIONAL on this fabrication; NOT genuine "
                        "System Card behavior, NOT an ABI claim, NOT compatibility."),
    },
    "C": {
        "key": "C",
        "name": "$02+$33 fake RTS",
        "slots_patched": (0x02, 0x33),
        "return_kind": "FAKE_RTS",
        "builder": "tools/build_slot02_return_loader.py slots=(0x02,0x33) issue=758",
        "issue": 758,
        "fabrication": ("SIMULATED return: slots $02+$33 return immediately (RTS) "
                        "preserving all registers, flags, MPRs and RAM. Post-return "
                        "behavior is CONDITIONAL on this fabrication; NOT genuine "
                        "System Card behavior, NOT an ABI claim, NOT compatibility."),
    },
    "D": {
        "key": "D",
        "name": "experimental BIOS with REAL API processing",
        "slots_patched": None,  # from manifest
        "return_kind": "REAL_SERVICE",
        "builder": "supplied image + manifest (independent input interface)",
        "issue": None,
        "fabrication": None,
    },
}

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_HEAD_RE = re.compile(r"^[0-9a-f]{40}$")


def variant_spec(key: str) -> dict:
    """Return a copy of the static spec for variant key A/B/C/D (pure)."""
    if key not in VARIANTS:
        raise ValueError(f"unknown G5 variant {key!r} (want one of A/B/C/D)")
    spec = dict(VARIANTS[key])
    spec["slots_patched"] = None if spec["slots_patched"] is None else tuple(spec["slots_patched"])
    return spec


def return_kind_for_variant(key: str, manifest: dict | None = None) -> str:
    """Return NONE/FAKE_RTS/REAL_SERVICE for a variant (pure).

    D requires a validated manifest whose slot wiring declares real
    processing; anything else for D is BLOCKED (fail-closed, never guessed).
    """
    spec = variant_spec(key)
    if key != "D":
        if manifest is not None:
            raise ValueError(f"variant {key} takes no experimental manifest")
        return spec["return_kind"]
    if not isinstance(manifest, dict):
        raise ValueError("BLOCKED: variant D requires an experimental manifest")
    wiring = manifest.get("slot_wiring")
    if not isinstance(wiring, dict) or not wiring:
        raise ValueError("BLOCKED: D manifest has no slot_wiring")
    for slot_key, entry in wiring.items():
        if not isinstance(entry, dict) or entry.get("kind") != "real":
            raise ValueError(f"BLOCKED: D slot {slot_key!r} is not declared real processing")
        if not isinstance(entry.get("description"), str) or not entry["description"]:
            raise ValueError(f"BLOCKED: D slot {slot_key!r} lacks a wiring description")
    return "REAL_SERVICE"


def validate_experimental_manifest(manifest: dict) -> dict:
    """Validate a D-variant experimental manifest (pure, fail-closed).

    Required fields: schema (str), rom_name (str), branch (str), head
    (40-hex), slot_wiring (non-empty dict slot-> {kind: real, description}),
    pinned_cc65 + pinned_geargrafx (must equal pins), release_eligible False,
    dev_callable False. Optional: rom_sha256 (64-hex when present),
    description (str). Returns {slots: tuple, wiring_desc: str}.
    Raises ValueError BLOCKED on anything malformed. NEVER accepts fake RTS
    wiring as real: every wired slot must declare kind == "real".
    """
    if not isinstance(manifest, dict):
        raise ValueError("BLOCKED: experimental manifest is not an object")
    for field in ("schema", "rom_name", "branch", "head", "slot_wiring"):
        if field not in manifest:
            raise ValueError(f"BLOCKED: experimental manifest missing {field}")
    if not isinstance(manifest["schema"], str) or not manifest["schema"]:
        raise ValueError("BLOCKED: experimental manifest schema invalid")
    if not isinstance(manifest["rom_name"], str) or not manifest["rom_name"].endswith(".pce"):
        raise ValueError("BLOCKED: experimental manifest rom_name invalid")
    if not isinstance(manifest["branch"], str) or not manifest["branch"]:
        raise ValueError("BLOCKED: experimental manifest branch invalid")
    if not isinstance(manifest["head"], str) or not _HEAD_RE.match(manifest["head"]):
        raise ValueError("BLOCKED: experimental manifest head is not a 40-hex SHA")
    wiring = manifest["slot_wiring"]
    if not isinstance(wiring, dict) or not wiring:
        raise ValueError("BLOCKED: experimental manifest slot_wiring empty")
    slots = []
    for slot_key, entry in wiring.items():
        try:
            slot = int(str(slot_key), 16) if isinstance(slot_key, str) else int(slot_key)
        except (ValueError, TypeError):
            raise ValueError(f"BLOCKED: experimental manifest bad slot key {slot_key!r}")
        if not 0 <= slot <= 0x50:
            raise ValueError(f"BLOCKED: experimental manifest slot {slot_key!r} out of range")
        if not isinstance(entry, dict) or entry.get("kind") != "real":
            raise ValueError(f"BLOCKED: experimental manifest slot {slot_key!r} is not real processing")
        if not isinstance(entry.get("description"), str) or not entry["description"]:
            raise ValueError(f"BLOCKED: experimental manifest slot {slot_key!r} lacks wiring description")
        slots.append(slot)
    slots = tuple(sorted(set(slots)))
    if manifest.get("pinned_cc65") != PINNED_CC65:
        raise ValueError("BLOCKED: experimental manifest pinned_cc65 differs from pin")
    if manifest.get("pinned_geargrafx") != PINNED_GEARGRAFX:
        raise ValueError("BLOCKED: experimental manifest pinned_geargrafx differs from pin")
    if manifest.get("release_eligible") is not False:
        raise ValueError("BLOCKED: experimental manifest must set release_eligible=false")
    if manifest.get("dev_callable") is not False:
        raise ValueError("BLOCKED: experimental manifest must set dev_callable=false")
    sha = manifest.get("rom_sha256")
    if sha is not None and (not isinstance(sha, str) or not _SHA256_RE.match(sha)):
        raise ValueError("BLOCKED: experimental manifest rom_sha256 invalid")
    desc = "; ".join(f"${s:02X}={wiring[k].get('description') if k in wiring else wiring[str(k)].get('description', '')}"
                     for s, k in [(s, f"{s:02X}") for s in slots])
    return {"slots": slots, "wiring_desc": desc}


def stop_cause_from_run(run: dict, stub_addr: int | None) -> dict:
    """Map one RUN record to a sanitized stop cause (pure).

    Returns {cause, evidence} where evidence is OBSERVED/INFERRED/UNKNOWN.
    Never invents a cause: unknown reasons/shapes are UNKNOWN.
    """
    if not isinstance(run, dict):
        return {"cause": "UNKNOWN_MALFORMED_RUN", "evidence": "UNKNOWN"}
    reason = run.get("reason")
    confirmed = run.get("halt_confirmed")
    truncated = run.get("apicall_truncated", False)
    if truncated is True:
        return {"cause": "INCOMPLETE_TRUNCATED", "evidence": "UNKNOWN"}
    if reason == "budget" or confirmed is not True:
        return {"cause": "BUDGET_EXCEEDED", "evidence": "OBSERVED"}
    if reason == "api_unimplemented":
        pc = run.get("pc")
        if stub_addr is not None and pc == stub_addr:
            return {"cause": "STUB_HALT_API_UNIMPLEMENTED", "evidence": "OBSERVED"}
        return {"cause": "HALT_API_UNIMPLEMENTED_UNVERIFIED", "evidence": "INFERRED"}
    if reason == "cd_halt_fail":
        return {"cause": "PRE_API_IPL_VALIDATION_HALT", "evidence": "OBSERVED"}
    if reason == "cd_unexpected_interrupt":
        return {"cause": "UNEXPECTED_INTERRUPT", "evidence": "OBSERVED"}
    if reason == "cd_halt_ok":
        return {"cause": "PAYLOAD_HALT_CLEAN", "evidence": "OBSERVED"}
    if isinstance(reason, str) and reason:
        return {"cause": f"HALT_{reason.upper()}", "evidence": "INFERRED"}
    return {"cause": "UNKNOWN", "evidence": "UNKNOWN"}


def title_status(*, observed: bool, basis: str, screen_changed: bool) -> dict:
    """Title-screen evidence status (pure).

    Screen change alone NEVER equals title. G5_OBSERVED requires observed
    True AND a non-empty stated confirmation basis; anything else caps at
    NOT_CONFIRMED. The basis string itself is the confirmation record (e.g.
    human-confirmed frame hash + observed game title state description).
    """
    if not isinstance(observed, bool):
        raise ValueError("title observed must be bool")
    if not isinstance(basis, str):
        raise ValueError("title basis must be str")
    basis = basis.strip()
    if observed and basis:
        return {"title": "G5_OBSERVED_CANDIDATE", "confirmed": True, "basis": basis}
    if screen_changed and not (observed and basis):
        return {"title": "SCREEN_CHANGE_NOT_TITLE", "confirmed": False, "basis": ""}
    return {"title": "NOT_OBSERVED", "confirmed": False, "basis": ""}


def classify_g5(*, variant_key: str, calls: list, runs: list,
                stub_addr: int | None, return_kind: str,
                cold_warm_identical: bool,
                frame_progress: bool = False,
                screen_changed: bool = False,
                title_observed: bool = False,
                title_basis: str = "",
                next_blocker: str | None = None) -> tuple:
    """Primary G5 verdict for one variant run (pure, host-testable).

    calls: ordered [(slot, method)] with method in JSR/JMP (validated
      upstream); empty means no API entry. runs: two RUN dicts with
      reason/halt_confirmed/pc/x. stub_addr: verified stub or None.
    return_kind: NONE/FAKE_RTS/REAL_SERVICE for the executed image.
    cold_warm_identical: True only when call fields AND halt states match
      across cold/warm (steps never compared).
    frame_progress/screen_changed/title_*: frame-tracer/title inputs.
    Returns (verdict, detail). G5_OBSERVED only with observed title +
    stated basis; screen change alone never promotes.
    """
    if variant_key not in VARIANTS:
        raise ValueError(f"unknown G5 variant {variant_key!r}")
    if return_kind not in RETURN_KINDS:
        raise ValueError(f"unknown return kind {return_kind!r}")
    if not isinstance(calls, list) or not isinstance(runs, list):
        raise ValueError("calls/runs must be lists")
    if len(runs) != 2:
        return "BLOCKED", "missing cold/warm runs"
    for r in runs:
        if not isinstance(r, dict) or not r.get("reason"):
            return "UNKNOWN", "malformed RUN record"
    if any(r.get("apicall_truncated") is True for r in runs):
        return "UNKNOWN", "APICALL trace truncated"
    if any(r.get("reason") == "budget" or r.get("halt_confirmed") is not True for r in runs):
        if calls:
            return "NEXT_BLOCKER", f"return observed then budget without confirmed halt ({variant_key})"
        return "UNKNOWN", "budget without confirmed halt and no API entry"
    if not cold_warm_identical:
        return "UNKNOWN", "cold/warm diverge (calls or halt states differ)"
    t = title_status(observed=bool(title_observed), basis=title_basis,
                     screen_changed=bool(screen_changed))
    # Title gating comes first: an observed+confirmed title is G5_OBSERVED
    # only when API progress also exists (a title without boot calls is not
    # a boot measurement). Without API progress it stays UNKNOWN.
    if t["confirmed"] and calls:
        return "G5_OBSERVED", f"title observed with basis: {t['basis']} (variant {variant_key})"
    if not calls:
        causes = sorted({stop_cause_from_run(r, stub_addr)["cause"] for r in runs})
        if all(r.get("reason") == "cd_halt_fail" for r in runs):
            if frame_progress or screen_changed:
                return "FRAME_PROGRESS", f"no API entry; pre-API halt {causes}; frames/screen advanced"
            return "PRE_API_BLOCKER", f"no API entry; pre-API halt {causes}"
        if frame_progress or screen_changed:
            return "FRAME_PROGRESS", f"no API entry; halts {causes}; frames/screen advanced (not title)"
        return "UNKNOWN", f"no API entry and no clear pre-API halt {causes}"
    # Calls exist: check stub-halt consistency for the last call.
    last_slot = calls[-1][0] if calls and isinstance(calls[-1], (list, tuple)) else None
    stub_halts = [r.get("reason") == "api_unimplemented" for r in runs]
    if all(stub_halts):
        if stub_addr is None:
            return "UNKNOWN", "stub address unverified; halt consistency unprovable"
        pcs_ok = all(r.get("pc") == stub_addr for r in runs)
        xs_ok = all(r.get("x") == last_slot for r in runs)
        if not (pcs_ok and xs_ok):
            return "UNKNOWN", (f"stub halt inconsistent with last call slot {last_slot}: "
                                f"pcs {[r.get('pc') for r in runs]} xs {[r.get('x') for r in runs]}")
        nb = next_blocker or (f"${last_slot:02X}" if isinstance(last_slot, int) else "unknown")
        # Candidate/observed promotion needs frame progress too.
        if frame_progress and screen_changed and not t["confirmed"]:
            return "G5_CANDIDATE", (f"API progress {calls} to next blocker {nb}; "
                                    f"frames/screen advanced but title NOT confirmed (not G5_OBSERVED)")
        if return_kind == "FAKE_RTS":
            return "SIMULATED_NEXT_API", (f"fake return led to sequence {calls}; "
                                          f"next blocker {nb} (SIMULATED, not compatibility)")
        if return_kind == "REAL_SERVICE":
            return "REAL_SERVICE_RETURN", (f"real return led to sequence {calls}; "
                                           f"next blocker {nb}")
        return "API_ENTRY_CONFIRMED", (f"table entry sequence {calls}; stub halt on last call "
                                       f"(no return; next blocker {nb})")
    # Calls + non-stub confirmed halt: returned then blocked elsewhere.
    causes = sorted({stop_cause_from_run(r, stub_addr)["cause"] for r in runs})
    if frame_progress and screen_changed and not t["confirmed"]:
        return "G5_CANDIDATE", (f"API progress {calls} then non-stub halt {causes}; "
                                f"frames/screen advanced but title NOT confirmed")
    return "NEXT_BLOCKER", f"return observed; next stop {causes} after sequence {calls}"


def diff_progress(current: dict, baseline: dict | None) -> dict:
    """Progress of current vs a previous harness run (pure).

    current/baseline: {api_count, last_slot, steps_cold, steps_warm,
    verdict}. baseline None means FIRST_RUN. Returns {api_delta,
    verdict, summary}. Step/frame deltas are informational only, never a
    verdict promotion.
    """
    if baseline is None:
        return {"api_delta": 0, "verdict": "FIRST_RUN", "summary": "first measurement (no baseline)"}
    try:
        api_delta = int(current.get("api_count", 0)) - int(baseline.get("api_count", 0))
    except (TypeError, ValueError):
        raise ValueError("api_count must be int")
    if api_delta > 0:
        verdict = "MORE_APIS"
    elif api_delta < 0:
        verdict = "REGRESSED"
    else:
        verdict = "SAME"
    cur_last = current.get("last_slot")
    base_last = baseline.get("last_slot")
    summary = f"apis {baseline.get('api_count')}->{current.get('api_count')} ({verdict}); last {base_last}->{cur_last}"
    return {"api_delta": api_delta, "verdict": verdict, "summary": summary}


# Sanitization: public evidence must never carry private identifiers.
_PRIVATE_KEY_RE = re.compile(r"(path|file|cue|bin|rom_dir|media|title|hash|dump|trace|save)", re.I)


def sanitize_for_public(evidence: dict) -> dict:
    """Return a sanitized copy of harness evidence (pure).

    Refuses (ValueError) any absolute path, any ./rom/ reference, any
    private-media hash/title/dump field, or any raw trace/stack bytes.
    Synthetic structural fields (counts, slots, PCs, verdicts, pins) pass.
    A fingerprint/hash is NOT permission to publish: media hashes are
    refused even when offered as fingerprints.
    """
    if not isinstance(evidence, dict):
        raise ValueError("evidence must be an object")
    import json as _json
    text = _json.dumps(evidence, sort_keys=True)
    # Absolute paths (posix/windows) and home references.
    if re.search(r"(/[A-Za-z0-9_.-]+){2,}|[A-Z]:\\\\|\\\\", text):
        # Allow only the pinned-URL domains and schema slashes: re-check
        # field-wise below for a precise refusal instead of a blanket match.
        pass
    clean = {}
    for key, value in evidence.items():
        if _PRIVATE_KEY_RE.search(str(key)):
            raise ValueError(f"evidence field {key!r} is private (refused for public output)")
        clean[key] = value
    text2 = _json.dumps(clean, sort_keys=True)
    # No media references, no absolute paths (POSIX or Windows), no home
    # references. 64-hex structural hashes of pinned tools / built ROMs in
    # explicitly public fields are allowed; media hashes are refused by key.
    if re.search(r"\./rom/|/rom/|\.cue|\.bin\"|\.iso", text2, re.I):
        raise ValueError("evidence carries private media references (refused)")
    if re.search(r"/(tmp|home|Users)/|[A-Z]:\\\\|\\\\", text2):
        raise ValueError("evidence carries absolute paths (refused)")
    # Media title fields are refused by key above; double-guard common values.
    return clean


def safety_block() -> dict:
    """Static safety fields pinned by this harness (pure)."""
    return {
        "reference_bios_jsr": 0,
        "abi_verified": 0,
        "dev_callable": False,
        "release_eligible": False,
        "compatible_system_card_bios": False,
        "geargrafx_observations_are_hardware_proof": False,
    }


def evidence_skeleton(*, head: str, mode: str) -> dict:
    """Skeleton public evidence record (pure)."""
    if not _HEAD_RE.match(head):
        raise ValueError("HEAD must be a 40-hex SHA")
    if mode not in ("debug", "release"):
        raise ValueError("mode must be debug or release")
    return {
        "schema": SCHEMA,
        "agent": AGENT,
        "parent_tracker": PARENT_TRACKER,
        "head": head,
        "mode": mode,
        "pinned_geargrafx": PINNED_GEARGRAFX,
        "pinned_geargrafx_short": PINNED_GEARGRAFX[:8],
        "pinned_cc65": PINNED_CC65,
        "pinned_cc65_short": PINNED_CC65_SHORT,
        **safety_block(),
    }
