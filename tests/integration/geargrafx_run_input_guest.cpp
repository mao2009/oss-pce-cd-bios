// Synthetic HuCARD RUN guest probe.  It uses only Geargrafx's public controller
// scheduler and reports only the fixture-owned WRAM result block ($2200-$220f).
// This is a pinned-core diagnostic, not System Card BIOS execution.
// argv: rom cold|warm frames schedule (for example: 1p,3r,4p,6r; - means no events)
#include "geargrafx_core.h"
#include "media.h"
#include "memory.h"
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

namespace {
const unsigned kResultOffset = 0x200;
const unsigned kResultBytes = 16;

struct Event { unsigned frame; char action; };

bool parse_schedule(const char* text, std::vector<Event>* events) {
    if (std::string(text) == "-") return true;
    const char* p = text;
    while (*p) {
        char* end = nullptr;
        long frame = std::strtol(p, &end, 10);
        if (end == p || frame < 1 || (*end != 'p' && *end != 'r')) return false;
        events->push_back({(unsigned)frame, *end++});
        if (!*end) return true;
        if (*end++ != ',') return false;
        p = end;
    }
    return false;
}
}

int main(int argc, char** argv) {
    if (argc != 5) return 2;
    char* end = nullptr;
    long frame_count = std::strtol(argv[3], &end, 10);
    if (!end || *end || frame_count < 1 || frame_count > 120) return 2;
    const std::string cycle(argv[2]);
    if (cycle != "cold" && cycle != "warm") return 2;
    std::vector<Event> events;
    if (!parse_schedule(argv[4], &events)) return 2;
    for (const Event& event : events)
        if (event.frame > (unsigned)frame_count) return 2;

    std::ifstream input(argv[1], std::ios::binary);
    std::vector<u8> rom((std::istreambuf_iterator<char>(input)), {});
    if (rom.empty()) return 3;
    GeargrafxCore core;
    core.Init(nullptr);
    core.GetMedia()->SetCDROMType(GG_CDROM_STANDARD);
    if (!core.LoadHuCardFromBuffer(rom.data(), (int)rom.size(), "run-input-guest.pce")) return 3;
    if (cycle == "warm") core.ResetMedia(true);

    // Input is scheduled exclusively through the core's public API, including the
    // released baseline; no guest RAM, CPU state, or input internals are edited.
    core.KeyReleased(GG_CONTROLLER_1, GG_KEY_RUN);
    std::vector<u8> frame(1024 * 512 * 4);
    std::vector<s16> audio(65536);
    GeargrafxCore::GG_Debug_Run debug = {};
    for (unsigned number = 1; number <= (unsigned)frame_count; ++number) {
        for (const Event& event : events) if (event.frame == number) {
            if (event.action == 'p') core.KeyPressed(GG_CONTROLLER_1, GG_KEY_RUN);
            else core.KeyReleased(GG_CONTROLLER_1, GG_KEY_RUN);
        }
        int samples = 0;
        core.RunToVBlank(frame.data(), audio.data(), &samples, &debug, false);
    }

    const u8* result = core.GetMemory()->GetWorkingRAM() + kResultOffset;
    std::printf("RESULT {\"cycle\":\"%s\",\"frames\":%ld,\"result_hex\":\"", cycle.c_str(), frame_count);
    for (unsigned i = 0; i < kResultBytes; ++i) std::printf("%02x", result[i]);
    std::printf("\"}\n");
    return 0;
}
