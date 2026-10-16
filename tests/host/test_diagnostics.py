"""Actual assembly of original experiments, not unknown BIOS contracts."""
import json
import tempfile
import unittest
from pathlib import Path

from tools.build_rom import build
from tools.diagnostics import build_probe_images, parse_labels


class DiagnosticBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(); cls.root = Path(cls.temp.name)
        cls.base = build(cls.root/'base').read_bytes()
        cls.images, cls.symbols, cls.trace = build_probe_images(cls.base,cls.root/'probes')

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def test_table_is_unchanged_in_every_experiment(self):
        for path in self.images.values():
            self.assertEqual(path.read_bytes()[:0x800], self.base[:0x800])

    def test_reset_selects_original_experiment_symbols(self):
        for name,path in self.images.items():
            entry = self.symbols[('irq' if name.startswith('irq') else 'api_call' if name.startswith('api_jsr') else name)+'_probe']
            data = path.read_bytes()
            self.assertEqual(len(data),262144)
            self.assertEqual(int.from_bytes(data[0x1ffe:0x2000],'little'),entry)
            self.assertEqual(data[entry-0xe000],0x78)

    def test_vectors_select_distinct_actual_irq_sources(self):
        handlers = {'irq1':'irq_entry','irq2':'irq_entry','timer':'timer_entry'}
        for name,offset in [('irq1',0x1ff8),('irq2',0x1ff6),('timer',0x1ffa)]:
            data = self.images[name].read_bytes()
            vectors = {at:int.from_bytes(data[at:at+2],'little') for at in range(0x1ff6,0x1ffe,2)}
            self.assertEqual(vectors.pop(offset),self.symbols[handlers[name]])
            # IRQ2/IRQ1/timer/NMI vectors that are not under test must all reach the nonreturning trap.
            self.assertEqual(set(vectors.values()),{self.symbols['irq_unexpected']})
            self.assertEqual(len(vectors),3)

    def test_negative_instructions_are_symbol_qualified(self):
        data=self.images['call'].read_bytes()
        self.assertEqual(data[self.symbols['call_rts']-0xe000],0x60)
        at=self.symbols['restore_mpr']-0xe000
        self.assertEqual(data[at:at+2],b'\x53\x04')
        for name,label in (('irq1','irq_exit'),('timer','timer_exit')):
            self.assertEqual(self.images[name].read_bytes()[self.symbols[label]-0xe000],0x40)  # RTI

    def test_probe_debug_release_bytes_match(self):
        release,_,_=build_probe_images(self.base,self.root/'release','release')
        for name,path in release.items():self.assertEqual(path.read_bytes(),self.images[name].read_bytes())

    def test_bad_base_rom_does_not_build_probes(self):
        for data in (self.base[:-1],b'\xff'*len(self.base)):
            with self.assertRaises(ValueError):build_probe_images(data,self.root/'bad')

    def test_bad_symbol_and_mode_fail(self):
        with self.assertRaises(ValueError):parse_labels('al 00E800 .bank_probe\n')
        with self.assertRaises(ValueError):build_probe_images(self.base,self.root/'bad-mode','invalid')

    def test_manifest_cannot_be_confused_with_api_implementation(self):
        data=json.loads((self.root/'probes/manifest.json').read_text())
        self.assertEqual(data['api_implementation'],'NONE')
        self.assertEqual(set(data['images']),{'bank','call','irq1','irq2','timer','ram','api_jsr_01','api_jsr_03','api_jsr_1E'})
