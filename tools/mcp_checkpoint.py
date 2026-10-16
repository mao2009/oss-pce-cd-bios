#!/usr/bin/env python3
"""Bounded MCP stdio checkpoint client for standalone Geargrafx (Issue #103).

Outcomes: PASS / FAIL / TIMEOUT / BLOCKED. A missing binary is BLOCKED (exit 3), never PASS.
The tool schema is discovered via tools/list; arguments absent from a tool's inputSchema are
rejected (schema_mismatch) instead of being guessed. Only an originally built HuCARD fixture is
loaded; no emulator code is copied here (the GPL emulator stays an external binary).
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time

EXPECTED_RAM = bytes.fromhex("50434521f85a")  # "PCE!" F8 5A
CHECKPOINT_PC = 0xE030
REQUIRED = ("load_media", "debug_pause", "debug_reset", "debug_continue", "set_breakpoint",
            "get_huc6280_status", "read_memory", "write_memory", "set_trace_log", "get_trace_log")


class Fail(Exception):
    def __init__(self, status, reason):
        super().__init__(reason)
        self.status, self.reason = status, reason


class Client:
    """Newline-delimited JSON-RPC over a child's stdio; every read has a deadline."""

    def __init__(self, argv, cwd):
        self.proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.lines, self.next_id, self.schemas = queue.Queue(), 0, {}
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.proc.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def request(self, method, params=None, timeout=5.0):
        self.next_id += 1
        msg = {"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}}
        try:
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()
        except OSError:
            raise Fail("FAIL", "process_exited")
        deadline = time.monotonic() + timeout
        while True:
            try:
                line = self.lines.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                raise Fail("TIMEOUT", f"{method}_timeout")
            if line is None:
                raise Fail("FAIL", "process_exited")
            try:
                reply = json.loads(line)
            except ValueError:
                continue  # non-protocol noise on stdout
            if isinstance(reply, dict) and reply.get("id") == self.next_id:
                if "error" in reply:
                    raise Fail("FAIL", f"{method}_rpc_error")
                return reply.get("result", {})

    def notify(self, method):
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        self.proc.stdin.flush()

    def call(self, name, args=None, timeout=5.0):
        args = args or {}
        props = self.schemas.get(name, {}).get("properties", {})
        if any(k not in props for k in args):
            raise Fail("FAIL", f"schema_mismatch:{name}")
        result = self.request("tools/call", {"name": name, "arguments": args}, timeout)
        if result.get("isError"):
            raise Fail("FAIL", f"{name}_error")
        text = "".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
        try:
            return json.loads(text)
        except ValueError:
            return text

    def close(self):
        """Stop the child; True only if it is verifiably gone."""
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                return False
        self.proc.stdout.close()
        return True


def find(obj, *names):
    """First value whose key matches a name (case-insensitive), searched recursively."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() in names:
                return v
        for v in obj.values():
            r = find(v, *names)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find(v, *names)
            if r is not None:
                return r
    return None


def num(v):
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        try:
            return int(v.removeprefix("$").removeprefix("0x"), 16)
        except ValueError:
            pass
    return None


def parse_bytes(reply):
    v = reply if isinstance(reply, (list, str)) else find(reply, "data", "bytes", "hex", "values")
    if isinstance(v, str):
        try:
            return bytes.fromhex("".join(v.replace("0x", "").replace("$", "").split()))
        except ValueError:
            return None
    if isinstance(v, list) and all(isinstance(x, int) and 0 <= x < 256 for x in v):
        return bytes(v)
    return None


def checkpoint(c, deadline_s, label):
    """Reset to $E030 and assert actual PC/MPR1/RAM. Returns observation dict."""
    c.call("write_memory", {"address": "2200", "data": "000000000000"})  # warm run must not see stale RAM
    c.call("debug_reset")
    c.call("debug_continue")
    end = time.monotonic() + deadline_s
    while True:
        st = c.call("get_huc6280_status")
        pc = num(find(st, "pc"))
        if pc is None:
            raise Fail("FAIL", "unparsable_status")
        if pc == CHECKPOINT_PC:
            break
        if time.monotonic() > end:
            raise Fail("TIMEOUT", "checkpoint_not_reached")
        time.sleep(0.02)
    mpr = find(st, "mpr")
    mpr1 = num(mpr[1]) if isinstance(mpr, list) and len(mpr) > 1 else None
    if mpr1 != 0xF8:
        raise Fail("FAIL", f"mpr1_mismatch:{mpr1}")
    ram = parse_bytes(c.call("read_memory", {"address": "2200", "size": 6}))
    if ram != EXPECTED_RAM:
        raise Fail("FAIL", f"ram_mismatch:{ram.hex() if ram else None}")
    trace = c.call("get_trace_log")
    entries = find(trace, "entries", "lines", "trace")  # field name unverified at the pin
    if not (entries if isinstance(entries, list) else trace):
        raise Fail("FAIL", "empty_trace")
    return {"label": label, "pc": pc, "mpr1": mpr1, "ram": ram.hex(), "trace_nonempty": True}


def run_session(binary, rom, per_step=5.0, deadline_s=10.0, cycles=("cold", "warm")):
    """Returns (status, reason, evidence). The binary is copied into a private --portable profile."""
    binary = Path(binary)
    if not binary.is_file() or not os.access(binary, os.X_OK):
        return "BLOCKED", "geargrafx_standalone_missing", {}
    evidence = {"rom_sha256": hashlib.sha256(Path(rom).read_bytes()).hexdigest(), "checkpoints": []}
    with tempfile.TemporaryDirectory(prefix="pce-mcp-") as profile:
        exe = shutil.copy2(binary, Path(profile) / "geargrafx")
        c = None
        try:
            c = Client([exe, "--headless", "--mcp-stdio", "--portable"], profile)
            c.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                     "clientInfo": {"name": "pce-checkpoint", "version": "0"}}, per_step)
            c.notify("notifications/initialized")
            tools = c.request("tools/list", timeout=per_step).get("tools", [])
            c.schemas = {t["name"]: t.get("inputSchema", {}) for t in tools}
            missing = [t for t in REQUIRED if t not in c.schemas]
            if missing:
                raise Fail("FAIL", "missing_tools:" + ",".join(missing))
            c.call("set_trace_log", {"action": "start"})
            c.call("debug_pause")
            c.call("load_media", {"file_path": str(Path(rom).resolve())})
            c.call("set_breakpoint", {"address": "E030"})
            for label in cycles:
                evidence["checkpoints"].append(checkpoint(c, deadline_s, label))
            status, reason = "PASS", "ok"
        except Fail as f:
            status, reason = f.status, f.reason
        finally:
            gone = c.close() if c else True
        if not gone:
            return "FAIL", "process_not_terminated", evidence
        evidence["process_terminated"] = True
        return status, reason, evidence


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--binary", default=os.environ.get("GEARGRAFX_DESKTOP", ""))
    p.add_argument("--rom", required=True)
    p.add_argument("--output")
    a = p.parse_args(argv)
    status, reason, ev = run_session(a.binary or "/nonexistent", a.rom)
    out = {"status": status, "reason": reason, **ev}
    if a.output:
        Path(a.output).write_text(json.dumps(out, indent=2) + "\n")
    print(f"{status}: {reason}")
    return {"PASS": 0, "BLOCKED": 3}.get(status, 1)


if __name__ == "__main__":
    sys.exit(main())
