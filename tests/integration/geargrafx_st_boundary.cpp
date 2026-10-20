// Local-only instruction-boundary monitor probe for ST0/ST1/ST2 (Issue #365).
// Never distribute this GPL-linked executable or the linked core. Synthetic BIOS/CD only.
//
// The probe single-steps the pinned Geargrafx instruction debugger one instruction at
// a time. BEFORE each step (including the entry instruction, the "prefetch" check) it
// reads the opcode at the current PC through the same MPR mapping the CPU fetches with.
// If that opcode is ST0 ($03), ST1 ($13) or ST2 ($23) the monitor refuses: the step is
// not taken, the latch stays set, and resume attempts re-run the same boundary check.
// No PC hint is given to the monitor, so the ST location is unknown to it.
//
// argv: bios cue slot budget allow_start allow_end mode neg_addr   (same shape as
// geargrafx_reference_jsr.cpp so geargrafx_reference_guard.run_mode() can drive it).
// Modes:
//   stN-unknown    monitor armed; ST opcode reached through JMP at an unknown PC (entry is a JMP)
//   st0-entry      monitor armed; ST0 is the ENTRY instruction (no preceding instruction)
//   stN-unguarded  MUTATION: same guests, monitor disabled; the ST must execute and land
//   pos-write      positive control: monitor armed, allowlisted RAM store must run unrefused
#include "geargrafx_core.h"
#include "huc6270.h"
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

const unsigned long kGuestBase = 0x3E00;  // page1 -> WRAM, guest entry
const unsigned long kStubIndex = 0x1E00;  // WRAM index of the guest program
const unsigned long kJumpTarget = 0x3E10;  // unknown-PC ST location (JMP target)
const unsigned kResumeAttempts = 3;

bool is_st_opcode(unsigned op)
{
    return op == 0x03 || op == 0x13 || op == 0x23;
}

}  // namespace

