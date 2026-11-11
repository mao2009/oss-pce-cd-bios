"""DEV slot-$02 RETURNING experiment for Issue #756 (synthetic + conditional commercial).

SIMULATED/CONDITIONAL observation only: a distinctly named loader variant in
which ONLY the api_slot_02 fallback is patched to an immediate RTS (see
tools/build_slot02_return_loader.py) is executed on the pinned core to see
what the IPL does AFTER a hypothetical slot-$02 return.

NOT a System Card CD_BASE implementation. NOT an ABI claim. NOT game
compatibility. NOT G5 success. Verdicts carry the SIMULATED_ prefix and can
never be API_ENTRY_CONFIRMED.

Synthetic (default): proves the instrument itself on PUBLIC-format loaders.
  Single-slot ROM (slot02ret, slots=(02,)):
    SEQ on experiment ROM  -> 2 ordered APICALLs (slot2 JSR returns, then
                               slot3 JSR), resumption marker, SP restored by
                               the first return, stub halt X=3 (slot 3 stays
                               unimplemented by design).
    SEQ on base ROM (control) -> 1 APICALL (slot2), stub halt X=2.
    SPIN2 on experiment ROM -> 1 APICALL, budget (no invented halt).
  Two-slot ROM (slot02-33ret, slots=(02,33)) with the SEQ23 payload
  (JSR $E006; JSR $E099; JSR $E009):
    base ROM      -> stops at the first API (stub X=2);
    slot-02-only  -> stops at the second API (stub X=51);
    both patched  -> both return, third call observed (stub X=3),
                     full SP restoration, resumption markers AA/CC/DD.
  The three-way differential is explained by the allowed patches only.
Commercial (--commercial, opt-in only): same read-only/sanitized discipline
as tests/integration/geargrafx_api_entry.py (single-CUE auto-select, never
auto-pick among multiples, stat-only auto-loader, snapshots, private-parent
output). Classification is SIMULATED_* and always states the fabrication
assumption; media bytes/paths/hashes are never logged.
"""
import argparse
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools"), str(Path(__file__).resolve().parent)]
from build_slot02_return_loader import build_slot02_return_bios  # noqa: E402
from environment import executable  # noqa: E402
from geargrafx_api import compile_probe  # noqa: E402
import commercial_media  # noqa: E402
import geargrafx_api_entry as E  # noqa: E402
import geargrafx_ipl_loader as L  # noqa: E402


def entry_of(slot: int) -> int:
    return 0xE000 + slot * 3


def build_seq_payload(out: Path, name: str, defines: list, origin: int = 0x3000) -> tuple:
    out.mkdir(parents=True, exist_ok=True)
    src = Path(__file__).resolve().parent / "ipl_slot02_return_payload.s"
    obj, rom, lbl, cfg = (out / f"{name}.o", out / f"{name}.bin",
                          out / f"{name}.lbl", out / f"{name}.cfg")
    cfg.write_text(f"MEMORY {{\n    RAM: start = ${origin:04X}, size = $0F00, file = %O;\n}}\n"
                   f"SEGMENTS {{\n    CODE: load = RAM, type = ro, start = ${origin:04X};\n}}\n",
                   encoding="ascii")
    subprocess.run([executable("ca65"), str(src), *defines, "-o", str(obj), "-g"], check=True)
    subprocess.run([executable("ld65"), "-C", str(cfg), "-o", str(rom), "-Ln", str(lbl), str(obj)],
                   check=True)
    code = rom.read_bytes()
    if not 1 <= len(code) <= 2048:
        raise ValueError("payload must fit one sector")
    return code


def payload_halt_of(tmp: Path, name: str) -> int:
    lbl_text = (tmp / f"{name}.lbl").read_text()
    m = re.search(r"^al ([0-9A-Fa-f]{6}) \.payload_halt$", lbl_text, re.M)
    if not m:
        raise ValueError(f"{name}: payload_halt label missing")
    return int(m.group(1), 16)


