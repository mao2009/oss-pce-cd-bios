"""Real make/compiler and verified tiny archives test build-profile isolation."""
import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools'))
import setup as bootstrap


class DebuggerSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.cache=Path(self.temp.name)
        self.env=patch.dict(os.environ,{'TOOLS_DIR':str(self.cache)});self.env.start();self.addCleanup(self.env.stop)
        archive=self.cache/'geargrafx-test.tar.gz'
        files={'platforms/libretro/Makefile':b'INCLUDES = -I.\nCXXFLAGS += -DGG_DISABLE_DISASSEMBLER\nall: geargrafx_libretro.so\ngeargrafx_libretro.so: tiny.o\n\t$(CXX) -shared tiny.o -o $@\ntiny.o: tiny.cpp\n\t$(CXX) $(CXXFLAGS) -c $< -o $@\n',
               'platforms/libretro/tiny.cpp':b'#if defined(GG_DISABLE_DISASSEMBLER) || !defined(__LIBRETRO__)\n#error wrong_profile\n#endif\nint value() { return 1; }\n'}
        with tarfile.open(archive,'w:gz') as tar:
            for name,content in files.items():
                info=tarfile.TarInfo('project/'+name);info.size=len(content);tar.addfile(info,io.BytesIO(content))
        pin={'revision':'test','sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'url':'unused'}
        self.pin=patch.dict(bootstrap.DEPENDENCIES,{'geargrafx':pin});self.pin.start();self.addCleanup(self.pin.stop)

    def test_normal_and_debugger_cache_are_independent(self):
        regular=bootstrap.fetch('geargrafx');debug=bootstrap.fetch('geargrafx',debugger=True)
        self.assertNotEqual(regular,debug)
        self.assertEqual((regular/'platforms/libretro/Makefile').read_bytes(),(debug/'platforms/libretro/Makefile').read_bytes())

    def test_debugger_configuration_really_compiles_without_disable_macro(self):
        debug=bootstrap.fetch('geargrafx',debugger=True)
        bootstrap.build_geargrafx(debug,1,debugger=True)
        self.assertTrue((debug/'platforms/libretro/geargrafx_libretro.so').is_file())
        self.assertEqual(bootstrap.fetch('geargrafx',debugger=True),debug)

    def test_debugger_repeated_setup_reuses_objects(self):
        debug=bootstrap.fetch('geargrafx',debugger=True)
        bootstrap.build_geargrafx(debug,1,debugger=True)
        obj=debug/'platforms/libretro/tiny.o';mtime=obj.stat().st_mtime_ns
        bootstrap.build_geargrafx(bootstrap.fetch('geargrafx',debugger=True),1,debugger=True)
        self.assertEqual(obj.stat().st_mtime_ns,mtime)

    def test_invalid_debugger_combination_fails_explicitly(self):
        for arguments in [('cc65','--debugger'),('geargrafx','--debugger','--desktop')]:
            result=subprocess.run([sys.executable,str(Path(bootstrap.__file__)),*arguments],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0);self.assertIn('--debugger',result.stderr)
