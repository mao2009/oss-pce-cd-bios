"""Wave1-B (Issue #762, child of #635): PUBLIC/DEV loader-validation gap differential.

Synthetic media only, pinned Geargrafx core. NOT a System Card BIOS, NOT a
BIOS ABI, NOT game compatibility, NOT G5 success. No commercial media, no
reference BIOS, no src/overrides changes, no RTS-chain extension: genuine
callees (slots $02/$33/$4A) stay UNKNOWN/BLOCKED.

Question: on commercial media the DEV loader halts in IPL validation
(PRE_API_BLOCKER_OBSERVED) while the PUBLIC loader proceeds to the first API
call. Which validation field(s) cause the gap?

Method: each synthetic disc is run on BOTH the DEV (main) and PUBLIC loader
ROMs (cold+warm) from ONE cue file, so the DEV/PUBLIC difference is explained
by the loader builds only. The differential table (see docs/635-public-dev-gap.md):

- D1 MPR-raw: the only single-field flip. DEV refuses code 25, PUBLIC runs.
- D2/D3/D4 (count 11 / load-end $8800 / reserved-nonzero, each with the
  PUBLIC-required raw MPR): the DEV code (22/23/28) proves that check fires
  BEFORE the MPR check in validation order, so each is an independent
  blocker; PUBLIC runs. Co-blockers isolated by code order, not by flip.
- D5 bad-checksum with DEV-valid MPR: DEV refuses code 30 (copied, stage 6),
  PUBLIC refuses code 25 (MPR first). Refusal-code divergence: the checksum
  can never be the first-firing check on a PUBLIC-passing disc.

Only the completed differential (all 5 discs match both ROM expectations)
exits 0. Heavy emu budget: this ONE focused run (5 discs x 2 ROMs).
"""

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(HERE))

import geargrafx_ipl_loader as L  # noqa: E402
from geargrafx_api import compile_probe  # noqa: E402
from geargrafx_cdread import execute  # noqa: E402

MPRS_RAW = [0, 1, 2, 3, 4]

# Each disc: shared synthetic-disc params + per-ROM judge expectations.
# dev/pub are complete judge() cases (names differ; the disc is built once).
DIFFERENTIAL = [
    dict(disc="D1_mpr_raw", kind="pass_gap", pay="A",
         n=5, iplblk=2, sta=0x3000, entry=0x3000, mprs=MPRS_RAW,
         info_edits=[],
         dev_code=25, dev_copied=0,
         rationale="single-field flip: only bytes 8..12 differ from the DEV "
                   "baseline (the header checksum stays correct); DEV code 25 "
                   "proves the MPR check is the refusing element"),
    dict(disc="D2_count_11", kind="pass_gap", pay="A",
         n=11, iplblk=2, sta=0x3000, entry=0x3000, mprs=MPRS_RAW,
         info_edits=[],
         dev_code=22, dev_copied=0,
         rationale="DEV count check (22) precedes MPR (25) in validation "
                   "order, so code 22 isolates the IPLBLN range as an "
                   "independent blocker although the disc also carries the "
                   "PUBLIC-required raw MPR"),
    dict(disc="D3_load_end_8800", kind="pass_gap", pay="D",
         n=5, iplblk=2, sta=0x6000, entry=0x6000, mprs=MPRS_RAW,
         info_edits=[],
         dev_code=23, dev_copied=0,
         rationale="DEV load-end check (23) precedes MPR (25); end $8800 is "
                   "above the DEV $8000 window but inside the PUBLIC $E000 "
                   "window"),
    dict(disc="D4_reserved_nonzero", kind="pass_gap", pay="A",
         n=5, iplblk=2, sta=0x3000, entry=0x3000, mprs=MPRS_RAW,
         info_edits=[(20, 0x01)],
         dev_code=28, dev_copied=0,
         rationale="DEV reserved-zero check (28) is the first field check, "
                   "so code 28 isolates bytes 16..31 as an independent "
                   "blocker although the disc also carries raw MPR"),
    dict(disc="D5_bad_checksum_std_mpr", kind="dual_refuse", pay="A",
         n=5, iplblk=2, sta=0x3000, entry=0x3000, mprs=list(L.MPRS_STD),
         info_edits=[(14, 0), (15, 0)],
         dev_code=30, dev_copied=5,
         rationale="checksum refusal needs DEV-valid MPR, which PUBLIC "
                   "refuses first (25); dual-refuse divergence proves the "
                   "order. DEV copies then refuses (stage 6, code 30)"),
]

