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
PATTERN = (0x13, 0x27, 0x3B, 0x4F, 0x63, 0x77, 0x8B, 0x9F)
DENY_CASES = tuple(f"{op}-deny-{kind}" for op in OPS for kind in ("hw", "rom", "backup"))
CASES = [f"{op}-len{n}" for op in OPS for n in LENGTHS]
CASES += ["tii-boundary-1fff", "tia-boundary-3fff", "tii-boundary-7fff", "tai-boundary-ffff",
          "tdd-reverse-4000", "tii-boundary-alias", "tii-alias-overlap", "tii-tam-alias",
          "tii-tam-deny"]
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

def mutation_test(row, case, deny=False):
    bad_src = row["byte_src"][::-1] or [1]
    bad_dst = row["preflight_dst"][::-1] or [1]
    if bad_src == row["byte_src"]: bad_src[0] ^= 1
    if bad_dst == row["preflight_dst"]: bad_dst[0] ^= 1
    mutations = (("preflight_callback_missing", {"preflight_calls":0}),
                 ("decision_inverted", {"preflight_allow":1 if deny else 0,"preflight_deny":0 if deny else 1}),
                 ("transfer_byte_count", {"byte_guard_calls":1 if deny else max(0,row["byte_guard_calls"]-1)}),
                 ("source_update_direction", {"byte_src":bad_src}),
                 ("destination_update_direction", {"preflight_dst":bad_dst}),
                 ("MPR_alias_resolution", {"dst_bank":[b^1 for b in row["dst_bank"]]}),
                 ("partial_destination_result", {"dest_values":[0]*len(row["dest_values"])}),
                 ("TransferEnd_stack", {"stack_wram_after":[0,0,1]}),
                 ("VDC_status_effect", {"vdc_sr":[row["vdc_sr"][0],row["vdc_sr"][0]^1]}))
    for name, change in mutations:
        broken=dict(row);broken.update(change)
        if name == "TransferEnd_stack" and (case.endswith("tam-alias") or case.endswith("tam-deny")):
            continue
        try: assert_core_row(broken,case,deny=deny)
        except (ValueError,KeyError): continue
        raise ValueError(f"{case}: classifier accepted mutation {name}")

def main():
    with tempfile.TemporaryDirectory(prefix="pce-block-matrix-") as tmp:
        d = Path(tmp); bios, cue = synthetic_media(d)
        probe = build_probes(d, "geargrafx_block_transfer_matrix.cpp", "block-transfer-matrix-probe")
        for case in CASES:
            row = run(probe, bios, cue, case)
            deny = "deny-" in case or case.endswith("tam-deny")
            # Operand fixture: default matrix tests use src=$3E80/dst=$3E90; explicit boundary
            # and denial cases are interpreted from the probe's returned decoded operands.
            if "-len" in case or case.endswith("alias-overlap"):
                assert_core_row(row, case)
                if case.endswith("alias-overlap"):
                    if row["src_wram_index"] != [0,1,2] or row["dst_wram_index"] != [1,2,3] or row["dest_values"] != [0x11,0x11,0x11]:
                        raise ValueError("TII overlapping physical WRAM alias did not show sequential read-after-write")
            elif deny:
                assert_core_row(row, case, deny=True)
            else:
                assert_core_row(row, case)
            print(f"REAL_CORE_PASS {case}: calls={row['preflight_calls']} deny={row['preflight_deny']} bytes={row['byte_guard_calls']}")
            print("EVIDENCE " + json.dumps({"observation_kind":"REAL_CORE_EXECUTION","core_revision":"b49ae82a012eade566d72e9d61c9d95a5caa4da3","case":case,"row":row},sort_keys=True))
            mutation_test(row,case,deny=deny)
    print(f"PASS: {len(CASES)} synthetic pinned real-core matrix cases; production safety remains BLOCKED")

if __name__ == "__main__":
    try: raise SystemExit(main())
    except (ValueError, OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        print(f"FAIL/BLOCKED block transfer matrix: {exc}", file=sys.stderr); raise SystemExit(1)
