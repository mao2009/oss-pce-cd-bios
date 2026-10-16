// Local-only, opt-in reference BIOS JSR experiment: explicitly injected guest RAM
// code. This is NOT a normal BIOS calling environment or full ABI validation.
// Never distribute the GPL-linked Geargrafx executable or private BIOS bytes.
#include "geargrafx_core.h"
#include "huc6280.h"
#include "memory.h"
#include <fstream>
#include <iterator>
#include <vector>
#include <cstdio>
#include <cstdlib>
#include <cstring>

int main(int argc, char** argv) {
    // bios-file synthetic-cue slot-decimal instructions; single allowlisted slot 0x1E.
    if (argc != 5) return 2;
    char* end = nullptr;
    unsigned long slot = std::strtoul(argv[3], &end, 0);
    if (!end || *end || slot != 0x1e) return 2;
    unsigned long budget = std::strtoul(argv[4], &end, 10);
    if (!end || *end || budget < 3 || budget > 1000000UL) return 2;
    std::ifstream file(argv[1], std::ios::binary);
    if (!file) return 3;
    std::vector<u8> bios((std::istreambuf_iterator<char>(file)), {});
    GeargrafxCore core;
    core.Init(nullptr);
    if (!core.LoadBiosFromBuffer(bios.data(), bios.size(), true)) return 4;
    if (!core.LoadMedia(argv[2])) return 5;
    if (!core.GetMedia()->IsCDROMHardwareEnabled()) return 6;
    auto cpu = core.GetHuC6280()->GetState();
    auto memory = core.GetMemory();
    // This explicitly constructs a test-only guest CPU context rather than
    // claiming that normal boot arrives here. HuC6280 RAM at MPR1=$F8.
    memory->SetMpr(1, 0xf8);
    memory->SetMpr(7, 0);
    auto ram = memory->GetWorkingRAM();
    const unsigned entry = 0xe000 + 3 * slot;
    // 2200 JSR $E05A; 2203 BRA $2203. All three bytes really fetched by CPU.
    ram[0x200] = 0x20;
    ram[0x201] = entry & 255;
    ram[0x202] = entry >> 8;
    ram[0x203] = 0x80;
    ram[0x204] = 0xfe;
    cpu->PC->SetValue(0x2200);
    cpu->S->SetValue(0xff);
    cpu->A->SetValue(0);
    cpu->X->SetValue(0);
    cpu->Y->SetValue(0);
    std::vector<u8> frame(1024 * 512 * 4);
    std::vector<s16> audio(65536);
    GeargrafxCore::GG_Debug_Run debug = {};
    debug.step_debugger = true;
    auto step = [&]() {
        int samples = 0;
        core.RunToVBlank(frame.data(), audio.data(), &samples, &debug, false);
    };
    auto snap = [&](const char* tag, unsigned long step_count) {
        std::printf("%s {\"step\":%lu,\"a\":%u,\"x\":%u,\"y\":%u,\"p\":%u,\"sp\":%u,\"pc\":%u,\"mpr\":[",
                    tag, step_count, cpu->A->GetValue(), cpu->X->GetValue(),
                    cpu->Y->GetValue(), cpu->P->GetValue(), cpu->S->GetValue(), cpu->PC->GetValue());
        for (int i = 0; i < 8; ++i)
            std::printf("%s%u", i ? "," : "", memory->GetMpr(i));
        std::printf("]}\n");
    };
    if (cpu->PC->GetValue() != 0x2200 || memory->Read(0x2200) != 0x20
        || memory->Read(0x2201) != (entry & 255)
        || memory->Read(0x2202) != (entry >> 8)) return 7;
    snap("BEFORE", 0);
    step(); // actual guest JSR instruction
    if (cpu->PC->GetValue() != entry || cpu->S->GetValue() != 0xfd) return 8;
    snap("ENTRY", 1);
    unsigned long executed = 1;
    bool returned = false;
    while (executed < budget) {
        step();
        ++executed;
        if (cpu->PC->GetValue() == 0x2203) {
            returned = true;
            break;
        }
    }
    if (!returned) return 9; // no successful result if budget exhausted
    snap("AFTER", executed);
    // If normal control returns but MPR/stack are unexpected the adapter reports
    // an experiment, not a compatible BIOS contract. Do not read proprietary ROM.
    return 0;
}
