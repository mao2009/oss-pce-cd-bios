"""Caller-stack contract experiment on the pinned Geargrafx core (Issue #473). Synthetic, test-only HuCARD.

Owner decision (Phase B): the CALLER must guarantee a RAM stack (MPR1 maps WRAM, stack page $2100-$21FF) when it
JSRs an API. This script only observes what the unmodified core does; it approves no production gate.
Three cases, each a separate build of tests/fixtures/caller_stack_contract.s on the real core:
  ram_contract  contract honoured: the guard reads MPR1 = $F8 (RAM), flags it, and A/X/Y/P/S/MPR0-7 are unchanged
                after the RTS. The VDC must see no write, and the stack writes must land in WRAM.
  io_violation  contract violated: the caller sets MPR1 = $FF before the JSR. The guard reads $FF and flags it. The
                side effects are recorded as observed (WRW/VDW events up to the guard's RTS); they are not asserted
                to be harmless. The stack must NOT land in WRAM.
  tii_irq1      IRQ1 is asserted during an 8-byte TII. The IRQ entry must come after the TII has ended
                (huc6280_inline.h:90, HandleIRQ only when m_transfer_state == 0).
Missing tools or core: FAIL (exit 1), never skip. Run on Linux with TOOLS_DIR set (make setup):
  python3 tests/integration/geargrafx_caller_stack_contract.py [--out DIR]
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from environment import DEPENDENCIES, executable  # noqa: E402
import geargrafx_api as api  # noqa: E402

FIXTURE = ROOT / "tests/fixtures/caller_stack_contract.s"
CFG = ROOT / "tests/fixtures/caller_stack_contract.cfg"
PROBE_SOURCE = "geargrafx_caller_stack_probe.cpp"
PROBE_TARGET = "geargrafx-caller-stack-probe"
RECORDS = ("CAP", "TRACE", "WRW", "VDW", "IRQ", "XFER", "HITS", "END")
LABEL_LINE = re.compile(r"^al ([0-9A-Fa-f]{6}) \.(\w+)$", re.M)
LABELS = ("pre_jsr", "post_return", "api_guard", "guard_check", "guard_rts", "tii_start", "tii_after", "irq1_entry")
P_MASK = 0xEF  # B (bit 4) is not a stored flag: PHP always pushes it set
P_CALLER = 0x0D  # I=1 (SEI), D=1 (SED), C=1 (SEC) must be set in the caller before the JSR (precondition)
RAM_MPR = 0xF8
IO_MPR = 0xFF
FLAG_INDEX = 0x10  # WRAM $0210 (the dump starts at $0200)
TII_INDEX = 0x20  # WRAM $0220 (the dump starts at $0200)
TII_ABS = 0x220  # the same bytes as WRAM indices in WRW records
TII_SOURCE = bytes(range(0xA1, 0xA9))
CASES = [
    {"name": "ram_contract", "define": "CASE_RAM", "steps": 80, "irq": "none"},
    {"name": "io_violation", "define": "CASE_IO", "steps": 80, "irq": "none"},
    {"name": "tii_irq1", "define": "CASE_IRQ", "steps": 160, "irq": "tii-irq1"},
]


def build_case(define, folder):
    obj, rom, ld_labels = folder / f"{define}.o", folder / f"{define}.pce", folder / f"{define}.ldlbl"
    subprocess.run([str(executable("ca65")), "-D", define, str(FIXTURE), "-o", str(obj)], check=True,
                   capture_output=True, text=True)
    subprocess.run([str(executable("ld65")), "-C", str(CFG), "-Ln", str(ld_labels), "-o", str(rom), str(obj)],
                   check=True, capture_output=True, text=True)
    labels = {name: int(address, 16) for address, name in LABEL_LINE.findall(ld_labels.read_text())}
    missing = [name for name in LABELS if name not in labels]
    if missing or rom.stat().st_size != 8192:
        raise ValueError(f"{define}: fixture missing labels {missing} or size {rom.stat().st_size} != 8192")
    return rom, labels


def run_probe(probe, rom, labels, steps, irq, folder):
    label_file = folder / f"{rom.stem}.labels"
    label_file.write_text("".join(f"{name} {address:x}\n" for name, address in labels.items()))
    result = subprocess.run([str(probe), str(rom), str(label_file), str(steps), irq],
                            check=True, capture_output=True, text=True, timeout=120)
    records = {}
    for line in result.stdout.splitlines():  # the core also prints banner lines; only tagged JSON records count
        prefix, _, payload = line.partition(" ")
        if prefix in RECORDS:
            records.setdefault(prefix, []).append(json.loads(payload))
    cap = records.get("CAP", [])
    if len(cap) != 1 or cap[0].get("instruction_step") is not True:
        raise ValueError("instruction stepping capability missing on the pinned core")
    if "END" not in records or "HITS" not in records:
        raise ValueError("probe produced no END/HITS record")
    return records


def trace(records, name):
    rows = [r for r in records.get("TRACE", []) if r["name"] == name]
    return rows[0] if len(rows) == 1 else None


def wram_bytes(records):
    return bytes.fromhex(records["END"][0]["wram_0200_hex"])


def check(results, name, ok, detail):
    results.append({"check": name, "ok": bool(ok), "detail": detail})


def judge_ram(records):
    results = []
    pre, post, guard, rts = (trace(records, n) for n in ("pre_jsr", "post_return", "guard_check", "guard_rts"))
    if None in (pre, post, guard, rts):
        check(results, "labels_reached", False, "pre_jsr/post_return/guard_check/guard_rts not all reached once")
        return results
    check(results, "caller_precondition", pre["mpr"][1] == RAM_MPR and pre["p"] & P_CALLER == P_CALLER,
          {"mpr1": pre["mpr"][1], "p": pre["p"]})
    check(results, "guard_saw_ram", guard["a"] == RAM_MPR, {"a_at_guard_check": guard["a"]})
    check(results, "guard_flag_allowed", wram_bytes(records)[FLAG_INDEX] == 1,
          {"flag_0210": wram_bytes(records)[FLAG_INDEX]})
    check(results, "return_to_post_return", post["step"] == rts["step"] + 1,
          {"rts_step": rts["step"], "post_return_step": post["step"]})
    check(results, "a_x_y_unchanged", (pre["a"], pre["x"], pre["y"]) == (post["a"], post["x"], post["y"]),
          {"pre": [pre["a"], pre["x"], pre["y"]], "post": [post["a"], post["x"], post["y"]]})
    check(results, "p_unchanged", (pre["p"] ^ post["p"]) & P_MASK == 0,
          {"pre": pre["p"], "post": post["p"], "mask": P_MASK})
    check(results, "s_unchanged", pre["sp"] == post["sp"], {"pre": pre["sp"], "post": post["sp"]})
    check(results, "mpr_unchanged", pre["mpr"] == post["mpr"], {"pre": pre["mpr"], "post": post["mpr"]})
    vdc_after = [v for v in records.get("VDW", []) if v["step"] > pre["step"]]
    check(results, "no_vdc_write", not vdc_after, {"vdw_after_call": vdc_after})
    wram_after = sorted({w["wram"] for w in records.get("WRW", []) if w["step"] >= pre["step"]})
    check(results, "stack_writes_in_wram", all(0x100 <= a <= 0x1FF or a == 0x210 for a in wram_after),
          {"wram_indices_written": [hex(a) for a in wram_after]})
    return results


def judge_io(records):
    results = []
    pre, guard, rts = (trace(records, n) for n in ("pre_jsr", "guard_check", "guard_rts"))
    post = trace(records, "post_return")
    if None in (pre, guard, rts):
        check(results, "labels_reached", False, "pre_jsr/guard_check/guard_rts not all reached once")
        return results
    check(results, "guard_saw_io", guard["a"] == IO_MPR, {"a_at_guard_check": guard["a"]})
    # The flag is 0 both when the guard stored 0 and when it never ran; the HITS count shows the guard reached its RTS
    # (the store to $0210 sits before the RTS and is not a stack access).
    check(results, "guard_flag_violation", wram_bytes(records)[FLAG_INDEX] == 0 and records["HITS"][0].get("guard_rts") == 1,
          {"flag_0210": wram_bytes(records)[FLAG_INDEX], "guard_rts_hits": records["HITS"][0].get("guard_rts")})
    vdw = [v for v in records.get("VDW", []) if pre["step"] <= v["step"] <= rts["step"]]
    wrw = [w for w in records.get("WRW", []) if pre["step"] <= w["step"] <= rts["step"]]
    check(results, "io_writes_observed", len(vdw) >= 1, {"vdw_in_guard_window": vdw})
    check(results, "no_stack_write_in_wram", not any(0x100 <= w["wram"] <= 0x1FF for w in wrw),
          {"wram_writes_in_guard_window": wrw})
    # Observation only (not a pass criterion): whether the RTS went back to the caller.
    check(results, "observed_returned_to_caller", True,
          {"post_return_reached": post is not None, "note": "informational, a violation is not asserted to return"})
    return results


def judge_irq(records):
    results = []
    xfer = records.get("XFER", [])
    asserted = [e for e in records.get("IRQ", []) if e["event"] == "asserted"]
    irq_entry = trace(records, "irq1_entry")
    partial = [x for x in xfer if not x["completed"]]
    done = [x for x in xfer if x["completed"]]
    check(results, "tii_multi_step", len(partial) >= 8, {"partial_steps_at_tii": len(partial), "xfer_steps": len(xfer)})
    mid = [x for x in partial if asserted and x["step"] == asserted[0]["step"]]
    check(results, "irq_asserted_mid_transfer", len(asserted) == 1 and len(mid) == 1,
          {"asserted": asserted, "assert_step_was_partial_transfer_step": bool(mid)})
    check(results, "no_dispatch_while_transfer_running",
          all(x["pc_after"] == x["pc_before"] for x in partial) and irq_entry is not None
          and irq_entry["step"] > (partial[-1]["step"] if partial else 1 << 30),
          {"partial_pc_after_values": sorted({x["pc_after"] for x in partial}),
           "irq1_entry_step": irq_entry and irq_entry["step"]})
    check(results, "irq_taken_at_transfer_end",
          len(done) == 1 and irq_entry is not None and irq_entry["step"] == done[0]["step"] + 1
          and irq_entry["pc"] == done[0]["pc_after"],
          {"completed_step": done and done[0], "irq1_entry": irq_entry})
    dest = wram_bytes(records)[TII_INDEX:TII_INDEX + 8]
    check(results, "tii_destination_correct", dest == TII_SOURCE, {"dest_hex": dest.hex(), "want": TII_SOURCE.hex()})
    dest_writes = [w for w in records.get("WRW", []) if TII_ABS <= w["wram"] < TII_ABS + 8]
    check(results, "tii_bytes_one_per_step",
          len(dest_writes) == 8 and len({w["step"] for w in dest_writes}) == 8
          and all(w["step"] < done[0]["step"] for w in dest_writes) if done else False,
          {"dest_write_steps": [w["step"] for w in dest_writes]})
    return results


JUDGES = {"ram_contract": judge_ram, "io_violation": judge_io, "tii_irq1": judge_irq}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, help="evidence directory (default: a temporary one)")
    args = parser.parse_args()
    if sys.platform != "linux":  # the pinned core is a Linux build; never skip silently
        raise ValueError(f"caller-stack experiment is Linux-pinned (platform {sys.platform}); BLOCKED, not PASS")
    out = args.out or Path(tempfile.mkdtemp(prefix="pce-caller-stack-"))
    out.mkdir(parents=True, exist_ok=True)
    probe = api.compile_probe(out, source_name=PROBE_SOURCE, target_name=PROBE_TARGET)
    evidence = {"scope": "synthetic test-only HuCARD on the unmodified pinned core; NOT a BIOS ABI, NOT a gate",
                "geargrafx_revision": DEPENDENCIES["geargrafx"]["revision"],
                "fixture_sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(), "cases": {}}
    failed = []
    with tempfile.TemporaryDirectory(prefix="pce-caller-stack-") as tmp:
        folder = Path(tmp)
        for case in CASES:
            rom, labels = build_case(case["define"], folder)
            records = run_probe(probe, rom, labels, case["steps"], case["irq"], folder)
            judged = JUDGES[case["name"]](records)
            ok = bool(judged) and all(item["ok"] for item in judged)
            evidence["cases"][case["name"]] = {"rom_sha256": hashlib.sha256(rom.read_bytes()).hexdigest(),
                                               "checks": judged, "records": records,
                                               "status": "PASS" if ok else "FAIL"}
            for item in judged:
                print(f"  {'PASS' if item['ok'] else 'FAIL'} {case['name']}.{item['check']}: {item['detail']}")
            if not ok:
                failed.append(case["name"])
    (out / "caller-stack-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    if failed:
        raise ValueError(f"caller stack contract cases failed: {failed}")
    print(f"PASS caller stack contract: {len(CASES)} real-core cases (evidence {out / 'caller-stack-evidence.json'})")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError, KeyError) as error:
        raise SystemExit(f"FAIL caller stack contract (missing core, tool, or check): {error}") from error
