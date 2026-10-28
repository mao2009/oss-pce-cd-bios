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

#ifdef CDRA_VFS_MARK
extern "C" void cdra_mark(const char* name);  // defined in cdra_vfs_shim.cpp
#endif
static const unsigned kResultOffset = 0x300;   // work RAM offset of CPU $2300
static const unsigned kBufferOffset = 0x800;   // CPU $2800
static const unsigned kBufferBytes = 6144;
static const long kHaltConfirmSteps = 64;      // keep equal to tools/build_cdread.py HALT_CONFIRM_STEPS (the judge cross-checks)
static const int kPhaseDataIn = 4;             // ScsiController::SCSI_PHASE_DATA_IN

int main(int argc, char** argv) {
    // usage: probe rom [card:]cue|- labels max_steps [eject-in-data] [cut-after-arrival=N]   ("-" = no media)
    // "card:" (Issue #144 paired control) uses ONE loader with or without a disc; see below.
    // cut-after-arrival=N stops the run exactly N instructions after the PC first lands on a halt label
    // (arrival times differ between processes because Geargrafx randomises the initial read phase).
    if (argc < 5 || argc > 7) return 2;
    bool eject_in_data = false;
    long cut_after_arrival = -1;
    for (int i = 5; i < argc; i++) {
        std::string option = argv[i];
        if (option == "eject-in-data") eject_in_data = true;
        else if (option.rfind("cut-after-arrival=", 0) == 0) cut_after_arrival = std::atol(option.c_str() + 18);
        else return 2;
    }
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
    // Halt labels: the #5 diagnostic's three, or the #143 reset entry's two (#144 observation point).
    std::vector<std::string> halts = {"cd_halt_ok", "cd_halt_fail", "cd_unexpected_interrupt"};
    if (labels.count("reset_ready")) halts = {"reset_ready", "reset_unexpected_interrupt"};
    // Issue #501: the DEV-only internal path's media-check halt (absent from every other label file).
    if (labels.count("dev_media_halt")) halts.push_back("dev_media_halt");
    for (const auto& needed : halts)
        if (!labels.count(needed)) return 2;

    GeargrafxCore core;
    core.Init(nullptr);
    std::string disc = argv[2];
    bool paired = disc.rfind("card:", 0) == 0;
    if (paired) disc = disc.substr(5);
    bool have_disc = disc != "-";
    if (paired) {
        // Paired control: both variants take exactly these steps; the only difference is whether a CUE
        // is inserted into the drive (CdRomMedia) after the card load (which clears the drive) and
        // before the cold reset. Media::IsCDROM stays false in both, so CRC/mapper/card RAM are equal.
        core.GetMedia()->SetCDROMType(GG_CDROM_STANDARD);
        if (!core.LoadHuCardFromBuffer(rom.data(), (int)rom.size(), "paired-card.pce")) return 3;
        if (have_disc && !core.GetCDROMMedia()->LoadCueFromFile(disc.c_str(), true)) return 4;
        core.ResetMedia(false);
        if (!core.GetMedia()->IsCDROMHardwareEnabled() || core.GetMedia()->IsCDROM()) return 7;
    } else if (have_disc) {
        if (!core.LoadBiosFromBuffer(rom.data(), rom.size(), true)) return 3;
        if (!core.LoadMedia(disc.c_str())) return 4;
    } else {
        // No media. LoadBiosFromBuffer alone never calls GeargrafxCore::Reset() (only LoadMedia /
        // LoadHuCardFromBuffer do), so Media stays not-ready and RunToVBlank refuses to run. Instead
        // load the ROM as the card itself with the CD unit forced on: Media::GatherMediaInfo enables
        // CD hardware when is_cdrom || cdrom_type != AUTO (the libretro "geargrafx_cdrom_type" option).
        // The drive then has no image, so every media-dependent SCSI command sees an empty drive.
        core.GetMedia()->SetCDROMType(GG_CDROM_STANDARD);
        if (!core.LoadHuCardFromBuffer(rom.data(), (int)rom.size(), "no-media-card.pce")) return 3;
        if (!core.GetMedia()->IsCDROMHardwareEnabled() || core.GetMedia()->IsCDROM()) return 7;
    }

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
    if (memory->Read(first_pc) != 0x78) return 5;  // every probe begins with SEI
    one_instruction();
    bool stepping = cpu->PC->GetValue() == first_pc + 1;
    std::printf("CAP {\"instruction_step\":%s,\"bios_loaded\":%s,\"loader\":\"%s\",\"cdrom_hardware\":%s,\"cdrom_media\":%s,\"disc_given\":%s,\"drive_ready\":%s}\n",
                stepping ? "true" : "false", have_disc && !paired ? "true" : "false",
                paired ? "paired:SetCDROMType(STANDARD)+LoadHuCardFromBuffer+ResetMedia(false)"
                       : have_disc ? "LoadBiosFromBuffer(syscard)+LoadMedia(cue)" : "SetCDROMType(STANDARD)+LoadHuCardFromBuffer",
                core.GetMedia()->IsCDROMHardwareEnabled() ? "true" : "false",
                core.GetMedia()->IsCDROM() ? "true" : "false", have_disc ? "true" : "false",
                core.GetCDROMMedia()->IsReady() ? "true" : "false");
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
        // A halt only counts after kHaltConfirmSteps consecutive instructions were executed with the PC
        // still on the SAME halt label (BRA self-loop). Leaving the label or running out of budget
        // resets/abandons the confirmation: the result is then "budget", never a success reason.
        long arrival = -1;           // instruction count when the PC first landed on the current halt label
        long confirmed_steps = 0;    // consecutive instructions executed while staying on that label
        unsigned halt_pc = 0;
        bool halt_confirmed = false;
        std::string reason = "budget";
        long step = 0;              // instructions executed in this run
        auto halt_name = [&](unsigned pc) -> const char* {
            for (const auto& name : halts)
                if (pc == labels[name]) return name.c_str();
            return nullptr;
        };
        while (step < max_steps) {
            unsigned pc = cpu->PC->GetValue();
            for (const auto& item : labels)
                if (pc == item.second && !seen.count(item.first)) {
                    seen.insert(item.first);
                    regs("HIT", item.first.c_str(), step);
                    hex("buffer_head_hex", kBufferOffset, 32);
                    std::printf("}\n");
                }
            if (halt_name(pc)) {
                if (pc != halt_pc) { halt_pc = pc; arrival = step; confirmed_steps = 0; }
            } else {
                halt_pc = 0; arrival = -1; confirmed_steps = 0;
            }
            if (cut_after_arrival >= 0 && arrival >= 0 && step - arrival >= cut_after_arrival) break;  // test budget cut
            one_instruction();
            step++;
            if (halt_pc && cpu->PC->GetValue() == halt_pc) {
                confirmed_steps++;
                if (confirmed_steps >= kHaltConfirmSteps) {
                    halt_confirmed = true;
                    reason = halt_name(halt_pc);
                    break;
                }
            } else if (halt_pc) {
                halt_pc = 0; arrival = -1; confirmed_steps = 0;  // left the halt loop mid-confirmation
            }
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
        std::printf(",\"reason\":\"%s\",\"halt_confirmed\":%s,\"halted_after_step\":%ld,\"halt_confirmation_steps\":%ld,\"halt_confirmation_required\":%ld",
                    reason.c_str(), halt_confirmed ? "true" : "false", arrival, confirmed_steps, kHaltConfirmSteps);
        hex("result_hex", kResultOffset, 32);
        hex("buffer_hex", kBufferOffset, kBufferBytes);
        std::printf("}\n");
    };
#ifdef CDRA_VFS_MARK  // read-ahead A/B harness only: splits load-time VFS reads from guest-driven reads
    cdra_mark("guest_start");
#endif
    run_once(false);
    core.ResetMedia(true);
    run_once(true);
    return 0;
}
