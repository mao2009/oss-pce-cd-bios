// Original test frontend. External Geargrafx GPL-linked executable stays local.
#include "geargrafx_core.h"
#include "huc6280.h"
#include "memory.h"
#include "media.h"
#include "cdrom.h"
#include "scsi_controller.h"
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
    // "-" = no media: the ROM becomes the card with CD hardware forced on (same loader as
    // geargrafx_cdread_probe.cpp; LoadBiosFromBuffer alone never Reset()s, so nothing would run).
    const bool have_disc = std::string(argv[2]) != "-";
    bool loaded = have_disc ? core.LoadBiosFromBuffer(rom.data(), rom.size(), true) : true;
    if (std::string(argv[2]) == "--load-only") {
        std::printf("LOAD {\"accepted\":%s,\"input_bytes\":%zu,\"execution\":\"NOT_RUN\"}\n",
                    loaded ? "true" : "false", rom.size());
        return 0;
    }
    if (!loaded) return 3;
    if (have_disc) {
        if (!core.LoadMedia(argv[2])) return 4;
    } else {
        core.GetMedia()->SetCDROMType(GG_CDROM_STANDARD);
        if (!core.LoadHuCardFromBuffer(rom.data(), (int)rom.size(), "no-media-card.pce")) return 3;
        if (!core.GetMedia()->IsCDROMHardwareEnabled() || core.GetMedia()->IsCDROM()) return 7;
    }
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
    // timer: the guest programs the timer itself; the frontend only records checkpoints.
    if (irq != "none" && irq != "irq1" && irq != "irq2" && irq != "timer") return 2;
    const std::string entry_label = irq == "timer" ? "timer_entry" : "irq_entry";
    auto processor = core.GetHuC6280();
    auto line = [&](bool asserted) {
        if (irq == "irq1") processor->AssertIRQ1(asserted);
        else if (irq == "irq2") processor->AssertIRQ2(asserted);
    };
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
                    // timer: nothing is injected; the guest already armed its own timer before CLI.
                    observe("TRACE", irq == "timer" ? "timer_armed" : "irq_asserted");
                    line(true);
                    injected=true;
                } else if (entered && !seen.count("irq_resumed")) {
                    observe("TRACE", "irq_resumed"); seen.insert("irq_resumed");
                }
            }
            if (injected && labels.count(entry_label) && pc == labels[entry_label] && !entered) {
                line(false);
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
    std::printf("CAP {\"instruction_step\":%s,\"pc_before\":%u,\"pc_after\":%u,\"bios_loaded\":true,\"loader\":\"%s\",\"disc_given\":%s,\"cdrom_hardware\":%s,\"cdrom_media\":%s,\"card_ram_bytes\":%d,\"scsi_phase\":%d,\"cd_irq_active\":%u}\n",
                stepping ? "true" : "false", first_pc, cpu->PC->GetValue(),
                have_disc ? "LoadBiosFromBuffer(syscard)+LoadMedia(cue)" : "SetCDROMType(STANDARD)+LoadHuCardFromBuffer",
                have_disc ? "true" : "false",
                core.GetMedia()->IsCDROMHardwareEnabled() ? "true" : "false",
                core.GetMedia()->IsCDROM() ? "true" : "false", core.GetMedia()->GetCardRAMSize(),
                (int)*core.GetScsiController()->GetState()->PHASE, (unsigned)*core.GetCDROM()->GetState()->ACTIVE_IRQS);
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
