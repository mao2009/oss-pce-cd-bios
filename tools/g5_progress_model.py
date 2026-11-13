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
    a return is proven ONLY by measured post-return execution -- arrival at
    the real callee, post-return execution position, SP restoration, MPR
    consistency, and expected API order -- never by manifest declaration,
    stack flags, budget-exhaustion, or stub-halt alone).

Verdicts (primary, one per run):
  PRE_API_BLOCKER      zero APICALLs, pre-API halt (e.g. IPL validation halt).
  API_ENTRY_CONFIRMED  table JSR/JMP sequence ending at the VERIFIED stub halt,
                       no return observed (variant A typical; also a D
                       single-entry stub halt with no proven return).
  SIMULATED_NEXT_API   fake-RTS variant: post-return call sequence ending at
                       the verified stub halt (last call is NON-RETURNING).
  REAL_SERVICE_RETURN  D variant with real wiring AND measured return proof
                       (all five gates); unmeasured -> UNKNOWN/BLOCKED.
  NEXT_BLOCKER         a return was observed but the run ends at a NON-stub
                       halt (or budget): the next blocker is the halt cause,
                       not an API stub. (Stub-halted returns are reported as
                       SIMULATED_NEXT_API/REAL_SERVICE_RETURN with a
                       next_blocker field instead. For D, NEXT_BLOCKER also
                       requires return proof; otherwise UNKNOWN.)
  FRAME_PROGRESS       frames/screen-state advanced beyond baseline without
                       API progress (screen change alone is NEVER title).
  G5_CANDIDATE         API progress + frames/screen change, but
                       title NOT confirmed (missing observation or basis).
  G5_OBSERVED          ONLY with observed + confirmed title evidence AND a
                       stated confirmation basis PLUS structured observation
                       (source/target/cold-warm/confirmation/fake-RTS
                       relation). Screen change alone NEVER equals title.
                       No real observation -> never G5_OBSERVED. Title
                       detection unimplemented -> UNKNOWN. Geargrafx
                       observations are not hardware proof. The project is
                       NOT a compatible System Card BIOS.
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


# ---------------------------------------------------------------------------
# MEDIUM-1: manifest slot-key normalization.
# ---------------------------------------------------------------------------

def normalize_slot_key(key) -> int:
    """Normalize one manifest slot key to an int 0x00..0x50 (pure).

    Accepts ints and strings with optional whitespace, "$", "0x"/"0X"
    prefixes, and leading zeros ("02", "0x02", "$02", " 0X02 ", 2 all mean
    slot $02). Hex is case-insensitive. Anything else -- empty, non-hex,
    out-of-range, non-int/str -- raises ValueError with a BLOCKED class
    (bad key vs out of range are distinguished in the message).
    """
    if isinstance(key, int):
        # bool is an int subclass; refuse it explicitly (fail-closed).
        if isinstance(key, bool):
            raise ValueError(f"BLOCKED: bad slot key {key!r} (not an int slot)")
        slot = key
    elif isinstance(key, str):
        t = key.strip()
        if t[:2].lower() == "0x":
            t = t[2:]
        elif t.startswith("$"):
            t = t[1:]
        if not t:
            raise ValueError(f"BLOCKED: bad slot key {key!r} (empty after normalization)")
        try:
            slot = int(t, 16)
        except ValueError:
            raise ValueError(f"BLOCKED: bad slot key {key!r} (not hex)") from None
    else:
        raise ValueError(f"BLOCKED: bad slot key {key!r} (want int or hex string)")
    if not 0 <= slot <= 0x50:
        raise ValueError(f"BLOCKED: slot {key!r} out of range (want $00..$50)")
    return slot


def normalize_slot_keys(keys) -> list:
    """Normalize an iterable of slot keys (pure; raises BLOCKED on bad keys)."""
    return [normalize_slot_key(k) for k in keys]


def return_kind_for_variant(key: str, manifest: dict | None = None) -> str:
    """Return NONE/FAKE_RTS/REAL_SERVICE for a variant (pure).

    D requires a validated manifest whose slot wiring declares real
    processing; anything else for D is BLOCKED (fail-closed, never guessed).
    Slot keys are normalized (MEDIUM-1): "02"/"0x02"/"$02" all mean $02.
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
    seen: dict = {}
    for slot_key, entry in wiring.items():
        try:
            slot = normalize_slot_key(slot_key)
        except ValueError as e:
            raise ValueError(str(e)) from None
        if slot in seen:
            raise ValueError(f"BLOCKED: duplicate slot ${slot:02X} after normalization")
        seen[slot] = slot_key
        if not isinstance(entry, dict) or entry.get("kind") != "real":
            raise ValueError(f"BLOCKED: D slot {slot_key!r} is not declared real processing")
        if not isinstance(entry.get("description"), str) or not entry["description"]:
            raise ValueError(f"BLOCKED: D slot {slot_key!r} lacks a wiring description")
    return "REAL_SERVICE"


# ---------------------------------------------------------------------------
# MAJOR-4: D linkage against existing experimental (Agent A/B) manifests.
# ---------------------------------------------------------------------------

# Native experimental blocks produced by the Agent-A (CD_BASE proto, PR #782)
# and Agent-B (EX_DSPOFF, PR #780) builders. G5 never edits those sources;
# it only reads their builder outputs and derives verifiable info.
NATIVE_CD_BASE_KEY = "cd_base_proto"
NATIVE_DSPOFF_KEY = "experimental_slot_33"
NATIVE_CD_READ_KEY = "cd_read_link"

# Fields a native manifest must additionally carry before G5 can claim a
# real-CPU measurement. When any are missing the validator reports them
# (BLOCKED) instead of guessing or generating values.
ADDITIONAL_NATIVE_FIELDS = (
    "branch (repo branch that built the experimental image)",
    "head (40-hex repo HEAD that built the experimental image)",
    "pinned_geargrafx (must equal the G5 pin)",
    "labels.api_unimplemented or symbols.api_unimplemented "
    "(verified stub address needed for halt classification)",
    "selection_contract.lba + media identity "
    "(embedded medium needed to cross-check the executed medium)",
)

ROM_BYTES = 256 * 1024


def _native_slot_from_entry(entry_addr: int) -> int:
    """Table entry $E000+3*slot -> slot (pure)."""
    if not 0xE000 <= entry_addr <= 0xFFFF:
        raise ValueError(f"BLOCKED: native table entry ${entry_addr:04X} outside BIOS window")
    if (entry_addr - 0xE000) % 3 != 0:
        raise ValueError(f"BLOCKED: native table entry ${entry_addr:04X} is not slot-aligned")
    slot = (entry_addr - 0xE000) // 3
    if not 0 <= slot <= 0x50:
        raise ValueError(f"BLOCKED: native slot ${slot:02X} out of range")
    return slot


def _parse_native_addr(value) -> int | None:
    """Parse "$E099"/"0xE099"/57497/"E099" to int, else None (pure)."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if not isinstance(value, str):
        return None
    t = value.strip().lower()
    if t.startswith("0x"):
        t = t[2:]
    elif t.startswith("$"):
        t = t[1:]
    if not t:
        return None
    try:
        return int(t, 16)
    except ValueError:
        return None


def derive_native_slots(manifest: dict) -> tuple:
    """Derive D-verifiable wired slots from a native A/B manifest (pure).

    Reads (never edits) the existing experimental blocks:
      cd_base_proto {slot: "$02", entry: "$E006", proto_entry: ...} (Agent A)
      experimental_slot_33 {table_entry: "0xE099", ...} (Agent B)
      cd_read_link {slot: "$03", entry: "$E009", link_entry: ...} (triple-link)
    Returns a sorted tuple of slot ints. Raises BLOCKED when neither block
    is present or when addresses do not parse. Notation drift ("02" vs
    "0x02" vs "$02") is normalized; duplicates after normalization are
    refused.
    """
    if not isinstance(manifest, dict):
        raise ValueError("BLOCKED: experimental manifest is not an object")
    slots: list = []
    base = manifest.get(NATIVE_CD_BASE_KEY)
    if isinstance(base, dict):
        raw_slot = base.get("slot")
        raw_entry = base.get("entry")
        slot = None
        if raw_slot is not None:
            try:
                slot = normalize_slot_key(raw_slot)
            except ValueError as e:
                raise ValueError(f"BLOCKED: native cd_base_proto.slot bad ({e})") from None
        if slot is None and raw_entry is not None:
            addr = _parse_native_addr(raw_entry)
            if addr is None:
                raise ValueError(f"BLOCKED: native cd_base_proto.entry bad {raw_entry!r}")
            slot = _native_slot_from_entry(addr)
        if slot is None:
            raise ValueError("BLOCKED: native cd_base_proto has neither slot nor entry")
        # Cross-check slot vs entry when both present. Any explicit entry
        # that fails to parse is BLOCKED (fail-closed); a silent pass here
        # would let a malformed entry ride on a valid slot (fail-open).
        if raw_slot is not None and raw_entry is not None:
            addr = _parse_native_addr(raw_entry)
            if addr is None:
                raise ValueError(f"BLOCKED: native cd_base_proto.entry bad {raw_entry!r}")
            try:
                entry_slot = _native_slot_from_entry(addr)
            except ValueError as e:
                raise ValueError(f"BLOCKED: native cd_base_proto.entry bad ({e})") from None
            if entry_slot != slot:
                raise ValueError("BLOCKED: native cd_base_proto slot/entry mismatch")
        slots.append(slot)
    dsp = manifest.get(NATIVE_DSPOFF_KEY)
    if isinstance(dsp, dict):
        raw_table = dsp.get("table_entry")
        if raw_table is None:
            # Fall back to body address? No -- table entry is the wiring
            # claim; a body without a table redirect proves nothing.
            raise ValueError("BLOCKED: native experimental_slot_33 lacks table_entry")
        addr = _parse_native_addr(raw_table)
        if addr is None:
            raise ValueError(f"BLOCKED: native experimental_slot_33.table_entry bad {raw_table!r}")
        slots.append(_native_slot_from_entry(addr))
    link = manifest.get(NATIVE_CD_READ_KEY)
    if isinstance(link, dict):
        raw_slot = link.get("slot")
        raw_entry = link.get("entry")
        raw_table = link.get("table_entry")
        slot = None
        if raw_slot is not None:
            try:
                slot = normalize_slot_key(raw_slot)
            except ValueError as e:
                raise ValueError(f"BLOCKED: native cd_read_link.slot bad ({e})") from None
        for raw in (raw_entry, raw_table):
            if slot is None and raw is not None:
                addr = _parse_native_addr(raw)
                if addr is None:
                    raise ValueError(f"BLOCKED: native cd_read_link entry bad {raw!r}")
                slot = _native_slot_from_entry(addr)
        if slot is None:
            raise ValueError("BLOCKED: native cd_read_link has neither slot nor entry")
        # Cross-check every explicit entry address against the slot; any
        # unparsable or mismatching entry is BLOCKED (fail-closed).
        for raw in (raw_entry, raw_table):
            if raw is not None:
                addr = _parse_native_addr(raw)
                if addr is None:
                    raise ValueError(f"BLOCKED: native cd_read_link entry bad {raw!r}")
                try:
                    entry_slot = _native_slot_from_entry(addr)
                except ValueError as e:
                    raise ValueError(f"BLOCKED: native cd_read_link entry bad ({e})") from None
                if entry_slot != slot:
                    raise ValueError("BLOCKED: native cd_read_link slot/entry mismatch")
        slots.append(slot)
    if not slots:
        raise ValueError("BLOCKED: no native experimental wiring block "
                         f"({NATIVE_CD_BASE_KEY}/{NATIVE_DSPOFF_KEY}/{NATIVE_CD_READ_KEY})")
    if len(set(slots)) != len(slots):
        raise ValueError(f"BLOCKED: duplicate native slot after normalization {slots!r}")
    return tuple(sorted(slots))


