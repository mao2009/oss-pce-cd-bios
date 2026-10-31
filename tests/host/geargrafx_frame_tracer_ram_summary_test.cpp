#include "geargrafx_frame_tracer_ram_summary.h"

#include <cassert>

int main() {
    FrameTracerRamMapping map = {};
    assert(FrameTracerMapRam(FRAME_TRACER_WRAM, 0x2000, 0, &map));
    assert(map.first_bank == 0xf8 && map.last_bank == 0xfb);
    assert(FrameTracerMapRam(FRAME_TRACER_CDROM_RAM, 0x10000, 0, &map));
    assert(map.first_bank == 0x80 && map.last_bank == 0x87);
    assert(FrameTracerMapRam(FRAME_TRACER_CARD_RAM, 0x8000, 0x40, &map));
    assert(map.first_bank == 0x40 && map.last_bank == 0x5f);
    assert(!FrameTracerMapRam(FRAME_TRACER_CARD_RAM, 0x8000, 0x68, &map));

    const unsigned char before[] = {0, 1, 2, 3, 4};
    const unsigned char first[] = {0, 9, 2, 8, 4};
    const unsigned char second[] = {7, 9, 2, 8, 6};
    FrameTracerRamDelta delta = FrameTracerCompareRam(before, first, 5);
    assert(delta.changed == 2 && delta.first == 1 && delta.last == 3);
    assert(FrameTracerCompareRam(before, before, 5).changed == 0);

    FrameTracerRamSummary summary = FrameTracerEmptyRamSummary();
    FrameTracerAccumulateRam(&summary, delta, 2, true, false);
    FrameTracerAccumulateRam(&summary, FrameTracerCompareRam(first, second, 5), 5, false, true);
    assert(summary.updates == 2 && summary.first_frame == 2 && summary.last_frame == 5);
    assert(summary.min_offset == 0 && summary.max_offset == 4 && summary.max_changed == 2);
    assert(summary.scsi_update_count == 1 && summary.first_scsi_frame == 2 && summary.last_scsi_frame == 2);
    assert(summary.scsi_correlation_unknown);
}