PAY_ORIGINS = dict(L.PAY_ORIGINS)
PAY_ORIGINS["D"] = (0x6000, 0xD0)

ALLOWED_KINDS = ("pass_gap", "dual_refuse")


def disc_case(disc, role):
    """Build the judge() case for one ROM role (pure; no I/O)."""
    if disc["kind"] not in ALLOWED_KINDS:
        raise ValueError(f"{disc['disc']}: unknown kind {disc['kind']!r}")
    if role not in ("dev", "pub"):
        raise ValueError(f"unknown role {role!r}")
    base = dict(name=f"{role}_{disc['disc']}", pay=disc["pay"], n=disc["n"],
                iplblk=disc["iplblk"], sta=disc["sta"], entry=disc["entry"],
                mprs=list(disc["mprs"]), openmode=0,
                info_edits=[tuple(e) for e in disc["info_edits"]])
    if role == "dev":
        base.update(reason="cd_halt_fail", code=disc["dev_code"],
                    copied=disc["dev_copied"], payload_ran=False)
    else:
        if disc["kind"] == "pass_gap":
            base.update(reason="cd_halt_ok", code=0, copied=disc["n"],
                        payload_ran=True, rom="public", public=True,
                        mpr_add=0x80)
        else:
            base.update(reason="cd_halt_fail", code=25, copied=0,
                        payload_ran=False, rom="public", public=True)
    return base


def check_gap_pair(dev_obs, pub_obs, disc):
    """Pure differential verdict for one disc (host-testable, no emu).

    dev_obs/pub_obs are per-cycle L.observe_run() dicts (exactly cold+warm
    each). Returns True when the pair matches the disc's kind; raises
    ValueError otherwise (swapped, stale, or partial matches never pass).
    """
    for label, obs_list in (("dev", dev_obs), ("pub", pub_obs)):
        cycles = sorted(o.get("cycle") for o in obs_list)
        if cycles != ["cold", "warm"]:
            raise ValueError(f"{disc['disc']} {label}: need cold+warm, got {cycles!r}")
    if disc["kind"] == "pass_gap":
        for obs in dev_obs:
            if obs.get("reason") != "cd_halt_fail" or obs.get("halt_confirmed") is not True:
                raise ValueError(f"{disc['disc']} dev {obs.get('cycle')}: no confirmed "
                                 f"validation halt ({obs.get('reason')})")
            if (obs.get("code"), obs.get("copied")) != (disc["dev_code"], 0):
                raise ValueError(f"{disc['disc']} dev {obs.get('cycle')}: code/copied "
                                 f"{obs.get('code')}/{obs.get('copied')}, expected "
                                 f"{disc['dev_code']}/0")
            if obs.get("payload_ran") is not False:
                raise ValueError(f"{disc['disc']} dev {obs.get('cycle')}: payload trace "
                                 f"present on a refused disc")
        for obs in pub_obs:
            if obs.get("reason") != "cd_halt_ok" or obs.get("halt_confirmed") is not True:
                raise ValueError(f"{disc['disc']} pub {obs.get('cycle')}: no confirmed "
                                 f"payload halt ({obs.get('reason')})")
            if (obs.get("code"), obs.get("copied")) != (0, disc["n"]):
                raise ValueError(f"{disc['disc']} pub {obs.get('cycle')}: code/copied "
                                 f"{obs.get('code')}/{obs.get('copied')}, expected 0/{disc['n']}")
            if obs.get("payload_ran") is not True:
                raise ValueError(f"{disc['disc']} pub {obs.get('cycle')}: payload did "
                                 f"not run on the PUBLIC ROM")
    elif disc["kind"] == "dual_refuse":
        for obs in dev_obs:
            if obs.get("reason") != "cd_halt_fail" or obs.get("halt_confirmed") is not True:
                raise ValueError(f"{disc['disc']} dev {obs.get('cycle')}: no confirmed halt")
            if (obs.get("code"), obs.get("copied")) != (30, disc["n"]):
                raise ValueError(f"{disc['disc']} dev {obs.get('cycle')}: code/copied "
                                 f"{obs.get('code')}/{obs.get('copied')}, expected 30/{disc['n']}")
            if obs.get("payload_ran") is not False:
                raise ValueError(f"{disc['disc']} dev {obs.get('cycle')}: payload ran")
        for obs in pub_obs:
            if obs.get("reason") != "cd_halt_fail" or obs.get("halt_confirmed") is not True:
                raise ValueError(f"{disc['disc']} pub {obs.get('cycle')}: no confirmed halt")
            if (obs.get("code"), obs.get("copied")) != (25, 0):
                raise ValueError(f"{disc['disc']} pub {obs.get('cycle')}: code/copied "
                                 f"{obs.get('code')}/{obs.get('copied')}, expected 25/0")
            if obs.get("payload_ran") is not False:
                raise ValueError(f"{disc['disc']} pub {obs.get('cycle')}: payload ran")
    else:
        raise ValueError(f"{disc['disc']}: unknown kind {disc['kind']!r}")
    return True


