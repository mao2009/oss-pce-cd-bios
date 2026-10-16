// Local-only, guarded reference-BIOS guest JSR observer (slot 0x1E / EX_GETVER).
// Never distribute this GPL-linked executable, the linked core, or private BIOS bytes.
//
// In-core safety mechanisms (implemented against the pinned Geargrafx core itself,
// NOT against a Python flag and NOT from a post-run empty diff):
//   1. RAM write protection: Memory::GetMemoryMapWrite() is cleared for every bank
//      except banks the configured allowlist pages actually cover. The core then
//      drops forbidden stores during execution (prevention).
//   2. CPU-address write breakpoints: a write covering every non-allowlisted page is
//      registered so the core flags the offending instruction while it executes
//      (detection).
//   3. Hardware page: no MPR is allowed to hold 0xFF, so the memory-mapped I/O page
//      is unreachable. If any MPR becomes 0xFF the run is rejected before another
//      instruction executes.
//   4. Instruction budget is enforced by stepping the real CPU one instruction at a
//      time and counting actual executions.
#include "geargrafx_core.h"
#include "huc6280.h"
#include "memory.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

namespace {

const unsigned long kGuestBase = 0x3E00;  // page1 -> WRAM, guest program entry
const unsigned long kStubIndex = 0x1E00;   // WRAM index the guest program is written to
const unsigned long kSlot = 0x1E;
const unsigned long kEntry = 0xE000 + 3 * kSlot;
const unsigned kAllowPages = 8 * 0x2000;   // full CPU space

template <typename Cpu>
std::string state_json(Cpu* cpu, Memory* memory)
{
    char buf[256];
    int n = std::snprintf(buf, sizeof(buf),
                 "{\"a\":%u,\"x\":%u,\"y\":%u,\"p\":%u,\"sp\":%u,\"pc\":%u,\"mpr\":[",
                 (unsigned)cpu->A->GetValue(), (unsigned)cpu->X->GetValue(),
                 (unsigned)cpu->Y->GetValue(), (unsigned)cpu->P->GetValue(),
                 (unsigned)cpu->S->GetValue(), (unsigned)cpu->PC->GetValue());
    std::string s(buf, n < 0 ? 0 : (size_t)n);
    for (int i = 0; i < 8; ++i) {
        n = std::snprintf(buf, sizeof(buf), "%s%u", i ? "," : "", (unsigned)memory->GetMpr(i));
        if (n > 0)
            s.append(buf, (size_t)n);
    }
    s.append("]}");
    return s;
}

struct Observer {
    GeargrafxCore core;
    HuC6280* cpu = nullptr;
    HuC6280::HuC6280_State* st = nullptr;
    Memory* memory = nullptr;
    std::vector<u8> frame;
    std::vector<s16> audio;
    GeargrafxCore::GG_Debug_Run debug = {};
    unsigned long executed = 0;
    unsigned long budget = 0;

    void step()
    {
        int samples = 0;
        core.RunToVBlank(frame.data(), audio.data(), &samples, &debug, false);
        ++executed;
    }

    bool hw_reachable() const
    {
        for (int i = 0; i < 8; ++i)
            if (memory->GetMpr(i) == 0xFF)
                return true;
        return false;
    }
};

}  // namespace

