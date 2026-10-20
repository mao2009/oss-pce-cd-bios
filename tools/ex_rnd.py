"""Host reference model and probe build for the project-own EX_RND (Issue #72).

This models src/ex_rnd.s as specified in docs/ex-rnd-internal.md. It is NOT the System Card
$3E/$E0BA ABI, which remains unconfirmed.
"""
from pathlib import Path
import re
import subprocess

if __package__:
    from .environment import ROOT, executable
else:
    from environment import ROOT, executable

TAPS = 0xB400


def lfsr_step(state):
    """One Galois step of the 16-bit state: shift right, feed back with TAPS when bit 0 was set."""
    bit = state & 1
    state >>= 1
    return state ^ TAPS if bit else state


def rnd_next(state):
    """Return (output byte, new state) for ex_rnd_next: zero state becomes 1, then 8 steps."""
    state &= 0xFFFF
    if state == 0:
        state = 1
    for _ in range(8):
        state = lfsr_step(state)
    return state & 0xFF, state


def rnd_sequence(seed, count):
    """Output bytes of `count` consecutive ex_rnd_next calls after ex_rnd_seed(seed)."""
    state, out = seed & 0xFFFF, []
    for _ in range(count):
        byte, state = rnd_next(state)
        out.append(byte)
    return out


def build_probe(output):
    """Assemble the routine plus the HuCARD probe; return (rom path, label dict)."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    ca65, ld65 = executable("ca65"), executable("ld65")
    objects = []
    for source in (ROOT / "src/ex_rnd.s", ROOT / "tests/fixtures/ex_rnd_probe.s"):
        obj = output / (source.stem + ".o")
        subprocess.run([ca65, "--cpu", "huc6280", "-o", str(obj), str(source)], check=True)
        objects.append(str(obj))
    rom, labels = output / "ex-rnd-probe-not-bios.pce", output / "ex_rnd.lbl"
    subprocess.run([ld65, "-C", str(ROOT / "tests/fixtures/ex_rnd_probe.cfg"), "-o", str(rom),
                    "-Ln", str(labels), *objects], check=True)
    symbols = {name: int(address, 16) for address, name in
               re.findall(r"^al ([0-9A-Fa-f]{6}) \.(\w+)$", labels.read_text(), re.M)}
    if rom.stat().st_size != 8192 or symbols.get("probe_reset") != 0xE000:
        raise ValueError("EX_RND probe layout mismatch")
    return rom, symbols
