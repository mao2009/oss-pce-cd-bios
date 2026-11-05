"""Host checks for the Issue #499 stack access pre-guard driver (method C of #473).

Synthetic only: no emulator, BIOS, ROM or Geargrafx core is used. The rows below are built here from the
driver's expectation table, independently of the probe, so these tests pin the classifier logic (what it
accepts and what it must reject). Real-core evidence comes only from tests/integration/
geargrafx_stack_access_guard.py on the pinned core (``make stack-access-guard``, part of ``make integration``).
"""
import copy
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "integration"))

import geargrafx_stack_access_guard as G  # noqa: E402

PATCH = ROOT / "tests" / "integration" / "patches" / "geargrafx-b49ae82-stack-access-guard.patch"
PROBE = ROOT / "tests" / "integration" / "geargrafx_stack_access_guard.cpp"


def _snap(**fields):
    snap = dict(pc=G.GUEST, a=0x5A, x=0x34, y=0x07, p=0x04, s=0x04, transfer_state=0, cycles=0, irq_pending=0,
                mpr=[0xF8, 0xF8, 0, 0, 0, 0, 0, 0])
    snap.update(fields)
    return snap


def _vdc(changed=()):
    vdc = {name: [5, 5] for name in ("ar", "r0", "r1", "r7", "sr", "read_buffer")}
    for name in changed:
        vdc[name] = [5, 6]
    return vdc


def _row(case, **fields):
    row = dict(mode=case, exit=0, guarded=True, mutation=0, halted=False, latched=False, refused=False,
               frames_run=2, frame_budget=2, guard_calls=0, guard_refusals=0, guard_latched_calls=0,
               instructions=10, instructions_after_refusal=0, io_reads=0, io_writes=0, io_before_refusal=0,
               io_after_refusal=0, refusal=dict(op=0, s=0, transfer_state=0, mpr1=0, cycles=0, vdc_ar=0),
               harness_mpr1_flip=False, flip_instruction=0, start=_snap(), refusal_instruction_start=_snap(),
               at_refusal=_snap(), stop=_snap(), vdc=_vdc(), irr_before=0, irr_after=0,
               stack_window_before=[0, 0, 0, 0, 0],
               stack_window_after=[0, 0, 0, 0, 0], stack_bytes_changed=0, dst=[0, 0], events_truncated=False,
               events=[])
    row.update(fields)
    return row


def _stack_event(op, decision, latched=False):
    return dict(seq=0, kind="stack", op=op, address=4, value=0, decision=decision, latched=latched, mpr1=0xF8,
                instruction=1)


def _io_event(op, address):
    return dict(seq=0, kind="io", op=op, address=address, value=0, decision=-1, latched=False, mpr1=0xFF,
                instruction=1)


def allowed_row(route):
    want = G.ROUTES[route]["allowed"]
    stop = _snap(**{k: want[k] for k in ("pc", "s", "a", "x", "y", "p") if k in want})
    return _row(f"{route}-allowed", guard_calls=want["calls"], stop=stop, stack_window_after=list(want["window"]),
                dst=[0, want.get("dst", 0)], events=[_stack_event(0, 1) for _ in range(want["calls"])])


def armed_row(route):
    want = G.ROUTES[route]["armed"]
    s = 0x01 if want["op"] in (G.POP8, G.POP16) else 0x04
    first = _snap(pc=want.get("instruction_pc", G.GUEST), s=s, transfer_state=want.get("instruction_transfer_state", 0),
                  mpr=[0xF8, 0xFF, 0, 0, 0, 0, 0, 0])
    stop = copy.deepcopy(first)
    for key in want["changed"]:
        if key == "pc":
            stop["pc"] = want["pc"]
        elif key == "transfer_state":
            stop["transfer_state"] = want["stop_transfer_state"]
        elif key == "mpr":
            first["mpr"][1] = 0xF8
            stop["mpr"][1] = want["stop_mpr1"]
        else:
            stop[key] = first[key] ^ 0xFF
    if "pc" not in want["changed"]:
        stop["pc"] = first["pc"]
    window = want.get("window", [0, 0, 0, 0, 0])
    return _row(f"{route}-armed", halted=True, latched=True, refused=True, guard_calls=want["calls"],
                guard_refusals=want["latched"] + 1, guard_latched_calls=want["latched"],
                refusal=dict(op=want["op"], s=s, transfer_state=want.get("refusal_transfer_state", 0), mpr1=0xFF,
                             cycles=0, vdc_ar=5),
                refusal_instruction_start=first, at_refusal=copy.deepcopy(first), stop=stop,
                stack_window_before=list(window), stack_window_after=list(window),
                stack_bytes_changed=want.get("stack_bytes_changed", 0), dst=[want.get("dst", 0)] * 2,
                events=[_stack_event(want["op"], 0)])


