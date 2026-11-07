// Verification-only API-entry observer for Issue #635 (DEV-only measurement).
// Single-steps the pinned Geargrafx core and records the first control transfers
// from the IPL payload (RAM $3000-$DFFF, the PUBLIC load range) into the BIOS
// region ($E000-$FFFF).
// It never writes guest RAM, never edits registers, never injects interrupts and
// never reads MMIO: state is read via pinned-core state pointers only. Stack
// return validation is done internally: the probe emits only a boolean match
// (return_matches_caller) plus a status (MATCH/MISMATCH/UNKNOWN), never stack
// bytes or the return address value.
//
// Output contract (commercial fail-closed at the C++ boundary):
// - APICALL: structural values only (addresses, slot, SP delta, entry regs,
//   MPR, IDR/IRR, return booleans). No guest RAM bytes in either mode.
// - RUN/HIT dumps (result_hex/buffer_hex/buffer_head_hex): SYNTHETIC mode only.
//   COMMERCIAL mode generates no RAM dumps at all (no hex() calls), so nothing
//   can leak via stdout/logs even before Python filtering.
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
#include <cstring>
#include <cstdio>
#include <cstdlib>

static const unsigned kResultOffset = 0x300;   // CPU $2300 in WRAM array
static const unsigned kBufferOffset = 0x800;   // CPU $2800
static const unsigned kBufferBytes = 6144;
static const long kHaltConfirmSteps = 64;
static const unsigned kTableBase = 0xE000;
static const unsigned kTableEnd = 0xE0F3;      // 81 slots * 3 bytes ($E000..$E0F2 inclusive)
static const unsigned kPayloadLo = 0x3000;
// PUBLIC IPL loads can reach $DFFF (MPR2..6 regions); watch the whole RAM
// load range up to the BIOS table at $E000. Loader code itself lives at
// $F000+ (BIOS region), so it is never counted as a payload caller.
static const unsigned kPayloadHi = 0xE000;     // exclusive

