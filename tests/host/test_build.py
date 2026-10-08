"""Host assertions execute pinned tools; missing setup is an error, not SKIP."""
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from build import build, verify
from environment import ROOT, core_path, executable, tools_dir


class BuildTests(unittest.TestCase):
    def test_reproducible_clean_directories_and_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            hashes = [build(base / name, mode) for name, mode in
                      [("first", "debug"), ("second", "debug"), ("release", "release")]]
            self.assertEqual(len(set(hashes)), 1)
            self.assertTrue((base / "first/smoke.lst").is_file())
            self.assertFalse((base / "release/smoke.lst").exists())
            data = (base / "first/smoke-test-not-bios.pce").read_bytes()
            self.assertEqual(len(data), 8192)
            self.assertEqual(struct.unpack("<5H", data[-10:]), (0xe030,) * 4 + (0xe000,))
            for damaged in (data[:-1], b"\0" * 8192, b"\0" * 512 + data):
                with self.assertRaises(ValueError):
                    verify(damaged)
            for offset in (0, 16, 0x30, 0x40, 0x100, 0x1ff6, 0x1ffe):
                damaged = bytearray(data)
                damaged[offset] ^= 1
                with self.assertRaises(ValueError):
                    verify(damaged)

    def test_tools_present(self):
        for name in ("ca65", "ld65", "cc65", "actionlint"):
            self.assertTrue(Path(executable(name)).is_file())
        self.assertTrue(core_path().is_file())

    def test_missing_tools_and_core_fail(self):
        for name in ("ca65", "ld65", "cc65", "actionlint"):
            with patch.dict(os.environ, {name.upper(): "/nonexistent/pce-tool"}):
                with self.assertRaises(ValueError):
                    executable(name)
        with patch.dict(os.environ, {"GEARGRAFX_CORE": "/nonexistent/pce-core.so"}):
            with self.assertRaises(ValueError):
                core_path()
        with patch.dict(os.environ, {"CA65": "/nonexistent/pce-tool"}):
            result = subprocess.run([sys.executable, str(ROOT / "tools/build.py")],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing tool ca65", result.stderr)

    def test_invalid_mode_fails_before_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            with self.assertRaises(ValueError):
                build(output, "nonsense")
            self.assertFalse(output.exists())

    def test_external_source_isolation(self):
        with patch.dict(os.environ, {"TOOLS_DIR": str(ROOT / ".cache")}):
            with self.assertRaises(ValueError):
                tools_dir()

    def test_assembler_rejects_invalid_syntax(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "invalid.s"
            source.write_text('.setcpu "huc6280"\nnot_an_opcode #123\n')
            result = subprocess.run([executable("ca65"), "-o", str(Path(directory) / "x.o"),
                                     str(source)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)

    def test_linker_rejects_overlap_and_bank_overflow(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = base / "overlap.cfg"
            config.write_text((ROOT / "tests/fixtures/smoke.cfg").read_text().replace(
                "start = $E030", "start = $E010"))
            with self.assertRaises(subprocess.CalledProcessError):
                build(base / "overlap", config=config)
            source = base / "overflow.s"
            source.write_text((ROOT / "tests/fixtures/smoke.s").read_text() +
                              '\n.segment "RESET"\n.res $2000, $ff\n')
            with self.assertRaises(subprocess.CalledProcessError):
                build(base / "overflow", source=source)

    def test_unpinned_tool_fails(self):
        with patch.dict(os.environ, {"CA65": "/usr/bin/true"}):
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaisesRegex(ValueError, "pinned"):
                    build(Path(directory))

    def test_huc6280_opcode_families(self):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run([executable("ca65"), "-o", str(Path(directory) / "probe.o"),
                            str(ROOT / "tests/fixtures/opcodes.s")], check=True)

    def test_c_frontend_assembly_interoperability(self):
        # Compile-only probe: cc65 runtime/startup/linking into BIOS remains future work.
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "probe.c").write_text("unsigned char twice(unsigned char x) { return x + x; }\n")
            subprocess.run([executable("cc65"), "--cpu", "huc6280", "-O",
                            "-o", str(base / "probe.s"), str(base / "probe.c")], check=True)
            includes = Path(executable("cc65")).resolve().parents[1] / "asminc"
            subprocess.run([executable("ca65"), "-I", str(includes), "-o", str(base / "probe.o"),
                            str(base / "probe.s")], check=True)
            self.assertIn('_twice', (base / "probe.s").read_text())

    def test_release_gate(self):
        result = subprocess.run(["make", "release"], cwd=ROOT, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("BLOCKED", result.stderr)


if __name__ == "__main__":
    unittest.main()