int main(int argc, char** argv)
{
    if (argc != 9)
        return 2;
    char* end = nullptr;
    unsigned long slot = std::strtoul(argv[3], &end, 0);
    if (!end || *end || slot != 0x1E)
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
    // Slot-1e allowlist is fixed: page 1 only (zero page and stack live there on this core).
    if (allow_start != 0x2000UL || allow_end != 0x4000UL)
        return 9;
    std::string mode = argv[7];
    unsigned long neg_addr = std::strtoul(argv[8], &end, 0);
    if (!end || *end || neg_addr > 65535UL)
        return 2;

    bool armed = true;
    std::string st_kind;  // "st0", "st1", "st2" or "" (pos-write)
    if (mode == "st0-entry" || mode == "st1-unknown" || mode == "st2-unknown") {
        st_kind = mode.substr(0, 3);
    } else if (mode == "st0-unguarded" || mode == "st1-unguarded" || mode == "st2-unguarded") {
        st_kind = mode.substr(0, 3);
        armed = false;
    } else if (mode == "pos-write") {
        if (neg_addr < allow_start || neg_addr >= allow_end)
            return 2;
    } else {
        return 2;
    }

    std::ifstream file(argv[1], std::ios::binary);
    if (!file)
        return 3;
    std::vector<u8> bios((std::istreambuf_iterator<char>(file)), {});

    GeargrafxCore core;
    core.Init(nullptr);
    if (!core.LoadBiosFromBuffer(bios.data(), bios.size(), true))
        return 4;
    if (!core.LoadMedia(argv[2]))
        return 5;
    if (!core.GetMedia()->IsCDROMHardwareEnabled())
        return 6;

    HuC6280* cpu = core.GetHuC6280();
    HuC6280::HuC6280_State* st = cpu->GetState();
    Memory* memory = core.GetMemory();

    GeargrafxCore::GG_Debug_Run debug = {};
    debug.step_debugger = true;
    debug.stop_on_breakpoint = true;
    debug.stop_on_irq = false;
    debug.stop_on_brk = false;
    debug.stop_on_run_to_breakpoint = false;
    std::vector<u8> frame(1024 * 512 * 4, 0);
    std::vector<s16> audio(65536, 0);

    // Deterministic synthetic environment (same mapping as geargrafx_reference_jsr.cpp slot 1E).
    memory->SetMpr(1, 0xF8);  // page 1 -> work RAM
    memory->SetMpr(7, 0x00);
    for (int i = 2; i <= 6; ++i)
        memory->SetMpr(i, 0x00);
    memory->SetMpr(0, 0x00);  // page 0 -> ROM, never the hardware page

    std::vector<u8> code;
    if (st_kind == "st0") {
        code = {0x03, 0x07, 0x80, 0xFE};  // ST0 #$07 at entry, BRA *
    } else if (!st_kind.empty()) {
        u8 op = (st_kind == "st1") ? 0x13 : 0x23;
        u8 val = (st_kind == "st1") ? 0x5A : 0xA5;
        // JMP $3E10 at entry; ST at $3E10 is reached only through the executed JMP.
        code = {0x4C, (u8)(kJumpTarget & 0xFF), (u8)(kJumpTarget >> 8)};
        while (kGuestBase + code.size() < kJumpTarget)
            code.push_back(0xEA);  // NOP padding
        code.push_back(op);
        code.push_back(val);
        code.push_back(0x80);  // BRA *
        code.push_back(0xFE);
    } else {
        code = {0xA9, 0x5A, 0x8D, (u8)(neg_addr & 0xFF), (u8)(neg_addr >> 8), 0x80, 0xFE};
    }
    if (kStubIndex + code.size() > 0x2000)
        return 2;
    u8* wram = memory->GetWorkingRAM();
    for (size_t i = 0; i < code.size(); ++i)
        wram[kStubIndex + i] = code[i];

    st->PC->SetValue((u16)kGuestBase);
    st->S->SetValue(0xFF);
    st->A->SetValue(0);
    st->X->SetValue(0);
    st->Y->SetValue(0);
    st->P->SetValue(0x04);

    std::vector<u8> mpr_before(8), mpr_after(8);
    for (int i = 0; i < 8; ++i)
        mpr_before[i] = memory->GetMpr(i);

    HuC6270::HuC6270_State* vs = core.GetHuC6270_1()->GetState();
    unsigned vdc_ar_before = (u8)*vs->AR;
    unsigned vdc_r_before = vs->R[0];
    unsigned guard_before = memory->Read((u16)neg_addr);
    unsigned st_pc_expected = (st_kind == "st0") ? kGuestBase : kJumpTarget;

    unsigned long executed = 0;
    bool refused = false;
    unsigned refused_op = 0;
    unsigned long refused_pc = 0;
    unsigned resume_attempts = 0, resume_refused = 0;
    unsigned long executed_at_refusal = 0, executed_after_resume = 0;
    bool halted = false;

    // Boundary check: runs BEFORE every instruction, including the entry instruction.
    // Returns true when the step may be taken.
    auto boundary_allows = [&]() -> bool {
        unsigned pc = st->PC->GetValue();
        unsigned op = memory->Read((u16)pc);
        if (armed && is_st_opcode(op)) {
            refused = true;
            refused_op = op;
            refused_pc = pc;
            return false;
        }
        return true;
    };

    while (!refused && !halted && executed < budget) {
        if (!boundary_allows())
            break;
        unsigned pc_before = st->PC->GetValue();
        int samples = 0;
        core.RunToVBlank(frame.data(), audio.data(), &samples, &debug, false);
        ++executed;
        if (st->PC->GetValue() == pc_before)
            halted = true;  // BRA * self-loop
    }

    if (refused) {
        executed_at_refusal = executed;
        // Resume attempts: the probe must re-run the boundary check and refuse again.
        for (unsigned i = 0; i < kResumeAttempts; ++i) {
            ++resume_attempts;
            if (!boundary_allows())
                ++resume_refused;
        }
        executed_after_resume = executed;
    }

    for (int i = 0; i < 8; ++i)
        mpr_after[i] = memory->GetMpr(i);
    unsigned vdc_ar_after = (u8)*vs->AR;
    unsigned vdc_r_after = vs->R[0];
    unsigned guard_after = memory->Read((u16)neg_addr);

    const char* status = refused ? "refused" : (halted ? "ok" : "budget_exhausted");
    std::fprintf(stderr, "{");
    std::fprintf(stderr, "\"mode\":\"%s\",\"status\":\"%s\",", mode.c_str(), status);
    std::fprintf(stderr, "\"reason\":\"%s\",",
                 refused ? "st_opcode_at_instruction_boundary" : (halted ? "" : "budget_exhausted"));
    std::fprintf(stderr, "\"armed\":%s,\"boundary_check\":%s,", armed ? "true" : "false", armed ? "true" : "false");
    std::fprintf(stderr, "\"refused\":%s,", refused ? "true" : "false");
    std::fprintf(stderr, "\"refused_opcode\":%u,\"refused_pc\":%lu,", refused_op, refused_pc);
    std::fprintf(stderr, "\"expected_st_pc\":%lu,", (unsigned long)st_pc_expected);
    std::fprintf(stderr, "\"resume_attempts\":%u,\"resume_refused\":%u,", resume_attempts, resume_refused);
    std::fprintf(stderr, "\"executed_at_refusal\":%lu,\"executed_after_resume\":%lu,",
                 executed_at_refusal, executed_after_resume);
    std::fprintf(stderr, "\"stop_pc\":%u,", (unsigned)st->PC->GetValue());
    std::fprintf(stderr, "\"instructions_executed\":%lu,", executed);
    std::fprintf(stderr, "\"budget\":%lu,", budget);
    std::fprintf(stderr, "\"st_mode\":\"%s\",", st_kind.c_str());
    std::fprintf(stderr, "\"before\":{\"mpr\":[%u,%u,%u,%u,%u,%u,%u,%u]},",
                 mpr_before[0], mpr_before[1], mpr_before[2], mpr_before[3],
                 mpr_before[4], mpr_before[5], mpr_before[6], mpr_before[7]);
    std::fprintf(stderr, "\"after\":{\"mpr\":[%u,%u,%u,%u,%u,%u,%u,%u]},",
                 mpr_after[0], mpr_after[1], mpr_after[2], mpr_after[3],
                 mpr_after[4], mpr_after[5], mpr_after[6], mpr_after[7]);
    std::fprintf(stderr, "\"vdc_ar_before\":%u,\"vdc_ar_after\":%u,", vdc_ar_before, vdc_ar_after);
    std::fprintf(stderr, "\"vdc_r_before\":%u,\"vdc_r_after\":%u,", vdc_r_before, vdc_r_after);
    std::fprintf(stderr, "\"guard_target\":%lu,\"guard_before\":%u,\"guard_after\":%u,",
                 neg_addr, guard_before, guard_after);
    std::fprintf(stderr, "\"detected\":false,\"detected_pc\":0");  // no write breakpoints armed here
    std::fprintf(stderr, "}");
    std::fflush(stderr);
    return 0;
}
