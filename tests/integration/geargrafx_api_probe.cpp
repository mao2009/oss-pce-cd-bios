// Original test frontend. Links the external GPL Geargrafx core; never firmware.
// Combined executable stays local and is not uploaded or licensed as MIT-only.
#include "geargrafx_core.h"
#include "huc6280.h"
#include "memory.h"
#include <fstream>
#include <iterator>
#include <vector>
#include <cstdio>

int main(int argc, char** argv) {
    if (argc != 3) return 2;
    std::ifstream input(argv[1], std::ios::binary);
    std::vector<u8> rom((std::istreambuf_iterator<char>(input)), {});
    GeargrafxCore core;
    core.Init(nullptr);
    if (!core.LoadBiosFromBuffer(rom.data(), rom.size(), true)) return 3;
    if (!core.LoadMedia(argv[2])) return 4;
    auto cpu = core.GetHuC6280()->GetState();
    auto memory = core.GetMemory();
    auto observe = [&](const char* phase) {
        std::printf("OBS {\"phase\":\"%s\",\"pc\":%u,\"x\":%u,\"mpr\":[", phase,
                    cpu->PC->GetValue(), cpu->X->GetValue());
        for (int i=0;i<8;i++) std::printf("%s%u", i ? "," : "", memory->GetMpr(i));
        std::printf("],\"physical_pc\":%u}\n", memory->GetPhysicalAddress(cpu->PC->GetValue()));
    };
    std::vector<u8> frame(1024*512*4);
    std::vector<s16> audio(65536);
    GeargrafxCore::GG_Debug_Run debug = {};
    debug.step_debugger = true;
    auto steps = [&](int count) {
        for (int i=0;i<count;i++) { int samples=0; core.RunToVBlank(frame.data(),audio.data(),&samples,&debug,false); }
    };
    observe("loaded");
    steps(16); observe("cold");
    steps(32); observe("nonreturning");
    core.ResetMedia(true);
    steps(16); observe("warm");
    memory->SetMpr(7,1);
    std::printf("BANK {\"mpr7\":1,\"physical_e000\":%u,\"byte_e000\":%u}\n",
                memory->GetPhysicalAddress(0xe000), memory->Read(0xe000));
    return 0;
}
