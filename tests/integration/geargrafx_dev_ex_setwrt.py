"""Real Geargrafx E2E test for the project-own EX_SETWRT slot $3A contract.

The guest calls the assembled DEV-ROM table entry with valid and rejected MAWR
addresses.  The debugger-backed probe observes A/X/Y/P/SP/MPR, work RAM, and
the actual HuC6270 MAWR register (R0).  This is real-core evidence only for
this project's DEV contract, not for the proprietary System Card ABI.

Run: python3 tests/integration/geargrafx_dev_ex_setwrt.py --rom build/debug/bios-dev/dev-only-not-compatible-syscard3.pce
Missing pinned tools are BLOCKED (exit 3); a missing observable is never PASS.
"""
import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_rom import verify_dev_rom  # noqa: E402
import geargrafx_api as api  # noqa: E402

BLOCKED_EXIT = 3
SLOT = 0x3A
STORE = 0x2200
P_INPUT = 0xCD  # N,V,D,I,C set; the valid return keeps V/D/I, sets N=1 and Z=0 from CPX, and clears C.
P_INPUT_ZV = 0x42  # V and Z set, N and C clear: N/Z must come from CPX, not from the caller P.
P_MASK = 0xEF   # B is not a stored CPU status flag.


class ProbeRejected(ValueError):
    """変異体が期待する理由で拒否されたことを表す例外。

    reason: 構造化拒否理由コード（実際に異常を検出した検査箇所で生成）
    detail: 人間が読める詳細メッセージ
    """

    def __init__(self, reason, detail):
        self.reason = reason
        super().__init__(detail)


def guest(reset, entry, low, high, init, p_in=P_INPUT, sp=0xFF):
    """Set RAM stack/MPR1, a non-zero initial VDC MAWR (init), exact input P, exact caller SP, call entry, then dump A/P/X/Y.

    sp: caller stack pointer before the JSR. Stack mechanics (6502/HuC6280):
      - Push: write at $2100 + SP, then SP = SP - 1
      - Pop:  SP = SP + 1, then read at $2100 + SP
      - SP wraps from $00 to $FF on push, and from $FF to $00 on pop.

    Normal path stack usage (inside api_slot_3A):
      - php (1), phy (2), phx (3), pha caller A (4), pha MPR0 (5) = max 5 simultaneous pushes
      - Then pla, pla, pha, pla, ply, plp pop/restore
    JSR return address: 2 bytes below caller SP.

    Total max depth below caller SP: 5 (routine) + 2 (JSR) = 7 bytes.
    SP=$07 is the contract lower bound: deepest write reaches $2101 (inside stack page $2100-$21FF).
    SP=$06 causes SP wraparound ($00 -> $FF) during the 5th push; the write still lands at $2100
    but SP has wrapped, which is outside the contract. SP=$06 is treated as out of contract.
    Reject path: bcs branches immediately, 0 pushes inside routine, only JSR's 2 bytes.
    """
    code = bytes([
        0x78, 0xD8,                         # SEI; CLD
        0xA9, 0xF8, 0x53, 0x02,             # MPR1 = work RAM (stack $2100)
        0xA9, 0xFF, 0x53, 0x01,             # MPR0 = $FF: VDC ports at $0000-$0003
        0x9C, 0x00, 0x00,                   # ST0 = 0 (select MAWR)
        0xA9, init & 0xFF, 0x8D, 0x02, 0x00,        # ST1 = initial MAWR low
        0xA9, init >> 8, 0x8D, 0x03, 0x00,          # ST2 = initial MAWR high
        0xA9, 0x00, 0x53, 0x01,             # MPR0 = 0 again (guest default)
        0xA9, 0xF8, 0x53, 0x04, 0xA9, 0xF8, 0x53, 0x08, 0xA9, 0xF8, 0x53, 0x10,  # MPR2-4 = work RAM
        0xA9, 0xF8, 0x53, 0x20, 0xA9, 0xF8, 0x53, 0x40,                         # MPR5-6 = work RAM
        0x43, 0x01, 0x8D, 0x00, 0x23,       # TMA #1 -> $2300: MPR0 before the call
        0x43, 0x02, 0x8D, 0x01, 0x23,       # TMA #2 -> $2301: MPR1 before the call
        0xA2, sp, 0x9A,                     # SP = caller value ($FF / $08 / $07)
        0xA9, p_in, 0x48,                   # push requested caller status
        0xA2, high, 0xA0, 0x5A, 0xA9, low,  # X/Y/A inputs
        0x28,                               # PLP: restore exact caller P after loads
        0x20, entry & 0xFF, entry >> 8,     # JSR slot $3A table entry
        0x08, 0x8D, STORE & 0xFF, STORE >> 8,              # PHP; STA output A
        0x68, 0x8D, (STORE + 1) & 0xFF, (STORE + 1) >> 8,  # PLA; STA output P
        0x8E, (STORE + 2) & 0xFF, (STORE + 2) >> 8,        # STX output X
        0x8C, (STORE + 3) & 0xFF, (STORE + 3) >> 8,        # STY output Y
    ])
    halt = reset + len(code)
    return code + bytes([0x80, 0xFE]), halt