def run_labels(tmp: Path, name: str, halt: int, base_addrs: dict) -> Path:
    for needed in ("cd_halt_fail", "cd_unexpected_interrupt", "api_unimplemented"):
        if needed not in base_addrs:
            raise ValueError(f"base labels missing {needed}")
    labels = tmp / f"{name}.labels"
    labels.write_text(f"cd_halt_ok {halt:04x}\n"
                      f"cd_halt_fail {base_addrs['cd_halt_fail']:04x}\n"
                      f"cd_unexpected_interrupt {base_addrs['cd_unexpected_interrupt']:04x}\n"
                      f"api_unimplemented {base_addrs['api_unimplemented']:04x}\n", encoding="ascii")
    return labels


def sp_balanced(calls: list) -> bool:
    """First sp_before equals last sp_after (JSR/RTS pairs nest cleanly)."""
    if not calls:
        return False
    try:
        return calls[0]["sp_before"] == calls[-1]["sp_after"]
    except KeyError:
        return False


def call_sequence(calls: list, stub_addr):
    """Ordered [(slot, method)] for table JSR/JMP calls, or None if unclassifiable.

    JSR needs internal return confirmation; JMP is allowed only as the last
    call. Anything else (DIRECT/IRQ/OTHER/unconfirmed) yields None: the
    SIMULATED classifier must not invent an ordering.
    """
    if not calls:
        return None
    seq = []
    for idx, call in enumerate(calls):
        method = E.classify_apicall(call, stub_addr)
        if method == "JSR":
            if not E.jsr_confirmed(call):
                return None
        elif method == "JMP":
            if idx != len(calls) - 1:
                return None
        else:
            return None
        seq.append((call["slot"], method))
    return seq


