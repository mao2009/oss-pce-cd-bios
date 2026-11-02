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

    // Complete SCSI observation on changed frames, no incompleteness: OBSERVED, never UNKNOWN.
    FrameTracerRamSummary observed = FrameTracerEmptyRamSummary();
    FrameTracerAccumulateRam(&observed, delta, 7, true, false);
    assert(!observed.scsi_correlation_unknown && observed.scsi_update_count == 1);
    assert(observed.first_scsi_frame == 7 && observed.last_scsi_frame == 7);

    // No SCSI notification and no incompleteness: NONE_OBSERVED (no fabricated OBSERVED).
    FrameTracerRamSummary none = FrameTracerEmptyRamSummary();
    FrameTracerAccumulateRam(&none, delta, 3, false, false);
    assert(!none.scsi_correlation_unknown && none.scsi_update_count == 0 && none.updates == 1);

    // Incomplete observation on a changed frame: UNKNOWN, even if SCSI was seen in the surviving part.
    FrameTracerRamSummary incomplete = FrameTracerEmptyRamSummary();
    FrameTracerAccumulateRam(&incomplete, delta, 4, true, true);
    assert(incomplete.scsi_correlation_unknown);

    // The loop passes (frame_scsi_incomplete && delta.changed): an unchanged, incomplete frame
    // must not make the region UNKNOWN.
    FrameTracerRamSummary unchanged = FrameTracerEmptyRamSummary();
    const unsigned char same[] = {1, 2, 3};
    FrameTracerAccumulateRam(&unchanged, FrameTracerCompareRam(same, same, 3), 9, false, false);
    assert(!unchanged.scsi_correlation_unknown && unchanged.updates == 0);

    // Cumulative RAM accounting survives many frames of ring-independent observation.
    FrameTracerRamSummary many = FrameTracerEmptyRamSummary();
    unsigned char page_a[8] = {0, 0, 0, 0, 0, 0, 0, 0};
    unsigned char page_b[8] = {0, 0, 0, 0, 0, 0, 0, 0};
    for (unsigned frame = 1; frame <= 1000; ++frame) {
        page_b[frame % 8] = (unsigned char)(page_b[frame % 8] + 1);
        FrameTracerAccumulateRam(&many, FrameTracerCompareRam(page_a, page_b, 8), frame, frame % 100 == 0, false);
        std::memcpy(page_a, page_b, sizeof page_a);
    }
    assert(many.updates == 1000 && many.first_frame == 1 && many.last_frame == 1000);
    assert(!many.scsi_correlation_unknown && many.scsi_update_count == 10);
}