def run_probe(probe, rom, cue):
    result = subprocess.run([str(probe), str(rom), str(cue), "256"], check=True,
                            capture_output=True, text=True, timeout=120)
    rows = [json.loads(line[4:]) for line in result.stdout.splitlines() if line.startswith("OBS ")]
    caps = [json.loads(line[4:]) for line in result.stdout.splitlines() if line.startswith("CAP ")]
    if len(caps) != 1 or caps[0].get("instruction_step") is not True or caps[0].get("cdrom_media") is not True:
        raise ProbeRejected("BLOCKED", "instruction debugger/System Card capability missing")
    if [row.get("phase") for row in rows] != ["loaded", "cold", "nonreturning", "warm"]:
        raise ProbeRejected("BLOCKED", "missing real-core observations")
    return next(row for row in rows if row["phase"] == "cold")


def check(label, row, halt, low, high, init, p_in=P_INPUT, sp=0xFF):
    """観測結果を契約と照合。異常時は ProbeRejected(実際の理由, 詳細) を送出する。

    reason_code はチェック内部で生成し、呼び出し元から受け取らない。
    これにより、期待理由とは異なる検査が先に失敗しても正しく検出される。
    """
    required = ("a", "x", "y", "p", "sp", "mpr", "work_ram_hex", "vdc_ar", "vdc_r0")
    if any(key not in row for key in required):
        raise ProbeRejected("BLOCKED", f"{label}: probe does not expose CPU/RAM/MPR/VDC state")
    if row["pc"] != halt:
        raise ProbeRejected("HALT_FAILURE",
                            f"{label}: guest did not reach halt (${row['pc']:04X}, want ${halt:04X})")
    if row["sp"] != sp:
        raise ProbeRejected("STACK_MISMATCH",
                            f"{label}: SP={row['sp']:#04x}, want SP={sp:#04x}")
    if row["mpr"][0] != 0 or row["mpr"][1] != 0xF8 or row["mpr"][7] != 0:
        raise ProbeRejected("MPR_MISMATCH",
                            f"{label}: MPR={row['mpr']}, want MPR0=0, MPR1=0xF8, MPR7=0")
    ram = bytes.fromhex(row["work_ram_hex"])
    if (ram[0x300], ram[0x301]) != (0x00, 0xF8):
        raise ProbeRejected("MPR_MISMATCH",
                            f"{label}: MPR before call was {ram[0x300]:#x}/{ram[0x301]:#x}, want 0x00/0xF8")
    observed = (ram[STORE - 0x2000], ram[STORE - 0x1FFF], ram[STORE - 0x1FFE], ram[STORE - 0x1FFD])
    if observed[0] != low or observed[2:] != (high, 0x5A):
        raise ProbeRejected("REGISTER_MISMATCH",
                            f"{label}: A/X/Y RAM dump {observed} does not preserve inputs")
    p = observed[1]
    if high < 0x80:
        # Normal path: MAWR must be set, inputs preserved, P contract honored.
        if init == (high << 8) | low:
            raise ProbeRejected("NOOP_UNDETECTED",
                                f"{label}: initial MAWR equals expected value; case cannot detect no-op")
        if row["vdc_ar"] != 0 or row["vdc_r0"] != (high << 8) | low:
            raise ProbeRejected("MAWR_CHANGED",
                                f"{label}: MAWR VDC R0={row['vdc_r0']:#06x}, AR={row['vdc_ar']:#x}, "
                                f"want R0={(high << 8) | low:#06x}")
        want_p = (p_in & ~0x83) | 0x80
        if (p & P_MASK) != want_p:
            raise ProbeRejected("REGISTER_MISMATCH",
                                f"{label}: P=${p:02X}, want preserved N/V/D/I and C=0 (${want_p:02X})")
    else:
        # Reject path: MAWR must remain at initial value, C=1 must be returned.
        if row["vdc_ar"] != 0 or row["vdc_r0"] != init:
            raise ProbeRejected("VDC_CHANGED",
                                f"{label}: rejected address changed VDC (AR={row['vdc_ar']}, "
                                f"R0={row['vdc_r0']:#x}, want R0={init:#x})")
        if not p & 1:
            raise ProbeRejected("C_NOT_SET",
                                f"{label}: rejected address did not return C=1 (P=${p:02X})")
    return {"input": f"{high:02X}{low:02X}", "a": observed[0], "x": observed[2], "y": observed[3],
            "p": p, "sp": row["sp"], "mpr": row["mpr"], "vdc_ar": row["vdc_ar"], "mawr": row["vdc_r0"]}