def missing_g5_native_fields(manifest: dict) -> list:
    """List additional manifest fields needed for a real-CPU D measurement.

    Pure. Never guesses values; only reports what is absent so the owning
    Agent (A/B) can add it. Covers branch/HEAD/pins/labels/selection.
    """
    missing: list = []
    if not isinstance(manifest, dict):
        return ["manifest object itself"]
    if not isinstance(manifest.get("branch"), str) or not manifest.get("branch"):
        missing.append("branch")
    head = manifest.get("head")
    if not isinstance(head, str) or not _HEAD_RE.match(head):
        missing.append("head (40-hex)")
    if manifest.get("pinned_geargrafx") != PINNED_GEARGRAFX:
        missing.append("pinned_geargrafx (must equal G5 pin)")
    # Verified stub address for halt classification.
    labels = manifest.get("labels")
    symbols = manifest.get("symbols")
    stub = None
    if isinstance(labels, dict) and isinstance(labels.get("api_unimplemented"), int):
        stub = labels["api_unimplemented"]
    elif isinstance(symbols, dict) and isinstance(symbols.get("api_unimplemented"), int):
        stub = symbols["api_unimplemented"]
    if not isinstance(stub, int) or not 0xE000 <= stub <= 0xFFFF:
        missing.append("labels.api_unimplemented (verified stub address)")
    sel = manifest.get("selection_contract")
    if not isinstance(sel, dict) or "lba" not in sel:
        missing.append("selection_contract.lba (+ media identity)")
    return missing


def validate_experimental_manifest(manifest: dict) -> dict:
    """Validate a D-variant experimental manifest (pure, fail-closed).

    Accepts two shapes (never hardcoded to A/B artifact paths):
      (a) legacy G5 D manifest: schema/rom_name/branch/head/slot_wiring/
          pinned_cc65+pinned_geargrafx/release_eligible=false/
          dev_callable=false, optional rom_sha256/labels;
      (b) native experimental manifests from the existing builders
          (cd_base_proto and/or experimental_slot_33 and/or cd_read_link
          blocks, symbols, rom_sha256/rom_bytes/pinned_cc65/...). Slots are derived via
          derive_native_slots(); G5-required fields that are still absent
          (branch/head/pinned_geargrafx/labels/selection) are reported as
          BLOCKED additional-required-fields instead of guessed.

    Slot keys are normalized (MEDIUM-1): "02"/"0x02"/"$02" all mean $02;
    post-normalization duplicates are refused. NEVER accepts fake RTS
    wiring as real: every wired slot must declare kind == "real" (legacy)
    or come from a native real-processing block.

    Returns {slots: tuple, wiring_desc: str, native: bool, missing: list}.
    Raises ValueError BLOCKED on anything malformed.
    """
    if not isinstance(manifest, dict):
        raise ValueError("BLOCKED: experimental manifest is not an object")
    # Native shape takes precedence when a native wiring block is present.
    has_native = isinstance(manifest.get(NATIVE_CD_BASE_KEY), dict) or \
        isinstance(manifest.get(NATIVE_DSPOFF_KEY), dict) or \
        isinstance(manifest.get(NATIVE_CD_READ_KEY), dict)
    if has_native:
        return _validate_native_manifest(manifest)
    # --- Legacy G5 D shape. ---
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
    by_slot: dict = {}
    for slot_key, entry in wiring.items():
        try:
            slot = normalize_slot_key(slot_key)
        except ValueError as e:
            raise ValueError(str(e)) from None
        if slot in by_slot:
            raise ValueError(f"BLOCKED: duplicate slot ${slot:02X} after normalization "
                             f"({by_slot[slot]!r} vs {slot_key!r})")
        by_slot[slot] = slot_key
        if not isinstance(entry, dict) or entry.get("kind") != "real":
            raise ValueError(f"BLOCKED: experimental manifest slot {slot_key!r} is not real processing")
        if not isinstance(entry.get("description"), str) or not entry["description"]:
            raise ValueError(f"BLOCKED: experimental manifest slot {slot_key!r} lacks wiring description")
    slots = tuple(sorted(by_slot))
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
    desc = "; ".join(f"${s:02X}={wiring[by_slot[s]]['description']}" for s in slots)
    return {"slots": slots, "wiring_desc": desc, "native": False, "missing": []}


def _validate_native_manifest(manifest: dict) -> dict:
    """Validate a native A/B experimental manifest for G5 D use (pure)."""
    # Structural identity (read, never hardcode paths).
    rom_name = manifest.get("rom_name")
    if not isinstance(rom_name, str) or not rom_name.endswith(".pce"):
        raise ValueError("BLOCKED: native manifest rom_name invalid")
    sha = manifest.get("rom_sha256")
    if sha is not None and (not isinstance(sha, str) or not _SHA256_RE.match(sha)):
        raise ValueError("BLOCKED: native manifest rom_sha256 invalid")
    rom_bytes = manifest.get("rom_bytes")
    if rom_bytes is not None and rom_bytes != ROM_BYTES:
        raise ValueError(f"BLOCKED: native manifest rom_bytes {rom_bytes!r} != 256 KiB")
    if manifest.get("pinned_cc65") != PINNED_CC65:
        raise ValueError("BLOCKED: native manifest pinned_cc65 differs from pin")
    if manifest.get("release_eligible") is not False:
        raise ValueError("BLOCKED: native manifest must set release_eligible=false")
    if manifest.get("dev_callable") is not False:
        raise ValueError("BLOCKED: native manifest must set dev_callable=false")
    slots = derive_native_slots(manifest)
    parts = []
    base = manifest.get(NATIVE_CD_BASE_KEY)
    if isinstance(base, dict):
        parts.append(f"${slots[0]:02X}=CD_BASE_PROTO_V1 experimental "
                     f"(entry {base.get('entry')}, proto {base.get('proto_entry')})"
                     if NATIVE_DSPOFF_KEY not in manifest or len(slots) == 1
                     else f"${slots[0]:02X}=CD_BASE_PROTO_V1")
    dsp = manifest.get(NATIVE_DSPOFF_KEY)
    if isinstance(dsp, dict):
        # Resolve the DSPOFF slot from its own table entry (never by position).
        # derive_native_slots above already fail-closed on a missing/bad entry.
        dslot = _native_slot_from_entry(_parse_native_addr(dsp.get("table_entry")))
        parts.append(f"${dslot:02X}=EX_DSPOFF project-own fragment "
                     f"(table {dsp.get('table_entry')})")
    link = manifest.get(NATIVE_CD_READ_KEY)
    if isinstance(link, dict):
        raw = link.get("table_entry") if link.get("table_entry") is not None else link.get("entry")
        laddr = _parse_native_addr(raw)
        if laddr is None:
            raise ValueError(f"BLOCKED: native cd_read_link entry bad {raw!r}")
        lslot = _native_slot_from_entry(laddr)
        parts.append(f"${lslot:02X}=CD_READ_LINK_V1 experimental "
                     f"(table {link.get('table_entry')}, link {link.get('link_entry')})")
    desc = "; ".join(parts) if parts else "; ".join(f"${s:02X}=native" for s in slots)
    missing = missing_g5_native_fields(manifest)
    if missing:
        raise ValueError("BLOCKED: native manifest lacks G5-required fields: "
                         + "; ".join(missing)
                         + " -- owning Agent must add them; G5 never guesses")
    # All G5-required fields present: verify pins/HEAD that missing() covers.
    if manifest.get("pinned_geargrafx") != PINNED_GEARGRAFX:
        raise ValueError("BLOCKED: native manifest pinned_geargrafx differs from pin")
    head = manifest.get("head")
    if not isinstance(head, str) or not _HEAD_RE.match(head):
        raise ValueError("BLOCKED: native manifest head is not a 40-hex SHA")
    return {"slots": slots, "wiring_desc": desc, "native": True, "missing": []}


def verify_d_rom_bytes(data: bytes, manifest: dict) -> None:
    """Verify a D ROM image against its manifest (pure). Raises BLOCKED.

    ROM SHA256 is REQUIRED for D execution (never optional): a missing or
    malformed hash is BLOCKED, and a mismatch is BLOCKED.
    """
    if not isinstance(data, bytes):
        raise ValueError("BLOCKED: experimental ROM bytes unreadable")
    if len(data) != ROM_BYTES:
        raise ValueError(f"BLOCKED: experimental ROM is not a 256 KiB image ({len(data)} bytes)")
    if not isinstance(manifest, dict):
        raise ValueError("BLOCKED: experimental manifest is not an object")
    sha = manifest.get("rom_sha256")
    if not isinstance(sha, str) or not _SHA256_RE.match(sha):
        raise ValueError("BLOCKED: experimental manifest rom_sha256 required for D execution")
    if hashlib.sha256(data).hexdigest() != sha:
        raise ValueError("BLOCKED: experimental ROM hash differs from manifest")


