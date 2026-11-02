#include "geargrafx_trace_cpu_region.h"

#include <cassert>
#include <cstring>
#include <initializer_list>

int main() {
    assert(std::strcmp(trace_cpu_region(0x80), "cdrom_ram") == 0);
    assert(std::strcmp(trace_cpu_region(0x87), "cdrom_ram") == 0);
    assert(std::strcmp(trace_cpu_region(0xf8), "working_ram") == 0);
    assert(std::strcmp(trace_cpu_region(0xfb), "working_ram") == 0);
    assert(std::strcmp(trace_cpu_region(0xff), "hardware") == 0);
    for (unsigned bank : {0x00u, 0x7fu, 0x88u, 0xf7u, 0xfcu})
        assert(std::strcmp(trace_cpu_region(bank), "other") == 0);
}
