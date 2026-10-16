"""SYNTHETIC tests of tools/mcp_checkpoint.py with a fake MCP stdio server.
Nothing here runs Geargrafx; a PASS here is NOT real standalone-core evidence."""
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import mcp_checkpoint as m  # noqa: E402

FAKE = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json, os, sys, time
    mode = os.environ.get("FAKE_MODE", "good")
    S = {"type": "object", "properties": {"action": {}, "file_path": {}, "address": {}, "size": {}, "data": {}}}
    names = ["load_media", "debug_pause", "debug_reset", "debug_continue", "set_breakpoint",
             "get_huc6280_status", "read_memory", "write_memory", "set_trace_log", "get_trace_log"]
    if mode == "missing_tool": names.remove("get_trace_log")
    st = {"pc": 0xFFFF}
    if os.environ.get("FAKE_PIDFILE"): open(os.environ["FAKE_PIDFILE"], "w").write(str(os.getpid()))
    def txt(o): return {"content": [{"type": "text", "text": json.dumps(o)}]}
    for line in sys.stdin:
        r = json.loads(line)
        if "id" not in r: continue
        m, p = r["method"], r.get("params", {})
        if mode == "hang" and m == "tools/list": time.sleep(60)
        if mode == "die" and m == "tools/list": os._exit(0)
        if mode == "oversize" and m == "tools/list":
            sys.stdout.write("x" * (3 << 20)); sys.stdout.flush(); time.sleep(60)
        if mode == "bad_result" and m == "tools/list":
            print(json.dumps({"jsonrpc": "2.0", "id": r["id"], "result": [1]}), flush=True); continue
        if mode == "bad_tools" and m == "tools/list":
            print(json.dumps({"jsonrpc": "2.0", "id": r["id"], "result": {"tools": [5]}}), flush=True); continue
        if mode == "noise": print("garbage {", flush=True); print(json.dumps({"id": 999, "result": {}}), flush=True)
        if m == "initialize": res = {"capabilities": {}}
        elif m == "tools/list":
            tools = [{"name": n, "inputSchema": S} for n in names]
            if mode == "schema": tools[7]["inputSchema"] = {"type": "object", "properties": {}}
            res = {"tools": tools}
        else:
            n = p["name"]
            res = txt({})
            if n == "load_media" and mode == "invalid_image": res = {"isError": True, "content": []}
            if n == "debug_continue": st["pc"] = 0xE031 if mode == "no_checkpoint" else 0xE030
            if n == "get_huc6280_status":
                res = txt({"registers": {"PC": "%04X" % st["pc"]}, "mpr": ["FF", "00" if mode == "bad_mpr" else "F8"] + ["00"] * 6})
            if n == "read_memory": res = txt({"data": "50434521F85B" if mode == "bad_ram" else "50 43 45 21 F8 5A"})
            if n == "get_trace_log": res = txt({"entries": [] if mode == "empty_trace" else [{"pc": "E030"}]})
        print(json.dumps({"jsonrpc": "2.0", "id": r["id"], "result": res}), flush=True)