def check_embedded_selection_vs_executed(manifest: dict, executed: dict | None) -> None:
    """Cross-check embedded media selection vs the executed medium (pure).

    manifest: native or legacy manifest (selection_contract/default_request).
    executed: {"lba": int, "identity": str|None} for the medium actually
      mounted, or None when the executed medium carries no selection record
      (then the check is unmeasured -> BLOCKED for D, never a pass).
    Mismatch -> BLOCKED. Never guesses; missing sides are reported.
    """
    if not isinstance(manifest, dict):
        raise ValueError("BLOCKED: experimental manifest is not an object")
    sel = manifest.get("selection_contract")
    embedded_lba = None
    embedded_id = None
    if isinstance(sel, dict):
        embedded_lba = sel.get("lba")
        embedded_id = sel.get("media_identity")
    if embedded_lba is None:
        req = manifest.get("default_request")
        if isinstance(req, dict):
            embedded_lba = req.get("lba")
    if embedded_lba is None:
        raise ValueError("BLOCKED: manifest carries no embedded selection LBA "
                         "(additional required field: selection_contract.lba)")
    if executed is None or not isinstance(executed, dict) or "lba" not in executed:
        raise ValueError("BLOCKED: executed medium selection unmeasured; "
                         "cannot cross-check embedded selection")
    if executed.get("lba") != embedded_lba:
        raise ValueError(f"BLOCKED: embedded selection LBA {embedded_lba!r} != "
                         f"executed medium LBA {executed.get('lba')!r}")
    exec_id = executed.get("identity")
    # Identity is required on BOTH sides (synthetic and commercial): the
    # executed medium's content identity must be measured from the exact
    # medium actually mounted for the probe, and the manifest's embedded
    # identity must match it. An LBA coincidence alone never suffices (a
    # different medium whose INDEX01 LBA coincides would otherwise pass), and
    # an unmeasured side (None, e.g. identity-fetch failure) is BLOCKED,
    # never a pass. Private identifiers never enter public evidence; this
    # check only passes/fails here.
    if embedded_id is None:
        raise ValueError("BLOCKED: manifest carries no embedded media identity; "
                         "cannot cross-check executed medium")
    if exec_id is None:
        raise ValueError("BLOCKED: executed medium identity unmeasured; "
                         "cannot cross-check embedded vs executed medium")
    if exec_id != embedded_id:
        raise ValueError("BLOCKED: embedded media identity != executed medium identity")


def read_ipl_sel_from_built_rom(rom_bytes: bytes, manifest: dict, variant: str) -> int:
    """Read the IPL SEL (INDEX01 LBA) bound to a built A/B/C ROM (pure).

    Issue #789 MEDIUM: A/B/C selection must be proven from the actually
    generated ROM content, never from a test constant passed to the builder.
    The builder records the SEL it assembled with (A: manifest
    "ipl_sel_lba" int; B/C: manifest "ipl_sel" "$XXXX (synthetic)" note) and
    binds it to the ROM bytes via manifest "rom_sha256". This helper verifies
    the hash (catches post-build byte tampering that would change the
    assembled IPL_SEL immediates) and parses the recorded SEL (catches a
    rebuild/swap with a different SEL). Redacted/private manifests
    ("ipl_sel_lba_redacted", "redacted (private media)") are BLOCKED (cannot
    prove a synthetic SEL). Returns the SEL int (0..0xFFFF).

    Track is NOT embedded in IPL ROMs (ipl_loader_rom.s takes only IPL_SEL;
    track is a property of the executed medium's first MODE1 track,
    validated at runtime by successful IPL boot). Callers must state that
    A/B/C track is INFERRED from successful stub arrival on the executed
    track (RUN halt at the expected stub with the expected X), never
    OBSERVED from ROM bytes; only LBA is ROM-OBSERVED here.
    """
    if variant not in ("A", "B", "C"):
        raise ValueError(f"BLOCKED: bad IPL variant {variant!r} (want A/B/C)")
    if not isinstance(rom_bytes, (bytes, bytearray)):
        raise ValueError(f"BLOCKED: variant {variant} ROM bytes unreadable")
    if not isinstance(manifest, dict):
        raise ValueError(f"BLOCKED: variant {variant} manifest is not an object")
    sha = manifest.get("rom_sha256")
    if not isinstance(sha, str) or not _SHA256_RE.match(sha):
        raise ValueError(f"BLOCKED: variant {variant} manifest rom_sha256 required")
    import hashlib as _hl
    if _hl.sha256(bytes(rom_bytes)).hexdigest() != sha:
        raise ValueError(f"BLOCKED: variant {variant} ROM hash != manifest rom_sha256 "
                         "(post-build tampering or swapped ROM)")
    if manifest.get("private_media_used") is True:
        raise ValueError(f"BLOCKED: variant {variant} manifest is private-media redacted; "
                         "cannot prove a synthetic SEL")
    if variant == "A":
        if "ipl_sel_lba_redacted" in manifest:
            raise ValueError("BLOCKED: variant A manifest SEL redacted; cannot prove synthetic SEL")
        sel = manifest.get("ipl_sel_lba")
        if type(sel) is not int or not 0 <= sel <= 0xFFFF:
            raise ValueError(f"BLOCKED: variant A manifest ipl_sel_lba bad {sel!r}")
        return sel
    note = manifest.get("ipl_sel")
    if not isinstance(note, str):
        raise ValueError(f"BLOCKED: variant {variant} manifest ipl_sel missing {note!r}")
    import re as _re
    m = _re.fullmatch(r"\$([0-9A-Fa-f]{4}) \(synthetic\)", note.strip())
    if not m:
        raise ValueError(f"BLOCKED: variant {variant} manifest ipl_sel not synthetic {note!r} "
                         "(redacted/private or malformed)")
    return int(m.group(1), 16)


def verify_ipl_phase_matches_sel(phase_lbas, manifest_sel: int, iplblk: int = 2) -> None:
    """Verify measured IPL DATA_IN LBAs match the ROM-bound SEL (pure).

    Issue #789 MEDIUM-1: manifest SEL alone (even hash-bound) cannot prove
    which SEL the ROM actually used at runtime -- a manifest-only SEL edit
    keeps rom_sha256 valid (ROM bytes unchanged). The public IPL loader
    contract (tests/integration/ipl_loader_rom.s) reads the information
    sector at SEL+1 then the body at SEL+IPLBLK (24-bit, Issue #744 carry);
    for the unified disc IPLBLK=2 (case_disc iplblk=2, INFO at SEL+1 carries
    it). This helper requires the caller-supplied hash-bound manifest_sel
    (from read_ipl_sel_from_built_rom, never a test constant) plus the
    measured DATA_IN (sector, count) tuples for one cold/warm cycle, and
    requires INFO (sel+1,1) and BODY (sel+iplblk,1) to each appear at least
    once, with INFO observed before BODY (first-occurrence order; duplicate
    re-reads of either sector are allowed). Extra reads are allowed (the
    loader may re-read); subset + order, never exact-equality (avoids
    brittleness from duplicate reads). The order is grounded in the loader
    program (INFO read/validate before BODY read/copy in ipl_loader_rom.s)
    and in measured PHASE preserving execution order; a reversed
    BODY-before-INFO sequence, a missing sector, unmeasured PHASE, or an
    unparsable SEL -> BLOCKED. Expected LBAs are derived from
    manifest_sel+iplblk (build contract), never reconstructed from PHASE
    alone (passing None as manifest_sel is BLOCKED). Does not confuse
    follow-up service reads (D link READ at SEL+3) with IPL load reads:
    callers pass only the IPL variant's measured PHASE (A/B/C halt before
    any service READ).
    """
    if type(manifest_sel) is not int or not 0 <= manifest_sel <= 0xFFFF:
        raise ValueError(f"BLOCKED: IPL manifest SEL bad {manifest_sel!r} (need 16-bit int)")
    if type(iplblk) is not int or isinstance(iplblk, bool) or not 0 <= iplblk <= 0xFFFFFF:
        raise ValueError(f"BLOCKED: IPL block bad {iplblk!r}")
    if not isinstance(phase_lbas, (list, tuple)) or not phase_lbas:
        raise ValueError("BLOCKED: IPL PHASE unmeasured or missing (need DATA_IN LBAs)")
    seen = set()
    for item in phase_lbas:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError(f"BLOCKED: IPL PHASE entry malformed {item!r}")
        sector, count = item
        if type(sector) is not int or isinstance(sector, bool) or type(count) is not int or isinstance(count, bool):
            raise ValueError(f"BLOCKED: IPL PHASE entry bad types {item!r}")
        seen.add((sector, count))
    info = ((manifest_sel + 1) & 0xFFFFFF, 1)
    body = ((manifest_sel + iplblk) & 0xFFFFFF, 1)
    for want, name in ((info, "INFO"), (body, "BODY")):
        if want not in seen:
            raise ValueError(f"BLOCKED: IPL {name} read {want!r} missing from measured PHASE "
                             f"(manifest SEL ${manifest_sel:04X} not proven at runtime)")
    # Order: first INFO DATA_IN must precede first BODY DATA_IN (duplicates
    # allowed). Loader reads INFO, validates, then reads BODY; measured PHASE
    # preserves that execution order.
    order = [(s, c) for (s, c) in phase_lbas
             if isinstance(s, int) and not isinstance(s, bool)]
    try:
        first_info = next(i for i, (s, c) in enumerate(order) if (s, c) == info)
        first_body = next(i for i, (s, c) in enumerate(order) if (s, c) == body)
    except StopIteration:
        raise ValueError("BLOCKED: IPL INFO/BODY order unobservable in measured PHASE")
    if first_info > first_body:
        raise ValueError(f"BLOCKED: IPL BODY {body!r} observed before INFO {info!r} "
                         f"(reversed loader order; manifest SEL ${manifest_sel:04X} unproven)")


