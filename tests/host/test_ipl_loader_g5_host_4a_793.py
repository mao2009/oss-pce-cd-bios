"""Issue #793 host: G5 $4A SIMULATED diagnostic variant (synthetic, no commercial).

Parent #635. NOT compatible, NOT ABI, release_eligible=false.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from tools.environment import cc65_skip_reason, cc65_required, requires_cc65  # noqa: E402


@requires_cc65
class G5FourASimHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import shutil
        import tempfile
        from build_rom import build
        cls.work = Path(tempfile.mkdtemp(prefix="pce-g54a-host-"))
        cls.addClassCleanup(shutil.rmtree, cls.work, ignore_errors=True)
        normal_path = build(cls.work / "normal", root=ROOT)
        cls.normal = normal_path.read_bytes()

    def _build(self, variant, sel=0x109, track=1, private=False):
        from build_ipl_loader_g5_4a import build_ipl_loader_g5_4a_simulated
        out = self.work / f"g5-{variant}"
        return build_ipl_loader_g5_4a_simulated(
            self.work / "normal" / "dev-only-not-compatible-syscard3.pce",
            out, sel, "debug", variant, ["-D", "IPL_PUBLIC"], track,
            private_input=private)

    def test_standard_rom_untouched_by_diagnostic(self):
        from build_ipl_loader_g5 import build_ipl_loader_g5_rom
        import hashlib
        out1 = self.work / "std-only"
        rom1, _ = build_ipl_loader_g5_rom(
            self.work / "normal" / "dev-only-not-compatible-syscard3.pce",
            out1, 0x109, "debug", "stdcheck", ["-D", "IPL_PUBLIC"], 1)
        h1 = hashlib.sha256(rom1.read_bytes()).hexdigest()
        std_rom, diag_rom, _, _ = self._build("stdcheck2")
        # Diagnostic must not overwrite the standard file of the same variant build;
        # standard SHA must be stable across rebuilds.
        out2 = self.work / "std-only2"
        rom2, _ = build_ipl_loader_g5_rom(
            self.work / "normal" / "dev-only-not-compatible-syscard3.pce",
            out2, 0x109, "debug", "stdcheck", ["-D", "IPL_PUBLIC"], 1)
        self.assertEqual(hashlib.sha256(rom2.read_bytes()).hexdigest(), h1)
        self.assertTrue(std_rom.is_file())
        self.assertTrue(diag_rom.is_file())
        self.assertNotEqual(std_rom, diag_rom)

    def test_patch_window_is_4a_only(self):
        std_rom, diag_rom, _, info = self._build("wincheck")
        std = std_rom.read_bytes()
        diag = diag_rom.read_bytes()
        diffs = [i for i, (a, b) in enumerate(zip(std, diag)) if a != b]
        self.assertEqual(len(diffs), 5)
        # Fallback offset derived from label, table entry $E0DE intact.
        self.assertEqual(info["table_entry"], "$E0DE")
        self.assertEqual(info["before"], "a24a4c3be2")
        self.assertEqual(info["after"], "60eaeaeaea")
        self.assertEqual(info["classification"][:9], "SIMULATED")
        self.assertFalse(info["dev_callable"])
        self.assertFalse(info["release_eligible"])

    def test_mismatch_blocked(self):
        from build_ipl_loader_g5_4a import build_ipl_loader_g5_4a_simulated
        # Bad track refuses before any diagnostic write.
        with self.assertRaises(ValueError):
            self._build("badtrk", track=0)
        with self.assertRaises(ValueError):
            self._build("badsel", sel=0x1FFFF)


@requires_cc65
class G5FourAStubSymlinkTests(unittest.TestCase):
    """MAJOR-1: stub-generation leaves refuse symlinks before the assembler runs.

    Isolated temp dirs only. Each of the three stub leaves is planted as a
    symlink to an external sentinel and as a dangling symlink. Refusal must
    leave the sentinel byte-identical and must not run the stub assembler.
    Plain-file rebuilds keep working.
    """

    def test_stub_leaves_refuse_symlink_and_dangling(self):
        import os
        import shutil
        import tempfile
        from build_ipl_loader_g5_4a import _STUB_LEAVES, _refuse_stub_leaves
        for leaf in _STUB_LEAVES:
            for mode in ("symlink", "dangling"):
                with self.subTest(leaf=leaf, mode=mode):
                    tmp = Path(tempfile.mkdtemp(prefix="pce-g54a-major1-"))
                    try:
                        out = tmp / "out"
                        out.mkdir()
                        if mode == "symlink":
                            sentinel = tmp / "sentinel.bin"
                            sentinel.write_bytes(b"SENTINEL-MAJOR1-" + leaf.encode())
                            before = sentinel.read_bytes()
                            os.symlink(sentinel, out / leaf)
                            with self.assertRaises(ValueError):
                                _refuse_stub_leaves(out)
                            self.assertEqual(sentinel.read_bytes(), before)
                            self.assertTrue(os.path.islink(out / leaf))
                        else:
                            target = tmp / "no-such-target.bin"
                            if target.exists():
                                target.unlink()
                            os.symlink(target, out / leaf)
                            with self.assertRaises(ValueError):
                                _refuse_stub_leaves(out)
                            self.assertTrue(os.path.islink(out / leaf))
                    finally:
                        shutil.rmtree(tmp, ignore_errors=True)

    def test_builder_refuses_before_stub_generation(self):
        import os
        import shutil
        import tempfile
        from build_ipl_loader_g5_4a import build_ipl_loader_g5_4a_simulated
        from build_rom import build as build_normal
        tmp = Path(tempfile.mkdtemp(prefix="pce-g54a-major1-e2e-"))
        try:
            normal_path = build_normal(tmp / "normal", root=ROOT)
            out = tmp / "g5out"
            out.mkdir()
            sentinel = tmp / "external-sentinel.bin"
            sentinel.write_bytes(b"EXTERNAL-SENTINEL-MAJOR1")
            before = sentinel.read_bytes()
            # Plant one stub leaf as a symlink before the builder runs.
            os.symlink(sentinel, out / "slot02_return_stub.o")
            with self.assertRaises(ValueError):
                build_ipl_loader_g5_4a_simulated(
                    normal_path, out, 0x109, "debug", "maj1",
                    ["-D", "IPL_PUBLIC"], 1)
            # Sentinel byte-identical, symlink still a symlink (assembler
            # never ran through it), diagnostic ROM never published.
            self.assertEqual(sentinel.read_bytes(), before)
            self.assertTrue(os.path.islink(out / "slot02_return_stub.o"))
            self.assertFalse((out / "dev-ipl-loader-g5-not-compatible-maj1-4a-sim.pce").exists())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_plain_file_stub_leaves_rebuild(self):
        import shutil
        import tempfile
        from build_rom import build as build_normal
        from build_ipl_loader_g5_4a import build_ipl_loader_g5_4a_simulated
        tmp = Path(tempfile.mkdtemp(prefix="pce-g54a-major1-plain-"))
        try:
            normal_path = build_normal(tmp / "normal", root=ROOT)
            out = tmp / "g5out"
            std1, diag1, _, _ = build_ipl_loader_g5_4a_simulated(
                normal_path, out, 0x109, "debug", "plainA",
                ["-D", "IPL_PUBLIC"], 1)
            self.assertTrue(diag1.is_file())
            # Second variant in the SAME output dir: stub leaves are now
            # plain files from the first run; the rebuild must still succeed.
            std2, diag2, _, _ = build_ipl_loader_g5_4a_simulated(
                normal_path, out, 0x109, "debug", "plainB",
                ["-D", "IPL_PUBLIC"], 1)
            self.assertTrue(diag2.is_file())
            self.assertNotEqual(diag1, diag2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


@requires_cc65
class G5FourAManifestOrderTests(unittest.TestCase):
    """MEDIUM-1: both final outputs are vetted before the first publish.

    Manifest symlink/dangling refuses the build with no diagnostic ROM
    published; os.replace failure leaves no temp file; normal generation
    still succeeds. Isolated temp dirs only.
    """

    def test_manifest_symlink_refuses_without_diag_publish(self):
        import os
        import shutil
        import tempfile
        from build_rom import build as build_normal
        from build_ipl_loader_g5_4a import build_ipl_loader_g5_4a_simulated
        for mode in ("symlink", "dangling"):
            with self.subTest(mode=mode):
                tmp = Path(tempfile.mkdtemp(prefix="pce-g54a-med1-"))
                try:
                    normal_path = build_normal(tmp / "normal", root=ROOT)
                    out = tmp / "g5out"
                    out.mkdir()
                    man = out / "manifest-med1-4a-sim.json"
                    # NOTE: diag_variant is f"{variant}-4a-sim", manifest is
                    # f"manifest-{diag_variant}.json" (single suffix).
                    diag = out / "dev-ipl-loader-g5-not-compatible-med1-4a-sim.pce"
                    if mode == "symlink":
                        sentinel = tmp / "sentinel.json"
                        sentinel.write_bytes(b"{\"sentinel\": true}")
                        before = sentinel.read_bytes()
                        os.symlink(sentinel, man)
                    else:
                        os.symlink(tmp / "no-such.json", man)
                        before = None
                    with self.assertRaises(ValueError):
                        build_ipl_loader_g5_4a_simulated(
                            normal_path, out, 0x109, "debug", "med1",
                            ["-D", "IPL_PUBLIC"], 1)
                    self.assertFalse(diag.exists())
                    self.assertTrue(os.path.islink(man))
                    if before is not None:
                        self.assertEqual((tmp / "sentinel.json").read_bytes(), before)
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)

    def test_replace_failure_leaves_no_temp(self):
        import shutil
        import tempfile
        from unittest import mock
        from build_rom import build as build_normal
        from build_ipl_loader_g5_4a import build_ipl_loader_g5_4a_simulated
        tmp = Path(tempfile.mkdtemp(prefix="pce-g54a-med1-tmp-"))
        try:
            normal_path = build_normal(tmp / "normal", root=ROOT)
            out = tmp / "g5out"
            out.mkdir()
            real_replace = __import__("os").replace

            def fail_manifest(src, dst):
                if str(dst).endswith("-4a-sim.json"):
                    raise OSError("injected manifest replace failure")
                return real_replace(src, dst)

            with mock.patch("os.replace", side_effect=fail_manifest):
                with self.assertRaises(OSError):
                    build_ipl_loader_g5_4a_simulated(
                        normal_path, out, 0x109, "debug", "medtmp",
                        ["-D", "IPL_PUBLIC"], 1)
            leftovers = [p for p in out.iterdir() if ".part-" in p.name and p.suffix == ".tmp"]
            self.assertEqual(leftovers, [])
            # Manifest failed: the orphan diagnostic ROM must have been removed.
            self.assertFalse((out / "dev-ipl-loader-g5-not-compatible-medtmp-4a-sim.pce").exists())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_normal_rom_and_manifest_succeed(self):
        import shutil
        import tempfile
        from build_rom import build as build_normal
        from build_ipl_loader_g5_4a import build_ipl_loader_g5_4a_simulated
        tmp = Path(tempfile.mkdtemp(prefix="pce-g54a-med1-ok-"))
        try:
            normal_path = build_normal(tmp / "normal", root=ROOT)
            out = tmp / "g5out"
            std_rom, diag_rom, _, info = build_ipl_loader_g5_4a_simulated(
                normal_path, out, 0x109, "debug", "medok",
                ["-D", "IPL_PUBLIC"], 1)
            self.assertTrue(std_rom.is_file())
            self.assertTrue(diag_rom.is_file())
            man = out / "manifest-medok-4a-sim.json"
            # (diag_variant "medok-4a-sim" -> manifest single suffix)
            self.assertTrue(man.is_file())
            self.assertEqual(info["diagnostic_rom"], diag_rom.name)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