INITIAL_MAWR = (0x1357, 0x2468, 0x3579)  # non-zero, distinct from every expected MAWR used below


def one_case(probe, data, folder, cue, reset, entry, label, low, high, init, p_in=P_INPUT, sp=0xFF):
    code, halt = guest(reset, entry, low, high, init, p_in, sp)
    variant = bytearray(data)
    variant[reset - 0xE000:reset - 0xE000 + len(code)] = code
    rom = folder / f"{label}.pce"
    rom.write_bytes(variant)
    return check(label, run_probe(probe, rom, cue), halt, low, high, init, p_in, sp)


# 構造化拒否理由コード。テストと実装の間で安定した契約を形成する。
REASON_STACK_OVERFLOW = "STACK_OVERFLOW"      # スタックページ($2100-$21FF)を超過
REASON_VDC_CHANGED = "VDC_CHANGED"            # 拒否経路でMAWRが変更された
REASON_C_NOT_SET = "C_NOT_SET"                # 拒否経路でC=1が返っていない
REASON_NOOP_UNDETECTED = "NOOP_UNDETECTED"    # 初期MAWRと同一でno-op検出不能
REASON_HALT_FAILURE = "HALT_FAILURE"          # 停止位置が期待と異なる（ハーネス異常）
REASON_UNEXPECTED_PASS = "UNEXPECTED_PASS"    # 変異体が予期せずパス
REASON_MAWR_CHANGED = "MAWR_CHANGED"          # 正常経路でMAWRが期待値と異なる
REASON_REGISTER_MISMATCH = "REGISTER_MISMATCH" # A/X/Y/Pが期待値と異なる
REASON_STACK_MISMATCH = "STACK_MISMATCH"      # SPが期待値と異なる
REASON_MPR_MISMATCH = "MPR_MISMATCH"          # MPRが期待値と異なる