def verify_matched_selection(*, d_manifest: dict, abc_sels: list,
                             unified_code: bytes, executed: dict | None) -> None:
    """Verify A/B/C/D matched-selection on one synthetic medium (pure).

    Issue #789: all four variants must share the same synthetic CUE/BIN,
    track, INDEX01 LBA, payload bytes, selection record, and run conditions.
    D's embedded selection is cross-checked via
    check_embedded_selection_vs_executed (LBA coincidence alone never
    suffices; identity required both sides). Each A/B/C entry in abc_sels
    is {"variant": "A"/"B"/"C", "lba": int, "track": int} (the SEL/track
    the ROM was built with); every entry must equal the executed
    track/LBA. unified_code must be 1..255 bytes starting at EXEC and must
    match D's manifest payload_sha256/payload_bytes (both mandatory here; a
    missing/malformed/mismatched payload field is BLOCKED, never a silent
    substitution). executed is
    {"lba": int, "track": int, "identity": dict} from selection_from_cue +
    media_identity on the exact mounted CUE, or None when unmeasured
    (BLOCKED, never a pass). Mismatch on any side -> BLOCKED. Never guesses;
    OBSERVED/INFERRED/UNKNOWN classification happens in the caller from
    measured probe records, never here.
    """
    if executed is None or not isinstance(executed, dict):
        raise ValueError("BLOCKED: executed matched-selection unmeasured; "
                         "cannot verify A/B/C/D inputs")
    for key in ("lba", "track", "identity"):
        if key not in executed:
            raise ValueError(f"BLOCKED: executed matched-selection lacks {key}")
    ex_lba = executed.get("lba")
    ex_track = executed.get("track")
    if not isinstance(ex_lba, int) or isinstance(ex_lba, bool):
        raise ValueError("BLOCKED: executed matched-selection LBA is not an int")
    if not isinstance(ex_track, int) or isinstance(ex_track, bool):
        raise ValueError("BLOCKED: executed matched-selection track is not an int")
    # D embedded vs executed (LBA + identity, never LBA alone).
    check_embedded_selection_vs_executed(d_manifest, {"lba": ex_lba, "identity": executed.get("identity")})
    # D embedded track vs executed track (strict int equality; bool counts
    # as malformed, never as 0/1).
    d_sel = d_manifest.get("selection_contract") if isinstance(d_manifest, dict) else None
    d_track = d_sel.get("track") if isinstance(d_sel, dict) else None
    if type(d_track) is not int or d_track != ex_track:
        raise ValueError(f"BLOCKED: embedded D track {d_track!r} != executed track {ex_track!r}")
    # A/B/C built SEL/track vs executed.
    if not isinstance(abc_sels, list) or not abc_sels:
        raise ValueError("BLOCKED: matched-selection needs A/B/C built inputs")
    seen = set()
    for entry in abc_sels:
        if not isinstance(entry, dict):
            raise ValueError("BLOCKED: matched-selection A/B/C entry is not an object")
        variant = entry.get("variant")
        if variant not in ("A", "B", "C"):
            raise ValueError(f"BLOCKED: matched-selection bad variant {variant!r}")
        if variant in seen:
            raise ValueError(f"BLOCKED: matched-selection duplicate variant {variant}")
        seen.add(variant)
        for key in ("lba", "track"):
            if entry.get(key) != (ex_lba if key == "lba" else ex_track):
                raise ValueError(f"BLOCKED: matched-selection variant {variant} {key} "
                                 f"{entry.get(key)!r} != executed {key} "
                                 f"{(ex_lba if key == 'lba' else ex_track)!r}")
    if seen != {"A", "B", "C"}:
        raise ValueError(f"BLOCKED: matched-selection needs A/B/C, got {sorted(seen)}")
    # Payload bytes vs D manifest (mandatory for Issue #789
    # matched-selection; matched-selection callers must record both).
    # Existing two-block manifests and other validators are untouched;
    # only this function requires both fields.
    if not isinstance(unified_code, (bytes, bytearray)) or not 1 <= len(unified_code) <= 255:
        raise ValueError("BLOCKED: matched-selection payload must be 1..255 bytes")
    if not isinstance(d_manifest, dict):
        raise ValueError("BLOCKED: experimental manifest is not an object")
    import hashlib as _hl
    sha = d_manifest.get("payload_sha256")
    if not isinstance(sha, str) or not _SHA256_RE.match(sha):
        raise ValueError(f"BLOCKED: matched-selection payload_sha256 missing or malformed {sha!r}")
    if sha != _hl.sha256(bytes(unified_code)).hexdigest():
        raise ValueError("BLOCKED: matched-selection payload bytes != D manifest payload_sha256")
    plen = d_manifest.get("payload_bytes")
    if type(plen) is not int or plen != len(unified_code):
        raise ValueError(f"BLOCKED: matched-selection payload_bytes bad {plen!r} "
                         f"(need int len {len(unified_code)})")


# ---------------------------------------------------------------------------
# MAJOR-2: real-service return proof (never manifest alone).
# ---------------------------------------------------------------------------

REAL_SERVICE_PROOF_KEYS = (
    "arrived_at_callee",
    "post_return_position_observed",
    "sp_restored",
    "mpr_consistent",
    "order_matches_expected",
)


def validate_real_service_proof(proof) -> tuple:
    """Check a D real-service return-proof record (pure).

    proof must be a dict with all five gates True (bool-True, not truthy):
      arrived_at_callee, post_return_position_observed, sp_restored,
      mpr_consistent, order_matches_expected.
    Returns (ok, reason). Unmeasured (None/non-dict/missing/False) -> not ok.
    Budget-exhaustion or stub-halt alone never satisfies this: the caller
    must have measured each gate from probe records.
    """
    if not isinstance(proof, dict):
        return False, "real-service proof unmeasured (not an object)"
    for key in REAL_SERVICE_PROOF_KEYS:
        if proof.get(key) is not True:
            return False, f"real-service gate unproven: {key}"
    return True, ""


def build_real_service_proof(*, arrived_at_callee: bool = False,
                             post_return_position_observed: bool = False,
                             sp_restored: bool = False,
                             mpr_consistent: bool = False,
                             order_matches_expected: bool = False) -> dict:
    """Build a real-service proof record with strict-bool gates (pure)."""
    for name, val in (("arrived_at_callee", arrived_at_callee),
                      ("post_return_position_observed", post_return_position_observed),
                      ("sp_restored", sp_restored),
                      ("mpr_consistent", mpr_consistent),
                      ("order_matches_expected", order_matches_expected)):
        if type(val) is not bool:
            raise ValueError(f"real-service gate {name} must be bool")
    return {"arrived_at_callee": arrived_at_callee,
            "post_return_position_observed": post_return_position_observed,
            "sp_restored": sp_restored,
            "mpr_consistent": mpr_consistent,
            "order_matches_expected": order_matches_expected}


def derive_real_service_proof(cold_calls: list, warm_calls: list,
                              observed_slots: list,
                              expected_slots: tuple | list | None) -> dict:
    """Derive a real-service proof from measured probe records (pure).

    cold_calls/warm_calls: detailed APICALL dicts (with sp_before/mpr/...).
    observed_slots: ordered slot ints from call_sequence().
    expected_slots: manifest slots (normalized) or None when the manifest
      linkage is absent (then order is unmeasured -> False, never guessed).

    Gates (all must be True for REAL_SERVICE_RETURN):
      arrived: every returned call (all but the stub-halted last) has
        table entry + SP-delta 2 + stack MATCH + return flag True;
      post_return: at least 2 calls (a next position exists);
      sp: every later sp_before equals the first sp_before (chain restored);
      mpr: every call mpr equals the first call mpr (consistent);
      order: expected known and observed starts with expected in order.
    Missing/unmeasured fields -> False (fail-closed, never True by default).
    """
    def _strict(b) -> bool:
        return b is True

    arrived = False
    post_return = False
    sp_ok = False
    mpr_ok = False
    order_ok = False
    try:
        if isinstance(observed_slots, list) and len(observed_slots) >= 2:
            post_return = True
            nret = len(observed_slots) - 1
            # Returned calls are all but the last (stub-halted, non-returning).
            returned_cold = (cold_calls or [])[:nret] if isinstance(cold_calls, list) else []
            returned_warm = (warm_calls or [])[:nret] if isinstance(warm_calls, list) else []
            if (returned_cold and len(returned_cold) == nret
                    and returned_warm and len(returned_warm) == nret):
                def _arrived_ok(calls):
                    return all(
                        isinstance(c, dict)
                        and c.get("in_table") is True
                        and c.get("sp_delta") == 2
                        and c.get("stack_check") == "MATCH"
                        and c.get("return_matches_caller") is True
                        for c in calls
                    )
                arrived = _arrived_ok(returned_cold) and _arrived_ok(returned_warm)
            # SP chain: every call (cold + warm) sp_before equals the first.
            sp_calls = []
            if isinstance(cold_calls, list):
                sp_calls.extend(cold_calls)
            if isinstance(warm_calls, list):
                sp_calls.extend(warm_calls)
            if (isinstance(cold_calls, list) and len(cold_calls) >= 2
                    and isinstance(warm_calls, list) and warm_calls):
                first_sp = (cold_calls[0] or {}).get("sp_before")
                if isinstance(first_sp, int):
                    sp_ok = all(
                        isinstance(c, dict) and c.get("sp_before") == first_sp
                        for c in sp_calls
                    )
            # MPR consistency: every call (cold + warm) mpr equals the first.
            mpr_calls = sp_calls
            if (isinstance(cold_calls, list) and cold_calls
                    and isinstance(warm_calls, list) and warm_calls):
                first_mpr = (cold_calls[0] or {}).get("mpr")
                if isinstance(first_mpr, list) and len(first_mpr) == 8:
                    mpr_ok = all(
                        isinstance(c, dict) and c.get("mpr") == first_mpr
                        for c in mpr_calls
                    )
            # Expected order: observed starts with manifest expected slots.
            if expected_slots is not None:
                try:
                    exp = [normalize_slot_key(s) for s in expected_slots]
                    obs = [normalize_slot_key(s) for s in observed_slots]
                except ValueError:
                    order_ok = False
                else:
                    order_ok = len(obs) >= len(exp) and obs[:len(exp)] == exp
    except (TypeError, AttributeError, IndexError):
        pass
    return {"arrived_at_callee": _strict(arrived and True or arrived),
            "post_return_position_observed": bool(post_return),
            "sp_restored": bool(sp_ok),
            "mpr_consistent": bool(mpr_ok),
            "order_matches_expected": bool(order_ok)}


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


# ---------------------------------------------------------------------------
# MEDIUM-2: G5_OBSERVED evidence bar (structured, never string-only).
# ---------------------------------------------------------------------------

TITLE_EVIDENCE_KEYS = (
    "source",
    "target",
    "cold_warm",
    "confirmation_method",
    "fake_rts_relation",
)


def validate_title_evidence(evidence) -> tuple:
    """Check structured title-observation evidence (pure).

    Required string fields (non-empty after strip):
      source (actual observation source, e.g. human/frame-hash/probe),
      target (observed title target, structural; never a private media name),
      cold_warm (must state both cycles observed: True/"both"/"cold+warm"),
      confirmation_method (how the title was confirmed),
      fake_rts_relation (how fabrication relates: e.g. "no fabrication" for
        A/D-measured-real, "fabricated $02 RTS; post-return conditional"
        for B/C).
    Returns (ok, reason). Anything missing/empty -> not ok.
    """
    if not isinstance(evidence, dict):
        return False, "title evidence unmeasured (not an object)"
    for key in TITLE_EVIDENCE_KEYS:
        if key not in evidence:
            return False, f"title evidence missing {key}"
    for key in ("source", "target", "confirmation_method", "fake_rts_relation"):
        val = evidence.get(key)
        if not isinstance(val, str) or not val.strip():
            return False, f"title evidence {key} must be a non-empty string"
    cw = evidence.get("cold_warm")
    if cw is True:
        pass
    elif isinstance(cw, str) and cw.strip().lower() in ("both", "cold+warm", "cold/warm"):
        pass
    else:
        return False, "title evidence cold_warm must state both cycles (True/'both')"
    return True, ""