def unguarded_row(route):
    io = G.ROUTES[route]["io"]
    writes = [a for op, a in io if op == G.WRITE]
    vdc = _vdc(() if G.ROUTES[route].get("state_invisible") else (("ar",) if writes else ("sr",)))
    return _row(f"{route}-unguarded", guarded=False, events=[_io_event(op, a) for op, a in io],
                io_reads=sum(1 for op, _ in io if op == G.READ), io_writes=len(writes), vdc=vdc)


class RouteTableTests(unittest.TestCase):
    def test_the_nine_required_routes_are_covered(self):
        self.assertEqual(set(G.REQUIRED_ROUTES),
                         {"jsr", "pha", "php", "bsr", "rts", "rti", "irq", "tstart", "tend"})
        for route in (*G.REQUIRED_ROUTES, *G.EXTRA_ROUTES):
            self.assertIn(route, G.ROUTES)

    def test_only_a_plain_push8_route_may_be_pass(self):
        for route, spec in G.ROUTES.items():
            if spec["armed"]["verdict"] == G.PASS:
                self.assertEqual(spec["armed"]["op"], G.PUSH8, route)
                self.assertEqual(spec["armed"]["changed"], {"pc"}, route)
                self.assertIn(route, G.FETCHED, route)
            else:
                self.assertEqual(spec["armed"]["verdict"], G.BLOCKED, route)

    def test_transfer_state_facts_are_pinned(self):
        self.assertEqual(G.ROUTES["tstart"]["armed"]["refusal_transfer_state"], 2)   # set before the pushes
        self.assertEqual(G.ROUTES["tend"]["armed"]["refusal_transfer_state"], 0)     # cleared before the pops
        self.assertEqual(G.ROUTES["tend"]["armed"]["instruction_transfer_state"], 1)

    def test_every_case_is_listed_once(self):
        cases = list(G.all_cases())
        self.assertEqual(len(cases), len(set(cases)))
        self.assertEqual(len(cases), 3 * len(G.ROUTES) + sum(len(r) for r in G.MUTANTS.values()))


