// Original test frontend. External Geargrafx GPL-linked executable stays local.
#include "geargrafx_core.h"
#include "huc6280.h"
#include "huc6270.h"
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
    // Diagnostic only. Geargrafx seeds its power-on A/X/Y/S/P from time(NULL). SetResetValue(0x04) sets A, X, Y, S and P
    // (S=4 too, which the guest overwrites before use). P=0x04 is I set, D clear, the same P the OBS check requires, so
    // the judge is unaffected. The setter is not separately tested against the pinned Geargrafx; its effect was seen only in
    // the 3-run loaded-row comparison and the gates. MPR power-on values stay random: pinning them to 0 broke the MA_COS
    // decimal-caller mutant (see commit 1f7bd2c).
    core.GetHuC6280()->SetResetValue(0x04);
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
    auto vdc = core.GetHuC6270_1()->GetState();
    const char* cycle = "cold"; unsigned long seq = 0; // diagnostic only: record order, never read by the judge
    auto observe = [&](const char* prefix, const char* phase) {
        std::printf("%s {\"phase\":\"%s\",\"cycle\":\"%s\",\"seq\":%lu,\"irq\":\"%s\",\"pc\":%u,\"a\":%u,\"x\":%u,\"y\":%u,\"p\":%u,\"sp\":%u,\"irr\":%u,\"idr\":%u,\"mpr\":[",
                    prefix, phase, cycle, seq++, irq.c_str(), cpu->PC->GetValue(), cpu->A->GetValue(), cpu->X->GetValue(),
                    cpu->Y->GetValue(), cpu->P->GetValue(), cpu->S->GetValue(), *cpu->IRR, *cpu->IDR);
        for (int i=0;i<8;i++) std::printf("%s%u", i ? "," : "", memory->GetMpr(i));
        std::printf("],\"vdc_ar\":%u,\"vdc_r0\":%u,\"physical_pc\":%u,\"work_ram_0200_hex\":\"",
                    (unsigned)(u8)*vdc->AR, (unsigned)vdc->R[0], memory->GetPhysicalAddress(cpu->PC->GetValue()));
        for (int i=0;i<64;i++) std::printf("%02x", memory->GetWorkingRAM()[0x200+i]);
        std::printf("\",\"stack_01f8_hex\":\"");
        for (int i=0;i<8;i++) std::printf("%02x", memory->GetWorkingRAM()[0x1f8+i]);
        std::printf("\"");
        // Issue #242: full 8 KiB work RAM, only on the three end-of-run observations that the handoff judge compares.
        const std::string name = phase;
        if (std::string(prefix) == "OBS" && (name == "loaded" || name == "cold" || name == "nonreturning")) {
            std::printf(",\"work_ram_hex\":\"");
            for (int i=0;i<0x2000;i++) std::printf("%02x", memory->GetWorkingRAM()[i]);
            std::printf("\"");
        }
        std::printf("}\n");
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
                    observe("TRACE", "irq_line_on"); line(true);
                    injected=true;
                } else if (entered && !seen.count("irq_resumed")) {
                    observe("TRACE", "irq_resumed"); seen.insert("irq_resumed");
                }
            }
            if (injected && labels.count(entry_label) && pc == labels[entry_label] && !entered) {
                observe("TRACE", "irq_entered"); line(false);
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
    std::printf("CAP {\"instruction_step\":%s,\"pc_before\":%u,\"pc_after\":%u,\"loader_used\":%s,\"loader\":\"%s\",\"disc_given\":%s,\"cdrom_hardware\":%s,\"cdrom_media\":%s,\"card_ram_bytes\":%d,\"scsi_phase\":%d,\"cd_irq_active\":%u}\n",
                stepping ? "true" : "false", first_pc, cpu->PC->GetValue(), have_disc ? "true" : "false",
                have_disc ? "LoadBiosFromBuffer(syscard)+LoadMedia(cue)" : "SetCDROMType(STANDARD)+LoadHuCardFromBuffer",
                have_disc ? "true" : "false",
                core.GetMedia()->IsCDROMHardwareEnabled() ? "true" : "false",
                core.GetMedia()->IsCDROM() ? "true" : "false", core.GetMedia()->GetCardRAMSize(),
                (int)*core.GetScsiController()->GetState()->PHASE, (unsigned)*core.GetCDROM()->GetState()->ACTIVE_IRQS);
    if (!stepping) return 6;
    steps(count); observe("OBS", "cold");
    steps(32); observe("OBS", "nonreturning");
    const bool cold_entered = entered, cold_resumed = seen.count("irq_resumed") > 0; // cold-run IRQ outcome, before warm reset
    core.ResetMedia(true);
    cycle = "warm"; seen.clear(); injected=false; entered=false;
    steps(count); observe("OBS", "warm");
    memory->SetMpr(7,1);
    // diagnostic stop reason; exit code and judge unchanged. irq_not_entered = an IRQ run never reached its entry.
    const bool irq_missed = irq != "none" && !cold_entered;
    std::printf("STOP {\"reason\":\"%s\",\"irq\":\"%s\",\"cold_irq_entered\":%s,\"cold_irq_resumed\":%s,\"records\":%lu}\n",
                irq_missed ? "irq_not_entered" : "completed", irq.c_str(), cold_entered ? "true" : "false",
                cold_resumed ? "true" : "false", seq);
    std::printf("BANK {\"mpr7\":1,\"physical_e000\":%u,\"byte_e000\":%u}\n",
                memory->GetPhysicalAddress(0xe000), memory->Read(0xe000));
    return 0;
}
