#ifndef OSS_PCE_CD_BIOS_GEARGRAFX_FRAME_TRACER_RAM_SUMMARY_H
#define OSS_PCE_CD_BIOS_GEARGRAFX_FRAME_TRACER_RAM_SUMMARY_H

#include <cstring>

// Metadata-only RAM accounting for the local frame tracer.  These helpers do
// not retain or expose RAM bytes.  A mapping is accepted only for the pinned
// core layouts documented by this diagnostic.
enum FrameTracerRamKind { FRAME_TRACER_WRAM, FRAME_TRACER_CDROM_RAM, FRAME_TRACER_CARD_RAM };

struct FrameTracerRamMapping {
    const char* name;
    unsigned size, first_bank, last_bank;
};

struct FrameTracerRamDelta {
    unsigned changed, first, last;
};

struct FrameTracerRamSummary {
    unsigned updates, first_frame, last_frame, min_offset, max_offset, max_changed;
    unsigned scsi_update_count, first_scsi_frame, last_scsi_frame;
    bool scsi_correlation_unknown;
};

inline bool FrameTracerMapRam(FrameTracerRamKind kind, unsigned size, unsigned start,
                               FrameTracerRamMapping* mapping) {
    if (kind == FRAME_TRACER_WRAM && size == 0x2000)
        *mapping = {"wram", size, 0xf8, 0xfb};
    else if (kind == FRAME_TRACER_CDROM_RAM && size == 0x10000)
        *mapping = {"cdrom_ram", size, 0x80, 0x87};
    else if (kind == FRAME_TRACER_CARD_RAM && size == 0x8000 && start == 0x40)
        *mapping = {"card_ram", size, 0x40, 0x5f};
    else if (kind == FRAME_TRACER_CARD_RAM && size == 0x30000 && start == 0x68)
        *mapping = {"card_ram", size, 0x68, 0x7f};
    else
        return false;
    return true;
}

inline FrameTracerRamDelta FrameTracerCompareRam(const unsigned char* before, const unsigned char* after,
                                                  unsigned size) {
    FrameTracerRamDelta delta = {0, size, 0};
    for (unsigned offset = 0; offset != size; ++offset) {
        if (before[offset] != after[offset]) {
            ++delta.changed;
            if (delta.first == size) delta.first = offset;
            delta.last = offset;
        }
    }
    return delta;
}

inline FrameTracerRamSummary FrameTracerEmptyRamSummary() {
    return {0, 0, 0, 0, 0, 0, 0, 0, 0, false};
}

inline void FrameTracerAccumulateRam(FrameTracerRamSummary* summary, const FrameTracerRamDelta& delta,
                                     unsigned frame, bool scsi_event_observed, bool scsi_correlation_unknown) {
    if (scsi_correlation_unknown) summary->scsi_correlation_unknown = true;
    if (!delta.changed) return;
    ++summary->updates;
    if (summary->updates == 1) {
        summary->first_frame = summary->last_frame = frame;
        summary->min_offset = delta.first;
        summary->max_offset = delta.last;
    } else {
        summary->last_frame = frame;
        if (delta.first < summary->min_offset) summary->min_offset = delta.first;
        if (delta.last > summary->max_offset) summary->max_offset = delta.last;
    }
    if (delta.changed > summary->max_changed) summary->max_changed = delta.changed;
    if (scsi_event_observed) {
        ++summary->scsi_update_count;
        if (summary->scsi_update_count == 1) summary->first_scsi_frame = frame;
        summary->last_scsi_frame = frame;
    }
}

#endif
