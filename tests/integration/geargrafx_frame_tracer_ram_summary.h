#ifndef OSS_PCE_CD_BIOS_GEARGRAFX_FRAME_TRACER_RAM_SUMMARY_H
#define OSS_PCE_CD_BIOS_GEARGRAFX_FRAME_TRACER_RAM_SUMMARY_H

#include <cstring>

// Cumulative, byte-free RAM accounting for the local frame tracer.  The
// classification and comparison helpers are shared with the frame tracer RAM
// header (single definition); this header adds only the per-region summary.
#include "geargrafx_frame_tracer_ram.h"

struct FrameTracerRamSummary {
    unsigned updates, first_frame, last_frame, min_offset, max_offset, max_changed;
    unsigned scsi_update_count, first_scsi_frame, last_scsi_frame;
    bool scsi_correlation_unknown;
};

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