class ClassifierTests(unittest.TestCase):
    def test_synthetic_rows_are_classified_as_specified(self):
        for route in G.ROUTES:
            with self.subTest(route=route):
                self.assertEqual(G.check_allowed(allowed_row(route), f"{route}-allowed"), "allowed_bytes_landed_in_wram")
                verdict, changed = G.check_armed(armed_row(route), f"{route}-armed")
                self.assertEqual(verdict, G.ROUTES[route]["armed"]["verdict"])
                self.assertEqual(set(changed), G.ROUTES[route]["armed"]["changed"])
                self.assertEqual(G.check_unguarded(unguarded_row(route), f"{route}-unguarded"),
                                 "io_page_accessed_without_guard")

    def test_every_falsified_armed_row_is_rejected(self):
        for route in G.ROUTES:
            row = armed_row(route)
            for name, broken in G.falsified_rows(row, f"{route}-armed"):
                with self.subTest(route=route, falsification=name):
                    with self.assertRaises(ValueError):
                        G.check_armed(broken, f"{route}-armed")

    def test_guardless_control_is_rejected_by_the_armed_classifier(self):
        for route in G.ROUTES:
            with self.subTest(route=route):
                with self.assertRaisesRegex(ValueError, "I/O page accessed"):
                    G.check_armed(unguarded_row(route), f"{route}-unguarded")

    def test_configuration_fields_are_not_evidence(self):
        """A mutant row is rejected by what happened, not by its mutation/guarded labels."""
        row = armed_row("pha")
        row.update(mutation=4, guarded=False)
        self.assertEqual(G.check_armed(row, "pha-armed")[0], G.PASS)

    def test_push16_partial_write_is_rejected(self):
        row = armed_row("jsr")
        row["events"].insert(0, _io_event(G.WRITE, 0x2104))
        row["io_writes"] = 1
        with self.assertRaisesRegex(ValueError, "I/O page accessed"):
            G.check_armed(row, "jsr-armed")

    def test_pop_moving_s_before_the_check_is_rejected(self):
        row = armed_row("rts")
        row["at_refusal"]["s"] += 1
        row["stop"]["s"] += 1
        with self.assertRaisesRegex(ValueError, "S moved"):
            G.check_armed(row, "rts-armed")

    def test_resumed_guest_is_rejected(self):
        row = armed_row("tstart")
        row["instructions_after_refusal"] = 3
        with self.assertRaisesRegex(ValueError, "resumed"):
            G.check_armed(row, "tstart-armed")

    def test_transferstart_state_update_is_not_preserved(self):
        verdict, changed = G.check_armed(armed_row("tstart"), "tstart-armed")
        self.assertEqual((verdict, changed), (G.BLOCKED, ["transfer_state"]))

    def test_same_value_and_restore_writes_need_the_event_log(self):
        for route in ("phasame", "pharestore"):
            row = unguarded_row(route)
            self.assertTrue(G.state_only_classifier_accepts(row), route)
            with self.assertRaises(ValueError):
                G.check_armed(row, f"{route}-unguarded")

    def test_truncated_log_is_never_accepted(self):
        row = armed_row("pha")
        row["events_truncated"] = True
        with self.assertRaisesRegex(ValueError, "truncated"):
            G.check_armed(row, "pha-armed")

    def test_unknown_or_malformed_rows_are_rejected(self):
        with self.assertRaises(ValueError):
            G.check_armed(_row("nosuch-armed"), "nosuch-armed")
        row = armed_row("pha")
        del row["stop"]["s"]
        with self.assertRaises(ValueError):
            G.check_armed(row, "pha-armed")

    def test_evaluate_runs_the_whole_suite_on_synthetic_rows(self):
        rows = {}
        for route in G.ROUTES:
            rows[f"{route}-allowed"] = allowed_row(route)
            rows[f"{route}-armed"] = armed_row(route)
            rows[f"{route}-unguarded"] = unguarded_row(route)
        for mutant, routes in G.MUTANTS.items():
            for route in routes:
                bad = armed_row(route)
                bad["mode"] = f"{route}-{mutant}"
                bad["instructions_after_refusal"] = 1   # any evidence-level failure
                rows[f"{route}-{mutant}"] = bad
        table, mutations = G.evaluate(rows)
        self.assertEqual({r: t["verdict"] for r, t in table.items()},
                         {r: s["armed"]["verdict"] for r, s in G.ROUTES.items()})
        for route, result in table.items():   # PASS never claims a restored pre-instruction state
            self.assertEqual(result["state"], G.STATE_PASS if result["verdict"] == G.PASS else G.STATE_BLOCKED)
        self.assertNotIn("PRESERVED", G.STATE_PASS)
        self.assertTrue(all(reason for _, _, reason in mutations))


