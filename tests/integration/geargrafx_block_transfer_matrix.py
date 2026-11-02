#!/usr/bin/env python3
"""Pinned real-core multi-byte transfer, mapping and whole-transfer preflight matrix.

Synthetic BIOS/CD only.  A verification-only Geargrafx patch preflights operands
with Memory::TryPeek only when the code/operand pages are mapped to ordinary WRAM;
it rejects unpeekable inputs before TransferStart.  The destination policy permits
WRAM physical mappings and refuses the whole block otherwise. This is not a
production gate and is not a reference-BIOS/API test.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geargrafx_block_write_guard import build_probes
from geargrafx_reference_guard import synthetic_media

OPS = {"tii": (0, 0x73), "tin": (1, 0xD3), "tia": (2, 0xE3), "tdd": (3, 0xC3), "tai": (4, 0xF3)}
LENGTHS = (1, 2, 3, 4, 8)
LEN_CASES = {f"{op}-len{n}" for op in OPS for n in LENGTHS}
PATTERN = (0x13, 0x27, 0x3B, 0x4F, 0x63, 0x77, 0x8B, 0x9F)
DENY_CASES = tuple(f"{op}-deny-{kind}" for op in OPS for kind in ("hw", "rom", "backup"))
CASES = [f"{op}-len{n}" for op in OPS for n in LENGTHS]
CASES += ["tii-boundary-1fff", "tia-boundary-3fff", "tii-boundary-7fff", "tai-boundary-ffff",
          "tdd-reverse-4000", "tii-boundary-alias", "tii-alias-overlap", "tii-tam-alias",
          "tii-tam-deny", "tii-len0"]
CASES += list(DENY_CASES) + ["tia-deny-nonmono"]

def run(probe, bios, cue, case):
    p = subprocess.run([str(probe), str(bios), str(cue), case], capture_output=True, text=True, timeout=120)
    if p.returncode:
        raise ValueError(f"{case}: probe exit {p.returncode}: {p.stderr[-300:]}")
    return json.loads(p.stderr)

def addr_lists(op, src, dst, n):
    ss, dd = [], []
    s, d = src, dst
    for i in range(n):
        ss.append(s & 0xFFFF); dd.append(d & 0xFFFF)
        if op in (0, 1, 2): s += 1
        elif op == 3: s -= 1
        else: s += -1 if i & 1 else 1
        if op in (0, 4): d += 1
        elif op == 1: pass
        elif op == 2: d += -1 if i & 1 else 1
        else: d -= 1
    return ss, dd

def fixture_operands(case):
    op_name, variant = case.split("-", 1)
    op = OPS[op_name][0]
    if variant.startswith("len"):
        n = int(variant[3:]); src = 0x3E80; dst = 0x3E90
        if op == 3: src += n - 1; dst += n - 1
    elif variant == "boundary-1fff": n,src,dst=2,0x3E80,0x1FFF
    elif variant == "boundary-3fff": n,src,dst=2,0x3E80,0x3FFF
    elif variant == "boundary-7fff": n,src,dst=2,0x3E80,0x7FFF
    elif variant == "boundary-ffff": n,src,dst=2,0x3E80,0xFFFF
    elif variant == "boundary-alias": n,src,dst=2,0x3E80,0x3FFF
    elif variant == "reverse-4000": n,src,dst=2,0x3E80,0x4000
    elif variant in ("deny-hw","deny-rom","deny-backup"):
        n,src,dst=2,0x1E80,(0x4000 if op in (1,3) else 0x3FFF)
    elif variant == "deny-nonmono": n,src,dst=3,0x3E80,0x3FFF
    elif variant in ("tam-alias","tam-deny"): n,src,dst=2,0x3E80,0x4010
    elif variant == "alias-overlap": n,src,dst=3,0x4000,0x2001
    else: raise ValueError(f"unknown fixture operands: {case}")
    return src,dst,n

def expected_transfer(row, case):
    op=OPS[case.split("-")[0]][0]
    n=row["length"]
    srcs=row["src_wram_index"]; dsts=row["dst_wram_index"]
    memory={}
    if case.endswith("alias-overlap"):
        memory.update({0:0x11,1:0x22,2:0x33,3:0x44})
    else:
        # Fixture source seeding follows the core's operand-address sequence;
        # repeated TAI source addresses are overwritten by later seed entries.
        for i,index in enumerate(srcs):
            if index >= 0: memory[index]=PATTERN[i]
    for index in dsts:
        if index >= 0 and not (case.endswith("alias-overlap") and index < 4): memory[index]=0xCC
    # TransferStart's ordinary WRAM stack pushes precede the first byte step.
    # safeabort-armed is classified separately and never reaches this model.
    if row["preflight_allow"]:
        # S=$FF, MPR1=$F8 for all allow cases.
        memory[0x1FF]=0x07; memory[0x1FE]=0x12; memory[0x1FD]=0x34
        for i in range(n):
            si=srcs[i]; di=dsts[i]
            val=memory.get(si,0xFF)
            if di >= 0: memory[di]=val
    return [memory.get(index,0xFFFFFFFF) if index>=0 else 0xFFFFFFFF for index in dsts]

def assert_core_row(row, case, *, deny=False):
    op_name = case.split("-")[0]; op, opcode = OPS[op_name]
    if row.get("status") != "ok" or row.get("op") != op or row.get("opcode") != opcode:
        raise ValueError(f"{case}: wrong operation/status")
    if row.get("preflight_calls") != 1:
        raise ValueError(f"{case}: preflight callback count {row.get('preflight_calls')}")
    if type(row.get("preflight_src")) is not list or type(row.get("preflight_dst")) is not list:
        raise ValueError(f"{case}: missing preflight address plan")
    src,dst,length=fixture_operands(case)
    if row["src"] != src or row["dst"] != dst or row["length"] != length:
        raise ValueError(f"{case}: decoded operands disagree with fixture")
    if case.endswith("tam-alias") and row["mpr_after"][2] != 0xF9:
        raise ValueError(f"{case}: guest TAM MPR2 alias not observed")
    if case.endswith("tam-deny") and row["mpr_after"][2] != 0xFF:
        raise ValueError(f"{case}: guest TAM forbidden MPR2 mapping not observed")
    expected_src,expected_dst=addr_lists(op,row["src"],row["dst"],row["length"])
    if row["preflight_src"]!=expected_src or row["preflight_dst"]!=expected_dst:
        raise ValueError(f"{case}: source/destination traversal differs from expected sequence")
    if len(row["src_bank"])!=length or len(row["dst_bank"])!=length:
        raise ValueError(f"{case}: missing MPR-resolved page bank for a transfer byte")
    if len(row["mpr_before"]) != 8 or len(row["mpr_after"]) != 8:
        raise ValueError(f"{case}: missing MPR0-7 snapshots")
    if case.endswith("tam-alias") and row["mpr_after"][2] != 0xF9:
        raise ValueError(f"{case}: guest TAM MPR2 remap not observed")
    if case.endswith("tam-deny") and row["mpr_after"][2] != 0xFF:
        raise ValueError(f"{case}: guest TAM forbidden MPR2 remap not observed")
    for logical, bank, physical in zip(row["preflight_src"], row["src_bank"], row["src_wram_index"]):
        expected_bank = row["mpr_after"][logical >> 13]
        if bank != expected_bank or ((bank >= 0xF8 and bank <= 0xFB) != (physical >= 0)):
            raise ValueError(f"{case}: source MPR/physical mapping inconsistent")
    for logical, bank, physical in zip(row["preflight_dst"], row["dst_bank"], row["dst_wram_index"]):
        expected_bank = row["mpr_after"][logical >> 13]
        if bank != expected_bank or ((bank >= 0xF8 and bank <= 0xFB) != (physical >= 0)):
            raise ValueError(f"{case}: destination MPR/physical mapping inconsistent")
    # MPR snapshot at preflight time must equal MPR at transfer end / abort.
    # This holds for all cases: deny (preflight state preserved), allow (no
    # unintended MPR change during transfer), and TAM (TAM change happens
    # before preflight, so preflight_mpr already reflects it).
    if type(row.get("preflight_mpr")) is not list or len(row["preflight_mpr"]) != 8:
        raise ValueError(f"{case}: preflight MPR0-7 snapshot missing or malformed")
    if row["preflight_mpr"] != row["mpr_after"]:
        raise ValueError(f"{case}: MPR changed between preflight and transfer end/abort")
    if case.endswith("len0"):
        if row["preflight_deny"] != 1 or row["preflight_allow"] != 0:
            raise ValueError(f"{case}: length 0 was not denied")
    # length=0 falls through to the whole-instruction deny block below so that
    # CPU/RAM/VDC/MPR/stack immutability is verified with the same checks as
    # other prohibited-destination refusals.  byte_guard_calls/start_calls are 0
    # and dest_values is empty for length=0, which the deny block tolerates.
    if deny:
        if row["preflight_deny"] != 1 or row["preflight_allow"] != 0:
            raise ValueError(f"{case}: prohibited destination did not cause whole-transfer refusal")
        if row["start_calls"] or row["byte_guard_calls"] or row["byte_src"] or row["byte_dst"]:
            raise ValueError(f"{case}: refused transfer entered TransferStart/per-byte path")
        pre=row["preflight_cpu"]
        if row["changed_wram"] or row["sp_after"] != pre[4] or row["p_after"] != pre[3]:
            raise ValueError(f"{case}: preflight refusal left RAM/stack/flag changes")
        if [row["a_after"],row["x_after"],row["y_after"],row["p_after"],row["sp_after"]] != pre:
            raise ValueError(f"{case}: refusal changed CPU state after operand preflight")
        if any(row[key][0] != row[key][1] for key in ("vdc_ar", "vdc_r0", "vdc_r7", "vdc_sr", "vdc_read_buffer")):
            raise ValueError(f"{case}: preflight refusal changed VDC")
        if row["dest_values"] != expected_transfer(row,case):
            raise ValueError(f"{case}: refused preflight altered earlier allowed destination byte")
        if row["stack_wram_after"] != row["stack_wram_before"]:
            raise ValueError(f"{case}: preflight refusal changed stack WRAM")
        return
    if row["preflight_allow"] != 1 or row["preflight_deny"] != 0:
        raise ValueError(f"{case}: expected safe range was denied")
    if row["byte_guard_calls"] != length or row["start_calls"] != 1:
        raise ValueError(f"{case}: expected {length} byte steps and one TransferStart")
    expected_src, expected_dst = addr_lists(op, src, dst, length)
    if row["byte_src"] != expected_src or row["byte_dst"] != expected_dst:
        raise ValueError(f"{case}: observed per-byte order mismatch")
    pre=row["preflight_cpu"]
    if [row["a_after"],row["x_after"],row["y_after"],row["p_after"],row["sp_after"]] != pre:
        raise ValueError(f"{case}: TransferEnd did not restore pre-transfer CPU state")
    expected_dest_values=expected_transfer(row,case)
    if row["dest_values"] != expected_dest_values:
        raise ValueError(f"{case}: destination data/order mismatch {row['dest_values']} != {expected_dest_values}")
    if case.endswith("tam-alias") or case.endswith("tam-deny"):
        pass
    elif row["stack_wram_after"] != [0x34,0x12,0x07]:
        raise ValueError(f"{case}: TransferEnd did not leave expected physical stack frame")
    if any(row[key][0] != row[key][1] for key in ("vdc_ar", "vdc_r0", "vdc_r7", "vdc_sr", "vdc_read_buffer")):
        raise ValueError(f"{case}: WRAM-only transfer changed VDC/I/O state")
    dest_phys={i for i in row["dst_wram_index"] if i>=0}
    allowed_delta=dest_phys | {0x1FD,0x1FE,0x1FF}
    if any(change["index"] not in allowed_delta for change in row["changed_wram"]):
        raise ValueError(f"{case}: unexpected WRAM write outside destination/stack")
    if case.endswith("boundary-alias"):
        if row["dst_bank"] != [0xF8, 0xF9] or row["dst_wram_index"] != [0x1FFF, 0]:
            raise ValueError(f"{case}: adjacent logical pages did not resolve to aliased WRAM bank offsets")
    if row["changed_wram"] is None:
        raise ValueError(f"{case}: missing whole-WRAM delta")

# Change marker: the observation field is removed from the row entirely.
_MISSING = object()

# Audited per-category exclusions (names) and expected (defined, excluded, applied).
# Enforced in mutation_test(); any drift is a FAIL, not a warning.
EXPECTED_EXCLUSIONS = {
    "normal": frozenset(),
    "len0": frozenset({"partial_destination_result", "MPR_alias_resolution"}),
    "tam": frozenset({"TransferEnd_stack"}),
}
MUTATION_DEFINED = 20

def mutation_category(case):
    if case.endswith("len0"):
        return "len0"
    # tam-deny takes the refusal branch, which checks the stack frame; only the
    # allowed TAM transfer skips the physical stack-frame check.
    if case.endswith("tam-alias"):
        return "tam"
    return "normal"

def expected_mutation_counts(case):
    excluded = EXPECTED_EXCLUSIONS[mutation_category(case)]
    return MUTATION_DEFINED, len(excluded), MUTATION_DEFINED - len(excluded)

def _noop_if(condition, reason):
    return ("noop", reason) if condition else None

def mutation_specs(row, case, deny=False):
    """Ordered (name, changes, exclusion) triples for one case.

    changes maps observation fields to replacement values (_MISSING removes the
    field).  exclusion is None (the mutation must be applied and detected) or a
    (kind, reason) pair where kind is 'noop' (declared to leave the observation
    unchanged; verified at run time) or 'unobserved' (the classifier does not
    evaluate the field for this case; verified to be accepted at run time).
    """
    tam = mutation_category(case) == "tam"
    bad_src = row["byte_src"][::-1] or [1]
    bad_dst = row["preflight_dst"][::-1] or [1]
    if bad_src == row["byte_src"]: bad_src[0] ^= 1
    if bad_dst == row["preflight_dst"]: bad_dst[0] ^= 1
    mpr_after = row["mpr_after"]
    preflight_mpr = row["preflight_mpr"]
    specs = [
        ("preflight_callback_missing", {"preflight_calls": 0}, None),
        ("decision_inverted", {"preflight_allow": 1 if deny else 0, "preflight_deny": 0 if deny else 1}, None),
        # Deny mode sets the count to 1 (a 0->1 change), so it is applicable to length=0 too.
        ("transfer_byte_count", {"byte_guard_calls": 1 if deny else max(0, row["byte_guard_calls"] - 1)}, None),
        ("source_update_direction", {"byte_src": bad_src}, None),
        ("destination_update_direction", {"preflight_dst": bad_dst}, None),
        ("MPR_alias_resolution", {"dst_bank": [b ^ 1 for b in row["dst_bank"]]},
         _noop_if(not row["dst_bank"], "no MPR-resolved destination page bank to alias")),
        ("partial_destination_result", {"dest_values": [0] * len(row["dest_values"])},
         _noop_if(not row["dest_values"], "no destination bytes to zero")),
        ("TransferEnd_stack", {"stack_wram_after": [0, 0, 1]},
         ("unobserved", "TAM: classifier does not check the physical stack frame") if tam else None),
        ("VDC_status_effect", {"vdc_sr": [row["vdc_sr"][0], row["vdc_sr"][0] ^ 1]}, None),
        # MPR immutability: the preflight MPR snapshot must match transfer-end/abort MPR.
        ("preflight_mpr_missing", {"preflight_mpr": _MISSING}, None),
        ("preflight_mpr_short", {"preflight_mpr": preflight_mpr[:7]}, None),
        ("preflight_mpr_type_wrong", {"preflight_mpr": [str(b) for b in preflight_mpr]}, None),
    ]
    for j in range(8):
        specs.append((f"MPR{j}_destroyed",
                      {"mpr_after": [b ^ (0xFF if k == j else 0) for k, b in enumerate(mpr_after)]}, None))
    return specs

def _field_state(d, key):
    return ("present", repr(d[key])) if key in d else ("absent", None)

def run_mutation_suite(row, case, deny=False, classify=None, specs=None):
    """Apply each mutation, verify it changed the observation, and count the outcome.

    defined = excluded + applied; applied = detected.  Duplicate observation changes,
    undeclared no-ops, exclusions that contradict the observation, and classifier
    acceptance of an applied mutation are collected and raised at the end
    (fail-closed; explicit raises only, so behaviour is identical under `python -O`).
    """
    if classify is None:
        classify = lambda broken, c, d: assert_core_row(broken, c, deny=d)
    if specs is None:
        specs = mutation_specs(row, case, deny)
    result = {"case": case, "deny": deny, "defined": len(specs), "excluded": [], "applied": [],
              "detected": [], "accepted": [], "duplicate": [], "unexpected_noop": [],
              "exclusion_violation": []}
    names = [name for name, _, _ in specs]
    for i, name in enumerate(names):
        if name in names[:i]:
            result["duplicate"].append(f"{name} (same name)")
    seen = {}
    for name, changes, exclusion in specs:
        broken = dict(row)
        for key, value in changes.items():
            if value is _MISSING:
                broken.pop(key, None)
            else:
                broken[key] = value
        changed = any(_field_state(row, k) != _field_state(broken, k) for k in changes)
        signature = tuple(sorted((k, _field_state(broken, k)) for k in changes))
        if signature in seen:
            result["duplicate"].append(f"{name} duplicates {seen[signature]} (same observation change)")
            continue
        seen[signature] = name
        if exclusion is not None:
            kind, reason = exclusion
            if kind == "noop":
                if changed:
                    result["exclusion_violation"].append(f"{name}: declared no-op but changed the observation")
            elif kind == "unobserved":
                if not changed:
                    result["exclusion_violation"].append(f"{name}: declared unobserved but did not change the row")
                else:
                    try:
                        classify(broken, case, deny)
                    except ValueError:
                        result["exclusion_violation"].append(f"{name}: declared unobserved but classifier rejects it")
            else:
                raise ValueError(f"{case}: unknown exclusion kind {kind!r} for {name}")
            result["excluded"].append({"name": name, "kind": kind, "reason": reason, "changed": changed})
            continue
        if not changed:
            result["unexpected_noop"].append(name)
            continue
        result["applied"].append(name)
        try:
            classify(broken, case, deny)
        except ValueError:
            result["detected"].append(name)
            continue
        result["accepted"].append(name)
    problems = {k: result[k] for k in ("duplicate", "unexpected_noop", "exclusion_violation", "accepted") if result[k]}
    if problems:
        raise ValueError(f"{case}: mutation suite violations {json.dumps(problems, sort_keys=True)}")
    if result["defined"] != len(result["excluded"]) + len(result["applied"]):
        raise ValueError(f"{case}: defined != excluded + applied")
    if len(result["applied"]) != len(result["detected"]):
        raise ValueError(f"{case}: applied != detected")
    return result

def mutation_test(row, case, deny=False):
    result = run_mutation_suite(row, case, deny)
    want = expected_mutation_counts(case)
    got = (result["defined"], len(result["excluded"]), len(result["applied"]))
    if got != want:
        raise ValueError(f"{case}: mutation counts (defined, excluded, applied) {got} != expected {want}")
    names = {e["name"] for e in result["excluded"]}
    if names != EXPECTED_EXCLUSIONS[mutation_category(case)]:
        raise ValueError(f"{case}: excluded mutations {sorted(names)} != expected")
    print("MUTATION_EVIDENCE " + json.dumps(result, sort_keys=True))
    return result

def mutation_totals(results):
    totals = {"defined": 0, "excluded": 0, "applied": 0, "detected": 0,
              "accepted": 0, "duplicate": 0, "unexpected_noop": 0}
    for r in results:
        totals["defined"] += r["defined"]
        totals["excluded"] += len(r["excluded"])
        totals["applied"] += len(r["applied"])
        totals["detected"] += len(r["detected"])
        totals["accepted"] += len(r["accepted"])
        totals["duplicate"] += len(r["duplicate"])
        totals["unexpected_noop"] += len(r["unexpected_noop"])
    if totals["defined"] != totals["excluded"] + totals["applied"]:
        raise ValueError(f"mutation totals: defined {totals['defined']} != excluded+applied")
    if totals["applied"] != totals["detected"]:
        raise ValueError(f"mutation totals: applied {totals['applied']} != detected {totals['detected']}")
    if totals["accepted"] or totals["duplicate"] or totals["unexpected_noop"]:
        raise ValueError(f"mutation totals: violations {totals}")
    return totals

def main():
    expected_cases = {
        # Length matrix: 5 ops x lengths 1/2/3/4/8 = 25
        *[f"{op}-len{n}" for op in OPS for n in LENGTHS],
        # Page boundary: 4
        "tii-boundary-1fff", "tia-boundary-3fff", "tii-boundary-7fff", "tai-boundary-ffff",
        # Reverse traversal: 1
        "tdd-reverse-4000",
        # Physical alias: 1
        "tii-boundary-alias",
        # Source/destination overlap: 1
        "tii-alias-overlap",
        # TAM: 2
        "tii-tam-alias", "tii-tam-deny",
        # Length=0 refusal: 1
        "tii-len0",
        # Prohibited destination: 5 ops x 3 kinds = 15
        *list(DENY_CASES),
        # TIA non-monotonic deny: 1
        "tia-deny-nonmono",
    }
    if len(CASES) != len(expected_cases):
        raise ValueError(f"CASES count {len(CASES)} != expected {len(expected_cases)}")
    if len(set(CASES)) != len(CASES):
        raise ValueError("duplicate cases in CASES")
    if set(CASES) != expected_cases:
        raise ValueError(f"CASES set mismatch: {set(CASES) ^ expected_cases}")
    executed = []
    mutation_results = []
    with tempfile.TemporaryDirectory(prefix="pce-block-matrix-") as tmp:
        d = Path(tmp); bios, cue = synthetic_media(d)
        probe = build_probes(d, "geargrafx_block_transfer_matrix.cpp", "block-transfer-matrix-probe")
        for case in CASES:
            row = run(probe, bios, cue, case)
            deny = case.endswith("len0") or "deny-" in case or case.endswith("tam-deny")
            # Operand fixture: default matrix tests use src=$3E80/dst=$3E90; explicit boundary
            # and denial cases are interpreted from the probe's returned decoded operands.
            if case.endswith("len0"):
                assert_core_row(row, case, deny=True)
            elif case in LEN_CASES or case.endswith("alias-overlap"):
                assert_core_row(row, case)
                if case.endswith("alias-overlap"):
                    if row["src_wram_index"] != [0,1,2] or row["dst_wram_index"] != [1,2,3] or row["dest_values"] != [0x11,0x11,0x11]:
                        raise ValueError("TII overlapping physical WRAM alias did not show sequential read-after-write")
            elif deny:
                assert_core_row(row, case, deny=True)
            else:
                assert_core_row(row, case)
            executed.append(case)
            print(f"REAL_CORE_PASS {case}: calls={row['preflight_calls']} deny={row['preflight_deny']} bytes={row['byte_guard_calls']}")
            print("EVIDENCE " + json.dumps({"observation_kind":"REAL_CORE_EXECUTION","core_revision":"b49ae82a012eade566d72e9d61c9d95a5caa4da3","case":case,"row":row},sort_keys=True))
            mutation_results.append(mutation_test(row,case,deny=deny))
    if executed != CASES:
        raise ValueError(f"case execution order mismatch: executed {len(executed)} vs expected {len(CASES)}")
    if executed[-1] != "tia-deny-nonmono":
        raise ValueError(f"last executed case was {executed[-1]!r}, expected tia-deny-nonmono")
    if [r["case"] for r in mutation_results] != CASES:
        raise ValueError("mutation audit did not complete for every case")
    print("MUTATION_TOTAL " + json.dumps(mutation_totals(mutation_results), sort_keys=True))
    print(f"PASS: {len(CASES)} synthetic pinned real-core matrix cases; production safety remains BLOCKED")

if __name__ == "__main__":
    try: raise SystemExit(main())
    except (ValueError, OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        print(f"FAIL/BLOCKED block transfer matrix: {exc}", file=sys.stderr); raise SystemExit(1)
