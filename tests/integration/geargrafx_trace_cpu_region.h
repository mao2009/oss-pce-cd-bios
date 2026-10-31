#ifndef OSS_PCE_CD_BIOS_GEARGRAFX_TRACE_CPU_REGION_H
#define OSS_PCE_CD_BIOS_GEARGRAFX_TRACE_CPU_REGION_H

// TRACE_CPU records the resolved bank at instruction time.  These ranges are
// the pinned core's fixed mappings (memory.cpp); do not infer a region from a
// later frame-boundary MPR snapshot.
inline const char* trace_cpu_region(unsigned bank) {
    if (bank >= 0x80 && bank <= 0x87) return "cdrom_ram";
    if (bank >= 0xf8 && bank <= 0xfb) return "working_ram";
    if (bank == 0xff) return "hardware";
    return "other";
}

#endif
