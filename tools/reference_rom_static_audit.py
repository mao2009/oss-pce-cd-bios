#!/usr/bin/env python3
"""Local-only static auditor for a private System Card BIOS image (Phase ABI-1).

Purpose
-------
Produce *reproducible* observations about the **reference (pure) System Card BIOS**
without executing it and without publishing anything derived from its bytes.

What it reads
-------------
A private BIOS image supplied on the local command line. The image is a local
input only: it is never committed, never uploaded, never printed. The tool
refuses to run inside GitHub Actions, where no private image may exist.

How it decodes
--------------
Instructions are decoded with the **pinned cc65 ``da65``** using the HuC6280 CPU
description, exactly like ``tools/build_rom.py`` already does for the DEV ROM.
Re-using the pinned disassembler avoids a hand-written opcode table, which would
become a verification blind spot of its own.

What it emits
-------------
A fixed-shape *projection*: counts, booleans and a closed vocabulary. It never
emits ROM bytes, disassembly text, internal routine addresses or extracted code.
The projection is the only artefact that may be committed; the raw decode stays
in the local process.

What it is not
--------------
It is **not** a compatibility claim, not an execution and not an ABI
verification. A static profile is an *upper bound* on the hazard opcodes of the
reachable code; a clean profile is necessary but not sufficient for a safe
execution, and the safety verdict itself is recorded separately in
``spec/abi1_reference_review.json``.

Usage
-----
    python3 tools/reference_rom_static_audit.py --rom /path/to/private.pce \\
        --slots 0x1e,0x3c,0x3d,0x3f,0x40,0x41,0x42 --output build/abi1/audit.json

The output is LOCAL_ONLY until a human reviews it and copies the projection
into ``spec/abi1_reference_review.json``.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from environment import executable  # noqa: E402  (path setup must precede the import)

SCHEMA = "oss-pce-cd-bios/reference-rom-static-audit/v1"

# --- image geometry ---------------------------------------------------------
# A .pce image carries a 512 byte header; the ROM banks follow it. The System
# Card BIOS is 256 KiB = 32 banks of 8 KiB. Bank 0 is the API bank: the 81 entry
# jump table lives in its $E000-$FFFF window.
HEADER_BYTES = 512
BANK_BYTES = 8192
API_BANK = 0
TABLE_BASE = 0xE000
ENTRY_COUNT = 81
ENTRY_STRIDE = 3
JMP_ABS = 0x4C
WINDOW_BYTES = 16

# --- CPU address windows (HuC6280: 8 pages of 8 KiB, one MPR each) ----------
PAGES = 8

# --- hazard vocabulary (closed set; never extended ad hoc) -------------------
BLOCK_TRANSFER = "BLOCK_TRANSFER"          # tii/tin/tia/tdd/tai
VDC_DIRECT_WRITE = "VDC_DIRECT_WRITE"      # st0/st1/st2: bypasses Memory::Write
MPR_WRITE = "MPR_WRITE"                    # tam
MPR_READ = "MPR_READ"                      # tma
IRQ_ENABLE = "IRQ_ENABLE"                  # cli
IRQ_DISABLE = "IRQ_DISABLE"                # sei
ZERO_PAGE_STORE = "ZERO_PAGE_STORE"        # store into $00-$FF
PAGE1_STORE = "PAGE1_STORE"                # store into $2000-$3FFF
OTHER_STORE = "OTHER_STORE"                # store anywhere else
DATA_DEPENDENT_STORE = "DATA_DEPENDENT_STORE"
CROSS_PAGE_CALL = "CROSS_PAGE_CALL"        # jsr/bsr into another 8 KiB page
INDIRECT_CONTROL_FLOW = "INDIRECT_CONTROL_FLOW"   # jmp (abs) / data-dependent
UNRESOLVED_TARGET = "UNRESOLVED_TARGET"    # branch into a non-instruction
NO_RETURN = "NO_RETURN"                    # path never reaches rts/rti

HAZARDS = (BLOCK_TRANSFER, VDC_DIRECT_WRITE, MPR_WRITE, MPR_READ, IRQ_ENABLE,
           IRQ_DISABLE, ZERO_PAGE_STORE, PAGE1_STORE, OTHER_STORE,
           DATA_DEPENDENT_STORE, CROSS_PAGE_CALL, INDIRECT_CONTROL_FLOW,
           UNRESOLVED_TARGET, NO_RETURN)

BRANCHES_REL = {"bra", "bcc", "bcs", "beq", "bne", "bmi", "bpl", "bvc", "bvs",
                "bbr0", "bbr1", "bbr2", "bbr3", "bbr4", "bbr5", "bbr6", "bbr7",
                "bbs0", "bbs1", "bbs2", "bbs3", "bbs4", "bbs5", "bbs6", "bbs7"}
UNCONDITIONAL_BRANCH = {"bra"}
CALLS = {"jsr", "bsr"}
RETURNS = {"rts", "rti"}
BLOCK_MOVES = {"tii", "tin", "tia", "tdd", "tai"}
BLOCK_MOVE_WRITES = {"tii", "tdd", "tai"}
# Only an immediate load into A makes the accumulator value known. Every other
# A modifier leaves it unknown: assuming "and #$0A" means "A = $0A" would make a
# later TAM resolve to the wrong bank and the profile unsound.
IMMEDIATE_LOAD = {"lda"}
A_MODIFIERS = {"lda", "ldx", "ldy", "tax", "tay", "txa", "tya", "tsx", "txs",
               "sbc", "adc", "and", "ora", "eor", "asl", "lsr", "rol", "ror",
               "inc", "dec", "tma", "inx", "dex", "iny", "dey", "sxy", "bit",
               "cmp", "cpx", "cpy"}
WRITES_MEMORY = {"sta", "stx", "sty", "stz", "inc", "dec", "asl",
                 "lsr", "rol", "ror", "rmb0", "rmb1", "rmb2", "rmb3", "rmb4",
                 "rmb5", "rmb6", "rmb7", "smb0", "smb1", "smb2", "smb3", "smb4",
                 "smb5", "smb6", "smb7", "tsb", "trb"}

# da65 prints a generated label either as a definition (``LXXXX := $XXXX``) or
# inline at column 0 (``LXXXX:  inx``), so the line pattern accepts both.
DA65_LINE = re.compile(r'^\s*(?:\w+:\s+)?(\S+)\s*([^;]*?)\s*;\s([0-9A-F]{4})\s((?:[0-9A-F]{2}\s)+)')
DA65_LABEL = re.compile(r'^\s*(\w+)\s*:=\s*\$([0-9A-Fa-f]{4})\s*$')
OPERAND_ADDRESS = re.compile(r'^\$([0-9A-Fa-f]{1,4})$')
IMMEDIATE_VALUE = re.compile(r'#\$([0-9A-Fa-f]{1,2})')
MASK_VALUE = re.compile(r'#\$([0-9A-Fa-f]{2})')


class AuditError(Exception):
    """Raised for every unrecoverable condition; the CLI turns it into BLOCKED."""


# --- image loading ----------------------------------------------------------
def load_image(path):
    """Return (body, header_bytes, bank_bytes, bank_count) for a private BIOS image.

    A .pce image is a 512 byte header followed by the ROM banks. A headerless
    image is accepted as well, because the private input may be either form.
    """
    data = Path(path).read_bytes()
    if len(data) % BANK_BYTES == 0:
        header = 0
    elif len(data) > HEADER_BYTES and (len(data) - HEADER_BYTES) % BANK_BYTES == 0:
        header = HEADER_BYTES
    else:
        raise AuditError("image size is not a whole number of 8 KiB banks (with or without a 512 byte header)")
    body = data[header:]
    banks = len(body) // BANK_BYTES
    if banks < 1:
        raise AuditError("image holds no ROM bank")
    return body, header, BANK_BYTES, banks


def bank_offset(body, bank, cpu_address):
    """Byte offset of a CPU address inside a bank, or None when out of range."""
    if bank is None or not 0 <= bank < len(body) // BANK_BYTES:
        return None
    return bank * BANK_BYTES + (cpu_address & 0x1FFF)


# --- decoding ---------------------------------------------------------------
class Decoder:
    """Instruction decoder built on the pinned cc65 da65 (HuC6280 CPU description).

    ``da65`` prints an address as a generated label when the target is not a
    plain immediate, so the label table of the same listing is parsed and used
    to resolve operands back to addresses. The instruction bytes are taken from
    the listing comment, so relative branch targets are computed exactly.
    """

    def __init__(self):
        self._cache = {}

    def decode(self, body, bank, cpu_address):
        """(length, mnemonic, operand, bytes, labels) or None when undecodable."""
        offset = bank_offset(body, bank, cpu_address)
        if offset is None:
            return None
        key = (bank, cpu_address)
        if key in self._cache:
            return self._cache[key]
        window = body[offset:offset + WINDOW_BYTES]
        if not window:
            return None
        with tempfile.TemporaryDirectory(prefix="pce-abi1-da65-") as tmp:
            binary = Path(tmp) / "window.bin"
            binary.write_bytes(window)
            result = subprocess.run(
                [executable("da65"), "--cpu", "huc6280", "--comments", "4",
                 "--start-addr", hex(cpu_address), str(binary)],
                capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise AuditError("da65 failed on a private image window")
        labels = {}
        parsed = None
        for line in result.stdout.splitlines():
            label = DA65_LABEL.match(line)
            if label:
                labels[label.group(1)] = int(label.group(2), 16)
                continue
            match = DA65_LINE.match(line)
            if match and int(match.group(3), 16) == cpu_address:
                raw = bytes.fromhex(match.group(4).replace(" ", ""))
                parsed = (len(raw), match.group(1).lower(), match.group(2).strip(), raw, labels)
                break
        self._cache[key] = parsed
        return parsed


# --- addressing-mode helpers ------------------------------------------------
def resolve_operand_address(operand, labels):
    """Absolute address of a direct operand, or None when the target is not direct.

    ``da65`` prints ``$22BB`` for an absolute operand and ``a:$00`` when a
    forced absolute form carries a zero-page value. Indexed and indirect forms
    are data dependent and are reported as such by the caller.
    """
    text = operand.strip()
    lowered = text.lower()
    for prefix in ("a:", "f:"):
        if lowered.startswith(prefix):
            text = text[len(prefix):]
            lowered = text.lower()
    if text.startswith("("):
        return None
    text = text.split(",")[0].strip()
    match = OPERAND_ADDRESS.match(text)
    if match:
        return int(match.group(1), 16)
    if text in labels:
        return labels[text]
    return None


# The HuC6280 places the CPU zero page at $2000 and the stack at $2100 (the
# high order byte of a zero page address always contains the logical address
# $20, and neither placement depends on the MPRs). The pinned core matches the
# hardware (huc6280.h: ZERO_PAGE_ADDR / STACK_ADDR in the non GG_TESTING build),
# so a zero page operand lands in page 1 both on the core and on hardware.
CORE_ZERO_PAGE = 0x2000


def is_zero_page_operand(operand, length):
    """True for a zero page operand (two byte instruction, direct $nn form)."""
    text = operand.strip().lower()
    if length != 2 or text.startswith("a:") or text.startswith("f:"):
        return False
    if text.startswith("(") or "," in text or "#" in text:
        return False
    return OPERAND_ADDRESS.match(text) is not None


def is_data_dependent(operand):
    """True when the effective address depends on a register or a pointer."""
    text = operand.strip().lower()
    if text.startswith("("):
        return True
    if "," in text:
        return True
    return False


def classify_address(address):
    """Closed vocabulary for a direct memory address."""
    if address < 0x0100:
        return "ZERO_PAGE"
    if 0x2000 <= address < 0x2100:
        return "PAGE1_ZERO_PAGE"
    if 0x2000 <= address < 0x4000:
        return "PAGE1"
    if 0x4000 <= address < 0x8000:
        return "PAGE2_3"
    if 0x8000 <= address < 0xE000:
        return "CD_REGION"
    return "BIOS_WINDOW"


# --- the walk ----------------------------------------------------------------
class SlotAudit:
    """Per-slot hazard profile of the reference BIOS entry."""

    def __init__(self):
        self.hazards = {name: 0 for name in HAZARDS}
        self.instructions = 0
        self.states = 0
        self.max_stack_bytes = 0
        self.return_kinds = set()
        self.store_classes = {}
        self.cross_page_pages = set()
        self.unresolved = []
        self.budget_exhausted = False
        self.saw_return = False
        self.zero_page_stores = 0
        self.register_swaps = 0

    def profile(self):
        return {
            "instructions_decoded": self.instructions,
            "states_visited": self.states,
            "returns_observed": sorted(self.return_kinds),
            "max_stack_bytes": self.max_stack_bytes,
            "hazard_counts": dict(sorted(self.hazards.items())),
            "store_target_classes": dict(sorted(self.store_classes.items())),
            "zero_page_operand_stores": self.zero_page_stores,
            "register_swap_ops": self.register_swaps,
            "cross_page_count": len(self.cross_page_pages),
            "unresolved": sorted(set(self.unresolved)),
            "budget_exhausted": self.budget_exhausted,
        }


class Auditor:
    """Recursive-descent walk of one reference API entry with an MPR model.

    The walk keeps one concrete MPR map when it can and marks a page's bank as
    unknown otherwise. A ``tam`` whose source value is known (a preceding
    ``lda #imm``) updates the map; every other ``tam`` makes the affected pages
    unknown. Calls are followed so that a callee's MPR change is visible to its
    caller, and a return restores the caller's own frame.
    """

    MAX_STATES = 20000
    MAX_DEPTH = 24

    def __init__(self, body):
        self.body = body
        self.decoder = Decoder()

    @staticmethod
    def _page(address):
        return (address >> 13) & 0x07

    def _apply_tam(self, mpr, mask, value):
        """Apply a TAM/TMA mask.

        The pinned core maps mask bit i to MPR[i] (Memory::SetMprTAM tests
        ``bits & (0x01 << i)``), so ``tam #$40`` selects MPR6. Getting this
        backwards would resolve every cross-page callee to the wrong bank.
        """
        updated = list(mpr)
        for index in range(PAGES):
            if mask & (0x01 << index):
                updated[index] = value
        return tuple(updated)

    def _mask_from_operand(self, operand):
        match = MASK_VALUE.search(operand)
        return int(match.group(1), 16) if match else None

    def audit(self, slot, target_cpu):
        """Walk the entry callee and collect its hazard profile.

        The walk is a worklist over (pc, mpr, acc, return stack) states. The
        return stack carries the caller's continuation, so a callee's MPR change
        is visible to its caller and a routine that never returns is detected
        instead of being silently treated as a fall-through.
        """
        audit = SlotAudit()
        mpr = tuple([API_BANK if index == 7 else None for index in range(PAGES)])
        start = (target_cpu, mpr, None, ())
        worklist = [start]
        visited = set()
        while worklist:
            if audit.states >= self.MAX_STATES:
                audit.budget_exhausted = True
                break
            pc, mpr, acc, ret_stack = worklist.pop()
            state = (pc, mpr, acc, ret_stack)
            if state in visited:
                continue
            visited.add(state)
            if len(ret_stack) > self.MAX_DEPTH:
                audit.hazards[UNRESOLVED_TARGET] += 1
                audit.unresolved.append("call_depth_exceeded")
                continue
            bank = mpr[self._page(pc)]
            if bank is None:
                audit.unresolved.append("page_bank_unknown")
                continue
            decoded = self.decoder.decode(self.body, bank, pc)
            if decoded is None:
                audit.unresolved.append("undecodable_instruction")
                continue
            length, mnemonic, operand, raw, labels = decoded
            audit.instructions += 1
            audit.states += 1
            next_pc = (pc + length) & 0xFFFF
            mpr, acc = self._effects(audit, mnemonic, operand, length, labels, mpr, acc)
            if mnemonic in CALLS:
                target = self._target(pc, length, mnemonic, operand, raw, labels)
                if target is None:
                    audit.hazards[UNRESOLVED_TARGET] += 1
                    audit.unresolved.append("call_target_unresolved")
                    continue
                if self._page(target) != self._page(pc):
                    audit.hazards[CROSS_PAGE_CALL] += 1
                    audit.cross_page_pages.add(self._page(target))
                audit.max_stack_bytes = max(audit.max_stack_bytes, 2)
                worklist.append((target, mpr, acc, ret_stack + (next_pc,)))
                continue
            if mnemonic in BRANCHES_REL:
                target = self._target(pc, length, mnemonic, operand, raw, labels)
                if target is None:
                    audit.hazards[UNRESOLVED_TARGET] += 1
                    audit.unresolved.append("branch_target_unresolved")
                    continue
                if mnemonic == "bsr":
                    if self._page(target) != self._page(pc):
                        audit.hazards[CROSS_PAGE_CALL] += 1
                        audit.cross_page_pages.add(self._page(target))
                    audit.max_stack_bytes = max(audit.max_stack_bytes, 2)
                    worklist.append((target, mpr, acc, ret_stack + (next_pc,)))
                    continue
                worklist.append((target, mpr, acc, ret_stack))
                if mnemonic not in UNCONDITIONAL_BRANCH:
                    worklist.append((next_pc, mpr, acc, ret_stack))
                continue
            if mnemonic == "jmp":
                if is_data_dependent(operand):
                    audit.hazards[INDIRECT_CONTROL_FLOW] += 1
                    audit.unresolved.append("indirect_jump")
                    continue
                target = self._target(pc, length, mnemonic, operand, raw, labels)
                if target is None:
                    audit.hazards[UNRESOLVED_TARGET] += 1
                    audit.unresolved.append("jump_target_unresolved")
                    continue
                if self._page(target) != self._page(pc):
                    audit.hazards[CROSS_PAGE_CALL] += 1
                    audit.cross_page_pages.add(self._page(target))
                worklist.append((target, mpr, acc, ret_stack))
                continue
            if mnemonic in RETURNS:
                audit.return_kinds.add(mnemonic)
                audit.saw_return = True
                if ret_stack:
                    worklist.append((ret_stack[-1], mpr, acc, ret_stack[:-1]))
                continue
            worklist.append((next_pc, mpr, acc, ret_stack))
        if not audit.saw_return:
            audit.hazards[NO_RETURN] += 1
        return audit

    def _target(self, pc, length, mnemonic, operand, raw, labels):
        """Resolve a branch/call target address, or None when it cannot be resolved."""
        if mnemonic in BRANCHES_REL and length == 2:
            offset = raw[1] - 256 if raw[1] & 0x80 else raw[1]
            return (pc + 2 + offset) & 0xFFFF
        return resolve_operand_address(operand, labels)

    def _effects(self, audit, mnemonic, operand, length, labels, mpr, acc):
        """Record the hazards of one instruction; return the updated (mpr, acc)."""
        if mnemonic in BLOCK_MOVES:
            audit.hazards[BLOCK_TRANSFER] += 1
            if mnemonic in BLOCK_MOVE_WRITES:
                audit.hazards[OTHER_STORE] += 1
        if mnemonic in ("st0", "st1", "st2"):
            audit.hazards[VDC_DIRECT_WRITE] += 1
        if mnemonic == "tam":
            audit.hazards[MPR_WRITE] += 1
            mask = self._mask_from_operand(operand)
            if mask is None:
                mpr = tuple([None] * PAGES)
            elif acc is None:
                updated = list(mpr)
                for index in range(PAGES):
                    if mask & (0x01 << index):
                        updated[index] = None
                mpr = tuple(updated)
            else:
                mpr = self._apply_tam(mpr, mask, acc)
            return mpr, None
        if mnemonic == "tma":
            audit.hazards[MPR_READ] += 1
            return mpr, None
        if mnemonic == "cli":
            audit.hazards[IRQ_ENABLE] += 1
        if mnemonic == "sei":
            audit.hazards[IRQ_DISABLE] += 1
        if mnemonic in ("pha", "php", "phx", "phy"):
            audit.max_stack_bytes = max(audit.max_stack_bytes, 1)
        if mnemonic in ("sax", "say", "sxy"):
            # Register swap on the HuC6280 (manual: "Swap A for X"): no memory
            # effect, and the pinned core implements it the same way.
            audit.register_swaps += 1
        if mnemonic in WRITES_MEMORY:
            self._record_store(audit, mnemonic, operand, length, labels)
        if mnemonic in IMMEDIATE_LOAD:
            match = IMMEDIATE_VALUE.search(operand)
            if match:
                return mpr, int(match.group(1), 16)
            return mpr, None
        if mnemonic in A_MODIFIERS:
            return mpr, None
        return mpr, acc

    def _record_store(self, audit, mnemonic, operand, length, labels):
        if operand.strip().lower() == "a":
            # Accumulator mode ("asl a") touches no memory.
            return
        if is_data_dependent(operand):
            audit.hazards[DATA_DEPENDENT_STORE] += 1
            return
        address = resolve_operand_address(operand, labels)
        if address is None:
            audit.hazards[DATA_DEPENDENT_STORE] += 1
            return
        if is_zero_page_operand(operand, length):
            # A zero page operand addresses $2000+offset on the HuC6280, so it
            # lands in page 1, not in the memory mapped I/O page.
            audit.zero_page_stores += 1
            address = CORE_ZERO_PAGE + address
        name = classify_address(address)
        audit.store_classes[name] = audit.store_classes.get(name, 0) + 1
        if name == "ZERO_PAGE":
            audit.hazards[ZERO_PAGE_STORE] += 1
        elif name in ("PAGE1", "PAGE1_ZERO_PAGE"):
            audit.hazards[PAGE1_STORE] += 1
        else:
            audit.hazards[OTHER_STORE] += 1


def parse_jump_table(body):
    """Structural facts about the 81 entry jump table at $E000."""
    base = bank_offset(body, API_BANK, TABLE_BASE)
    entries = []
    all_jmp = True
    distinct = set()
    for index in range(ENTRY_COUNT):
        offset = base + index * ENTRY_STRIDE
        if offset + ENTRY_STRIDE > len(body):
            raise AuditError("image is too small for an 81 entry jump table")
        opcode = body[offset]
        target = body[offset + 1] | (body[offset + 2] << 8)
        if opcode != JMP_ABS:
            all_jmp = False
        distinct.add(target)
        entries.append({"index": index, "entry_cpu": TABLE_BASE + index * ENTRY_STRIDE,
                        "opcode_is_jmp_abs": opcode == JMP_ABS, "target": target})
    return {
        "first_entry_cpu": f"0x{TABLE_BASE:04X}",
        "entry_count": ENTRY_COUNT,
        "entry_stride_bytes": ENTRY_STRIDE,
        "all_entries_are_jmp_abs": all_jmp,
        "distinct_targets": len(distinct),
        "all_targets_distinct": len(distinct) == ENTRY_COUNT,
        "entries": entries,
    }


def audit_image(path, slots):
    body, header, bank_bytes, banks = load_image(path)
    table = parse_jump_table(body)
    auditor = Auditor(body)
    apis = []
    for slot in slots:
        if not 0 <= slot < ENTRY_COUNT:
            raise AuditError(f"slot {slot} is outside the {ENTRY_COUNT} entry table")
        entry = table["entries"][slot]
        target = entry["target"]
        record = {
            "api_id": f"slot-{slot:02x}",
            "slot": slot,
            "candidate_entry": f"0x{entry['entry_cpu']:04X}",
            "entry_opcode": "JMP_ABS" if entry["opcode_is_jmp_abs"] else "OTHER",
        }
        if not 0xE000 <= target <= 0xFFFF:
            # The target is outside the API bank window: resolving it would need a
            # bank switch that the table alone cannot justify.
            record["callee_in_api_bank_window"] = False
            record["callee"] = {"status": "TARGET_OUTSIDE_API_BANK_WINDOW"}
            apis.append(record)
            continue
        profile = auditor.audit(slot, target)
        record["callee_in_api_bank_window"] = True
        record["callee"] = profile.profile()
        apis.append(record)
    return {
        "schema": SCHEMA,
        "provenance": {
            "image_bytes": len(body) + header,
            "header_bytes": header,
            "body_bytes": len(body),
            "bank_bytes": bank_bytes,
            "bank_count": banks,
            "decoder": "pinned cc65 da65 (HuC6280)",
            "trust": "LOCAL_ONLY_NOT_VERIFIED",
        },
        "jump_table": {key: value for key, value in table.items() if key != "entries"},
        "apis": apis,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, help="private reference BIOS image (local only)")
    parser.add_argument("--slots", default="0x1e",
                        help="comma separated slot numbers to analyse (default: 0x1e)")
    parser.add_argument("--output", type=Path, help="write the projection JSON here")
    arguments = parser.parse_args(argv)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print("BLOCKED: a private BIOS image is never read in CI", file=sys.stderr)
        return 3
    if not arguments.rom:
        print("BLOCKED: --rom is required (a private image path, never committed)", file=sys.stderr)
        return 3
    try:
        slots = [int(part, 16) for part in arguments.slots.split(",") if part.strip()]
        for slot in slots:
            if not 0 <= slot < ENTRY_COUNT:
                raise AuditError(f"slot {slot} is outside the 81 entry table")
        result = audit_image(arguments.rom, slots)
    except (AuditError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"BLOCKED: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
