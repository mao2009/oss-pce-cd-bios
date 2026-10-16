// Local-only GPL-linked Geargrafx reference BIOS reset observer.
// Captures a real CPU checkpoint only. DOES NOT invoke a BIOS API.
#include "geargrafx_core.h"
#include "huc6280.h"
#include "memory.h"
#include <fstream>
#include <iterator>
#include <vector>
#include <cstdio>
#include <cstdlib>
#include <string>

int main(int argc, char** argv) {
    if (argc != 5) return 2; // bios, synthetic CUE, step-budget, expected checkpoint PC
    std::ifstream f(argv[1], std::ios::binary);
    if (!f) return 2;
    std::vector<u8> bios((std::istreambuf_iterator<char>(f)), {});
    char* end = nullptr;
    unsigned long budget = std::strtoul(argv[3], &end, 10);
    if (!end || *end || budget < 1 || budget > 10000000UL) return 2;
    unsigned long checkpoint = std::string(argv[4]) == "auto" ? 65536UL : std::strtoul(argv[4], &end, 0);
    if (checkpoint != 65536UL && (!end || *end || checkpoint > 65535)) return 2;
    GeargrafxCore core;
    core.Init(nullptr);
    if (!core.LoadBiosFromBuffer(bios.data(), bios.size(), true)) return 3;
    if (!core.LoadMedia(argv[2])) return 4;
    if (!core.GetMedia()->IsCDROMHardwareEnabled()) return 5;
    auto cpu = core.GetHuC6280()->GetState();
    auto memory = core.GetMemory();
    std::vector<u8> frame(1024 * 512 * 4);
    std::vector<s16> audio(65536);
    GeargrafxCore::GG_Debug_Run debug = {};
    debug.step_debugger = true;
    unsigned long steps = 0;
    bool reached = false;
    while (!reached && steps < budget) {
        int samples = 0;
        core.RunToVBlank(frame.data(), audio.data(), &samples, &debug, false);
        ++steps;
        reached = checkpoint == 65536UL || cpu->PC->GetValue() == checkpoint;
    }
    if (!reached) return 6; // deadline reached: no observation claim
    // The Geargrafx core logs to stdout; emit the observation on stderr so the
    // adapter can parse it without ever touching BIOS bytes.
    std::fprintf(stderr, "{\"checkpoint\":\"pc_checkpoint\",\"instructions_executed\":%lu,"
                 "\"registers\":{\"a\":%u,\"x\":%u,\"y\":%u,\"p\":%u,\"sp\":%u,\"pc\":%u},\"mpr\":[",
                 steps, cpu->A->GetValue(), cpu->X->GetValue(), cpu->Y->GetValue(),
                 cpu->P->GetValue(), cpu->S->GetValue(), cpu->PC->GetValue());
    for (int i = 0; i < 8; ++i)
        std::fprintf(stderr, "%s%u", i ? "," : "", memory->GetMpr(i));
    std::fprintf(stderr, "]}\n");
    return 0;
}
