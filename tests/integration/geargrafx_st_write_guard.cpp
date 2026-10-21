// Free-running pre-write guard probe for ST0/ST1/ST2 (Issue #365, parent #173).
// Local-only: links the GPL-3.0 pinned Geargrafx core (b49ae82a012eade566d72e9d61c9d95a5caa4da3).
// Never distribute this executable or the linked core. Synthetic BIOS/CD only.
//
// Unlike geargrafx_st_boundary.cpp (single-stepped debugger), this probe runs the core with
// RunToVBlank and no debugger stepping. The refusal happens INSIDE the core: the patched
// ST0/ST1/ST2 handlers (tests/integration/patches/geargrafx-b49ae82-st-write-guard.patch)
// call gg_verify_st_write_guard before WriteFromCPU. This probe records the VDC AR/R
// values seen at the moment of the call, so "refused before side effect" is observed at the
// point of refusal, and again after the frame.
//
// argv: bios cue mode frames
// Modes (ST = ST0 $03 / ST1 $13 / ST2 $23):
//   stN-armed                guard installed and refusing; ST is the entry instruction
//   stN-unguarded            MUTATION: patched build, null hook (guard not installed); ST lands
//   stN-after-tam-armed      LDA #$FF; TAM #$01 (MPR0 = $FF, dynamic change), then ST
//   stN-after-tam-unguarded  same, guard not installed
//   pos-write-armed          positive control: guard installed, STA $3E50 (RAM) is not a ST and lands
// Exit 0 with one JSON observation on stderr; 2 = usage; 3..6 = load failures.
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

// Symbol defined only by the VERIFICATION-ONLY core patch (see patches/README.md).
extern bool (*gg_verify_st_write_guard)(void* context, unsigned st_index, unsigned value);

namespace {

const unsigned long kGuestBase = 0x3E00;  // page 1 -> WRAM, guest entry
const unsigned long kStubIndex = 0x1E00;  // WRAM index of the guest program
const unsigned long kRamTarget = 0x3E50;  // allowlisted RAM store target (positive control)

bool g_armed = false;
unsigned g_calls = 0, g_refusals = 0;
unsigned g_last_index = 0, g_last_value = 0;
unsigned g_ar_at_call = 0, g_r0_at_call = 0;
HuC6270::HuC6270_State* g_vdc = nullptr;

bool st_guard(void*, unsigned st_index, unsigned value)
{
    ++g_calls;
    g_last_index = st_index;
    g_last_value = value;
    g_ar_at_call = (u8)*g_vdc->AR;
    g_r0_at_call = g_vdc->R[0];
    if (g_armed) {
        ++g_refusals;
        return false;
    }
    return true;
}

}  // namespace

