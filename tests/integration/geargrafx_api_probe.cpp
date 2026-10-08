// Original test frontend. External Geargrafx GPL-linked executable stays local.
#include "geargrafx_core.h"
#include "huc6280.h"
#include "memory.h"
#include "media.h"
#include <fstream>
#include <iterator>
#include <vector>
#include <map>
#include <set>
#include <string>
#include <cstdio>
#include <cstdlib>

int main(int argc, char** argv) {
    if (argc < 3 || argc > 6) return 2;
    std::ifstream input(argv[1], std::ios::binary);
    if (!input) return 2;
    std::vector<u8> rom((std::istreambuf_iterator<char>(input)), {});
    GeargrafxCore core;
    core.Init(nullptr);
    bool loaded = core.LoadBiosFromBuffer(rom.data(), rom.size(), true);
    if (std::string(argv[2]) == "--load-only") {
        std::printf("LOAD {\"accepted\":%s,\"input_bytes\":%zu,\"execution\":\"NOT_RUN\"}\n",
                    loaded ? "true" : "false", rom.size());
        return 0;
    }
    if (!loaded) return 3;
    if (!core.LoadMedia(argv[2])) return 4;
    int count = argc > 3 ? std::atoi(argv[3]) : 16;
    if (count < 1 || count > 2048) return 2;
    std::map<std::string, unsigned> labels;
    if (argc > 4) {
        std::ifstream file(argv[4]);
        if (!file) return 2;
        std::string name; unsigned address;
        while (file >> name >> std::hex >> address) labels[name] = address;
    }
    std::string irq = argc > 5 ? argv[5] : "none";
    if (irq != "none" && irq != "irq1" && irq != "irq2") return 2;
    auto processor = core.GetHuC6280();
    auto cpu = processor->GetState();
    auto memory = core.GetMemory();
    const char* cycle = "cold";
    auto observe = [&](const char* prefix, const char* phase) {
        std::printf("%s {\"phase\":\"%s\",\"cycle\":\"%s\",\"pc\":%u,\"a\":%u,\"x\":%u,\"y\":%u,\"p\":%u,\"sp\":%u,\"irr\":%u,\"idr\":%u,\"mpr\":[",
                    prefix, phase, cycle, cpu->PC->GetValue(), cpu->A->GetValue(), cpu->X->GetValue(),
                    cpu->Y->GetValue(), cpu->P->GetValue(), cpu->S->GetValue(), *cpu->IRR, *cpu->IDR);
        for (int i=0;i<8;i++) std::printf("%s%u", i ? "," : "", memory->GetMpr(i));
        std::printf("],\"physical_pc\":%u,\"work_ram_0200_hex\":\"", memory->GetPhysicalAddress(cpu->PC->GetValue()));
        for (int i=0;i<64;i++) std::printf("%02x", memory->GetWorkingRAM()[0x200+i]);
        std::printf("\",\"stack_01f8_hex\":\"");
        for (int i=0;i<8;i++) std::printf("%02x", memory->GetWorkingRAM()[0x1f8+i]);
        std::printf("\"}\n");
    };
    std::vector<u8> frame(1024*512*4);
    std::vector<s16> audio(65536);
    GeargrafxCore::GG_Debug_Run debug = {};
    debug.step_debugger = true;
    auto one_instruction = [&]() {
        int samples=0;
        core.RunToVBlank(frame.data(),audio.data(),&samples,&debug,false);
    };
    std::set<std::string> seen;
    bool injected=false, entered=false;
    auto steps = [&](int amount) {
        for (int i=0;i<amount;i++) {
            unsigned pc = cpu->PC->GetValue();
            for (const auto& item : labels) {
                if (pc == item.second && !seen.count(item.first)) {
                    observe("TRACE", item.first.c_str()); seen.insert(item.first);
                }
            }
            if (irq != "none" && labels.count("irq_wait") && pc == labels["irq_wait"]) {
                if (!injected) {
                    observe("TRACE", "irq_asserted");
                    if (irq == "irq1") processor->AssertIRQ1(true); else processor->AssertIRQ2(true);
                    injected=true;
                } else if (entered && !seen.count("irq_resumed")) {
                    observe("TRACE", "irq_resumed"); seen.insert("irq_resumed");
                }
            }
            if (injected && labels.count("irq_entry") && pc == labels["irq_entry"] && !entered) {
                if (irq == "irq1") processor->AssertIRQ1(false); else processor->AssertIRQ2(false);
                entered=true;
            }
            one_instruction();
        }
    };
    observe("OBS", "loaded");
    unsigned first_pc = cpu->PC->GetValue();
    if (memory->Read(first_pc) != 0x78) return 5; // all execution probes begin SEI
    one_instruction();
    bool stepping = cpu->PC->GetValue() == first_pc + 1;
    std::printf("CAP {\"instruction_step\":%s,\"pc_before\":%u,\"pc_after\":%u,\"bios_loaded\":true,\"cdrom_hardware\":%s,\"cdrom_media\":%s,\"card_ram_bytes\":%d}\n",
                stepping ? "true" : "false", first_pc, cpu->PC->GetValue(),
                core.GetMedia()->IsCDROMHardwareEnabled() ? "true" : "false",
                core.GetMedia()->IsCDROM() ? "true" : "false", core.GetMedia()->GetCardRAMSize());
    if (!stepping) return 6;
    steps(count); observe("OBS", "cold");
    steps(32); observe("OBS", "nonreturning");
    core.ResetMedia(true);
    cycle = "warm"; seen.clear(); injected=false; entered=false;
    steps(count); observe("OBS", "warm");
    memory->SetMpr(7,1);
    std::printf("BANK {\"mpr7\":1,\"physical_e000\":%u,\"byte_e000\":%u}\n",
                memory->GetPhysicalAddress(0xe000), memory->Read(0xe000));
    return 0;
}
