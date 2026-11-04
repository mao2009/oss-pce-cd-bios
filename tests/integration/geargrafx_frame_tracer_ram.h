#ifndef OSS_PCE_CD_BIOS_GEARGRAFX_FRAME_TRACER_RAM_H
#define OSS_PCE_CD_BIOS_GEARGRAFX_FRAME_TRACER_RAM_H

// Metadata-only RAM classifications from the pinned Geargrafx Memory map:
// WRAM $F8-$FB, CD-ROM RAM $80-$87, and the two Card RAM layouts.
enum FrameTracerRamKind { FRAME_TRACER_WRAM, FRAME_TRACER_CDROM_RAM, FRAME_TRACER_CARD_RAM };

struct FrameTracerRamMapping {
    const char* name;
    unsigned size;
    unsigned first_bank;
    unsigned last_bank;
};

struct FrameTracerRamDelta {
    unsigned changed;
    unsigned first;
    unsigned last;
};

inline bool FrameTracerMapRam(FrameTracerRamKind kind, unsigned size, unsigned card_start,
                               FrameTracerRamMapping* mapping) {
    if (kind == FRAME_TRACER_WRAM && (size == 0x2000 || size == 0x8000))
        *mapping = {"wram", size, 0xf8, 0xfb};
    else if (kind == FRAME_TRACER_CDROM_RAM && size == 0x10000)
        *mapping = {"cdrom_ram", size, 0x80, 0x87};
    // Geargrafx maps the 32 KiB Card RAM layout repeatedly across $40-$5F.
    else if (kind == FRAME_TRACER_CARD_RAM && size == 0x8000 && card_start == 0x40)
        *mapping = {"card_ram", size, 0x40, 0x5f};
    else if (kind == FRAME_TRACER_CARD_RAM && size == 0x30000 && card_start == 0x68)
        *mapping = {"card_ram", size, 0x68, 0x7f};
    else
        return false;
    return true;
}

inline FrameTracerRamDelta FrameTracerCompareRam(const unsigned char* previous,
                                                  const unsigned char* current, unsigned size) {
    FrameTracerRamDelta delta = {0, size, 0};
    for (unsigned offset = 0; offset != size; ++offset) {
        if (previous[offset] != current[offset]) {
            ++delta.changed;
            if (delta.first == size)
                delta.first = offset;
            delta.last = offset;
        }
    }
    return delta;
}

#endif
