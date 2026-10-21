// Free-running pre-write guard probe for TII/TIN/TIA/TDD/TAI byte stores (Issue #367, parent #173).
// Local-only: links the GPL-3.0 pinned Geargrafx core (b49ae82a012eade566d72e9d61c9d95a5caa4da3).
// Never distribute this executable or the linked core. Synthetic BIOS/CD only.
//
// The refusal happens INSIDE the core: the patched block-transfer handlers
// (tests/integration/patches/geargrafx-b49ae82-block-write-guard.patch) call
// gg_verify_block_write_guard before the byte read and Memory::Write. RunToVBlank runs free,
// with no debugger stepping.
//
// argv: bios cue mode frames
// Modes: <op>-forbidden-armed | <op>-forbidden-unguarded | <op>-allowed-armed
//   op = tii | tin | tia | tdd | tai (one-byte block transfer, guest code at $3E00)
//   forbidden: MPR0 = $FF (hardware page); dest $0000 = VDC AR register; source byte 0x13
//   allowed:   MPR0 = $00; dest $3E90 = WRAM; source byte 0x5A
// The armed guard refuses exactly the stores whose dest page is mapped to the hardware page.
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

// Symbols defined only by the VERIFICATION-ONLY core patch (see patches/).
extern bool (*gg_verify_block_write_guard)(void* context, unsigned block_op, unsigned dest);

namespace {

const unsigned long kGuestBase = 0x3E00;  // page 1 -> WRAM, guest entry
const unsigned long kStubIndex = 0x1E00;  // WRAM index of the guest program
const unsigned long kSrcAddr = 0x3E80;    // WRAM source byte
const unsigned long kAllowedDst = 0x3E90; // WRAM destination (allowed)
const unsigned long kForbiddenDst = 0x0000;  // hardware page: VDC AR register

bool g_armed = false;
Memory* g_memory = nullptr;
unsigned g_calls = 0, g_refusals = 0;
unsigned g_last_op = 0, g_last_dest = 0;
unsigned g_ar_at_call = 0, g_r0_at_call = 0;
HuC6270::HuC6270_State* g_vdc = nullptr;

// Refuse a store whose destination page is mapped to the hardware page at call time.
bool block_guard(void*, unsigned block_op, unsigned dest)
{
    ++g_calls;
    g_last_op = block_op;
    g_last_dest = dest;
    g_ar_at_call = (u8)*g_vdc->AR;
    g_r0_at_call = g_vdc->R[0];
    if (g_armed && g_memory->GetMpr((dest >> 13) & 7) == 0xFF) {
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

    std::string op_name, variant;
    size_t first = mode.find('-');
    size_t second = first == std::string::npos ? first : mode.find('-', first + 1);
    if (first == std::string::npos || second == std::string::npos)
        return 2;
    op_name = mode.substr(0, first);
    variant = mode.substr(first + 1, second - first - 1);
    std::string role = mode.substr(second + 1);
    if (role == "armed")
        g_armed = true;
    else if (role == "unguarded")
        g_armed = false;
    else
        return 2;
    bool forbidden = variant == "forbidden";
    if (!forbidden && variant != "allowed")
        return 2;

    u8 opcode = 0, block_index = 0;
    if (op_name == "tii") { opcode = 0x73; block_index = 0; }
    else if (op_name == "tin") { opcode = 0xD3; block_index = 1; }
    else if (op_name == "tia") { opcode = 0xE3; block_index = 2; }
    else if (op_name == "tdd") { opcode = 0xC3; block_index = 3; }
    else if (op_name == "tai") { opcode = 0xF3; block_index = 4; }
    else return 2;

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
    g_memory = memory;

    GeargrafxCore::GG_Debug_Run debug = {};  // free-running: no stepping, no breakpoints
    std::vector<u8> frame(1024 * 512 * 4, 0);
    std::vector<s16> audio(65536, 0);

    // Page 1 -> WRAM (guest and source); page 2 -> WRAM (stack at $21xx); page 0 by variant.
    memory->SetMpr(1, 0xF8);
    memory->SetMpr(2, 0xF8);
    memory->SetMpr(7, 0x00);
    for (int i = 3; i <= 6; ++i)
        memory->SetMpr(i, 0x00);
    memory->SetMpr(0, forbidden ? 0xFF : 0x00);

    u16 dst = forbidden ? (u16)kForbiddenDst : (u16)kAllowedDst;
    u8 src_value = forbidden ? 0x13 : 0x5A;
    std::vector<u8> code = {opcode, (u8)(kSrcAddr & 0xFF), (u8)(kSrcAddr >> 8),
                            (u8)(dst & 0xFF), (u8)(dst >> 8), 0x01, 0x00,  // length = 1 byte
                            0x80, 0xFE};  // BRA *  (end of guest)
    unsigned long end_pc = kGuestBase + code.size() - 2;
    u8* wram = memory->GetWorkingRAM();
    for (size_t i = 0; i < code.size(); ++i)
        wram[kStubIndex + i] = code[i];
    wram[kSrcAddr - 0x2000] = src_value;
    wram[kAllowedDst - 0x2000] = 0x00;

    st->PC->SetValue((u16)kGuestBase);
    st->S->SetValue(0xFF);
    st->A->SetValue(0);
    st->X->SetValue(0);
    st->Y->SetValue(0);
    st->P->SetValue(0x04);

    gg_verify_block_write_guard = g_armed ? block_guard : nullptr;

    std::vector<u8> mpr_before(8), mpr_after(8);
    for (int i = 0; i < 8; ++i)
        mpr_before[i] = memory->GetMpr(i);
    unsigned vdc_ar_before = (u8)*vs->AR;
    unsigned vdc_r_before = vs->R[0];
    unsigned dst_before = memory->Read((u16)kAllowedDst);

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
    unsigned dst_after = memory->Read((u16)kAllowedDst);
    unsigned src_after = memory->Read((u16)kSrcAddr);

    const char* status = halted ? "ok" : "budget_exhausted";
    std::fprintf(stderr, "{");
    std::fprintf(stderr, "\"mode\":\"%s\",\"status\":\"%s\",\"armed\":%s,", mode.c_str(), status,
                 g_armed ? "true" : "false");
    std::fprintf(stderr, "\"op\":\"%s\",\"variant\":\"%s\",", op_name.c_str(), variant.c_str());
    std::fprintf(stderr, "\"opcode\":%u,\"block_op\":%u,\"dest\":%u,\"src_value\":%u,", opcode, block_index, dst, src_value);
    std::fprintf(stderr, "\"guard_calls\":%u,\"guard_refusals\":%u,", g_calls, g_refusals);
    std::fprintf(stderr, "\"guard_block_op\":%u,\"guard_dest\":%u,", g_last_op, g_last_dest);
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
    std::fprintf(stderr, "\"dst_before\":%u,\"dst_after\":%u,\"src_after\":%u", dst_before, dst_after, src_after);
    std::fprintf(stderr, "}");
    std::fflush(stderr);
    gg_verify_block_write_guard = nullptr;
    return 0;
}