def build_title_evidence(*, source: str, target: str, cold_warm,
                         confirmation_method: str, fake_rts_relation: str) -> dict:
    """Build a structured title-evidence record (pure; strict types)."""
    if not isinstance(source, str) or not source.strip():
        raise ValueError("title source must be a non-empty string")
    if not isinstance(target, str) or not target.strip():
        raise ValueError("title target must be a non-empty string")
    if not isinstance(confirmation_method, str) or not confirmation_method.strip():
        raise ValueError("title confirmation_method must be a non-empty string")
    if not isinstance(fake_rts_relation, str) or not fake_rts_relation.strip():
        raise ValueError("title fake_rts_relation must be a non-empty string")
    if cold_warm is not True and not (
            isinstance(cold_warm, str) and cold_warm.strip().lower() in ("both", "cold+warm", "cold/warm")):
        raise ValueError("title cold_warm must be True or 'both'")
    return {"source": source.strip(), "target": target.strip(),
            "cold_warm": cold_warm if isinstance(cold_warm, bool) else cold_warm.strip(),
            "confirmation_method": confirmation_method.strip(),
            "fake_rts_relation": fake_rts_relation.strip()}


def title_status(*, observed: bool, basis: str, screen_changed: bool,
                 evidence: dict | None = None) -> dict:
    """Title-screen evidence status (pure).

    Screen change alone NEVER equals title. G5_OBSERVED requires observed
    True AND a non-empty stated confirmation basis AND structured evidence
    (source/target/cold-warm/confirmation/fake-RTS relation); a bare
    observed=True plus a string is INSUFFICIENT. Anything less caps at
    NOT_CONFIRMED. The basis string itself is the confirmation record (e.g.
    human-confirmed frame hash + observed title state description), but it
    never substitutes for the structured observation.
    """
    if not isinstance(observed, bool):
        raise ValueError("title observed must be bool")
    if not isinstance(basis, str):
        raise ValueError("title basis must be str")
    basis = basis.strip()
    if observed and basis and evidence is not None:
        ok, _ = validate_title_evidence(evidence)
        if ok:
            return {"title": "G5_OBSERVED_CANDIDATE", "confirmed": True, "basis": basis,
                    "evidence": dict(evidence)}
    if screen_changed and not (observed and basis):
        return {"title": "SCREEN_CHANGE_NOT_TITLE", "confirmed": False, "basis": ""}
    if observed and basis and evidence is None:
        # Claimed observation without structured evidence: recorded as a
        # claim, never a confirmation (MEDIUM-2).
        return {"title": "CLAIMED_UNVERIFIED", "confirmed": False, "basis": basis}
    return {"title": "NOT_OBSERVED", "confirmed": False, "basis": ""}