int main(int argc, char** argv) {
    // argv: probe rom cue labels max_steps mode (synthetic|commercial)
    if (argc != 6) return 2;
    std::string mode = argv[5];
    bool commercial = false;
    if (mode == "synthetic") commercial = false;
    else if (mode == "commercial") commercial = true;
    else return 2;
    std::ifstream input(argv[1], std::ios::binary);
    if (!input) return 2;
    std::vector<u8> rom((std::istreambuf_iterator<char>(input)), {});
    std::string disc = argv[2];
    bool have_disc = disc != "-";
    long max_steps = std::atol(argv[4]);
    if (max_steps < 1 || max_steps > 200000000L) return 2;
    std::map<std::string, unsigned> labels;
    {
        std::ifstream file(argv[3]);
        if (!file) return 2;
        std::string name; unsigned address;
        while (file >> name >> std::hex >> address) labels[name] = address;
    }
    std::vector<std::string> halts = {"cd_halt_ok", "cd_halt_fail", "cd_unexpected_interrupt"};
    if (labels.count("api_unimplemented")) halts.push_back("api_unimplemented");
    for (const auto& needed : halts)
        if (!labels.count(needed)) return 2;

    GeargrafxCore core;
    core.Init(nullptr);
    if (have_disc) {
        if (!core.LoadBiosFromBuffer(rom.data(), rom.size(), true)) return 3;
        if (!core.LoadMedia(disc.c_str())) return 4;
    } else {
        core.GetMedia()->SetCDROMType(GG_CDROM_STANDARD);
        if (!core.LoadHuCardFromBuffer(rom.data(), (int)rom.size(), "api-entry-card.pce")) return 3;
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
    if (memory->Read(first_pc) != 0x78) return 5;
    one_instruction();
    bool stepping = cpu->PC->GetValue() == first_pc + 1;
    std::printf("CAP {\"instruction_step\":%s,\"bios_loaded\":%s,\"loader\":\"%s\",\"cdrom_hardware\":%s,\"cdrom_media\":%s,\"disc_given\":%s,\"drive_ready\":%s}\n",
                stepping ? "true" : "false", have_disc ? "true" : "false",
                have_disc ? "LoadBiosFromBuffer(syscard)+LoadMedia(cue)" : "SetCDROMType(STANDARD)+LoadHuCardFromBuffer",
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
        long arrival = -1;
        long confirmed_steps = 0;
        unsigned halt_pc = 0;
        bool halt_confirmed = false;
        std::string reason = "budget";
        long step = 0;
        unsigned prev_pc = cpu->PC->GetValue();
        unsigned prev_sp = cpu->S->GetValue() & 0xFF;
        int api_calls = 0;
        bool api_truncated = false;
        auto halt_name = [&](unsigned pc) -> const char* {
            for (const auto& name : halts)
                if (pc == labels[name]) return name.c_str();
            return nullptr;
        };
        while (step < max_steps) {
            unsigned pc = cpu->PC->GetValue();
            unsigned cur_sp = cpu->S->GetValue() & 0xFF;
            // API-entry watch: WRAM payload -> BIOS region, before executing entry.
            if (step > 0 &&
                prev_pc >= kPayloadLo && prev_pc < kPayloadHi &&
                pc >= kTableBase) {
                if (api_calls >= 8) { api_truncated = true; }
                else { api_calls++;
                unsigned slot = 0; bool in_table = false;
                if (pc >= kTableBase && pc < kTableEnd && (pc - kTableBase) % 3 == 0) {
                    in_table = true; slot = (pc - kTableBase) / 3;
                }
                unsigned sdelta = (prev_sp + 256 - cur_sp) & 0xFF;
                // Return validation internally (no stack bytes / addresses emitted).
                // Stack page is logical $2100 via MPR1; MPR1 must be WRAM bank $F8.
                const char* stack_check = "UNKNOWN";
                bool ret_match = false;
                if (memory->GetMpr(1) == 0xF8) {
                    const u8* wram = memory->GetWorkingRAM();
                    unsigned b0 = wram[0x100 + ((cur_sp + 1) & 0xFF)] & 0xFF;
                    unsigned b1 = wram[0x100 + ((cur_sp + 2) & 0xFF)] & 0xFF;
                    unsigned ret = b0 | (b1 << 8);
                    if (ret == (unsigned)(prev_pc + 2)) { stack_check = "MATCH"; ret_match = true; }
                    else stack_check = "MISMATCH";
                }
                const char* ret_json = stack_check[0] == 'M' && std::strcmp(stack_check, "MATCH") == 0 ? "true" :
                    std::strcmp(stack_check, "MISMATCH") == 0 ? "false" : "null";
                std::printf("APICALL {\"cycle\":\"%s\",\"step\":%ld,\"caller_pc\":%u,\"entry_pc\":%u,"
                            "\"in_table\":%s,\"slot\":%u,\"sp_before\":%u,\"sp_after\":%u,\"sp_delta\":%u,"
                            "\"caller_a\":%u,\"caller_x\":%u,\"caller_y\":%u,\"caller_p\":%u,"
                            "\"stack_check\":\"%s\",\"return_matches_caller\":%s,\"idr\":%u,\"irr\":%u,\"mpr\":[",
                            cycle, step, prev_pc, pc, in_table ? "true" : "false", slot,
                            prev_sp, cur_sp, sdelta,
                            cpu->A->GetValue(), cpu->X->GetValue(), cpu->Y->GetValue(), cpu->P->GetValue(),
                            stack_check, ret_json, (unsigned)*cpu->IDR, (unsigned)*cpu->IRR);
                for (int i = 0; i < 8; i++) std::printf("%s%u", i ? "," : "", memory->GetMpr(i));
                std::printf("]}\n"); }
            }
            for (const auto& item : labels)
                if (pc == item.second && !seen.count(item.first)) {
                    seen.insert(item.first);
                    regs("HIT", item.first.c_str(), step);
                    if (!commercial) hex("buffer_head_hex", kBufferOffset, 32);
                    std::printf("}\n");
                }
            if (halt_name(pc)) {
                if (pc != halt_pc) { halt_pc = pc; arrival = step; confirmed_steps = 0; }
            } else {
                halt_pc = 0; arrival = -1; confirmed_steps = 0;
            }
            prev_pc = pc; prev_sp = cur_sp;
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
                halt_pc = 0; arrival = -1; confirmed_steps = 0;
            }
            if ((int)*scsi->PHASE != last_phase && events < 64) {
                last_phase = (int)*scsi->PHASE;
                events++;
                regs("PHASE", "scsi_phase_change", step);
                std::printf("}\n");
            }
        }
        regs("RUN", reason.c_str(), step);
        std::printf(",\"reason\":\"%s\",\"halt_confirmed\":%s,\"halted_after_step\":%ld,\"halt_confirmation_steps\":%ld,\"halt_confirmation_required\":%ld,\"apicall_truncated\":%s",
                    reason.c_str(), halt_confirmed ? "true" : "false", arrival, confirmed_steps, kHaltConfirmSteps, api_truncated ? "true" : "false");
        if (!commercial) { hex("result_hex", kResultOffset, 32); hex("buffer_hex", kBufferOffset, kBufferBytes); }
        std::printf("}\n");
    };
    run_once(false);
    core.ResetMedia(true);
    run_once(true);
    return 0;
}