class PatchBoundaryTests(unittest.TestCase):
    def test_patch_touches_only_the_listed_core_files(self):
        text = PATCH.read_text(encoding="utf-8")
        files = re.findall(r"^\+\+\+ b/(\S+)", text, re.M)
        # (#654 adds memory.h, for one read-only accessor and its comment only.)
        self.assertEqual(files, ["src/huc6280.h", "src/huc6280.cpp", "src/huc6280_inline.h", "src/memory.h",
                                 "src/memory_inline.h"])
        memory_h = text.split("+++ b/src/memory.h", 1)[1].split("--- a/src/", 1)[0]
        added = [line for line in memory_h.splitlines() if line.startswith("+")]
        self.assertEqual(len(added), 2)
        self.assertIn("u8 GG_VerifyMprBuffer() const { return m_mpr_buffer; }", added[1])
        self.assertEqual([line for line in memory_h.splitlines() if line.startswith("-")], [])

    def test_patch_is_marked_verification_only_and_null_by_default(self):
        text = PATCH.read_text(encoding="utf-8")
        self.assertGreaterEqual(text.count("VERIFICATION-ONLY"), 6)
        for symbol in ("gg_verify_stack_guard", "gg_verify_io_observer", "gg_verify_pre_instruction"):
            self.assertRegex(text, rf"\+.*\(\*{symbol}\)\(.*\) = nullptr;")
        self.assertIn("+int gg_verify_stack_mutation = 0;", text)

    def test_every_stack_access_is_checked_before_memory_and_s(self):
        """In the patched huc6280_inline.h, each stack function asks the guard before its first memory access,
        and the only S change before the check is the one switched on by mutant 2."""
        diff = PATCH.read_text(encoding="utf-8").split("+++ b/src/huc6280_inline.h", 1)[1].split("--- a/", 1)[0]
        new_view = [line[1:] for line in diff.splitlines() if line[:1] in (" ", "+")]
        for fn in ("StackPush16", "StackPush8", "StackPop16", "StackPop8"):
            with self.subTest(fn=fn):
                start = next(i for i, line in enumerate(new_view) if f"HuC6280::{fn}(" in line)
                end = next(i for i in range(start, len(new_view)) if new_view[i] == "}")
                body = new_view[start:end]
                check = next(i for i, line in enumerate(body) if "GG_VerifyStackAllowed" in line)
                memory = next(i for i, line in enumerate(body) if "m_memory->" in line)
                self.assertLess(check, memory)
                for i, line in enumerate(body[:check]):
                    if "m_S." in line:
                        self.assertIn("gg_verify_stack_mutation == 2", body[i - 1])
                # A refusal returns before any access, with a stop request.
                refused = body[check:memory]
                self.assertTrue(any("GG_VERIFY_STACK_STOP();" in line for line in refused))
                self.assertTrue(any(line.strip().startswith("return") for line in refused))

    def test_product_sources_do_not_reference_the_hooks(self):
        for path in (ROOT / "src").rglob("*"):
            if path.is_file():
                self.assertNotIn("gg_verify", path.read_text(encoding="utf-8", errors="replace"), str(path))
        for path in (ROOT / "tools").rglob("*.py"):
            self.assertNotIn("gg_verify_stack", path.read_text(encoding="utf-8"), str(path))

    def test_probe_never_resumes_after_a_refusal(self):
        text = PROBE.read_text(encoding="utf-8")
        loop = text[text.index("while (frames_run < frames)"):text.index("Snapshot stop = snap();")]
        # The method C latch (and, since #498, the method A instruction refusal) ends the run loop.
        # (#647 adds the harness budget stop of the TAM bitmask sweep.)
        self.assertRegex(loop, r"if \(gg_verify_stack_refused( \|\| g_instruction_refused)?( \|\| g_budget_stop)?\)")
        self.assertIn("break;", loop)
        self.assertIn("allow = !latched && mpr1 == 0xF8", text)

    def test_make_integration_runs_the_driver(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        target = re.search(r"^integration:.*$", makefile, re.M)
        self.assertIsNotNone(target)
        recipe = []
        for line in makefile[target.end():].splitlines()[1:]:
            if not line.startswith("\t"):
                break   # the recipe ends at the first line that is not a tab-indented command
            recipe.append(line.strip())
        self.assertIn("$(MAKE) stack-access-guard", recipe)
        self.assertIn("stack-access-guard:\n\t$(PYTHON) tests/integration/geargrafx_stack_access_guard.py", makefile)

    def test_stop_request_and_its_mutants_are_in_the_patch(self):
        text = PATCH.read_text(encoding="utf-8")
        self.assertIn("gg_verify_stack_mutation != 3 && gg_verify_stack_mutation != 5", text)
        self.assertIn("m_memory_breakpoint_hit = true", text)
        self.assertEqual(set(G.MUTANTS), {"mut-late16", "mut-popinc", "mut-nolatch", "mut-ignore", "mut-nostop"})


if __name__ == "__main__":
    unittest.main()