def classify_simulated(records, stub_addr, expect_prefix=((2, "JSR"),)):
    """SIMULATED verdict for an experiment-ROM run (pure; never CONFIRMED).

    Return-evidence discipline (A-2): ``stack_check == MATCH`` / ``jsr_confirmed``
    prove only INTERNAL stack consistency (a JSR pushed caller+2 while MPR1 is
    the WRAM bank) -- never that a callee executed a real return. An actual
    control return is proven only by post-return execution: resumption markers
    and the next observed call. The LAST call of a stub-halted sequence is
    NON-RETURNING by construction; no verdict may claim it returned.

    Returns (verdict, detail); verdicts: SIMULATED_NEXT_API (a post-return
    call sequence ending at the VERIFIED stub halt, with halt PC and halt X
    matching the stub address and the last call slot in BOTH cycles),
    SIMULATED_RETURN_THEN_HALT (returned, then a non-stub halt with no
    further API), SIMULATED_PRE_HALT (halted before any API; the experiment
    changed nothing on that path), SIMULATED_UNEXPECTED_FIRST (the first call
    is not the assumed slot-2 JSR, or the halt is inconsistent with the
    recorded sequence), SIMULATED_DIVERGENT (cold/warm disagree, in calls or
    in halt outcome), SIMULATED_INCOMPLETE, SIMULATED_BUDGET, or FAIL.
    """
    runs = records.get("RUN", [])
    apis = records.get("APICALL", [])
    if len(runs) != 2:
        return "FAIL", "missing cold/warm runs"
    if any(r.get("reason") == "budget" or r.get("halt_confirmed") is not True for r in runs):
        return "SIMULATED_BUDGET", "no confirmed halt (budget is not a halt)"
    if any(r.get("apicall_truncated") is True for r in runs):
        return "SIMULATED_INCOMPLETE", "APICALL trace truncated"
    by_cycle = {}
    for cycle in ("cold", "warm"):
        calls = [c for c in apis if c.get("cycle") == cycle]
        cruns = [r for r in runs if r.get("cycle") == cycle]
        if len(cruns) != 1:
            return "FAIL", f"run count for {cycle}"
        by_cycle[cycle] = (calls, cruns[0])
    cold_calls, cold_run = by_cycle["cold"]
    warm_calls, warm_run = by_cycle["warm"]
    if not cold_calls and not warm_calls:
        return ("SIMULATED_PRE_HALT",
                f"no API entry; halts {[cold_run['reason'], warm_run['reason']]} in both cycles")
    cold_seq = call_sequence(cold_calls, stub_addr)
    warm_seq = call_sequence(warm_calls, stub_addr)
    if cold_seq is None or warm_seq is None:
        return "SIMULATED_UNEXPECTED_FIRST", "entry present without JSR/JMP evidence"
    prefix = list(expect_prefix)
    npre = len(prefix)
    cold_pre = list(cold_seq[:npre]) if len(cold_seq) >= npre else None
    warm_pre = list(warm_seq[:npre]) if len(warm_seq) >= npre else None
    if cold_pre != prefix or warm_pre != prefix:
        return ("SIMULATED_UNEXPECTED_FIRST",
                f"first calls are not the assumed {prefix}: {cold_pre} / {warm_pre}")
    if cold_seq != warm_seq:
        return "SIMULATED_DIVERGENT", f"cold/warm sequences differ: {cold_seq} vs {warm_seq}"
    stub_halts = [r.get("reason") == "api_unimplemented" for r in (cold_run, warm_run)]
    if len(cold_seq) == npre:
        if any(stub_halts):
            # Every assumed slot returns on the experiment ROM, so a stub
            # halt with no further recorded call implies an UNRECORDED table
            # entry (out-of-window caller or hidden jump): the trace cannot
            # explain the halt, so this is never a clean return.
            return ("SIMULATED_UNEXPECTED_FIRST",
                    "recorded calls cannot reach the stub halt; "
                    f"possible unobserved entry (halts {[cold_run.get('reason'), warm_run.get('reason')]})")
        return ("SIMULATED_RETURN_THEN_HALT",
                f"experiment slots returned, no further API; halts {[cold_run['reason'], warm_run['reason']]}")
    if all(stub_halts):
        if stub_addr is None:
            return ("SIMULATED_UNEXPECTED_FIRST",
                    "stub address unverified; halt consistency unprovable")
        last_slot = cold_seq[-1][0]
        pcs_ok = cold_run.get("pc") == stub_addr and warm_run.get("pc") == stub_addr
        xs_ok = cold_run.get("x") == last_slot and warm_run.get("x") == last_slot
        if not pcs_ok or not xs_ok:
            return ("SIMULATED_UNEXPECTED_FIRST",
                    f"stub halt inconsistent with last call slot {last_slot}: "
                    f"pcs {[cold_run.get('pc'), warm_run.get('pc')]} "
                    f"xs {[cold_run.get('x'), warm_run.get('x')]}")
        return ("SIMULATED_NEXT_API",
                f"post-return sequence {list(cold_seq)} in both cycles, stub halt on last call "
                f"(last call is non-returning; only the assumed returns are proven by resumption)")
    if any(stub_halts):
        return ("SIMULATED_DIVERGENT",
                f"halt outcomes differ: {[cold_run['reason'], warm_run['reason']]}")
    return ("SIMULATED_RETURN_THEN_HALT",
            f"post-return sequence {list(cold_seq)} in both cycles, "
            f"halts {[cold_run['reason'], warm_run['reason']]}")


