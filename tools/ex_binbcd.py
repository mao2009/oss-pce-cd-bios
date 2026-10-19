"""Host reference model and probe build for the project-own EX_BINBCD (Issue #70).

This models src/ex_binbcd.s as specified in docs/ex-binbcd-internal.md. It is NOT the
System Card $3C/$E0B4 ABI, which remains unconfirmed.
"""
from pathlib import Path
import re
import subprocess

if __package__:
    from .environment import ROOT, executable
else:
    from environment import ROOT, executable


def binbcd_packed(value):
    """Return (A, C) after ex_binbcd_packed: packed BCD byte for 0..99, else input with C=1."""
    if not 0 <= value <= 0xFF:
        raise ValueError("EX_BINBCD input is one unsigned byte")
    if value >= 100:
        return value, 1
    return (value // 10) << 4 | value % 10, 0


def build_probe(output):
    """Assemble the routine plus the HuCARD probe; return (rom path, label dict)."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    ca65, ld65 = executable("ca65"), executable("ld65")
    objects = []
    for source in (ROOT / "src/ex_binbcd.s", ROOT / "tests/fixtures/ex_binbcd_probe.s"):
        obj = output / (source.stem + ".o")
        subprocess.run([ca65, "--cpu", "huc6280", "-o", str(obj), str(source)], check=True)
        objects.append(str(obj))
    rom, labels = output / "ex-binbcd-probe-not-bios.pce", output / "ex_binbcd.lbl"
    subprocess.run([ld65, "-C", str(ROOT / "tests/fixtures/ex_binbcd_probe.cfg"), "-o", str(rom),
                    "-Ln", str(labels), *objects], check=True)
    symbols = {name: int(address, 16) for address, name in
               re.findall(r"^al ([0-9A-Fa-f]{6}) \.(\w+)$", labels.read_text(), re.M)}
    if rom.stat().st_size != 8192 or symbols.get("probe_reset") != 0xE000:
        raise ValueError("EX_BINBCD probe layout mismatch")
    return rom, symbols