int main(int argc, char** argv)
{
    if (argc != 5)
        return 2;
    char* end = nullptr;
    unsigned long frames = std::strtoul(argv[4], &end, 10);
    if (!end || *end || frames < 1 || frames > 10)
        return 2;
    std::string mode = argv[3];

    std::string st_kind;  // "st0", "st1", "st2" or "" (positive control)
    bool after_tam = false;
    if (mode.size() >= 7 && mode.compare(mode.size() - 6, 6, "-armed") == 0) {
        g_armed = true;
    } else if (mode.size() >= 12 && mode.compare(mode.size() - 10, 10, "-unguarded") == 0) {
        g_armed = false;
    } else {
        return 2;
    }
    std::string base = mode.substr(0, mode.rfind('-'));
    if (base == "st0-after-tam" || base == "st1-after-tam" || base == "st2-after-tam") {
        st_kind = base.substr(0, 3);
        after_tam = true;
    } else if (base == "st0" || base == "st1" || base == "st2") {
        st_kind = base;
    } else if (base == "pos-write") {
        // positive control: the guard is installed but no ST executes
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
    HuC6270::HuC6270_State* vs = core.GetHuC6270_1()->GetState();
    g_vdc = vs;

    GeargrafxCore::GG_Debug_Run debug = {};  // free-running: no stepping, no breakpoints
    std::vector<u8> frame(1024 * 512 * 4, 0);
    std::vector<s16> audio(65536, 0);

    // Same synthetic mapping as geargrafx_st_boundary.cpp: page 1 -> WRAM, everything else ROM/0.
    memory->SetMpr(1, 0xF8);
    memory->SetMpr(7, 0x00);
    for (int i = 2; i <= 6; ++i)
        memory->SetMpr(i, 0x00);
    memory->SetMpr(0, 0x00);

    std::vector<u8> code;
    if (base == "pos-write") {
        code = {0xA9, 0x5A, 0x8D, (u8)(kRamTarget & 0xFF), (u8)(kRamTarget >> 8), 0x80, 0xFE};
    } else {
        u8 op = st_kind == "st0" ? 0x03 : st_kind == "st1" ? 0x13 : 0x23;
        u8 val = st_kind == "st0" ? 0x07 : st_kind == "st1" ? 0x5A : 0xA5;
        if (after_tam)
            code = {0xA9, 0xFF, 0x53, 0x01};  // LDA #$FF; TAM #$01 -> MPR0 = $FF
        code.push_back(op);
        code.push_back(val);
        code.push_back(0x80);  // BRA *  (end of guest)
        code.push_back(0xFE);
    }
    unsigned long end_pc = kGuestBase + code.size() - 2;
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

    gg_verify_st_write_guard = g_armed ? st_guard : nullptr;

    std::vector<u8> mpr_before(8), mpr_after(8);
    for (int i = 0; i < 8; ++i)
        mpr_before[i] = memory->GetMpr(i);
    unsigned vdc_ar_before = (u8)*vs->AR;
    unsigned vdc_r_before = vs->R[0];
    unsigned ram_before = memory->Read((u16)kRamTarget);

    bool halted = false;
    unsigned frames_run = 0;
    while (!halted && frames_run < frames) {
        int samples = 0;
        core.RunToVBlank(frame.data(), audio.data(), &samples, &debug, false);
        ++frames_run;
        if (st->PC->GetValue() == end_pc)
            halted = true;
    }

    for (int i = 0; i < 8; ++i)
        mpr_after[i] = memory->GetMpr(i);
    unsigned vdc_ar_after = (u8)*vs->AR;
    unsigned vdc_r_after = vs->R[0];
    unsigned ram_after = memory->Read((u16)kRamTarget);

    const char* status = halted ? "ok" : "budget_exhausted";
    std::fprintf(stderr, "{");
    std::fprintf(stderr, "\"mode\":\"%s\",\"status\":\"%s\",\"armed\":%s,", mode.c_str(), status,
                 g_armed ? "true" : "false");
    std::fprintf(stderr, "\"st_kind\":\"%s\",\"after_tam\":%s,", st_kind.c_str(), after_tam ? "true" : "false");
    std::fprintf(stderr, "\"guard_calls\":%u,\"guard_refusals\":%u,", g_calls, g_refusals);
    std::fprintf(stderr, "\"guard_st_index\":%u,\"guard_value\":%u,", g_last_index, g_last_value);
    std::fprintf(stderr, "\"guard_ar_at_call\":%u,\"guard_r0_at_call\":%u,", g_ar_at_call, g_r0_at_call);
    std::fprintf(stderr, "\"expected_end_pc\":%lu,\"stop_pc\":%u,", end_pc, (unsigned)st->PC->GetValue());
    std::fprintf(stderr, "\"frames_run\":%u,\"frame_budget\":%lu,", frames_run, frames);
    std::fprintf(stderr, "\"before\":{\"mpr\":[%u,%u,%u,%u,%u,%u,%u,%u]},",
                 mpr_before[0], mpr_before[1], mpr_before[2], mpr_before[3],
                 mpr_before[4], mpr_before[5], mpr_before[6], mpr_before[7]);
    std::fprintf(stderr, "\"after\":{\"mpr\":[%u,%u,%u,%u,%u,%u,%u,%u]},",
                 mpr_after[0], mpr_after[1], mpr_after[2], mpr_after[3],
                 mpr_after[4], mpr_after[5], mpr_after[6], mpr_after[7]);
    std::fprintf(stderr, "\"vdc_ar_before\":%u,\"vdc_ar_after\":%u,", vdc_ar_before, vdc_ar_after);
    std::fprintf(stderr, "\"vdc_r_before\":%u,\"vdc_r_after\":%u,", vdc_r_before, vdc_r_after);
    std::fprintf(stderr, "\"ram_target\":%lu,\"ram_before\":%u,\"ram_after\":%u", kRamTarget, ram_before, ram_after);
    std::fprintf(stderr, "}");
    std::fflush(stderr);
    gg_verify_st_write_guard = nullptr;
    return 0;
}