def judge_seq(tmp, probe, rom, base_addrs, stub_addr, name, defines, budget, on_base: bool):
    """Strict synthetic judge for one SEQ/SPIN2 case (raises on mismatch)."""
    code = build_seq_payload(tmp, name, defines)
    body = L.body_for(code, 1)
    # PUBLIC-format loaders take 0..7 MPR indices (mapped to $80+index in
    # guest); raw $80..$87 bank values are the DEV format and are refused.
    info = L.info_sector(2, 1, 0x3000, 0x3000, [0, 1, 2, 3, 4], 0, sum(body) & 0xFFFF)
    cue = L.make_disc(tmp / name, L.case_disc({"n": 1, "iplblk": 2}, body, info))
    E.verify_stub_bytes(rom, stub_addr)
    labels = run_labels(tmp, name, payload_halt_of(tmp, name), base_addrs)
    records = E.run_probe(probe, rom, cue, labels, budget, "synthetic")
    fails = []
    for cycle in ("cold", "warm"):
        calls = [c for c in records["APICALL"] if c["cycle"] == cycle]
        runs = [r for r in records["RUN"] if r["cycle"] == cycle]
        if len(runs) != 1:
            raise ValueError(f"{name} {cycle}: RUN count")
        run = runs[0]
        if on_base:
            if len(calls) != 1:
                fails.append(f"{cycle}: control expects 1 APICALL, got {len(calls)}")
                continue
            if E.classify_apicall(calls[0], stub_addr) != "JSR" or calls[0]["slot"] != 2:
                fails.append(f"{cycle}: control call is not slot-2 JSR")
            if run["reason"] != "api_unimplemented" or run["x"] != 2:
                fails.append(f"{cycle}: control halt {run['reason']} x={run['x']}")
        elif name.startswith("spin2"):
            if len(calls) != 1 or calls[0]["slot"] != 2:
                fails.append(f"{cycle}: expected single slot-2 call, got {len(calls)}")
            if run["reason"] != "budget" or run["halt_confirmed"]:
                fails.append(f"{cycle}: post-return spin must be budget, got {run['reason']}")
        else:
            # SEQ: slot 2 returns (experiment), slot 3 is unimplemented so the
            # run must end at the stub halt with X=3. The first return is
            # proven by resumption (A marker) and by SP restoration:
            # the second call starts with the same SP as the first.
            seq = call_sequence(calls, stub_addr)
            if seq != [(2, "JSR"), (3, "JSR")]:
                fails.append(f"{cycle}: sequence {seq} != [(2, JSR), (3, JSR)]")
                continue
            if calls[0]["caller_a"] != 0xAA or calls[1]["caller_a"] != 0xCC:
                fails.append(f"{cycle}: resumption marker missing {calls[0].get('caller_a')}/{calls[1].get('caller_a')}")
            if calls[0]["caller_x"] != 0x11 or calls[1]["caller_x"] != 0x11:
                fails.append(f"{cycle}: entry regs clobbered")
            if calls[1]["sp_before"] != calls[0]["sp_before"]:
                fails.append(f"{cycle}: SP not restored by slot-2 return")
            if run["reason"] != "api_unimplemented" or run["x"] != 3 or run.get("pc") != stub_addr:
                fails.append(f"{cycle}: expected stub halt X=3 at stub, "
                             f"got {run['reason']} x={run['x']} pc={run.get('pc')}")
    if fails:
        raise ValueError(f"{name} failed: {fails}")
    return records