def run_disc(probe, roms, pay_code, pay_halt, disc, folder: Path):
    """Run ONE cue file on both ROMs and judge the differential (real core)."""
    dev_case, pub_case = disc_case(disc, "dev"), disc_case(disc, "pub")
    cue, body = L.prepare_case_disc(dev_case, pay_code, folder)
    # The PUBLIC case shares the identical disc bytes: same n/iplblk/sta/
    # entry/mprs/edits, so judging it against the same cue/body is exact.
    # (Only expectations differ.)
    results = {}
    for role, case, (rom, rom_labels) in (
            ("dev", dev_case, roms["main"]), ("pub", pub_case, roms["public"])):
        labels = folder / f"{case['name']}.labels"
        labels.write_text(
            f"cd_halt_ok {pay_halt:04x}\n"
            f"cd_halt_fail {rom_labels['cd_halt_fail']:04x}\n"
            f"cd_unexpected_interrupt {rom_labels['cd_unexpected_interrupt']:04x}\n",
            encoding="ascii")
        records = execute(probe, rom, f"card:{cue}", labels, L.BUDGET, None)
        L.judge(records, case, body, PAY_ORIGINS[disc["pay"]][1])
        runs = L.require_cold_warm_cycles(records, case["name"])
        results[role] = [L.observe_run(r) for r in runs]
    check_gap_pair(results["dev"], results["pub"], disc)
    return {"status": "GAP_CONFIRMED" if disc["kind"] == "pass_gap" else "DIVERGENT_REFUSAL",
            "dev_code": disc["dev_code"],
            "body_sha256": hashlib.sha256(body).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("debug", "release"), default="debug")
    args = parser.parse_args()
    out = ROOT / "build" / args.mode / "public-dev-gap"
    out.mkdir(parents=True, exist_ok=True)
    dev_rom, dev_labels = L.build_loader(out, L.SEL)
    dev_sha = hashlib.sha256(dev_rom.read_bytes()).hexdigest()
    pub_rom, pub_labels = L.build_loader(out, L.SEL, "public", ["-D", "IPL_PUBLIC"])
    pub_sha = hashlib.sha256(pub_rom.read_bytes()).hexdigest()
    roms = {"main": (dev_rom, dev_labels), "public": (pub_rom, pub_labels)}
    payloads = {}
    for key, (origin, tag) in PAY_ORIGINS.items():
        # D payload: same builder, new origin/tag (position-independent code).
        payloads[key] = L.build_payload(out, origin, tag)
    probe = compile_probe(out, "geargrafx_cdread_probe.cpp", "geargrafx-public-dev-gap-probe")
    discs = {}
    with tempfile.TemporaryDirectory(prefix="pce-public-dev-gap-") as tmp:
        tmp = Path(tmp)
        for disc in DIFFERENTIAL:
            code, halt = payloads[disc["pay"]]
            try:
                discs[disc["disc"]] = run_disc(probe, roms, code, halt, disc,
                                               tmp / disc["disc"])
            except ValueError as error:
                raise SystemExit(f"FAIL {disc['disc']}: {error}") from error
        if hashlib.sha256(dev_rom.read_bytes()).hexdigest() != dev_sha:
            raise SystemExit("FAIL: DEV loader ROM changed during the test")
        if hashlib.sha256(pub_rom.read_bytes()).hexdigest() != pub_sha:
            raise SystemExit("FAIL: PUBLIC loader ROM changed during the test")
    evidence = {
        "scope": "PUBLIC/DEV loader-validation gap differential (Issue #762, child of "
                 "#635). Synthetic media only; NOT compatible, NOT a BIOS ABI, NOT "
                 "game compatibility, NOT G5 success.",
        "mode": args.mode, "dev_loader_rom_sha256": dev_sha,
        "public_loader_rom_sha256": pub_sha, "discs": discs, "status": "PASS"}
    (out / "public-dev-gap-evidence.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"PASS PUBLIC/DEV gap differential (synthetic): {len(discs)}/{len(DIFFERENTIAL)} "
          f"discs (cold+warm each, same cue on both ROMs); NOT compatible, NOT BIOS, "
          f"release_eligible=false")


if __name__ == "__main__":
    main()
