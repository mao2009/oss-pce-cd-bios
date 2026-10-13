// Original test frontend for the Issue #5 CD read diagnostic. External Geargrafx
// GPL-linked executable stays local and is never distributed.
// Runs the guest HuC6280 until it reaches a halt label (or the step budget), then
// dumps CPU/MPR/CD/SCSI state and the guest RAM that the guest wrote. It never
// writes guest RAM, never services the CD for the guest and never edits registers.
// The only environment injection is the optional, clearly logged "eject-in-data"
// drive-side abort (media-eject notification) used by a negative test.
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

static const unsigned kResultOffset = 0x300;   // work RAM offset of CPU $2300
static const unsigned kBufferOffset = 0x800;   // CPU $2800
static const unsigned kBufferBytes = 6144;
static const int kPhaseDataIn = 4;             // ScsiController::SCSI_PHASE_DATA_IN

int main(int argc, char** argv) {
    // usage: probe rom cue|- labels max_steps [eject-in-data]
    if (argc != 5 && argc != 6) return 2;
    bool eject_in_data = argc == 6 && std::string(argv[5]) == "eject-in-data";
    if (argc == 6 && !eject_in_data) return 2;
    std::ifstream input(argv[1], std::ios::binary);
    if (!input) return 2;
    std::vector<u8> rom((std::istreambuf_iterator<char>(input)), {});
    long max_steps = std::atol(argv[4]);
    if (max_steps < 1 || max_steps > 200000000L) return 2;
    std::map<std::string, unsigned> labels;
    {
        std::ifstream file(argv[3]);
        if (!file) return 2;
        std::string name; unsigned address;
        while (file >> name >> std::hex >> address) labels[name] = address;
    }
    for (const char* needed : {"cd_halt_ok", "cd_halt_fail", "cd_unexpected_interrupt"})
        if (!labels.count(needed)) return 2;

    GeargrafxCore core;
    core.Init(nullptr);
    if (!core.LoadBiosFromBuffer(rom.data(), rom.size(), true)) return 3;
    bool have_disc = std::string(argv[2]) != "-";
    if (have_disc && !core.LoadMedia(argv[2])) return 4;

    auto processor = core.GetHuC6280();
    auto cpu = processor->GetState();
    auto memory = core.GetMemory();
    auto scsi = core.GetScsiController()->GetState();
    auto cdrom = core.GetCDROM()->GetState();
    std::vector<u8> frame(1024 * 512 * 4);
    std::vector<s16> audio(65536);
    GeargrafxCore::GG_Debug_Run debug = {};
    debug.step_debugger = true;
    auto one_instruction = [&]() {
        int samples = 0;
        core.RunToVBlank(frame.data(), audio.data(), &samples, &debug, false);
    };

    unsigned first_pc = cpu->PC->GetValue();
    if (memory->Read(first_pc) != 0x78) {  // every probe begins with SEI
        std::printf("BLOCK {\"reason\":\"guest ROM not executed at reset\",\"disc_given\":%s,\"first_pc\":%u,\"first_opcode\":%u,\"cdrom_hardware\":%s}\n",
                    have_disc ? "true" : "false", first_pc, memory->Read(first_pc),
                    core.GetMedia()->IsCDROMHardwareEnabled() ? "true" : "false");
        return 0;
    }
    one_instruction();
    bool stepping = cpu->PC->GetValue() == first_pc + 1;
    std::printf("CAP {\"instruction_step\":%s,\"bios_loaded\":true,\"cdrom_hardware\":%s,\"cdrom_media\":%s,\"disc_given\":%s}\n",
                stepping ? "true" : "false",
                core.GetMedia()->IsCDROMHardwareEnabled() ? "true" : "false",
                core.GetMedia()->IsCDROM() ? "true" : "false", have_disc ? "true" : "false");
    if (!stepping) return 6;

    const char* cycle = "cold";
    auto regs = [&](const char* tag, const char* name, long step) {
        std::printf("%s {\"name\":\"%s\",\"cycle\":\"%s\",\"step\":%ld,\"pc\":%u,\"a\":%u,\"x\":%u,\"y\":%u,\"p\":%u,\"sp\":%u,\"mpr\":[",
                    tag, name, cycle, step, cpu->PC->GetValue(), cpu->A->GetValue(), cpu->X->GetValue(),
                    cpu->Y->GetValue(), cpu->P->GetValue(), cpu->S->GetValue());
        for (int i = 0; i < 8; i++) std::printf("%s%u", i ? "," : "", memory->GetMpr(i));
        std::printf("],\"scsi_phase\":%d,\"scsi_signals\":%u,\"load_sector\":%u,\"load_count\":%u,\"cd_irq_active\":%u,\"cd_irq_enabled\":%u",
                    (int)*scsi->PHASE, (unsigned)*scsi->SIGNALS, (unsigned)*scsi->LOAD_SECTOR,
                    (unsigned)*scsi->LOAD_SECTOR_COUNT, (unsigned)*cdrom->ACTIVE_IRQS, (unsigned)*cdrom->ENABLED_IRQS);
    };
    auto hex = [&](const char* key, unsigned offset, unsigned length) {
        std::printf(",\"%s\":\"", key);
        for (unsigned i = 0; i < length; i++) std::printf("%02x", memory->GetWorkingRAM()[offset + i]);
        std::printf("\"");
    };

    auto run_once = [&](bool warm) {
        cycle = warm ? "warm" : "cold";
        std::set<std::string> seen;
        int last_phase = (int)*scsi->PHASE;
        int events = 0;
        bool ejected = false;
        long halted_after = -1;
        std::string reason = "budget";
        long step = 0;
        for (; step < max_steps; step++) {
            unsigned pc = cpu->PC->GetValue();
            for (const auto& item : labels)
                if (pc == item.second && !seen.count(item.first)) {
                    seen.insert(item.first);
                    regs("HIT", item.first.c_str(), step);
                    hex("buffer_head_hex", kBufferOffset, 32);
                    std::printf("}\n");
                }
            if (pc == labels["cd_halt_ok"] || pc == labels["cd_halt_fail"] || pc == labels["cd_unexpected_interrupt"]) {
                if (halted_after < 0) {
                    halted_after = step;
                    reason = pc == labels["cd_halt_ok"] ? "cd_halt_ok" : pc == labels["cd_halt_fail"] ? "cd_halt_fail" : "cd_unexpected_interrupt";
                }
                if (step - halted_after >= 64) break;  // prove the halt is stable, not a passing PC
            }
            one_instruction();
            if ((int)*scsi->PHASE != last_phase && events < 64) {
                last_phase = (int)*scsi->PHASE;
                events++;
                regs("PHASE", "scsi_phase_change", step);
                std::printf("}\n");
                if (eject_in_data && !ejected && last_phase == kPhaseDataIn) {
                    ejected = true;  // drive-side abort: media-eject notification resets the SCSI bus
                    core.GetCDROM()->NotifyMediaEjected();
                    std::printf("INJECT {\"cycle\":\"%s\",\"step\":%ld,\"event\":\"media_ejected_notification\"}\n", cycle, step);
                }
            }
        }
        regs("RUN", reason.c_str(), step);
        std::printf(",\"reason\":\"%s\",\"halted_after_step\":%ld", reason.c_str(), halted_after);
        hex("result_hex", kResultOffset, 32);
        hex("buffer_hex", kBufferOffset, kBufferBytes);
        std::printf("}\n");
    };
    run_once(false);
    core.ResetMedia(true);
    run_once(true);
    return 0;
}