def judge_seq23(tmp, probe, rom, base_addrs, stub_addr, name, defines, budget,
                expect_seq, expect_markers, expect_x):
    """Strict synthetic judge for the two-return chain (raises on mismatch).

    expect_seq lists the ordered (slot, method) calls; expect_markers the
    caller_a resumption values; the run must end at the stub halt with X ==
    expect_x at the verified stub PC. Every assumed return is proven by the
    next call's resumption marker plus SP restoration (each later call starts
    with the first call's SP).
    """
    code = build_seq_payload(tmp, name, defines)
    body = L.body_for(code, 1)
    info = L.info_sector(2, 1, 0x3000, 0x3000, [0, 1, 2, 3, 4], 0, sum(body) & 0xFFFF)
    cue = L.make_disc(tmp / name, L.case_disc({"n": 1, "iplblk": 2}, body, info))
    E.verify_stub_bytes(rom, stub_addr)
    labels = run_labels(tmp, name, payload_halt_of(tmp, name), base_addrs)
    records = E.run_probe(probe, rom, cue, labels, budget, "synthetic")
    fails = []
    for cycle in ("cold", "warm"):
        calls = [c for c in records["APICALL"] if c["cycle"] == cycle]
        runs = [r for r in records["RUN"] if r["cycle"] == cycle]
        if len(runs) != 1:
            raise ValueError(f"{name} {cycle}: RUN count")
        run = runs[0]
        seq = call_sequence(calls, stub_addr)
        if seq != expect_seq:
            fails.append(f"{cycle}: sequence {seq} != {expect_seq}")
            continue
        markers = [c["caller_a"] for c in calls]
        if markers != expect_markers:
            fails.append(f"{cycle}: resumption markers {markers} != {expect_markers}")
        if any(c["caller_x"] != 0x11 or c["caller_y"] != 0x22 for c in calls):
            fails.append(f"{cycle}: entry regs clobbered")
        if len(calls) > 1 and calls[-1]["sp_before"] != calls[0]["sp_before"]:
            fails.append(f"{cycle}: SP not restored by the assumed returns")
        if run["reason"] != "api_unimplemented" or run["x"] != expect_x \
                or run.get("pc") != stub_addr:
            fails.append(f"{cycle}: expected stub halt X={expect_x} at stub, "
                         f"got {run['reason']} x={run['x']} pc={run.get('pc')}")
    if fails:
        raise ValueError(f"{name} failed: {fails}")
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("debug", "release"), default="debug")
    parser.add_argument("--commercial", action="store_true")
    parser.add_argument("--commercial-index", type=int, default=None)
    parser.add_argument("--rom-dir", type=Path, default=None)
    parser.add_argument("--commercial-cue", type=Path)
    parser.add_argument("--commercial-labels", type=Path)
    parser.add_argument("--commercial-output", type=Path)
    parser.add_argument("--commercial-auto-loader", action="store_true")
    parser.add_argument("--commercial-loader-format", choices=("public", "dev"), default="public")
    args = parser.parse_args()

    has_commercial_args = (
        args.commercial_cue is not None or args.commercial_labels is not None
        or args.commercial_output is not None or args.commercial_index is not None
        or args.rom_dir is not None
        or args.commercial_auto_loader or args.commercial_loader_format != "public")
    if has_commercial_args and not args.commercial:
        raise SystemExit("FAIL: commercial options require --commercial")

    original = ROOT / "build" / args.mode / "bios-dev" / "dev-only-not-compatible-syscard3.pce"
    if not original.is_file():
        raise SystemExit("BLOCKED: run make dev-rom first")
    out = ROOT / "build" / args.mode / "slot02-return"
    out.mkdir(parents=True, exist_ok=True)

    if args.commercial:
        if args.commercial_labels is not None and not args.commercial_auto_loader:
            raise SystemExit("FAIL: experiment commercial runs require --commercial-auto-loader")
        if not args.commercial_output or not args.commercial_auto_loader:
            raise SystemExit("FAIL: experiment commercial needs --commercial-auto-loader and --commercial-output")
        if args.rom_dir is not None and os.environ.get("OSS_PCE_ALLOW_ROM_DIR") != "1":
            raise SystemExit("FAIL: --rom-dir is a test hook requiring OSS_PCE_ALLOW_ROM_DIR=1")
        try:
            rom_dir = Path(os.path.abspath(args.rom_dir)) if args.rom_dir else commercial_media.resolve_rom_dir(ROOT)
        except ValueError as error:
            raise SystemExit(str(error))
        try:
            cue_path = E.resolve_commercial_cue(rom_dir, args.commercial_cue, args.commercial_index)
        except ValueError as error:
            raise SystemExit(str(error))
        try:
            E.validate_commercial_output(args.commercial_output)
        except ValueError as error:
            raise SystemExit(f"FAIL commercial output rejected: {error}")
        try:
            snap = E.snapshot_media(cue_path, rom_dir)
        except ValueError as error:
            raise SystemExit(str(error))
        try:
            sel = commercial_media.data_track_index01_lba(cue_path, rom_dir)
        except ValueError as error:
            raise SystemExit(str(error))
        try:
            E.verify_media_unchanged(snap)
        except ValueError as error:
            raise SystemExit(str(error))
        defines = ["-D", "IPL_PUBLIC"] if args.commercial_loader_format == "public" else []
        slots = (0x02, 0x33)
        prefix = ((2, "JSR"), (51, "JSR"))
        try:
            base_rom, exp_rom, _addrs = build_slot02_return_bios(
                original, out, sel, args.mode, "slot02-33ret-comm",
                defines, private_input=True, slots=slots)
        except Exception as error:
            raise SystemExit(f"FAIL commercial experiment loader build: {error}")
        try:
            E.verify_media_unchanged(snap)
        except ValueError as error:
            raise SystemExit(str(error))
        from build_dev_ipl_loader_bios import parse_labels as _parse
        base_labels = out / "ipl-loader-bios-slot02-33ret-comm-base.lbl"
        try:
            full = _parse(base_labels, ("cd_halt_fail", "cd_unexpected_interrupt", "api_unimplemented",
                                         "reset"))
        except OSError:
            raise SystemExit("FAIL commercial labels unreadable")
        for needed in ("cd_halt_fail", "cd_unexpected_interrupt", "api_unimplemented", "reset"):
            if needed not in full:
                raise SystemExit(f"FAIL commercial labels: missing {needed}")
        if args.commercial_labels is not None:
            labels_path = args.commercial_labels
        else:
            # cd_halt_ok is a dummy reset address that never holds as a halt
            # (same contract as the #635 auto labels); the experiment has no
            # payload halt on commercial media.
            if full["reset"] in (full["cd_halt_fail"], full["cd_unexpected_interrupt"],
                                   full["api_unimplemented"]):
                raise SystemExit("FAIL commercial labels collision for dummy halt")
            labels_path = out / "commercial-labels-slot02-33ret-comm.labels"
            labels_path.write_text(
                f"cd_halt_ok {full['reset']:04x}\n"
                f"cd_halt_fail {full['cd_halt_fail']:04x}\n"
                f"cd_unexpected_interrupt {full['cd_unexpected_interrupt']:04x}\n"
                f"api_unimplemented {full['api_unimplemented']:04x}\n", encoding="ascii")
        probe = compile_probe(out, "geargrafx_api_entry_probe.cpp", "geargrafx-slot02ret-probe")
        try:
            exp_stub = E.resolve_commercial_stub(labels_path, exp_rom)
        except ValueError as error:
            raise SystemExit(f"FAIL commercial labels: {error}")
        try:
            records = E.run_probe(probe, exp_rom, cue_path, labels_path, 80_000_000, "commercial")
        except ValueError as error:
            raise SystemExit(f"FAIL commercial observation: {error}")
        try:
            E.verify_media_unchanged(snap)
        except ValueError as error:
            raise SystemExit(str(error))
        verdict, detail = classify_simulated(records, exp_stub, prefix)
        for cycle in ("cold", "warm"):
            calls = [c for c in records["APICALL"] if c["cycle"] == cycle]
            runs = [r for r in records["RUN"] if r["cycle"] == cycle]
            trunc = runs[0].get("apicall_truncated", False) if runs else False
            print(f"cycle={cycle} apicalls={len(calls)} truncated={trunc} "
                  f"runs={[(r['reason'], r['halt_confirmed']) for r in runs]}")
            for c in calls:
                print(f"  caller={hex(c['caller_pc'])} entry={hex(c['entry_pc'])} table={c['in_table']} "
                      f"slot={c['slot']} delta={c['sp_delta']} "
                      f"method={E.classify_apicall(c, exp_stub)} return={c.get('stack_check')}")
        evidence = {"verdict": verdict, "detail": detail,
                    "assumption": ("SIMULATED slots $02+$33 RTS return (regs/flags/MPR preserved); "
                                   "conditioned on this fabrication, NOT genuine CD_BASE/EX_DSPOFF, "
                                   "NOT compatibility"),
                    "apicalls": records["APICALL"],
                    "runs": [{k: r[k] for k in ("cycle", "reason", "pc", "x", "halt_confirmed", "step")} |
                             {"apicall_truncated": r.get("apicall_truncated", False)}
                             for r in records["RUN"]]}
        try:
            E.save_commercial_evidence(args.commercial_output,
                                       json.dumps(evidence, indent=2, sort_keys=True) + "\n")
        except ValueError as error:
            raise SystemExit(f"FAIL commercial evidence save failed: {error}")
        print(f"SIMULATED commercial slot02-return: {verdict} ({detail})")
        return 0 if verdict.startswith("SIMULATED_") else 1

    probe = compile_probe(out, "geargrafx_api_entry_probe.cpp", "geargrafx-slot02ret-probe")
    with tempfile.TemporaryDirectory(prefix="pce-slot02ret-") as tmp:
        tmp = Path(tmp)
        defines = ["-D", "IPL_PUBLIC"]
        base_rom, exp_rom, _addrs = build_slot02_return_bios(
            original, out, L.SEL, args.mode, "slot02ret", defines)
        _base23, exp23, _addrs23 = build_slot02_return_bios(
            original, out, L.SEL, args.mode, "slot02-33ret", defines,
            slots=(0x02, 0x33))
        from build_dev_ipl_loader_bios import parse_labels as _parse
        full = _parse(out / "ipl-loader-bios-slot02ret-base.lbl",
                      ("cd_halt_fail", "cd_unexpected_interrupt", "api_unimplemented"))
        full23 = _parse(out / "ipl-loader-bios-slot02-33ret-base.lbl",
                        ("cd_halt_fail", "cd_unexpected_interrupt", "api_unimplemented"))
        stub_addr = full["api_unimplemented"]
        stub23 = full23["api_unimplemented"]
        E.verify_stub_bytes(exp_rom, stub_addr)
        E.verify_stub_bytes(base_rom, stub_addr)
        E.verify_stub_bytes(exp23, stub23)
        slot2, slot3, slot33 = entry_of(0x02), entry_of(0x03), entry_of(0x33)
        judge_seq(tmp, probe, exp_rom, full, stub_addr, "seq_return",
                  [f"-DSEQ", f"-DSEQ2=${slot2:04X}", f"-DSEQ3=${slot3:04X}"], 20_000_000, False)
        print("PASS seq_return cold/warm: slot2 JSR returned, slot3 JSR observed in order, SP restored, stub halt X=3")
        judge_seq(tmp, probe, base_rom, full, stub_addr, "seq_control",
                  [f"-DSEQ", f"-DSEQ2=${slot2:04X}", f"-DSEQ3=${slot3:04X}"], 20_000_000, True)
        print("PASS seq_control cold/warm: same payload on base ROM halts at stub X=2 with 1 APICALL")
        judge_seq(tmp, probe, exp_rom, full, stub_addr, "spin2_budget",
                  [f"-DSPIN2", f"-DSEQ2=${slot2:04X}"], 20_000_000, False)
        print("PASS spin2_budget cold/warm: post-return spin is budget, never a halt")
        seq23 = [f"-DSEQ23", f"-DSEQ2=${slot2:04X}", f"-DSEQ3=${slot33:04X}",
                 f"-DSEQ4=${slot3:04X}"]
        judge_seq23(tmp, probe, base_rom, full, stub_addr, "seq23_base", seq23,
                    20_000_000, [(2, "JSR")], [0xAA], 2)
        print("PASS seq23_base cold/warm: unpatched base stops at the first API (stub X=2)")
        judge_seq23(tmp, probe, exp_rom, full, stub_addr, "seq23_single", seq23,
                    20_000_000, [(2, "JSR"), (51, "JSR")], [0xAA, 0xCC], 51)
        print("PASS seq23_single cold/warm: slot-02-only patch stops at the second API (stub X=51)")
        judge_seq23(tmp, probe, exp23, full23, stub23, "seq23_both", seq23,
                    20_000_000, [(2, "JSR"), (51, "JSR"), (3, "JSR")],
                    [0xAA, 0xCC, 0xDD], 3)
        print("PASS seq23_both cold/warm: both patches return, third call observed, stub halt X=3")
    print("PASS synthetic slot02+33-return experiment controls (two-return instrument verified)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
