"""Caller-defined raw ROM structure checks; no BIOS validity or CPU emulation."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Placement:
    name: str
    offset: int
    size: int
    cpu_address: int


def validate_rom_structure(data, *, size, bank_size, cpu_base, placements=(), image_format="headerless"):
    """Validate byte count and explicit bank/placement constraints only.

    The caller supplies the format contract. Passing says nothing about reset
    behavior, opcode content, System Card ABI or hardware compatibility.
    """
    if image_format != "headerless":
        raise ValueError("only explicitly headerless raw ROM structures are supported")
    if (size <= 0 or bank_size <= 0 or bank_size & (bank_size - 1)
            or size % bank_size or not 0 <= cpu_base <= 0xffff
            or cpu_base + bank_size > 0x10000):
        raise ValueError("invalid ROM size/bank/CPU window contract")
    if len(data) != size:
        raise ValueError(f"expected headerless {size}-byte ROM, got {len(data)}")
    end = 0
    names = set()
    for item in sorted(placements, key=lambda item: item.offset):
        if (item.name in names or item.offset < end or item.size <= 0
                or item.offset + item.size > size
                or item.offset // bank_size != (item.offset + item.size - 1) // bank_size
                or item.cpu_address != cpu_base + item.offset % bank_size):
            raise ValueError(f"invalid ROM placement: {item.name}")
        names.add(item.name)
        end = item.offset + item.size