int main(int argc, char** argv)
{
    // argv: bios cue slot budget allow_start allow_end mode neg_addr
    if (argc != 9)
        return 2;
    char* end = nullptr;
    unsigned long slot = std::strtoul(argv[3], &end, 0);
    if (!end || *end || slot != kSlot)
        return 2;
    unsigned long budget = std::strtoul(argv[4], &end, 10);
    if (!end || *end || budget < 4 || budget > 1000000UL)
        return 2;
    unsigned long allow_start = std::strtoul(argv[5], &end, 0);
    if (!end || *end)
        return 2;
    unsigned long allow_end = std::strtoul(argv[6], &end, 0);
    if (!end || *end)
        return 2;
    if (allow_start >= allow_end || allow_end > kAllowPages)
        return 2;
    if (allow_start % 0x2000 != 0 || allow_end % 0x2000 != 0)
        return 2;  // enforcement granularity is the 8 KiB MPR page
    std::string mode = argv[7];
    if (mode != "call" && mode != "neg-write" && mode != "pos-write")
        return 2;
    unsigned long neg_addr = std::strtoul(argv[8], &end, 0);
    if (!end || *end || neg_addr > 65535UL)
        return 2;
    if (mode == "neg-write" && (neg_addr < 0x4000 || neg_addr >= 0x6000))
        return 2;  // forbidden target must sit on page 2 (CD-ROM RAM bank 0x80)
    if (mode == "pos-write" && (neg_addr < 0x2000 || neg_addr >= 0x4000))
        return 2;  // allowed-control target must sit on allowlisted page 1 (WRAM)

    std::ifstream file(argv[1], std::ios::binary);
    if (!file)
        return 3;
    std::vector<u8> bios((std::istreambuf_iterator<char>(file)), {});

    Observer o;
    o.budget = budget;
    o.frame.assign(1024 * 512 * 4, 0);
    o.audio.assign(65536, 0);
    o.debug.step_debugger = true;
    o.debug.stop_on_breakpoint = true;  // activate CPU_ADDRESS write breakpoints
    o.debug.stop_on_irq = false;
    o.debug.stop_on_brk = false;
    o.debug.stop_on_run_to_breakpoint = false;

    o.core.Init(nullptr);
    if (!o.core.LoadBiosFromBuffer(bios.data(), bios.size(), true))
        return 4;
    if (!o.core.LoadMedia(argv[2]))
        return 5;
    if (!o.core.GetMedia()->IsCDROMHardwareEnabled())
        return 6;

    o.cpu = o.core.GetHuC6280();
    o.st = o.cpu->GetState();
    o.memory = o.core.GetMemory();

    // Deterministic synthetic caller environment.
    // This core hardwires ZERO_PAGE_ADDR=0x2000 and STACK_ADDR=0x2100, so the
    // zero page and the 8-bit stack both live in page 1: MPR1 must map WRAM and
    // the allowlist must cover page 1 for any realistic call to return.
    o.memory->SetMpr(0, 0xF8);  // keep every low page on work RAM for determinism
    o.memory->SetMpr(1, 0xF8);  // zero page + stack + guest code -> work RAM
    o.memory->SetMpr(7, 0x00);  // API jump table at $E000 -> ROM bank 0
    for (int i = 2; i <= 6; ++i)
        o.memory->SetMpr(i, 0x00);
    if (mode == "neg-write")
        o.memory->SetMpr(2, 0x80);  // expose core-writable CD-ROM RAM on page2

    // Guest program written directly into work RAM (not through the CPU).
    std::vector<u8> code;
    unsigned long jsr_offset = 0;
    if (mode == "call") {
        // Preload distinct sentinels so the callee's true register output is measurable.
        const u8 pre[] = {0xA9, 0xAA,   // LDA #$AA
                          0xA2, 0x55,   // LDX #$55
                          0xA0, 0xCC};  // LDY #$CC
        code.assign(pre, pre + sizeof(pre));
        jsr_offset = code.size();
        code.push_back(0x20);                       // JSR entry
        code.push_back((u8)(kEntry & 0xFF));
        code.push_back((u8)(kEntry >> 8));
        code.push_back(0x80);                       // BRA *
        code.push_back(0xFE);
    } else {
        code = {0xA9, 0x5A, 0x8D, (u8)(neg_addr & 0xFF), (u8)(neg_addr >> 8), 0x80, 0xFE};
    }
    const unsigned long kReturnAddr = kGuestBase + jsr_offset + 3;
    if (kStubIndex + code.size() > 0x2000)
        return 2;
    u8* wram = o.memory->GetWorkingRAM();
    for (size_t i = 0; i < code.size(); ++i)
        wram[kStubIndex + i] = code[i];

    // --- write protection: deny by default, only allowlist-covered pages stay writable
    bool* map_write = o.memory->GetMemoryMapWrite();
    for (int bank = 0; bank < 0x100; ++bank)
        map_write[bank] = false;
    for (unsigned page = 0; page < 8; ++page) {
        unsigned long lo = page * 0x2000UL;
        if (lo >= allow_start && lo + 0x2000UL <= allow_end)
            map_write[o.memory->GetMpr(page)] = true;
    }

    // --- detection: core flags any store to a page outside the allowlist
    if (allow_end <= 0xFFFFUL &&
        !o.cpu->AddBreakpoint(HuC6280::HuC6280_BREAKPOINT_TYPE_CPU_ADDRESS,
                              (u32)allow_end, 0xFFFFUL, /*range=*/true,
                              /*read=*/false, /*write=*/true, /*execute=*/false))
        return 7;

    // --- pin the guest CPU context
    o.st->PC->SetValue((u16)kGuestBase);
    o.st->S->SetValue(0xFF);
    o.st->A->SetValue(0);
    o.st->X->SetValue(0);
    o.st->Y->SetValue(0);
    o.st->P->SetValue(0x04);  // I=1 (mask IRQs), D=0, T=0, B=0

    auto wram_snap = [&]() { return std::vector<u8>(wram, wram + 0x2000); };
    auto stub_ok = [&]() {
        for (size_t i = 0; i < code.size(); ++i)
            if (wram[kStubIndex + i] != code[i])
                return false;
        return true;
    };

    std::string reason;
    bool ok = true;
    bool got_entry = false;
    bool got_callee = false;
    unsigned long callee_pc = 0;
    bool returned = false;
    bool detected = false;
    u16 detected_pc = 0;
    std::vector<u8> wram0 = wram_snap();
    u8 cdram0 = 0;
    if (mode == "neg-write") {
        // Forbidden target: page 2 maps CD-ROM RAM bank 0x80 (8 KiB window).
        cdram0 = o.memory->GetCDROMRAM()[neg_addr & 0x1FFF];
    }
    std::string before_state = state_json(o.st, o.memory);

    FILE* trace = nullptr;
    if (const char* tp = std::getenv("PCE_REF_TRACE"))
        trace = std::fopen(tp, "w");

    while (ok && o.executed < o.budget) {
        o.step();
        if (trace)
            std::fprintf(trace, "%lu pc=%04X a=%02X x=%02X y=%02X p=%02X sp=%02X\n",
                         o.executed, (unsigned)o.st->PC->GetValue(),
                         (unsigned)o.st->A->GetValue(), (unsigned)o.st->X->GetValue(),
                         (unsigned)o.st->Y->GetValue(), (unsigned)o.st->P->GetValue(),
                         (unsigned)o.st->S->GetValue());

        if (o.hw_reachable()) {
            ok = false;
            reason = "hardware_page_made_reachable";
            break;
        }
        if (o.cpu->MemoryBreakpointHit()) {
            o.cpu->GetBreakpointHitAddress(&detected_pc);
            detected = true;
            if (mode == "call") {
                char buf[72];
                std::snprintf(buf, sizeof(buf), "write_outside_allowlist_at_pc=%04X", detected_pc);
                ok = false;
                reason = buf;
                break;
            }
            if (mode == "pos-write") {
                ok = false;
                reason = "allowed_write_was_flagged";
                break;
            }
            break;  // neg-write: detection reached, verify prevention below
        }
        if (!stub_ok()) {
            ok = false;
            reason = "guest_stub_corrupted";
            break;
        }

        u16 pc = o.st->PC->GetValue();
        if (mode == "call") {
            if (!got_entry && pc == kEntry) {
                got_entry = true;
                continue;
            }
            if (got_entry && !got_callee && pc != kEntry) {
                got_callee = true;
                callee_pc = pc;
            }
            if (pc == kReturnAddr) {
                returned = true;
                break;
            }
        } else {
            if (pc == kGuestBase + 5)
                break;  // reached BRA* without a detected store
        }
    }

    if (ok && mode == "call") {
        if (!got_entry)
            { ok = false; reason = "jsr_did_not_reach_slot_entry"; }
        else if (!returned)
            { ok = false; reason = "budget_exhausted_before_rts"; }
    }

    // --- write-guard verdicts
    unsigned long guard_before = 0, guard_after = 0;
    if (mode == "neg-write") {
        // Forbidden store must be both detected and prevented.
        u8 cdram_after = o.memory->GetCDROMRAM()[neg_addr & 0x1FFF];
        guard_before = cdram0;
        guard_after = cdram_after;
        if (!detected) {
            ok = false;
            reason = "forbidden_write_not_detected";
        } else if (cdram_after != cdram0) {
            ok = false;
            reason = "forbidden_write_not_prevented";
        }
    } else if (mode == "pos-write") {
        // Allowed control store must actually land (proves the guard is not a blanket drop).
        guard_before = wram0[neg_addr & 0x1FFF];
        guard_after = wram[neg_addr & 0x1FFF];
        if (guard_after != 0x5A) {
            ok = false;
            reason = "allowed_write_did_not_land";
        } else if (detected) {
            ok = false;
            reason = "allowed_write_was_flagged";
        }
    }

    FILE* out = stderr;
    std::fprintf(out, "{");
    std::fprintf(out, "\"mode\":\"%s\",", mode.c_str());
    std::fprintf(out, "\"status\":\"%s\",", ok ? "ok" : "rejected");
    std::fprintf(out, "\"reason\":\"%s\",", reason.c_str());
    std::fprintf(out, "\"before\":%s,", before_state.c_str());
    std::fprintf(out, "\"after\":%s,", state_json(o.st, o.memory).c_str());
    if (mode != "call") {
        std::fprintf(out, "\"guard_target\":%lu,\"guard_before\":%lu,\"guard_after\":%lu,",
                     neg_addr, guard_before, guard_after);
    }
    std::fprintf(out, "\"callee_pc\":%lu,", got_callee ? callee_pc : 0);
    std::fprintf(out, "\"entry_reached\":%s,", got_entry ? "true" : "false");
    std::fprintf(out, "\"returned\":%s,", returned ? "true" : "false");
    std::fprintf(out, "\"detected\":%s,", detected ? "true" : "false");
    std::fprintf(out, "\"detected_pc\":%u,", (unsigned)detected_pc);
    std::fprintf(out, "\"instructions_executed\":%lu,", o.executed);
    std::fprintf(out, "\"budget\":%lu,", budget);
    std::fprintf(out, "\"ram_diff\":[");
    std::vector<u8> wramN = wram_snap();
    bool first = true;
    for (unsigned long i = 0; i < 0x2000UL; ++i) {
        if (i >= kStubIndex && i < kStubIndex + code.size())
            continue;
        if (wramN[i] == wram0[i])
            continue;
        if (!first)
            std::fprintf(out, ",");
        first = false;
        // Report a CPU address: MPR1 maps page 1 (0x2000-0x3FFF) onto WRAM index 0.
        std::fprintf(out, "{\"address\":%lu,\"before\":%u,\"after\":%u}",
                     0x2000UL + i, (unsigned)wram0[i], (unsigned)wramN[i]);
    }
    std::fprintf(out, "]}");
    std::fflush(out);
    if (trace)
        std::fclose(trace);
    return 0;
}
