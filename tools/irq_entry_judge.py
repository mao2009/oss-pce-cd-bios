"""Issue #366: judge for IRQ entry (IRQ2 / IRQ1 / TIQ) pre-handler blocking. Pure stdlib, no emulator.

Consumes one observation per vector from a real-core probe (debugger build only) and returns PASS, BLOCKED or FAIL.
* PASS    - the run is a debugger-build observation, the stop is at the handler entry resolved from the synthetic
            vector table, no handler instruction ran, and every stack write is inside page 1 [$2000,$4000).
* BLOCKED - no valid evidence, or the libretro build (no execute breakpoint, no PC/trace API: GG_DISABLE_DISASSEMBLER).
* FAIL    - a valid observation that contradicts the expectation (stop not at the handler, handler instruction ran,
            stack write outside page 1, wrong vector byte pair).

The handler address is judged against the expected value supplied by the caller (the synthetic ROM layout), never
against the observation alone. Vector addresses come from docs/geargrafx-contract-evidence.md (pinned core
HandleIRQ dispatches $FFF6 IRQ2, $FFF8 IRQ1, $FFFA timer/TIQ). The 3-byte entry push (PC hi/lo, P) is taken from the
same doc (SP $FF->$FC). Nothing here is a reference-BIOS contract: the release gate stays BLOCKED_ABI, release_eligible=false.

  python3 tools/irq_entry_judge.py   # runs the mutation self-check on synthetic rows
"""
import copy

VECTORS = {"IRQ2": 0xFFF6, "IRQ1": 0xFFF8, "TIQ": 0xFFFA}
STACK_WINDOW = (0x2000, 0x4000)   # page 1, as tests/integration/geargrafx_reference_guard.py ALLOW
ENTRY_PUSH_BYTES = 3              # PC hi, PC lo, P (pinned core evidence: SP $FF->$FC)
PASS, BLOCKED, FAIL = "PASS", "BLOCKED", "FAIL"
LIBRETRO_REASON = ("libretro build: no execute breakpoint and no PC/trace API "
                   "(GG_DISABLE_DISASSEMBLER); entry-stop not observable, not PASS")


def _int(row, key):
    value = row.get(key)
    return value if type(value) is int else None


def judge_entry(row, vector, expected_handler, build="debugger"):
    """Return (verdict, detail) for one vector run. ``row`` is a probe observation dict."""
    if build == "libretro":
        return BLOCKED, LIBRETRO_REASON
    if build != "debugger":
        return BLOCKED, f"unknown build {build!r}"
    if vector not in VECTORS:
        return BLOCKED, f"unknown vector {vector!r}"
    if row.get("exit", 0) != 0 or row.get("status") != "ok" or row.get("mode") != "irq-entry":
        return BLOCKED, f"no valid observation (exit={row.get('exit')}, status={row.get('status')})"
    if row.get("vector") != vector:
        return BLOCKED, f"observation is for {row.get('vector')!r}, expected {vector!r}"
    stop_pc, executed = _int(row, "stop_pc"), _int(row, "handler_instructions")
    vec_lo, vec_hi = _int(row, "vector_lo"), _int(row, "vector_hi")
    if None in (stop_pc, executed, vec_lo, vec_hi):
        return BLOCKED, "stop_pc / handler_instructions / vector bytes not observed"
    if row.get("execbp_hit") is not True:
        return FAIL, "execute breakpoint at the handler was not raised"
    # The vector bytes are device readback; the resolved handler must equal the synthetic layout.
    resolved = (vec_hi << 8) | vec_lo
    if resolved != expected_handler:
        return FAIL, f"{vector} vector resolved to {resolved:#06x}, expected {expected_handler:#06x}"
    if stop_pc != expected_handler:
        return FAIL, f"{vector} stopped at {stop_pc:#06x}, expected handler entry {expected_handler:#06x}"
    if executed != 0:
        return FAIL, f"{vector}: {executed} handler instruction(s) executed before the stop"
    if row.get("handler_writes") not in ([], None):
        return FAIL, f"{vector}: handler body wrote memory before the stop"
    writes = row.get("stack_writes")
    if type(writes) is not list or not all(type(a) is int for a in writes):
        return BLOCKED, "stack writes not observed"
    if len(writes) != ENTRY_PUSH_BYTES:
        return FAIL, f"{vector}: {len(writes)} stack writes, expected {ENTRY_PUSH_BYTES}"
    for address in writes:
        if not STACK_WINDOW[0] <= address < STACK_WINDOW[1]:
            return FAIL, f"{vector}: stack write {address:#06x} outside page 1 {STACK_WINDOW[0]:#06x}..{STACK_WINDOW[1]:#06x}"
    return PASS, f"{vector} stopped at handler {expected_handler:#06x} with 0 instructions, stack writes in page 1"


def mutation_self_check(base_row, expected_handler, vector):
    """Every falsified observation must NOT be PASS. A removed stop judgement would let one of these through."""
    mutations = [
        ("stop_pc+3", {"stop_pc": expected_handler + 3}),
        ("stop_pc=missing", {"stop_pc": None}),
        ("handler_instructions=1", {"handler_instructions": 1}),
        ("execbp_hit=false", {"execbp_hit": False}),
        ("vector_lo wrong", {"vector_lo": (expected_handler + 1) & 0xFF}),
        ("stack_write outside page 1", {"stack_writes": [0x1FFF, 0x21FF, 0x21FE]}),
        ("handler wrote memory", {"handler_writes": [0x2010]}),
        ("stack writes=4", {"stack_writes": [0x21FF, 0x21FE, 0x21FD, 0x21FC]}),
        ("build=libretro", {"__build": "libretro"}),
        ("status=rejected", {"status": "rejected"}),
        ("vector mismatch", {"vector": "TIQ" if vector != "TIQ" else "IRQ2"}),
    ]
    for name, change in mutations:
        broken = copy.deepcopy(base_row)
        build = change.pop("__build", "debugger")
        for key, value in change.items():
            if value is None:
                broken.pop(key, None)
            else:
                broken[key] = value
        verdict, _ = judge_entry(broken, vector, expected_handler, build=build)
        if verdict == PASS:
            raise ValueError(f"mutation {name!r} for {vector} was accepted as PASS")
    return len(mutations)


def base_row(vector, expected_handler):
    """Synthetic debugger-build observation that must PASS (shape of a future probe run)."""
    vector_addr = VECTORS[vector]
    return {
        "exit": 0, "status": "ok", "mode": "irq-entry", "vector": vector,
        "vector_addr": vector_addr,
        "vector_lo": expected_handler & 0xFF, "vector_hi": expected_handler >> 8,
        "execbp_hit": True, "stop_pc": expected_handler, "handler_instructions": 0,
        "handler_writes": [], "stack_writes": [0x21FF, 0x21FE, 0x21FD],
    }


def main():
    handlers = {"IRQ2": 0x4010, "IRQ1": 0x4020, "TIQ": 0x4030}   # synthetic layout, not a BIOS address
    results = []
    for vector, handler in handlers.items():
        row = base_row(vector, handler)
        verdict, detail = judge_entry(row, vector, handler)
        if verdict != PASS:
            raise ValueError(f"base row for {vector} not PASS: {detail}")
        count = mutation_self_check(row, handler, vector)
        results.append(f"{vector} PASS ({count} mutations rejected)")
        libretro, reason = judge_entry(row, vector, handler, build="libretro")
        results.append(f"{vector} libretro {libretro}")
    print("\n".join(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