def classify_g5(*, variant_key: str, calls: list, runs: list,
                stub_addr: int | None, return_kind: str,
                cold_warm_identical: bool,
                frame_progress: bool = False,
                screen_changed: bool = False,
                title_observed: bool = False,
                title_basis: str = "",
                title_evidence: dict | None = None,
                next_blocker: str | None = None,
                real_service_proof: dict | None = None,
                expected_slots: tuple | list | None = None) -> tuple:
    """Primary G5 verdict for one variant run (pure, host-testable).

    calls: ordered [(slot, method)] with method in JSR/JMP (validated
      upstream); empty means no API entry. runs: two RUN dicts with
      reason/halt_confirmed/pc/x. stub_addr: verified stub or None.
    return_kind: NONE/FAKE_RTS/REAL_SERVICE for the executed image.
    cold_warm_identical: True only when call fields AND halt states match
      across cold/warm (steps never compared).
    frame_progress/screen_changed/title_*: frame-tracer/title inputs.
    title_evidence: structured observation (source/target/cold-warm/
      confirmation/fake-RTS relation); observed+basis alone never confirms.
    real_service_proof: for D, the five measured gates
      (arrived/post-return/SP/MPR/order); manifest declaration alone never
      yields REAL_SERVICE_RETURN. expected_slots: manifest slots to
      cross-check observed order (None = unmeasured order).
    Returns (verdict, detail). G5_OBSERVED only with observed title +
    stated basis + structured evidence; screen change alone never promotes.
    Budget-exhaustion or stub-halt alone NEVER yields REAL_SERVICE_RETURN.
    A D single-entry stub halt is API_ENTRY_CONFIRMED (no return proven),
    never REAL_SERVICE_RETURN.
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
        # Budget-exhaustion is never a real-service return (MAJOR-2).
        if calls:
            return "NEXT_BLOCKER", f"return observed then budget without confirmed halt ({variant_key})"
        return "UNKNOWN", "budget without confirmed halt and no API entry"
    if not cold_warm_identical:
        return "UNKNOWN", "cold/warm diverge (calls or halt states differ)"
    # Title gating comes first, but a bare claim without structured
    # observation is never G5_OBSERVED (MEDIUM-2). An observed claim with no
    # real observation infrastructure stays UNKNOWN (detection unimplemented).
    if bool(title_observed):
        t_ev_ok, t_ev_why = validate_title_evidence(title_evidence)
        if not t_ev_ok:
            # Distinguish "claimed but unproven" (basis given, no structure)
            # from "no basis at all": both are UNKNOWN, never G5_OBSERVED,
            # and the detail states that detection is unimplemented or the
            # evidence is incomplete.
            return "UNKNOWN", (f"title claimed without real observation ({t_ev_why}); "
                                f"title detection unimplemented or evidence incomplete")
    t = title_status(observed=bool(title_observed), basis=title_basis,
                     screen_changed=bool(screen_changed), evidence=title_evidence)
    # Title gating: an observed+confirmed title is G5_OBSERVED only when API
    # progress also exists (a title without boot calls is not a boot
    # measurement). Without API progress it stays UNKNOWN.
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
            # MAJOR-2: stub-halt alone NEVER yields REAL_SERVICE_RETURN.
            # A single stub-halted entry proves entry, not a return.
            if len(calls) < 2:
                return "API_ENTRY_CONFIRMED", (f"table entry sequence {calls}; stub halt on first call "
                                               f"(no return proven; next blocker {nb})")
            ok, why = validate_real_service_proof(real_service_proof)
            if not ok:
                return "UNKNOWN", (f"real-service return unproven ({why}); "
                                    f"stub-halt alone never yields REAL_SERVICE_RETURN")
            if expected_slots is not None:
                try:
                    exp = [normalize_slot_key(s) for s in expected_slots]
                    obs = [normalize_slot_key(s) for s, _m in calls]
                except (ValueError, TypeError):
                    return "UNKNOWN", "expected/observed slot order unmeasurable"
                if not (len(obs) >= len(exp) and obs[:len(exp)] == exp):
                    return "UNKNOWN", (f"observed order {obs} does not start with "
                                        f"expected {[f'${s:02X}' for s in exp]}")
            return "REAL_SERVICE_RETURN", (f"real return led to sequence {calls}; "
                                           f"next blocker {nb}")
        return "API_ENTRY_CONFIRMED", (f"table entry sequence {calls}; stub halt on last call "
                                       f"(no return; next blocker {nb})")
    # Calls + non-stub confirmed halt: returned then blocked elsewhere.
    if return_kind == "REAL_SERVICE":
        # A D non-stub halt still claims a return; without measured proof it
        # is UNKNOWN, never a promoted return verdict.
        ok, why = validate_real_service_proof(real_service_proof)
        if not ok:
            return "UNKNOWN", f"real-service return unproven for non-stub halt ({why})"
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


# ---------------------------------------------------------------------------
# MAJOR-3: public-evidence sanitization (allow-schema + deep recursion).
# ---------------------------------------------------------------------------

# Allow-schema: every key that may appear in PUBLIC evidence. Anything else
# that matches a private pattern is refused; unknown-but-innocent keys still
# pass value checks (fail-closed on values, not on novelty). The safe title
# and media markers below are the only keys allowed to contain the private
# substrings "title"/"media".
PUBLIC_EVIDENCE_ALLOW_KEYS = frozenset({
    "schema", "agent", "parent_tracker", "head", "mode",
    "pinned_geargrafx", "pinned_geargrafx_short", "pinned_cc65", "pinned_cc65_short",
    "reference_bios_jsr", "abi_verified", "dev_callable", "release_eligible",
    "compatible_system_card_bios", "geargrafx_observations_are_hardware_proof",
    "issue", "title_basis_stated",
    "variants_synthetic", "comparison_abc", "abc_differential",
    "progress_vs_baseline", "commercial",
    # Per-variant structural keys.
    "variant", "return_kind", "verdict", "detail", "api_count", "last_slot",
    "sequence_cold", "sequence_warm", "cold_warm_identical",
    "sp_chain_restored_cold", "stop_cold", "stop_warm", "runs",
    "call_count_cold", "call_count_warm", "first_caller_pc", "insn_steps",
    "frames", "title", "stub_addr", "calls_cold",
    "rom_name", "ipl_format", "media",
    "manifest_slots", "branch", "wiring_desc", "native",
    "cause", "evidence", "cycle", "reason", "pc", "x", "halt_confirmed",
    "step", "apicall_truncated",
    "slot", "caller_pc", "entry_pc", "sp_before", "sp_after", "sp_delta",
    "caller_a", "caller_x", "caller_y", "caller_p", "mpr", "stack_check",
    "return_matches_caller", "in_table",
    "status", "progress", "screen_changed",
    "confirmed", "basis", "source", "target", "cold_warm",
    "confirmation_method", "fake_rts_relation",
    "apis", "last", "api_delta", "summary",
    "results", "note", "observation",
    "real_service_proof", "expected_slots",
    "arrived_at_callee", "post_return_position_observed", "sp_restored",
    "mpr_consistent", "order_matches_expected",
})

# Exact safe keys that contain an otherwise-private substring.
_SAFE_EXACT_KEYS = frozenset({
    "title", "title_basis_stated", "media",
    "confirmed", "basis", "source", "target", "cold_warm",
    "confirmation_method", "fake_rts_relation",
    "rom_name", "rom_sha256", "payload_sha256",
    "pinned_geargrafx", "pinned_cc65",
})

# Any key containing one of these (case-insensitive) is private, unless it
# is in _SAFE_EXACT_KEYS (exact match). "rom" alone is NOT blocked so that
# structural rom_name survives; rom_dir/rom_path are blocked via "path"/"_dir".
_PRIVATE_KEY_SUBSTRINGS = (
    "cue", "media", "media_identity", "rom_dir", "dump", "save", "trace",
    "trace_bytes", "stack_b", "stack_word", "ram_dump", "image_bytes",
    "snapshot", "bin_path", "file_path", "vdc_dump", "vdc",
    "path", "file", "hash", "sha256", "sha512", "md5",
    "cue_path", "bin", "iso", "title", "title_name", "game_title",
    "trace_dump", "save_path",
)


def _is_private_key(key: str) -> bool:
    kl = str(key).strip().lower()
    if kl in _SAFE_EXACT_KEYS:
        return False
    # "media" alone is safe (structural "synthetic"/"commercial-structural-only"
    # marker); any longer media key (media_hash/media_path/...) is private.
    if kl == "media":
        return False
    # "title" alone is safe (structured verdict); longer title keys holding a
    # private name (title_name/game_title/...) are private.
    if kl == "title":
        return False
    for sub in _PRIVATE_KEY_SUBSTRINGS:
        if sub in kl:
            # "title"/"media" already handled; any other hit is private.
            # Note "trace" blocks trace_dump but no safe key contains it.
            return True
    return False


_PRIVATE_VALUE_RES = (
    # Media filenames: .bin/.cue/.iso/.chd (case-insensitive). The
    # trailing boundary keeps plain words ("binary") and dotted words
    # ("foo.binary") passing while "game.bin"/".BIN\"/"x.chd" refuse.
    # NOTE: .pce is intentionally NOT a value-pattern here: the fix for the
    # experimental-ROM filename leak kills the publication path itself
    # (harness D rom_name is a fixed structural identifier), not the
    # extension string.
    re.compile(r"\.(bin|cue|iso|chd)(?![A-Za-z0-9])", re.I),
    # Private checkout/media dir marker (legacy ./rom/ and /rom/).
    re.compile(r"\./rom/|/rom/", re.I),
    # Any POSIX absolute-path token: "/" at start-of-value or after a
    # delimiter, followed by a path char. A mid-word slash ("cold/warm",
    # "and/or") is NOT a hit, so structural markers still pass.
    re.compile(r"(?:^|[\s\"'\(\[{=:;,\.])/[^/\s\"']"),
    # Windows drive-absolute (C:\... / C:/..., case-insensitive) and
    # UNC (\\server\share...).
    re.compile(r"[A-Za-z]:[\\/]|\\\\", re.I),
)

# Free-text 64-hex-digit identifier (private media SHA-256 shape).
# Case-insensitive (lower/upper/mixed); token boundaries keep normal PCs
# ($E245/0xE245), slot numbers ($02), ordinary numbers, short git SHAs
# (7-8 chars) and 40-hex repo HEADs passing: only an exact 64-hex token
# refuses. Fixed public revision keys below are exempt field-by-field;
# any other (free-text) field refuses even when an allowlisted key exists
# nearby. The rejection message never re-emits the value.
_SHA64_TOKEN_RE = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{64}(?![0-9A-Fa-f])")

# Fixed public revision keys whose own value may legitimately hold pinned
# hex (head/pinned_*). Exemption is per-field only (leaf key match), never
# a global bypass for sibling free-text fields.
_REVISION_VALUE_ALLOW_KEYS = frozenset({
    "head", "pinned_geargrafx", "pinned_geargrafx_short",
    "pinned_cc65", "pinned_cc65_short",
})


def _leaf_key(key_path: str) -> str:
    """Leaf field name for a sanitizer key_path (pure)."""
    leaf = str(key_path).rsplit(".", 1)[-1]
    # Strip list indices: "results[0]" -> "results".
    if "[" in leaf:
        leaf = leaf.split("[", 1)[0]
    return leaf.strip().lower()


def _check_private_value(value: str, key_path: str) -> None:
    if not isinstance(value, str):
        return
    for rx in _PRIVATE_VALUE_RES:
        if rx.search(value):
            raise ValueError(f"evidence {key_path!r} carries private media/path references (refused)")
    # Fail-closed free-text SHA-256: any 64-hex token in a non-allowlisted
    # field refuses (case-insensitive, recursive via _sanitize_recursive).
    if _leaf_key(key_path) not in _REVISION_VALUE_ALLOW_KEYS:
        if _SHA64_TOKEN_RE.search(value):
            raise ValueError(f"evidence {key_path!r} carries private identifier references (refused)")


def _sanitize_recursive(obj, key_path: str):
    """Deep-check obj for private keys/values; return a sanitized copy (pure)."""
    if isinstance(obj, dict):
        clean = {}
        for key, value in obj.items():
            child_path = f"{key_path}.{key}" if key_path else str(key)
            if _is_private_key(key):
                raise ValueError(f"evidence field {child_path!r} is private (refused for public output)")
            # Safe title/media markers still get value checks below.
            _check_private_value(value, child_path) if isinstance(value, str) else None
            clean[key] = _sanitize_recursive(value, child_path)
        return clean
    if isinstance(obj, (list, tuple)):
        return [_sanitize_recursive(v, f"{key_path}[{i}]") for i, v in enumerate(obj)]
    if isinstance(obj, str):
        _check_private_value(obj, key_path)
        return obj
    return obj


def sanitize_for_public(evidence: dict) -> dict:
    """Return a sanitized copy of harness evidence (pure).

    Allow-schema + deep recursion (MAJOR-3):
      - every key at every depth is checked (nested private fields refused);
      - safe structured fields (title_basis_stated bool, title/media markers
        with structural values) pass key checks but still pass value checks;
      - private media names/paths/hashes/images/raw traces/RAM dumps are
        refused by key (cue/media/dump/save/trace/stack/ram/image/...) and
        by value (./rom/, .cue/.bin/.iso, absolute/POSIX/Windows paths);
      - any 64-hex-digit token in free-text evidence (detail/reason/note/
        nested fields) is refused fail-closed (case-insensitive); only the
        fixed public revision keys (head/pinned_*) are exempt field-by-field;
      - structural counts/slots/PCs/verdicts/pins pass.
    A fingerprint/hash is NOT permission to publish: media hashes are
    refused even when offered as fingerprints (any *hash*/*sha* key outside
    the structural allowlist is refused).
    Raises ValueError on anything private. Never mutates the input.
    """
    if not isinstance(evidence, dict):
        raise ValueError("evidence must be an object")
    return _sanitize_recursive(evidence, "")


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

# ---------------------------------------------------------------------------
# MAJOR-1: real ROM slot-$02 JMP target + slot-$33 table redirect.
# ---------------------------------------------------------------------------


def _parse_slot_arg(slot):
    try:
        return normalize_slot_key(slot)
    except ValueError:
        if isinstance(slot, int) and not isinstance(slot, bool) and 0 <= slot <= 0x50:
            return slot
        raise


def parse_slot_table_target(data, slot):
    """Parse the 3-byte JMP target at ROM table entry $E000+3*slot."""
    if not isinstance(data, (bytes, bytearray)) or len(data) != ROM_BYTES:
        raise ValueError("BLOCKED: experimental ROM bytes unreadable")
    data = bytes(data)
    sl = _parse_slot_arg(slot)
    off = sl * 3
    if off + 3 > len(data):
        raise ValueError("BLOCKED: slot %02X table entry outside ROM" % sl)
    if data[off] != 0x4C:
        raise ValueError("BLOCKED: slot %02X table entry is not a JMP (got %02X)" % (sl, data[off]))
    target = data[off + 1] | (data[off + 2] << 8)
    if not (0xE000 <= target <= 0xFFFF):
        raise ValueError("BLOCKED: slot %02X JMP target %04X outside BIOS window" % (sl, target))
    return target


slot_table_target = parse_slot_table_target
rom_slot_table_target = parse_slot_table_target


def _native_expected_targets(manifest):
    out = {}
    if not isinstance(manifest, dict):
        return out
    base = manifest.get(NATIVE_CD_BASE_KEY)
    if isinstance(base, dict):
        slot = None
        raw_slot = base.get("slot")
        raw_entry = base.get("entry")
        try:
            if raw_slot is not None:
                slot = normalize_slot_key(raw_slot)
            elif raw_entry is not None:
                addr = _parse_native_addr(raw_entry)
                if addr is not None:
                    slot = _native_slot_from_entry(addr)
        except ValueError:
            slot = None
        if slot is None and raw_entry is not None:
            addr = _parse_native_addr(raw_entry)
            if addr is not None:
                try:
                    slot = _native_slot_from_entry(addr)
                except ValueError:
                    slot = None
        target = None
        for key in ("proto_entry", "body", "routine", "target", "entry_target",
                    "callee", "impl_entry", "real_entry"):
            if target is None and base.get(key) is not None:
                target = _parse_native_addr(base.get(key))
        syms = manifest.get("symbols") if isinstance(manifest.get("symbols"), dict) else None
        if target is None and isinstance(syms, dict):
            for name in ("cd_base_proto", "proto_entry", "slot_02_body"):
                if target is None and syms.get(name) is not None:
                    v = syms.get(name)
                    if isinstance(v, int) and not isinstance(v, bool):
                        target = v
                    else:
                        target = _parse_native_addr(v)
        if slot is not None:
            out[slot] = target
    dsp = manifest.get(NATIVE_DSPOFF_KEY)
    if isinstance(dsp, dict):
        raw_table = dsp.get("table_entry")
        slot = None
        if raw_table is not None:
            addr = _parse_native_addr(raw_table)
            if addr is not None:
                try:
                    slot = _native_slot_from_entry(addr)
                except ValueError:
                    slot = None
        target = None
        for key in ("body", "routine", "target", "entry_target", "callee",
                    "impl_entry", "real_entry", "proto_entry"):
            if target is None and dsp.get(key) is not None:
                target = _parse_native_addr(dsp.get(key))
        syms = manifest.get("symbols") if isinstance(manifest.get("symbols"), dict) else None
        if target is None and isinstance(syms, dict):
            for name in ("experimental_slot_33", "ex_dspoff", "slot_33_body"):
                if target is None and syms.get(name) is not None:
                    v = syms.get(name)
                    if isinstance(v, int) and not isinstance(v, bool):
                        target = v
                    else:
                        target = _parse_native_addr(v)
        if slot is not None:
            out[slot] = target
    link = manifest.get(NATIVE_CD_READ_KEY)
    if isinstance(link, dict):
        slot = None
        for raw in (link.get("slot"), link.get("entry"), link.get("table_entry")):
            if slot is None and raw is not None:
                if isinstance(raw, int) and not isinstance(raw, bool):
                    try:
                        slot = normalize_slot_key(raw)
                    except ValueError:
                        slot = None
                else:
                    addr = _parse_native_addr(raw)
                    if addr is not None:
                        try:
                            slot = _native_slot_from_entry(addr)
                        except ValueError:
                            slot = None
                    elif isinstance(raw, str):
                        try:
                            slot = normalize_slot_key(raw)
                        except ValueError:
                            slot = None
        # Target resolution is centralized in _native_link_entry_addr (single
        # BOOT-gated resolver; no duplicated alias key lists here). A range
        # violation raises BLOCKED out of the expected-target computation,
        # which stays fail-closed for every verify_d_rom_slot_targets caller.
        target = _native_link_entry_addr(manifest)
        if slot is not None:
            out[slot] = target
    return out


def _legacy_expected_targets(manifest):
    out = {}
    if not isinstance(manifest, dict):
        return out
    wiring = manifest.get("slot_wiring")
    if isinstance(wiring, dict):
        for slot_key, entry in wiring.items():
            try:
                slot = normalize_slot_key(slot_key)
            except ValueError:
                continue
            if isinstance(entry, dict):
                target = None
                for key in ("target", "body", "routine", "entry_target", "callee",
                            "impl_entry", "real_entry", "proto_entry"):
                    if target is None and entry.get(key) is not None:
                        target = _parse_native_addr(entry.get(key))
                out[slot] = target
    return out


def _all_expected_targets(manifest):
    out = {}
    out.update(_legacy_expected_targets(manifest))
    out.update(_native_expected_targets(manifest))
    return out


def _slot_body_is_fake_rts(data, slot, target):
    data = bytes(data)
    off = target - 0xE000
    if off < 0 or off >= len(data):
        return False
    if data[off] == 0x60:
        return True
    # NOP-slide then RTS: a fabricated immediate return padded with NOPs
    # (e.g. EA 60 / EA EA 60 as produced by RTS+NOP-pad experiments) is the
    # same fabrication as a bare RTS, never real processing. Only leading
    # NOPs count: a real JMP body starts with $4C, so this cannot misfire on
    # the verified JMP+NOP wiring.
    i = off
    while i < len(data) and data[i] == 0xEA and i - off < 4:
        i += 1
    if i > off and i < len(data) and data[i] == 0x60:
        return True
    if off + 5 <= len(data) and data[off] == 0xA2 and data[off + 1] == slot and data[off + 2] == 0x4C:
        return True
    return False


def _manifest_label_addr(manifest, name):
    """Link-derived address for manifest label `name` (pure, read-only).

    Accepts the labels.* link table first, then symbols.* (both are
    link-derived, never hardcoded). Ints pass through; hex strings
    ("$E123"/"0xE123"/"E123") are parsed. Anything else -> None
    (the caller reports BLOCKED with the missing-target reason).
    """
    if not isinstance(manifest, dict):
        return None
    for section in ("labels", "symbols"):
        sec = manifest.get(section)
        if isinstance(sec, dict) and name in sec:
            val = sec[name]
            if isinstance(val, int) and not isinstance(val, bool):
                if 0xE000 <= val <= 0xFFFF:
                    return val
                return None
            parsed = _parse_native_addr(val)
            if parsed is not None and 0xE000 <= parsed <= 0xFFFF:
                return parsed
            return None
    return None


def _native_proto_entry_addr(manifest):
    """Native $02 callee: cd_base_proto.proto_entry (pure, read-only)."""
    if not isinstance(manifest, dict):
        return None
    base = manifest.get(NATIVE_CD_BASE_KEY)
    if not isinstance(base, dict):
        return None
    return _parse_native_addr(base.get("proto_entry"))


def _native_slot33_body_addr(manifest):
    """Native $33 callee body address (pure, read-only).

    Canonical source is experimental_slot_33.body; labels/symbols
    experimental_slot_33 is accepted as the link-derived alias. None when
    the manifest declares the slot without identifying its callee.
    """
    if not isinstance(manifest, dict):
        return None
    dsp = manifest.get(NATIVE_DSPOFF_KEY)
    if isinstance(dsp, dict) and dsp.get("body") is not None:
        addr = _parse_native_addr(dsp.get("body"))
        if addr is not None and 0xE000 <= addr <= 0xFFFF:
            return addr
        return None
    lab = _manifest_label_addr(manifest, "experimental_slot_33")
    if lab is not None:
        return lab
    if isinstance(dsp, dict):
        for key in ("routine", "target", "entry_target", "callee",
                    "impl_entry", "real_entry", "proto_entry"):
            if dsp.get(key) is not None:
                addr = _parse_native_addr(dsp.get(key))
                if addr is not None and 0xE000 <= addr <= 0xFFFF:
                    return addr
    return None


def _verify_native_slot02_two_stage(data, manifest):
    """Verify native $02 two-stage wiring $E006 -> api_slot_02 -> proto (pure).

    Unified BIOS (#784) wiring: the slot table entry $E006 holds JMP
    api_slot_02, and the api_slot_02 stub holds EXACTLY the 5 bytes
    JMP cd_base_proto_entry; NOP; NOP (4C LL HH EA EA). Raises BLOCKED on a
    missing target at either hop, a wrong JMP dest at either hop, a fake
    RTS / non-returning fallback at either hop, or the proto body equalling
    the unimplemented stub. Returns (table_target, proto_entry).
    """
    data = bytes(data)
    api_slot_02 = _manifest_label_addr(manifest, "api_slot_02")
    if api_slot_02 is None:
        raise ValueError("BLOCKED: native slot $02 declares no labels.api_slot_02 "
                         "stub target (missing; refusing self-claimed wiring)")
    proto = _native_proto_entry_addr(manifest)
    if proto is None or not 0xE000 <= proto <= 0xFFFF:
        raise ValueError("BLOCKED: native slot $02 declares no cd_base_proto.proto_entry "
                         "callee target (missing; refusing self-claimed wiring)")
    table_target = parse_slot_table_target(data, 0x02)
    if table_target != api_slot_02:
        raise ValueError("BLOCKED: slot $02 ROM table JMP target %04X != "
                         "manifest labels.api_slot_02 %04X" % (table_target, api_slot_02))
    # First hop body must be the redirect stub, never a return/fallback.
    if _slot_body_is_fake_rts(data, 0x02, table_target):
        raise ValueError("BLOCKED: slot $02 stub %04X is a fake RTS / "
                         "non-returning fallback" % table_target)
    off = api_slot_02 - 0xE000
    if off + 5 > len(data):
        raise ValueError("BLOCKED: slot $02 stub %04X outside BIOS window" % api_slot_02)
    want = bytes((0x4C, proto & 0xFF, (proto >> 8) & 0xFF, 0xEA, 0xEA))
    if bytes(data[off:off + 5]) != want:
        raise ValueError("BLOCKED: slot $02 stub %04X is not JMP proto_entry %04X+NOP pad "
                         "(wrong second-hop dest or patched bytes)" % (api_slot_02, proto))
    # Second hop callee must be real processing, never a stub/return.
    if _slot_body_is_fake_rts(data, 0x02, proto):
        raise ValueError("BLOCKED: slot $02 proto body %04X is a fake RTS / "
                         "non-returning fallback" % proto)
    stub = _manifest_label_addr(manifest, "api_unimplemented")
    if isinstance(stub, int) and proto == stub:
        raise ValueError("BLOCKED: slot $02 proto body %04X equals the unimplemented stub" % proto)
    return table_target, proto


# BOOT-region bounds for the experimental link callee (parity with the
# builder's _redirect_slot03_to_link: $F000 <= entry < $FFF6, keeping the
# callee clear of the API table/fallback area and the vectors).
_LINK_BOOT_START = 0xF000
_LINK_BOOT_VECTORS = 0xFFF6


def _require_link_boot_range(addr) -> int:
    """Fail-closed BOOT-range gate shared by every link_entry resolver path.

    A self-looping table target (e.g. $E009 -> JMP $E009) or any address
    outside BOOT is never the real link helper: BLOCKED, never accepted.
    """
    if (isinstance(addr, int) and not isinstance(addr, bool)
            and _LINK_BOOT_START <= addr < _LINK_BOOT_VECTORS):
        return addr
    raise ValueError("BLOCKED: native cd_read_link entry %r outside BOOT $F000-$FFF5 "
                     "(self-loop/fallback addresses are never real processing)" % (addr,))


# All manifest sources that may name the $03 link callee. Every present
# source must agree (unanimity); omission is allowed for backward
# compatibility (two-block manifests, legitimate alias omission).
_LINK_ALIAS_KEYS = ("link_entry", "body", "routine", "target",
                    "entry_target", "callee", "impl_entry", "real_entry",
                    "proto_entry")


def _checked_link_addr(raw):
    """Parse one link_entry candidate (pure).

    None when absent/unparsable (the caller tries the next alias source);
    the BOOT address when valid; BLOCKED raise when parsable but outside
    BOOT (a self-loop or fallback address hiding in an alias is refused).

    Kept for single-source callers; the strict multi-source resolver below
    (_native_link_entry_addr) requires unanimity across all present aliases
    and treats a present-but-unparsable alias as BLOCKED (never silent skip).
    """
    if raw is None or isinstance(raw, bool):
        return None
    addr = raw if isinstance(raw, int) else _parse_native_addr(raw)
    if addr is None:
        return None
    return _require_link_boot_range(addr)


def _parse_link_candidate(raw, source: str) -> int | None:
    """Parse one present link callee candidate (pure, strict).

    None only when the source is absent (raw is None: legitimate omission,
    the caller skips it). Any present value must parse to a BOOT address:
    a bool, an unparsable string, or an out-of-BOOT address raises BLOCKED
    (a contradictory/malformed alias hiding beside the canonical entry is
    refused, never silently ignored).
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise ValueError("BLOCKED: native cd_read_link alias %s is malformed (%r)" % (source, raw))
    if isinstance(raw, int):
        addr = raw
    else:
        addr = _parse_native_addr(raw)
        if addr is None:
            raise ValueError("BLOCKED: native cd_read_link alias %s is malformed (%r)" % (source, raw))
    return _require_link_boot_range(addr)


def _native_link_entry_addr(manifest):
    """Native $03 callee: cd_read_link.link_entry (pure, read-only).

    Every present alias source must agree on one BOOT address (unanimity):
    cd_read_link.link_entry/body/routine/target/entry_target/callee/
    impl_entry/real_entry/proto_entry plus labels.cd_read_link_entry plus
    symbols.cd_read_link_entry. A present-but-malformed, out-of-BOOT, or
    disagreeing alias raises BLOCKED (a contradiction hiding beside the
    canonical entry is refused). None only when no source names a callee
    (legitimate omission; the two-block manifest stays valid). Every parsed
    address enforces the BOOT range ($F000 <= entry < $FFF6).
    """
    if not isinstance(manifest, dict):
        return None
    candidates: list = []
    link = manifest.get(NATIVE_CD_READ_KEY)
    if isinstance(link, dict):
        for key in _LINK_ALIAS_KEYS:
            if link.get(key) is not None:
                addr = _parse_link_candidate(link.get(key), f"cd_read_link.{key}")
                if addr is not None:
                    candidates.append(addr)
    labs = manifest.get("labels") if isinstance(manifest.get("labels"), dict) else None
    if isinstance(labs, dict) and "cd_read_link_entry" in labs:
        addr = _parse_link_candidate(labs.get("cd_read_link_entry"), "labels.cd_read_link_entry")
        if addr is not None:
            candidates.append(addr)
    syms = manifest.get("symbols") if isinstance(manifest.get("symbols"), dict) else None
    if isinstance(syms, dict) and "cd_read_link_entry" in syms:
        addr = _parse_link_candidate(syms.get("cd_read_link_entry"), "symbols.cd_read_link_entry")
        if addr is not None:
            candidates.append(addr)
    if not candidates:
        return None
    first = candidates[0]
    for other in candidates[1:]:
        if other != first:
            raise ValueError("BLOCKED: native cd_read_link aliases disagree "
                             "(%s; refusing contradictory manifest)" % (candidates,))
    return first


def _require_link_entry_prologue(data, link_entry: int) -> None:
    """Fail-closed callee-entry check for the $03 link helper (pure).

    The builder contract (tools/build_experimental_bios_boot.py
    _redirect_slot03_to_link) requires the BOOT callee to start with the
    link prologue PHP,PHA (bytes 08 48, src/cd_read_link.s cd_read_link_entry).
    A bare JMP at the entry -- a BOOT self-loop (e.g. $F1A0: JMP $F1A0) or a
    JMP to another BOOT address -- is never real processing and is refused
    explicitly before the generic prologue comparison. A bare RTS / NOP-slide
    RTS is refused by the existing fake-RTS gate; anything else without the
    PHP,PHA prologue is refused as body drift.

    This entry-shape check is necessary but never sufficient: it does not
    prove the service executed or returned (that proof comes only from
    measured real-core execution: LINK_RES + $2800 transfer + return gates).
    """
    data = bytes(data)
    off = link_entry - 0xE000
    if off < 0 or off + 2 > len(data):
        raise ValueError("BLOCKED: slot $03 link entry %04X outside BIOS window" % link_entry)
    b0 = data[off]
    if b0 == 0x4C:
        if off + 3 > len(data):
            raise ValueError("BLOCKED: slot $03 link entry %04X truncated JMP" % link_entry)
        tgt = data[off + 1] | (data[off + 2] << 8)
        if tgt == link_entry:
            raise ValueError("BLOCKED: slot $03 link entry %04X is a BOOT self-loop "
                             "JMP (never real processing)" % link_entry)
        raise ValueError("BLOCKED: slot $03 link entry %04X is JMP $%04X "
                         "(not the PHP,PHA prologue; never real processing)" % (link_entry, tgt))
    if not (data[off] == 0x08 and data[off + 1] == 0x48):
        raise ValueError("BLOCKED: slot $03 link entry %04X lacks the PHP,PHA prologue "
                         "(got %02X %02X; refusing body drift)" % (link_entry, data[off], data[off + 1]))


def _verify_native_slot03_direct(data, manifest):
    """Verify native $03 direct table redirect $E009 -> link (pure).

    Triple-link wiring: the slot table entry $E009 holds JMP
    cd_read_link_entry (the BOOT-region CD_READ_LINK_V1 helper). The wired
    entry keeps the link's EXPERIMENTAL contract only; genuine $03 ABI
    stays UNKNOWN/BLOCKED. The callee must resolve inside BOOT
    ($F000 <= entry < $FFF6) on every resolver path, so a self-loop such
    as $E009 -> JMP $E009 is refused, and the ROM callee entry must start
    with the builder's PHP,PHA prologue (08 48 per src/cd_read_link.s); a
    BOOT self-loop (e.g. $F1A0: JMP $F1A0), a JMP to another BOOT address,
    a bare RTS, or a bad prologue is BLOCKED. Raises BLOCKED when the manifest
    declares the slot without identifying the link, outside BOOT, on a
    wrong table dest, or on a fake RTS / stub / non-prologue body.
    The entry-shape check alone never proves execution or return; that
    proof comes only from measured real-core transfer + return gates.
    Returns (table_target, link_entry).
    """
    data = bytes(data)
    link = _native_link_entry_addr(manifest)
    if link is None:
        raise ValueError("BLOCKED: native slot $03 declares no link callee target "
                         "(missing; refusing self-claimed wiring)")
    table_target = parse_slot_table_target(data, 0x03)
    if table_target != link:
        raise ValueError("BLOCKED: slot $03 ROM table JMP target %04X != "
                         "manifest-declared link %04X" % (table_target, link))
    if _slot_body_is_fake_rts(data, 0x03, table_target):
        raise ValueError("BLOCKED: slot $03 ROM target %04X is a fake RTS / "
                         "non-returning fallback" % table_target)
    stub = _manifest_label_addr(manifest, "api_unimplemented")
    if isinstance(stub, int) and table_target == stub:
        raise ValueError("BLOCKED: slot $03 ROM target %04X equals the unimplemented stub"
                         % table_target)
    _require_link_entry_prologue(data, table_target)
    return table_target, link


def _verify_native_slot33_direct(data, manifest):
    """Verify native $33 direct table redirect $E099 -> body (pure).

    Raises BLOCKED when the manifest declares the slot without identifying
    the body, on a wrong table dest, or on a fake RTS / stub body.
    Returns (table_target, body).
    """
    data = bytes(data)
    body = _native_slot33_body_addr(manifest)
    if body is None:
        raise ValueError("BLOCKED: native slot $33 declares no body callee target "
                         "(missing; refusing self-claimed wiring)")
    table_target = parse_slot_table_target(data, 0x33)
    if table_target != body:
        raise ValueError("BLOCKED: slot $33 ROM table JMP target %04X != "
                         "manifest-declared body %04X" % (table_target, body))
    if _slot_body_is_fake_rts(data, 0x33, table_target):
        raise ValueError("BLOCKED: slot $33 ROM target %04X is a fake RTS / "
                         "non-returning fallback" % table_target)
    stub = _manifest_label_addr(manifest, "api_unimplemented")
    if isinstance(stub, int) and table_target == stub:
        raise ValueError("BLOCKED: slot $33 ROM target %04X equals the unimplemented stub"
                         % table_target)
    return table_target, body


def verify_d_rom_slot_targets(data, manifest):
    if not isinstance(data, (bytes, bytearray)) or len(data) != ROM_BYTES:
        raise ValueError("BLOCKED: experimental ROM bytes unreadable")
    data = bytes(data)
    if not isinstance(manifest, dict):
        raise ValueError("BLOCKED: experimental manifest is not an object")
    try:
        checked = validate_experimental_manifest(manifest)
    except ValueError as e:
        raise ValueError(str(e)) from None
    slots = list(checked["slots"])
    expected = _all_expected_targets(manifest)
    # stub address for equality check (link-derived labels/symbols only).
    stub = _manifest_label_addr(manifest, "api_unimplemented")
    has_native_base = isinstance(manifest.get(NATIVE_CD_BASE_KEY), dict)
    has_native_dsp = isinstance(manifest.get(NATIVE_DSPOFF_KEY), dict)
    has_native_link = isinstance(manifest.get(NATIVE_CD_READ_KEY), dict)
    for slot in slots:
        # Native $02 is TWO-stage ($E006 -> api_slot_02 -> proto_entry);
        # a one-stage table->proto comparison would BLOCKED even the correct
        # unified (#784) ROM, and a manifest self-claim alone never suffices:
        # both hops are verified against exact ROM bytes below.
        if slot == 0x02 and has_native_base:
            _verify_native_slot02_two_stage(data, manifest)
            continue
        # Native $33 stays a direct table redirect ($E099 -> body).
        if slot == 0x33 and has_native_dsp:
            _verify_native_slot33_direct(data, manifest)
            continue
        # Native $03 stays a direct table redirect ($E009 -> link).
        if slot == 0x03 and has_native_link:
            _verify_native_slot03_direct(data, manifest)
            continue
        target = parse_slot_table_target(data, slot)
        if _slot_body_is_fake_rts(data, slot, target):
            raise ValueError("BLOCKED: slot %02X ROM target %04X is a fake RTS / non-returning fallback" % (slot, target))
        if isinstance(stub, int) and target == stub:
            raise ValueError("BLOCKED: slot %02X ROM target %04X equals the unimplemented stub" % (slot, target))
        # Every declared D slot must identify its callee target; a manifest
        # that declares a slot/entry but omits the body/proto target is
        # BLOCKED, never a skipped ROM comparison.
        exp = expected.get(slot)
        if exp is None:
            raise ValueError("BLOCKED: slot %02X declares no callee target "
                             "(body/proto_entry missing; refusing self-claimed wiring)" % slot)
        if target != exp:
            raise ValueError("BLOCKED: slot %02X ROM JMP target %04X != manifest-declared %04X" % (slot, target, exp))
    if not slots:
        raise ValueError("BLOCKED: no wired slots to verify")


def verify_slot02_jmp_target(*args, **kwargs):
    if len(args) == 2 and isinstance(args[1], dict):
        data, manifest = args
        return verify_d_rom_slot_targets(data, manifest)
    if len(args) >= 2:
        data = args[0]
        slot = args[1]
        sl = _parse_slot_arg(slot)
        target = parse_slot_table_target(data, sl)
        if len(args) >= 3 and args[2] is not None:
            exp = _parse_native_addr(args[2])
            if exp is None and isinstance(args[2], int) and not isinstance(args[2], bool):
                exp = args[2]
            if exp is not None and target != exp:
                raise ValueError("BLOCKED: slot %02X ROM JMP target %04X != expected %04X" % (sl, target, exp))
        if _slot_body_is_fake_rts(data, sl, target):
            raise ValueError("BLOCKED: slot %02X ROM target %04X is a fake RTS" % (sl, target))
        return target
    if "data" in kwargs:
        return verify_slot02_jmp_target(kwargs["data"], kwargs.get("manifest", kwargs.get("slot", 0x02)), kwargs.get("expected"))
    raise ValueError("BLOCKED: invalid arguments")


verify_slot33_table_redirect = verify_slot02_jmp_target
verify_slot02_target = verify_slot02_jmp_target
verify_slot33_redirect = verify_slot02_jmp_target
check_slot02_jmp_target = verify_slot02_jmp_target
check_slot33_table_redirect = verify_slot02_jmp_target
verify_rom_slot_targets = verify_d_rom_slot_targets
check_rom_slot_targets = verify_d_rom_slot_targets
verify_real_rom_slot_targets = verify_d_rom_slot_targets
