#include "geargrafx_frame_tracer_ram.h"

#include <cassert>

int main() {
    FrameTracerRamMapping map = {};
    assert(FrameTracerMapRam(FRAME_TRACER_WRAM, 0x2000, 0, &map));
    assert(map.first_bank == 0xf8 && map.last_bank == 0xfb);
    assert(FrameTracerMapRam(FRAME_TRACER_CDROM_RAM, 0x10000, 0, &map));
    assert(map.first_bank == 0x80 && map.last_bank == 0x87);
    assert(FrameTracerMapRam(FRAME_TRACER_CARD_RAM, 0x8000, 0x40, &map));
    assert(map.first_bank == 0x40 && map.last_bank == 0x5f);
    assert(FrameTracerMapRam(FRAME_TRACER_CARD_RAM, 0x30000, 0x68, &map));
    assert(map.first_bank == 0x68 && map.last_bank == 0x7f);
    assert(!FrameTracerMapRam(FRAME_TRACER_CARD_RAM, 0x8000, 0x68, &map));

    const unsigned char before[] = {0, 1, 2, 3, 4};
    const unsigned char after[] = {0, 9, 2, 8, 4};
    FrameTracerRamDelta delta = FrameTracerCompareRam(before, after, 5);
    assert(delta.changed == 2 && delta.first == 1 && delta.last == 3);
    delta = FrameTracerCompareRam(before, before, 5);
    assert(delta.changed == 0 && delta.first == 5);
}