''')


class McpCheckpoint(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = Path(d.name)
        self.bin = self.dir / "fake"
        self.bin.write_text(FAKE)
        self.bin.chmod(self.bin.stat().st_mode | stat.S_IXUSR)
        self.rom = self.dir / "smoke.pce"
        self.rom.write_bytes(b"\0" * 8192)

    def run_mode(self, mode, **kw):
        os.environ["FAKE_MODE"] = mode
        self.addCleanup(os.environ.pop, "FAKE_MODE", None)
        return m.run_session(self.bin, self.rom, **kw)

    def test_good_cold_and_warm_synthetic(self):
        status, reason, ev = self.run_mode("good", per_step=3)
        self.assertEqual((status, reason), ("PASS", "ok"))
        self.assertEqual([c["label"] for c in ev["checkpoints"]], ["cold", "warm"])
        self.assertTrue(ev["process_terminated"])

    def test_missing_binary_is_blocked_not_pass(self):
        self.assertEqual(m.run_session(self.dir / "nope", self.rom)[:2], ("BLOCKED", "geargrafx_standalone_missing"))
        self.assertEqual(m.main(["--binary", str(self.dir / "nope"), "--rom", str(self.rom)]), 3)

    def test_checkpoint_not_reached_times_out(self):
        self.assertEqual(self.run_mode("no_checkpoint", deadline_s=0.3)[:2], ("TIMEOUT", "checkpoint_not_reached"))

    def test_hung_server_times_out_and_is_killed(self):
        status, reason, ev = self.run_mode("hang", per_step=0.3)
        self.assertEqual((status, reason), ("TIMEOUT", "tools/list_timeout"))
        self.assertTrue(ev["process_terminated"])

    def test_wrong_mpr_ram_and_empty_trace_fail(self):
        for mode, why in (("bad_mpr", "mpr1_mismatch:0"), ("bad_ram", "ram_mismatch:50434521f85b"),
                          ("empty_trace", "empty_trace")):
            with self.subTest(mode):
                self.assertEqual(self.run_mode(mode)[:2], ("FAIL", why))

    def test_missing_tool_and_schema_mismatch_fail(self):
        self.assertEqual(self.run_mode("missing_tool")[:2], ("FAIL", "missing_tools:get_trace_log"))
        self.assertEqual(self.run_mode("schema")[:2], ("FAIL", "schema_mismatch:write_memory"))

    def test_invalid_image_rejected(self):
        self.assertEqual(self.run_mode("invalid_image")[:2], ("FAIL", "load_media_error"))

    def test_server_exit_mid_session_fails_without_hang(self):
        self.assertEqual(self.run_mode("die", per_step=3)[:2], ("FAIL", "process_exited"))

    def test_oversized_response_fails_bounded_and_child_killed(self):
        pidfile = self.dir / "pid"
        os.environ["FAKE_PIDFILE"] = str(pidfile)
        self.addCleanup(os.environ.pop, "FAKE_PIDFILE", None)
        status, reason, ev = self.run_mode("oversize", per_step=10)
        self.assertEqual((status, reason), ("FAIL", "oversized_response"))
        self.assertTrue(ev["process_terminated"])
        self.assertNotAlive(int(pidfile.read_text()))

    def test_timeout_leaves_no_orphan(self):
        pidfile = self.dir / "pid"
        os.environ["FAKE_PIDFILE"] = str(pidfile)
        self.addCleanup(os.environ.pop, "FAKE_PIDFILE", None)
        self.assertEqual(self.run_mode("hang", per_step=0.3)[:2], ("TIMEOUT", "tools/list_timeout"))
        self.assertNotAlive(int(pidfile.read_text()))

    def assertNotAlive(self, pid):
        try:
            os.kill(pid, 0)
        except OSError:
            return
        self.fail(f"child {pid} still alive")

    def test_malformed_shapes_fail_cleanly(self):
        self.assertEqual(self.run_mode("bad_result")[:2], ("FAIL", "tools/list_malformed_result"))
        self.assertEqual(self.run_mode("bad_tools")[:2], ("FAIL", "malformed_tools_list"))

    def test_non_protocol_noise_and_foreign_ids_ignored(self):
        self.assertEqual(self.run_mode("noise")[:2], ("PASS", "ok"))

    def test_non_executable_spawn_failure_is_fail_not_traceback(self):
        bad = self.dir / "bad"
        bad.write_text("not an executable format")
        bad.chmod(0o755)
        self.assertEqual(m.run_session(bad, self.rom)[:2], ("FAIL", "protocol_error"))

    def test_helpers_boundaries(self):
        self.assertEqual(m.parse_bytes({"data": "zz"}), None)
        self.assertEqual(m.parse_bytes([1, 256]), None)
        self.assertEqual(m.parse_bytes({"bytes": [80, 67]}), b"PC")
        self.assertEqual(m.num("$E030"), 0xE030)
        self.assertIsNone(m.num("xyz"))


if __name__ == "__main__":
    unittest.main()