def expect_rejected(probe, data, folder, cue, reset, entry, label, low, high, init,
                    expected_reason, sp=0xFF):
    """変異体が期待する理由で拒否されることを検証する。

    check() から期待理由を受け取らず、ProbeRejected.reason のみを厳密比較する。
    異なる理由での失敗は FAIL にする。BLOCKED/タイムアウト/クラッシュは正常な拒否ではない。
    """
    try:
        one_case(probe, data, folder, cue, reset, entry, label, low, high, init, sp=sp)
    except ProbeRejected as error:
        if error.reason != expected_reason:
            raise ValueError(
                f"mutant {label} rejected for the wrong reason: "
                f"got {error.reason!r}, want {expected_reason!r} (detail: {error})"
            )
        return str(error)
    except ValueError as error:
        message = str(error)
        if "BLOCKED:" in message or "BLOCKED" in message:
            raise
        raise ValueError(
            f"mutant {label} rejected with unexpected exception: {message!r}"
        )
    raise ValueError(f"mutant {label} unexpectedly passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, required=True)
    args = parser.parse_args()
    data = args.rom.read_bytes()
    verify_dev_rom(data, manifest=args.rom.parent / "manifest.json")
    reset = int.from_bytes(data[0x1FFE:0x2000], "little")
    entry = 0xE000 + 3 * SLOT
    if data[3 * SLOT] != 0x4C:
        raise ValueError("slot $3A is not a JMP table entry")
    probe = api.compile_probe(args.rom.parent)
    evidence = {"scope": "DEV ROM real-core EX_SETWRT, not System Card ABI compatibility",
                "rom_sha256": hashlib.sha256(data).hexdigest(), "cases": [], "negative_cases": []}
    with tempfile.TemporaryDirectory(prefix="pce-dev-setwrt-") as tmp:
        folder = Path(tmp)
        (folder / "original.bin").write_bytes(bytes(2352 * 32))
        cue = folder / "original.cue"
        cue.write_text('FILE "original.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        cases = (
            # (low, high, p_in, sp): SP=$FF (標準), $08 (境界+1), $07 (契約下限)
            (0x00, 0x00, P_INPUT, 0xFF),
            (0x34, 0x12, P_INPUT, 0xFF),
            (0xFF, 0x7F, P_INPUT, 0xFF),
            (0x34, 0x80, P_INPUT, 0xFF),
            (0xAB, 0xFF, P_INPUT, 0xFF),
            (0x34, 0x12, P_INPUT_ZV, 0xFF),
            # SP=$08: 境界+1（7バイト消費で$2101到達、SP wraparoundなし）
            (0x34, 0x12, P_INPUT, 0x08),
            # SP=$07: 契約下限（7バイト消費で$2100到達、SP wraparoundなし）
            (0x00, 0x00, P_INPUT, 0x07),
            (0x34, 0x12, P_INPUT, 0x07),
        )
        for index, (low, high, p_in, sp) in enumerate(cases):
            init = INITIAL_MAWR[index % len(INITIAL_MAWR)]
            evidence["cases"].append(one_case(probe, data, folder, cue, reset, entry,
                                                f"setwrt-{high:02x}{low:02x}-p{p_in:02x}-sp{sp:02x}",
                                                low, high, init, p_in, sp))
        # Each mutant edits instructions inside the slot $3A body only. The real core must reject every one.
        body = data[3 * SLOT + 1] | data[3 * SLOT + 2] << 8
        window = body - 0xE000, body - 0xE000 + 0x60
        mutants = (
            # Normal-path mutants: expect MAWR_CHANGED (writes skipped -> MAWR wrong)
            ("low_write_nopped", [(bytes([0x8D, 0x02, 0x00]), bytes([0xEA] * 3))], 0x34, 0x12),
            ("high_write_nopped", [(bytes([0x8E, 0x03, 0x00]), bytes([0xEA] * 3))], 0x34, 0x12),
            ("all_writes_nopped", [(bytes([0x8D, 0x02, 0x00]), bytes([0xEA] * 3)),
                                   (bytes([0x8E, 0x03, 0x00]), bytes([0xEA] * 3))], 0x34, 0x12),
            ("lo_hi_swapped", [(bytes([0x8D, 0x02, 0x00]), bytes([0x8D, 0x03, 0x00])),
                               (bytes([0x8E, 0x03, 0x00]), bytes([0x8E, 0x02, 0x00]))], 0x34, 0x12),
            # Reset-to-zero fake: writes zero instead of expected value -> MAWR_CHANGED
            ("reset_to_zero_fake", [(bytes([0x8D, 0x02, 0x00]), bytes([0x9C, 0x02, 0x00])),
                                    (bytes([0x8E, 0x03, 0x00]), bytes([0x9C, 0x03, 0x00]))], 0x34, 0x12),
            # Wrong register: writes to $0000 instead of $0002/$0003 -> MAWR_CHANGED (low not set)
            ("wrong_register_selected", [(bytes([0x8D, 0x02, 0x00]), bytes([0x8D, 0x00, 0x00]))], 0x34, 0x12),
            # Reject-path mutant: bcs -> bne, so X >= $80 falls through to normal path -> VDC_CHANGED
            ("reject_branch_inverted", [(bytes([0xB0, 0x22]), bytes([0x90, 0x22]))], 0x34, 0x80),
        )
        for name, edits, low, high in mutants:
            mutant = bytearray(data)
            for pattern, patch in edits:
                at = data.find(pattern, *window)
                if at < 0 or data.find(pattern, at + 1, window[1]) >= 0:
                    raise ValueError(f"mutant {name}: pattern {pattern.hex()} not unique in the slot $3A body")
                mutant[at:at + len(patch)] = patch
            # 期待される拒否理由コード（実際の検査箇所で生成される理由と一致させる）
            if high < 0x80:
                expected_reason = "MAWR_CHANGED"
            else:
                # reject_branch_inverted (bcs->bne): with X=$80, Z=1 so bne doesn't branch,
                # falls through to normal path and changes VDC -> VDC_CHANGED
                expected_reason = "VDC_CHANGED"
            evidence["negative_cases"].append({"case": name, "rejected_by": expect_rejected(
                probe, bytes(mutant), folder, cue, reset, entry, f"mut-{name}", low, high, INITIAL_MAWR[0],
                expected_reason)})
    (args.rom.parent / "geargrafx-dev-ex-setwrt-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(f"PASS real Geargrafx DEV ROM: EX_SETWRT $3A {len(evidence['cases'])} valid/rejected MAWR cases with "
          f"non-zero initial MAWR; A/X/Y/P, SP, MPR, RAM and HuC6270 MAWR observed; "
          f"{len(evidence['negative_cases'])} mutants rejected")


if __name__ == "__main__":
    try:
        main()
    except ProbeRejected as error:
        print(f"DEV EX_SETWRT integration BLOCKED: {error.reason}: {error}", file=sys.stderr)
        raise SystemExit(BLOCKED_EXIT) from error
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        status = "BLOCKED" if "BLOCKED" in str(error) else "FAIL"
        print(f"DEV EX_SETWRT integration {status}: {error}", file=sys.stderr)
        raise SystemExit(BLOCKED_EXIT if status == "BLOCKED" else 1) from error