#!/usr/bin/env python3
"""G5 boot-progress measurement harness for Agent C (parent tracker #635).

G5 = reach a commercial PC-Engine CD-ROM2 / SUPER CD-ROM2 game title screen.
This harness automatically measures how far each BIOS variant advances boot
(synthetic CD + opt-in commercial CD) and identifies stop causes. It does NOT
implement APIs, does NOT lift safety gates, and makes NO compatibility claim.

Variants (built WITHOUT waiting for Agent A/B completion):
  A. normal PUBLIC loader (no patch).
  B. $02 fake RTS (PR #757 5-byte-patch pattern).
  C. $02+$33 fake RTS (PR #759 5-byte-patch pattern).
  D. experimental BIOS with REAL API processing: independent input interface
     (--experimental-rom + --experimental-manifest). The manifest carries
     paths/branch/HEAD/slot-wiring description; A/B outputs are NEVER
     hardcoded as D inputs.

Synthetic measurement: a 3-API SEQ23 chain (JSR $E006 slot2 -> JSR $E099
slot51 -> JSR $E009 slot3) on a synthetic IPL disc. Expected differential:
  A stops at 1st API (stub X=2), B at 2nd (stub X=51), C reaches 3rd (X=3).
Commercial measurement (opt-in --commercial only): the game IPL itself from
./rom/*.cue (auto-detect, read-only, sanitized, stat-only SEL derivation).

Metrics per run: API call order, real-vs-fake-RTS distinction, actual return
(RTS-like resumption vs halt), PC/SP/MPR/registers, insn + frame counts,
control transfer (IPL jump), screen-state change, cold/warm delta, stop
cause, progress vs previous run (--baseline), title-screen evidence.
Verdicts: PRE_API_BLOCKER, API_ENTRY_CONFIRMED, SIMULATED_NEXT_API,
REAL_SERVICE_RETURN, NEXT_BLOCKER, FRAME_PROGRESS, G5_CANDIDATE,
G5_OBSERVED, UNKNOWN/BLOCKED. Screen change alone NEVER equals title.
G5_OBSERVED only with observed + confirmed title evidence and a stated
confirmation basis. Geargrafx observations are not hardware proof. The
project is NOT a compatible System Card BIOS.

Safety: reference BIOS JSR 0, ABI VERIFIED 0, DEV_CALLABLE false,
release_eligible=false. No merge/undraft/close without Owner approval.
No experimental code in normal/release artifacts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "tests" / "integration")]
from build_dev_ipl_loader_bios import build_ipl_loader_bios  # noqa: E402
from build_slot02_return_loader import build_slot02_return_bios  # noqa: E402
from environment import DEPENDENCIES  # noqa: E402
from geargrafx_api import compile_probe  # noqa: E402
import geargrafx_api_entry as E  # noqa: E402
import geargrafx_ipl_loader as L  # noqa: E402
import geargrafx_slot02_return as S  # noqa: E402
import commercial_media  # noqa: E402
import g5_progress_model as M  # noqa: E402


def entry_of(slot: int) -> int:
    return 0xE000 + slot * 3


def git_head() -> str:
    r = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                       capture_output=True, text=True, timeout=30)
    head = r.stdout.strip()
    if r.returncode != 0 or not M._HEAD_RE.match(head):
        raise ValueError("BLOCKED: cannot resolve repo HEAD")
    return head


def build_variant_a(original: Path, out: Path, sel: int, mode: str, variant: str,
                    defines: list, private: bool):
    rom, _symbols = build_ipl_loader_bios(original, out, sel, mode, variant,
                                          defines, private_input=private)
    from build_dev_ipl_loader_bios import parse_labels as _parse
    labels = _parse(out / f"ipl-loader-bios-{variant}.lbl",
                    ("api_unimplemented", "cd_halt_fail", "cd_unexpected_interrupt", "reset"))
    for needed in ("api_unimplemented", "cd_halt_fail", "cd_unexpected_interrupt", "reset"):
        if needed not in labels:
            raise ValueError(f"BLOCKED: variant A labels missing {needed}")
    return rom, labels


def build_variant_bc(original: Path, out: Path, sel: int, mode: str, variant: str,
                     defines: list, private: bool, slots: tuple, issue: int):
    base_rom, exp_rom, addrs = build_slot02_return_bios(
        original, out, sel, mode, variant, defines,
        private_input=private, slots=tuple(slots), issue=issue)
    stub = addrs.get("api_unimplemented")
    if stub is None:
        raise ValueError("BLOCKED: experiment labels missing api_unimplemented")
    E.verify_stub_bytes(exp_rom, stub)
    E.verify_stub_bytes(base_rom, stub)
    return base_rom, exp_rom, addrs


def seq23_disc(tmp: Path, name: str, probe_stub_note: str = "") -> tuple:
    """Build the synthetic 3-API SEQ23 disc + payload halt (pure build step)."""
    _ = probe_stub_note
    slot2, slot33, slot3 = entry_of(0x02), entry_of(0x33), entry_of(0x03)
    defines = [f"-DSEQ23", f"-DSEQ2=${slot2:04X}", f"-DSEQ3=${slot33:04X}",
               f"-DSEQ4=${slot3:04X}"]
    code = S.build_seq_payload(tmp, name, defines)
    body = L.body_for(code, 1)
    info = L.info_sector(2, 1, 0x3000, 0x3000, [0, 1, 2, 3, 4], 0, sum(body) & 0xFFFF)
    cue = L.make_disc(tmp / name, L.case_disc({"n": 1, "iplblk": 2}, body, info))
    halt = S.payload_halt_of(tmp, name)
    return cue, halt


def run_variant_synthetic(probe, rom: Path, labels: dict, tmp: Path, name: str,
                          budget: int = 20_000_000) -> dict:
    """Run one variant ROM on the synthetic SEQ23 disc; return raw probe records."""
    stub_addr = labels["api_unimplemented"]
    E.verify_stub_bytes(rom, stub_addr)
    cue, halt = seq23_disc(tmp, name)
    probe_labels = S.run_labels(tmp, name, halt, labels)
    records = E.run_probe(probe, rom, cue, probe_labels, budget, "synthetic")
    return records


def summarize_run(records: dict, stub_addr: int | None, variant_key: str,
                  return_kind: str, frame_progress: bool = False,
                  screen_changed: bool = False, title_observed: bool = False,
                  title_basis: str = "", title_evidence: dict | None = None,
                  real_service_proof: dict | None = None,
                  expected_slots: tuple | list | None = None) -> dict:
    """Reduce raw probe records to G5 metrics + verdict (pure-ish, no I/O).

    For D (REAL_SERVICE) the return proof is derived from measured probe
    records when not supplied explicitly: arrival/SP/MPR/order gates via
    M.derive_real_service_proof. A bare manifest declaration never yields
    REAL_SERVICE_RETURN (MAJOR-2). expected_slots comes from the validated
    D manifest; None means order unmeasured (never guessed).
    """
    runs = records.get("RUN", [])
    apis = records.get("APICALL", [])
    per_cycle = {}
    seqs = {}
    for cycle in ("cold", "warm"):
        calls = [c for c in apis if c.get("cycle") == cycle]
        cruns = [r for r in runs if r.get("cycle") == cycle]
        per_cycle[cycle] = {"calls": calls, "run": cruns[0] if len(cruns) == 1 else None}
        seqs[cycle] = S.call_sequence(calls, stub_addr)
    cold_calls = per_cycle["cold"]["calls"]
    warm_calls = per_cycle["warm"]["calls"]
    cold_run, warm_run = per_cycle["cold"]["run"], per_cycle["warm"]["run"]
    proof_for_d = real_service_proof
    if cold_run is None or warm_run is None:
        verdict, detail = "BLOCKED", "missing cold/warm runs"
        identical = False
    else:
        fields_ok, _ = S.calls_match(cold_calls, warm_calls) if cold_calls or warm_calls else (True, "")
        halts_ok, _ = S.halts_match(cold_run, warm_run)
        identical = bool(fields_ok and halts_ok) if (cold_calls or warm_calls) else bool(halts_ok)
        seq = seqs["cold"] if seqs["cold"] == seqs["warm"] else None
        calls_list = [list(t) for t in seq] if seq is not None else []
        # Unclassifiable raw sequences (None) still feed classify_g5 as
        # non-JSR/JMP evidence: map to empty calls + UNKNOWN detail via the
        # classifier's cold_warm_identical=False path when cycles disagree,
        # else let classify_g5 judge the stub halt consistency.
        if seq is None and identical and (cold_calls or warm_calls):
            verdict, detail = "UNKNOWN", "entry present without JSR/JMP evidence"
        else:
            # MAJOR-2: D proof comes from measured records, never the manifest
            # declaration alone. Derive when the caller did not supply an
            # explicit proof (harness path); host tests may supply one directly.
            proof_for_d = real_service_proof
            if variant_key == "D" and return_kind == "REAL_SERVICE" and proof_for_d is None:
                try:
                    obs_slots = [s for s, _m in calls_list]
                    proof_for_d = M.derive_real_service_proof(
                        cold_calls, warm_calls, obs_slots, expected_slots)
                except (ValueError, TypeError):
                    proof_for_d = None
            verdict, detail = M.classify_g5(
                variant_key=variant_key, calls=calls_list, runs=[cold_run, warm_run],
                stub_addr=stub_addr, return_kind=return_kind,
                cold_warm_identical=identical, frame_progress=frame_progress,
                screen_changed=screen_changed, title_observed=title_observed,
                title_basis=title_basis, title_evidence=title_evidence,
                next_blocker=(f"${calls_list[-1][0]:02X}" if calls_list else None),
                real_service_proof=proof_for_d, expected_slots=expected_slots)
    # Structural metrics (sanitized: counts/PCs/slots only, no dumps).
    def _run_bits(r):
        if r is None:
            return None
        return {k: r.get(k) for k in ("cycle", "reason", "pc", "x", "halt_confirmed", "step",
                                      "apicall_truncated")}
    def _call_bits(c):
        return {k: c.get(k) for k in ("cycle", "slot", "caller_pc", "entry_pc", "sp_before",
                                      "sp_after", "sp_delta", "caller_a", "caller_x",
                                      "caller_y", "caller_p", "mpr", "stack_check",
                                      "return_matches_caller", "in_table")}
    cold_seq = seqs["cold"]
    api_count = len(cold_seq) if isinstance(cold_seq, list) else 0
    last_slot = f"${cold_seq[-1][0]:02X}" if api_count else None
    chain_ok = None
    if cold_calls and warm_calls and isinstance(cold_seq, list):
        ok, _ = S.sp_chain_restored(cold_calls)
        chain_ok = bool(ok)
    return {
        "variant": variant_key,
        "return_kind": return_kind,
        "verdict": verdict,
        "detail": detail,
        "api_count": api_count,
        "last_slot": last_slot,
        "sequence_cold": seqs["cold"],
        "sequence_warm": seqs["warm"],
        "cold_warm_identical": identical,
        "sp_chain_restored_cold": chain_ok,
        "stop_cold": M.stop_cause_from_run(cold_run or {}, stub_addr),
        "stop_warm": M.stop_cause_from_run(warm_run or {}, stub_addr),
        "runs": [_run_bits(cold_run), _run_bits(warm_run)],
        "call_count_cold": len(cold_calls),
        "call_count_warm": len(warm_calls),
        "first_caller_pc": (cold_calls[0].get("caller_pc") if cold_calls else None),
        "insn_steps": {"cold": (cold_run or {}).get("step"), "warm": (warm_run or {}).get("step")},
        "frames": {"status": "MEASURED" if frame_progress else "NOT_MEASURED",
                   "progress": bool(frame_progress), "screen_changed": bool(screen_changed)},
        "title": M.title_status(observed=bool(title_observed),
                                basis=title_basis, screen_changed=bool(screen_changed),
                                evidence=title_evidence),
        "stub_addr": stub_addr,
        "calls_cold": [_call_bits(c) for c in cold_calls],
        "real_service_proof": proof_for_d if variant_key == "D" else None,
        "expected_slots": [f"${s:02X}" for s in expected_slots] if expected_slots is not None else None,
    }


def list_variants():
    return {k: M.variant_spec(k) for k in ("A", "B", "C", "D")}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=("debug", "release"), default="debug")
    p.add_argument("--variants", nargs="*", default=["A", "B", "C"],
                   help="subset of A/B/C (+D when experimental inputs supplied)")
    p.add_argument("--list-variants", action="store_true")
    p.add_argument("--budget", type=int, default=20_000_000)
    p.add_argument("--baseline", type=Path, default=None,
                   help="previous public evidence JSON for progress diff")
    p.add_argument("--output", type=Path, default=None,
                   help="public evidence JSON (default build/<mode>/g5-harness/g5-evidence.json)")
    p.add_argument("--experimental-rom", type=Path, default=None)
    p.add_argument("--experimental-manifest", type=Path, default=None)
    p.add_argument("--title-observed", action="store_true")
    p.add_argument("--title-basis", default="")
    p.add_argument("--title-evidence", default="", help="structured title observation JSON (source/target/cold_warm/confirmation_method/fake_rts_relation); required for G5_OBSERVED")
    p.add_argument("--frame-progress", action="store_true",
                   help="CLAIMED frame progress (recorded as CLAIMED_UNVERIFIED, never measured)")
    p.add_argument("--screen-changed", action="store_true",
                   help="CLAIMED screen-state change (never equals title)")
    # Opt-in commercial options (same discipline as geargrafx_api_entry).
    p.add_argument("--commercial", action="store_true")
    p.add_argument("--commercial-index", type=int, default=None)
    p.add_argument("--rom-dir", type=Path, default=None)
    p.add_argument("--commercial-cue", type=Path, default=None)
    p.add_argument("--commercial-output", type=Path, default=None)
    p.add_argument("--commercial-auto-loader", action="store_true")
    p.add_argument("--commercial-loader-format", choices=("public", "dev"), default="public")
    args = p.parse_args(argv)

    if args.list_variants:
        print(json.dumps(list_variants(), indent=2, sort_keys=True))
        return 0
    for v in args.variants:
        if v not in ("A", "B", "C", "D"):
            print(f"FAIL: unknown variant {v!r}", file=sys.stderr)
            return 1
    has_commercial_args = (
        args.commercial_cue is not None or args.commercial_output is not None
        or args.commercial_index is not None or args.rom_dir is not None
        or args.commercial_auto_loader or args.commercial_loader_format != "public")
    if has_commercial_args and not args.commercial:
        print("FAIL: commercial options require --commercial", file=sys.stderr)
        return 1
    if bool(args.experimental_rom) != bool(args.experimental_manifest):
        print("FAIL: --experimental-rom and --experimental-manifest are required together",
              file=sys.stderr)
        return 1
    if "D" in args.variants and not args.experimental_rom:
        print("FAIL: variant D requires --experimental-rom + --experimental-manifest",
              file=sys.stderr)
        return 1

    try:
        head = git_head()
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 3
    original = ROOT / "build" / args.mode / "bios-dev" / "dev-only-not-compatible-syscard3.pce"
    if not original.is_file():
        print("BLOCKED: run make dev-rom first", file=sys.stderr)
        return 3
    out = ROOT / "build" / args.mode / "g5-harness"
    out.mkdir(parents=True, exist_ok=True)
    output = Path(args.output) if args.output else out / "g5-evidence.json"

    frame_progress = bool(args.frame_progress)
    screen_changed = bool(args.screen_changed)
    if frame_progress or screen_changed:
        print("NOTE: frame/screen progress is CLAIMED_UNVERIFIED (no tracer ran); "
              "screen change alone is NEVER title", file=sys.stderr)

    # MEDIUM-2: structured title evidence (never a bare string claim).
    title_evidence = None
    if args.title_evidence.strip():
        try:
            title_evidence = json.loads(args.title_evidence)
        except ValueError:
            print("FAIL: --title-evidence is not valid JSON", file=sys.stderr)
            return 1
        ok, why = M.validate_title_evidence(title_evidence)
        if not ok:
            print(f"FAIL: --title-evidence invalid ({why})", file=sys.stderr)
            return 1
    if args.title_observed and title_evidence is None:
        print("NOTE: --title-observed without --title-evidence stays UNKNOWN "
              "(title detection unimplemented or evidence incomplete; never G5_OBSERVED)", file=sys.stderr)

    evidence = M.evidence_skeleton(head=head, mode=args.mode)
    evidence["issue"] = "G5-harness (parent #635)"
    evidence["title_basis_stated"] = bool(args.title_basis.strip()) if args.title_observed else False
    results: dict = {}
    rom_labels: dict = {}

    try:
        probe = compile_probe(out, "geargrafx_api_entry_probe.cpp", "geargrafx-g5-probe")
    except ValueError as e:
        print(f"BLOCKED: {e}", file=sys.stderr)
        return 3

    with tempfile.TemporaryDirectory(prefix="pce-g5-") as tmp_s:
        tmp = Path(tmp_s)
        # --- Synthetic builds + runs for A/B/C. ---
        defines_public = ["-D", "IPL_PUBLIC"]
        sel = L.SEL
        if "A" in args.variants:
            rom_a, lab_a = build_variant_a(original, out, sel, args.mode, "g5a-public",
                                           defines_public, False)
            rom_labels["A"] = (rom_a, lab_a)
        if "B" in args.variants:
            _base_b, exp_b, add_b = build_variant_bc(
                original, out, sel, args.mode, "g5b-slot02ret", defines_public,
                False, (0x02,), 756)
            # Full halt/stub labels come from the base labels file (the builder
            # returns only the patched-slot addrs; the patch moves nothing).
            from build_dev_ipl_loader_bios import parse_labels as _parse
            full_b = _parse(out / "ipl-loader-bios-g5b-slot02ret-base.lbl",
                            ("api_unimplemented", "cd_halt_fail", "cd_unexpected_interrupt"))
            rom_labels["B"] = (exp_b, full_b)
        if "C" in args.variants:
            _base_c, exp_c, add_c = build_variant_bc(
                original, out, sel, args.mode, "g5c-slot0233ret", defines_public,
                False, (0x02, 0x33), 758)
            from build_dev_ipl_loader_bios import parse_labels as _parse2
            full_c = _parse2(out / "ipl-loader-bios-g5c-slot0233ret-base.lbl",
                             ("api_unimplemented", "cd_halt_fail", "cd_unexpected_interrupt"))
            rom_labels["C"] = (exp_c, full_c)
        # D: independent input interface (never hardcoded to A/B outputs).
        # MAJOR-4: derive from existing experimental manifests (read-only),
        # verify size/SHA/HEAD/pins/wiring, cross-check embedded selection.
        d_manifest = None
        d_expected_slots = None
        if args.experimental_rom:
            try:
                d_manifest = json.loads(Path(args.experimental_manifest).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                print("BLOCKED: experimental manifest unreadable", file=sys.stderr)
                return 3
            try:
                checked = M.validate_experimental_manifest(d_manifest)
            except ValueError as e:
                print(str(e), file=sys.stderr)
                return 3
            d_expected_slots = tuple(checked["slots"])
            d_rom = Path(args.experimental_rom)
            try:
                data = d_rom.read_bytes()
            except OSError:
                print("BLOCKED: experimental ROM unreadable", file=sys.stderr)
                return 3
            try:
                M.verify_d_rom_bytes(data, d_manifest)
                M.verify_d_rom_slot_targets(data, d_manifest)
            except ValueError as e:
                print(str(e), file=sys.stderr)
                return 3
            branch = d_manifest.get("branch", "?")
            head12 = (d_manifest.get("head") or "?")[:12]
            print(f"D manifest OK: slots {[f'${s:02X}' for s in checked['slots']]} "
                  f"native={checked.get('native', False)} branch={branch} head={head12}")
            # D execution needs a verified stub label; without it the run is
            # BLOCKED (never guessed). Accept legacy labels.* or native
            # symbols.* (read-only; A/B sources untouched).
            d_stub = None
            d_labels = d_manifest.get("labels")
            if isinstance(d_labels, dict) and isinstance(d_labels.get("api_unimplemented"), int):
                d_stub = d_labels["api_unimplemented"]
            else:
                d_syms = d_manifest.get("symbols")
                if isinstance(d_syms, dict) and isinstance(d_syms.get("api_unimplemented"), int):
                    d_stub = d_syms["api_unimplemented"]
            # Native trace_symbols may carry the stub under another name;
            # only api_unimplemented counts (never guessed from body addrs).
            if isinstance(d_stub, int) and 0xE000 <= d_stub <= 0xFFFF:
                try:
                    E.verify_stub_bytes(d_rom, d_stub)
                except ValueError as e:
                    print(f"BLOCKED: D stub unverifiable ({e})", file=sys.stderr)
                    return 3
                rom_labels["D"] = (d_rom, {"api_unimplemented": d_stub,
                                              "cd_halt_fail": d_labels.get("cd_halt_fail", d_stub) if isinstance(d_labels, dict) else d_stub,
                                              "cd_unexpected_interrupt": d_labels.get("cd_unexpected_interrupt", d_stub) if isinstance(d_labels, dict) else d_stub})
            else:
                results["D"] = {"variant": "D", "return_kind": "REAL_SERVICE",
                                "verdict": "BLOCKED",
                                "detail": ("manifest valid; execution labels missing (not guessed); "
                                           "additional required field: labels.api_unimplemented"),
                                "manifest_slots": [f"${s:02X}" for s in checked["slots"]],
                                "branch": d_manifest.get("branch"), "head": d_manifest.get("head")}

        for key in ("A", "B", "C"):
            if key not in rom_labels:
                continue
            rom, lab = rom_labels[key]
            rk = M.return_kind_for_variant(key)
            try:
                records = run_variant_synthetic(probe, rom, lab, tmp, f"g5_seq23_{key.lower()}",
                                                args.budget)
            except (ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                results[key] = {"variant": key, "return_kind": rk, "verdict": "BLOCKED",
                                "detail": f"probe failed: {type(e).__name__}"}
                continue
            summary = summarize_run(records, lab["api_unimplemented"], key, rk,
                                    frame_progress=False, screen_changed=False,
                                    title_observed=False, title_basis="")
            # Attach build identity (structural only, synthetic SEL is public).
            summary["rom_name"] = rom.name
            summary["ipl_format"] = "public"
            summary["media"] = "synthetic"
            results[key] = summary
            print(f"variant={key} apis={summary['api_count']} last={summary['last_slot']} "
                  f"verdict={summary['verdict']} stop={[summary['stop_cold']['cause'], summary['stop_warm']['cause']]}")
        if "D" in args.variants and "D" in rom_labels:
            rom_d, lab_d = rom_labels["D"]
            # MAJOR-4: cross-check embedded selection vs the synthetic SEQ23
            # medium (LBA SEL). Mismatch -> BLOCKED (never measured as return).
            # Identity is measured from the generated synthetic cue when possible;
            # an LBA coincidence alone is never enough (MEDIUM).
            synth_identity = None
            try:
                _cue, _halt = seq23_disc(tmp, "g5_seq23_identity")
                import experimental_track_selection as _ets
                synth_identity = _ets.media_identity(_cue)
            except Exception:
                synth_identity = None
            if d_manifest is not None:
                try:
                    M.check_embedded_selection_vs_executed(
                        d_manifest, {"lba": sel, "identity": synth_identity})
                except ValueError as e:
                    # No embedded LBA (legacy G5 manifests) means the linkage
                    # is unmeasured -> BLOCKED with the required-field report.
                    # A synthetic LBA mismatch is also BLOCKED (wrong medium).
                    results["D"] = {"variant": "D", "return_kind": "REAL_SERVICE",
                                    "verdict": "BLOCKED", "detail": str(e),
                                    "manifest_slots": [f"${s:02X}" for s in (d_expected_slots or ())]}
                    print(f"variant=D BLOCKED selection cross-check: {e}", file=sys.stderr)
                else:
                    try:
                        records = run_variant_synthetic(probe, rom_d, lab_d, tmp, "g5_seq23_d",
                                                        args.budget)
                    except (ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e2:
                        results["D"] = {"variant": "D", "return_kind": "REAL_SERVICE",
                                        "verdict": "BLOCKED",
                                        "detail": f"probe failed: {type(e2).__name__}"}
                    else:
                        summary = summarize_run(records, lab_d.get("api_unimplemented"), "D",
                                                "REAL_SERVICE", frame_progress=False,
                                                screen_changed=False, title_observed=False,
                                                title_basis="", title_evidence=None,
                                                expected_slots=d_expected_slots)
                        summary["media"] = "synthetic"
                        summary["rom_name"] = Path(rom_d).name
                        results["D"] = summary
                        print(f"variant=D apis={summary['api_count']} verdict={summary['verdict']}")
            else:
                results["D"] = {"variant": "D", "return_kind": "REAL_SERVICE",
                                "verdict": "BLOCKED", "detail": "D manifest missing"}

        # --- Comparison A vs B vs C (immediate). ---
        comparison = {}
        for key in ("A", "B", "C"):
            r = results.get(key)
            comparison[key] = ({"apis": r.get("api_count"), "last": r.get("last_slot"),
                                "verdict": r.get("verdict")} if r else {"verdict": "NOT_RUN"})
        evidence["variants_synthetic"] = results
        evidence["comparison_abc"] = comparison
        expected = {"A": 1, "B": 2, "C": 3}
        diff_note = []
        for key, want in expected.items():
            if key in results and isinstance(results[key].get("api_count"), int):
                got = results[key]["api_count"]
                diff_note.append(f"{key}:{got}(want {want})")
        evidence["abc_differential"] = ", ".join(diff_note)

        # --- Baseline progress diff. ---
        if args.baseline:
            try:
                baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                print("BLOCKED: baseline unreadable", file=sys.stderr)
                return 3
            prog = {}
            base_vars = baseline.get("variants_synthetic", baseline.get("variants", {}))
            for key, cur in results.items():
                if not isinstance(cur, dict) or "api_count" not in cur:
                    continue
                b = base_vars.get(key, {}) if isinstance(base_vars, dict) else {}
                prog[key] = M.diff_progress(
                    {"api_count": cur.get("api_count", 0), "last_slot": cur.get("last_slot"),
                     "steps_cold": (cur.get("insn_steps") or {}).get("cold"),
                     "verdict": cur.get("verdict")}, b or None)
            evidence["progress_vs_baseline"] = prog
            print(f"progress vs baseline: {prog}")

        # --- Opt-in commercial measurement. ---
        commercial = {"status": "NOT_RUN", "reason": "no --commercial flag"}
        if args.commercial:
            if args.rom_dir is not None and os.environ.get("OSS_PCE_ALLOW_ROM_DIR") != "1":
                print("FAIL: --rom-dir is a test hook requiring OSS_PCE_ALLOW_ROM_DIR=1",
                      file=sys.stderr)
                return 1
            try:
                rom_dir = (Path(os.path.abspath(args.rom_dir)) if args.rom_dir
                           else commercial_media.resolve_rom_dir(ROOT))
            except ValueError as e:
                commercial = {"status": "BLOCKED", "reason": str(e)}
                evidence["commercial"] = commercial
                return finish(evidence, output, commercial_only=True)
            try:
                cue_path = E.resolve_commercial_cue(rom_dir, args.commercial_cue,
                                                    args.commercial_index)
            except ValueError as e:
                commercial = {"status": ("NOT_RUN" if str(e).startswith("NOT_RUN") else "BLOCKED"),
                              "reason": str(e)}
                evidence["commercial"] = commercial
                return finish(evidence, output, commercial_only=True)
            if not args.commercial_output or not args.commercial_auto_loader:
                print("FAIL: commercial mode needs --commercial-auto-loader and --commercial-output",
                      file=sys.stderr)
                return 1
            try:
                E.validate_commercial_output(args.commercial_output)
            except ValueError as e:
                print(f"FAIL commercial output rejected: {e}", file=sys.stderr)
                return 1
            try:
                snap = E.snapshot_media(cue_path, rom_dir)
            except ValueError as e:
                print(str(e), file=sys.stderr)
                return 1
            try:
                sel_c = commercial_media.data_track_index01_lba(cue_path, rom_dir)
            except ValueError as e:
                print(str(e), file=sys.stderr)
                return 1
            try:
                E.verify_media_unchanged(snap)
            except ValueError as e:
                print(str(e), file=sys.stderr)
                return 1
            defines_c = (["-D", "IPL_PUBLIC"] if args.commercial_loader_format == "public" else [])
            comm_out = out
            c_results = {}
            try:
                rom_ca, lab_ca = build_variant_a(
                    original, comm_out, sel_c, args.mode, "g5a-comm", defines_c, True)
                _bb, exp_cb, _ab = build_variant_bc(
                    original, comm_out, sel_c, args.mode, "g5b-comm", defines_c,
                    True, (0x02,), 756)
                _bc, exp_cc, _ac = build_variant_bc(
                    original, comm_out, sel_c, args.mode, "g5c-comm", defines_c,
                    True, (0x02, 0x33), 758)
            except (ValueError, subprocess.CalledProcessError) as e:
                print(f"FAIL commercial loader build: {e}", file=sys.stderr)
                return 1
            try:
                E.verify_media_unchanged(snap)
            except ValueError as e:
                print(str(e), file=sys.stderr)
                return 1
            from build_dev_ipl_loader_bios import parse_labels as _parse3
            full_cb = _parse3(comm_out / "ipl-loader-bios-g5b-comm-base.lbl",
                              ("api_unimplemented", "cd_halt_fail", "cd_unexpected_interrupt", "reset"))
            full_cc = _parse3(comm_out / "ipl-loader-bios-g5c-comm-base.lbl",
                              ("api_unimplemented", "cd_halt_fail", "cd_unexpected_interrupt", "reset"))
            comm_roms = {"A": (rom_ca, lab_ca), "B": (exp_cb, full_cb), "C": (exp_cc, full_cc)}
            # MAJOR-4 commercial-D: same opt-in/read-only/sanitize discipline.
            # D runs on the commercial medium only when an experimental ROM +
            # manifest were supplied; embedded selection is cross-checked vs
            # the executed commercial LBA (mismatch -> BLOCKED, never measured).
            if "D" in args.variants and args.experimental_rom and d_manifest is not None:
                try:
                    _d_data = Path(args.experimental_rom).read_bytes()
                    M.verify_d_rom_bytes(_d_data, d_manifest)
                    M.verify_d_rom_slot_targets(_d_data, d_manifest)
                except (OSError, ValueError) as _e:
                    c_results["D"] = {"verdict": "BLOCKED", "detail": str(_e),
                                      "api_count": 0, "last_slot": None,
                                      "cold_warm_identical": False,
                                      "stop_cold": {"cause": "BLOCKED", "evidence": "UNKNOWN"},
                                      "stop_warm": {"cause": "BLOCKED", "evidence": "UNKNOWN"}}
                    print(f"commercial variant=D BLOCKED ROM verify: {_e}", file=sys.stderr)
                    d_manifest = None
                if d_manifest is not None and d_expected_slots is None and "D" not in c_results:
                    try:
                        d_expected_slots = tuple(M.validate_experimental_manifest(d_manifest)["slots"])
                    except ValueError:
                        d_expected_slots = None
                d_stub_c = None
                d_lab = d_manifest.get("labels") if isinstance(d_manifest.get("labels"), dict) else None
                if d_lab is None and isinstance(d_manifest.get("symbols"), dict):
                    d_lab = d_manifest.get("symbols")
                if isinstance(d_lab, dict) and isinstance(d_lab.get("api_unimplemented"), int):
                    d_stub_c = d_lab["api_unimplemented"]
                # Reuse the existing read-only snapshot/identity mechanism for
                # commercial: content identity of the mounted (snapshot-verified)
                # medium, never the private stat snapshot itself.
                comm_identity = None
                try:
                    import experimental_track_selection as _ets2
                    comm_identity = _ets2.media_identity(cue_path)
                except Exception:
                    comm_identity = None
                try:
                    M.check_embedded_selection_vs_executed(
                        d_manifest, {"lba": sel_c, "identity": comm_identity})
                except ValueError as e:
                    c_results["D"] = {"verdict": "BLOCKED", "detail": str(e),
                                      "api_count": 0, "last_slot": None,
                                      "cold_warm_identical": False,
                                      "stop_cold": {"cause": "BLOCKED", "evidence": "UNKNOWN"},
                                      "stop_warm": {"cause": "BLOCKED", "evidence": "UNKNOWN"}}
                    print(f"commercial variant=D BLOCKED selection cross-check: {e}", file=sys.stderr)
                else:
                    if d_stub_c is None:
                        c_results["D"] = {"verdict": "BLOCKED",
                                          "detail": "D commercial needs labels.api_unimplemented (not guessed)",
                                          "api_count": 0, "last_slot": None,
                                          "cold_warm_identical": False,
                                          "stop_cold": {"cause": "BLOCKED", "evidence": "UNKNOWN"},
                                          "stop_warm": {"cause": "BLOCKED", "evidence": "UNKNOWN"}}
                    else:
                        try:
                            E.verify_stub_bytes(Path(args.experimental_rom), d_stub_c)
                        except ValueError as e:
                            c_results["D"] = {"verdict": "BLOCKED", "detail": f"D stub unverifiable ({e})",
                                              "api_count": 0, "last_slot": None,
                                              "cold_warm_identical": False,
                                              "stop_cold": {"cause": "BLOCKED", "evidence": "UNKNOWN"},
                                              "stop_warm": {"cause": "BLOCKED", "evidence": "UNKNOWN"}}
                        else:
                            # Commercial-D probe on the game IPL (structural only).
                            d_labels_path = comm_out / "g5-commercial-labels-d.labels"
                            d_labels_path.write_text(
                                f"cd_halt_ok {lab_ca['reset']:04x}\ncd_halt_fail {lab_ca['cd_halt_fail']:04x}\n"
                                f"cd_unexpected_interrupt {lab_ca['cd_unexpected_interrupt']:04x}\n"
                                f"api_unimplemented {d_stub_c:04x}\n", encoding="ascii")
                            try:
                                stub_d = E.resolve_commercial_stub(d_labels_path, Path(args.experimental_rom))
                            except ValueError as e:
                                print(f"FAIL commercial D labels: {e}", file=sys.stderr)
                                return 1
                            try:
                                d_records = E.run_probe(probe, Path(args.experimental_rom), cue_path, d_labels_path, 80_000_000, "commercial")
                            except ValueError as e:
                                print(f"FAIL commercial D observation: {e}", file=sys.stderr)
                                return 1
                            try:
                                E.verify_media_unchanged(snap)
                            except ValueError as e:
                                print(str(e), file=sys.stderr)
                                return 1
                            d_summary = summarize_run(d_records, stub_d, "D", "REAL_SERVICE",
                                                      title_evidence=None, expected_slots=d_expected_slots)
                            d_summary["media"] = "commercial-structural-only"
                            d_summary.pop("calls_cold", None)
                            c_results["D"] = {"verdict": d_summary["verdict"], "detail": d_summary["detail"],
                                              "api_count": d_summary["api_count"], "last_slot": d_summary["last_slot"],
                                              "cold_warm_identical": d_summary["cold_warm_identical"],
                                              "stop_cold": d_summary["stop_cold"],
                                              "stop_warm": d_summary["stop_warm"]}
                            print(f"commercial variant=D apis={d_summary['api_count']} verdict={d_summary['verdict']}")
            for key, (crom, clab) in comm_roms.items():
                if key not in args.variants:
                    continue
                dummy = clab["reset"]
                labels_path = comm_out / f"g5-commercial-labels-{key.lower()}.labels"
                labels_path.write_text(
                    f"cd_halt_ok {dummy:04x}\ncd_halt_fail {clab['cd_halt_fail']:04x}\n"
                    f"cd_unexpected_interrupt {clab['cd_unexpected_interrupt']:04x}\n"
                    f"api_unimplemented {clab['api_unimplemented']:04x}\n", encoding="ascii")
                try:
                    stub_c = E.resolve_commercial_stub(labels_path, crom)
                except ValueError as e:
                    print(f"FAIL commercial labels: {e}", file=sys.stderr)
                    return 1
                try:
                    records = E.run_probe(probe, crom, cue_path, labels_path, 80_000_000,
                                          "commercial")
                except ValueError as e:
                    print(f"FAIL commercial observation: {e}", file=sys.stderr)
                    return 1
                try:
                    E.verify_media_unchanged(snap)
                except ValueError as e:
                    print(str(e), file=sys.stderr)
                    return 1
                rk = M.return_kind_for_variant(key)
                summary = summarize_run(records, stub_c, key, rk)
                summary["media"] = "commercial-structural-only"
                # Strip anything private (fail-closed): commercial summaries
                # must already be structural; refuse to save otherwise.
                summary.pop("calls_cold", None)
                c_results[key] = {"verdict": summary["verdict"], "detail": summary["detail"],
                                  "api_count": summary["api_count"], "last_slot": summary["last_slot"],
                                  "cold_warm_identical": summary["cold_warm_identical"],
                                  "stop_cold": summary["stop_cold"],
                                  "stop_warm": summary["stop_warm"]}
                print(f"commercial variant={key} apis={summary['api_count']} "
                      f"verdict={summary['verdict']}")
            # Structural commercial evidence to the OUTSIDE-repo path only.
            commercial = {"status": "OBSERVED_STRUCTURAL_ONLY", "results": c_results,
                          "note": ("structural counts only; no media names/paths/hashes; "
                                   "Geargrafx observations are not hardware proof")}
            try:
                E.save_commercial_evidence(
                    args.commercial_output,
                    json.dumps(commercial, indent=2, sort_keys=True) + "\n")
            except ValueError as e:
                print(f"FAIL commercial evidence save failed: {e}", file=sys.stderr)
                return 1
        evidence["commercial"] = commercial

    # --- Write public evidence (sanitized when commercial ran). ---
    return finish(evidence, output, commercial_only=False)


def finish(evidence: dict, output: Path, commercial_only: bool) -> int:
    _ = commercial_only
    if evidence.get("commercial", {}).get("status", "NOT_RUN") not in ("NOT_RUN",):
        # Any commercial observation path must pass public sanitization
        # before anything is written inside the repo.
        try:
            M.sanitize_for_public(evidence)
        except ValueError as e:
            print(f"BLOCKED: public evidence refused ({e})", file=sys.stderr)
            return 3
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"G5 harness evidence: {output.name} "
          f"abc=[{evidence.get('abc_differential')}] commercial={evidence.get('commercial', {}).get('status')}")
    print("NOT a compatible System Card BIOS; Geargrafx observations are not hardware proof; "
          "release_eligible=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
