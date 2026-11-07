// Per-byte mid-transfer refusal and transfer-source read side effect probe (Issue #367, parent #173).
// Local-only: links the GPL-3.0 pinned Geargrafx core (b49ae82a012eade566d72e9d61c9d95a5caa4da3).
// Never distribute this executable or the linked core. Synthetic BIOS/CD only.
//
// Verification-only patch (tests/integration/patches/geargrafx-b49ae82-block-write-guard.patch):
//   - gg_verify_block_write_guard: called before each byte Read/Write. Returning false skips that one
//     byte's Read and Write, but the core still advances source/destination/length and still runs
//     TransferEnd. This probe measures exactly that behaviour for multi-byte transfers; it is NOT a
//     whole-instruction safe stop.
//   - gg_verify_block_byte_observer: called only for bytes the guard allows, immediately before Write.
//   - gg_verify_transfer_start_guard: set to an always-allow callback here, so TransferStart pushes
//     are not the variable under test (stack page is WRAM in every case below).
//   - gg_verify_transfer_abort_guard: left null, so the preflight path is not exercised here.
//     Whole-instruction preflight is measured separately (geargrafx_block_transfer_matrix.py and
//     the #473 stack-s6 suite); this probe isolates method B (per-byte refusal).
//
// argv: bios cue mode frames
// Modes: <op>-<variant>-<armed|unguarded>, op = tii|tin|tia|tdd|tai
//   cross:     length 3, destination crosses the $3FFF/$4000 page boundary. MPR2 = $FF (hardware
//              page), guest/source in WRAM. See the driver for the per-op destination sequences.
//   srcread:   length 1, source $4104 (hardware page, VDC status register; reading it clears it),
//              destination $3E90 (WRAM). Guest $3E00 (page 1 WRAM), MPR2 = $FF.
//   srcreadff: length 1, source $4104, destination $0000 (page 0 hardware page, VDC AR).
//              Guest $3E00, MPR0 = $FF, MPR2 = $FF.
//
// Exit 0 with one JSON observation on stderr; 2 = usage; 3..6 = load failures.
#include "geargrafx_core.h"
#include "huc6270.h"
#include "huc6280.h"
#include "memory.h"
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

extern bool (*gg_verify_block_write_guard)(void* context, unsigned block_op, unsigned dest);
extern bool (*gg_verify_transfer_start_guard)(void* context, unsigned stack_pointer);
extern bool (*gg_verify_transfer_abort_guard)(void* context, unsigned block_op,
                                              unsigned source, unsigned dest, unsigned length);
extern void (*gg_verify_block_byte_observer)(void* context, unsigned block_op,
                                             unsigned source, unsigned dest);
extern void* gg_verify_block_write_context;

namespace {

bool g_armed = false;
Memory* g_memory = nullptr;
unsigned g_calls = 0, g_refusals = 0;
std::vector<unsigned> g_guard_dests;
std::vector<unsigned> g_byte_src, g_byte_dst;
HuC6270::HuC6270_State* g_vdc = nullptr;

bool block_guard(void*, unsigned, unsigned dest)
{
    ++g_calls;
    g_guard_dests.push_back(dest);
    if (g_armed && g_memory->GetMpr((dest >> 13) & 7) == 0xFF) {
        ++g_refusals;
        return false;
    }
    return true;
}

bool start_guard(void*, unsigned) { return true; }

void observe(void*, unsigned, unsigned src, unsigned dst)
{
    g_byte_src.push_back(src);
    g_byte_dst.push_back(dst);
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
    size_t first = mode.find('-');
    size_t second = first == std::string::npos ? first : mode.find('-', first + 1);
    if (first == std::string::npos || second == std::string::npos)
        return 2;
    std::string op_name = mode.substr(0, first);
    std::string variant = mode.substr(first + 1, second - first - 1);
    std::string role = mode.substr(second + 1);
    if (role == "armed")
        g_armed = true;
    else if (role == "unguarded")
        g_armed = false;
    else
        return 2;
    if (variant != "cross" && variant != "srcread" && variant != "srcreadff")
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

    GeargrafxCore::GG_Debug_Run debug = {};
    std::vector<u8> frame(1024 * 512 * 4, 0);
    std::vector<s16> audio(65536, 0);

    const unsigned long guest_base = (variant == "cross") ? 0x1E00 : 0x3E00;
    u16 src = 0x1E80, dst = 0x3E90;
    unsigned length = 1;
    if (variant == "cross") {
        length = 3;
        // Per-op crossing layout (see driver for the exact expected sequences).
        if (op_name == "tin") { src = 0x1E80; dst = 0x4000; }
        else if (op_name == "tdd") { src = 0x1E82; dst = 0x4000; }
        else if (op_name == "tia") { src = 0x1E80; dst = 0x3FFF; }
        else { src = 0x1E80; dst = 0x3FFE; }  // tii, tai
    } else {
        src = 0x4104;  // VDC status register via page 2 ($FF)
        dst = (variant == "srcreadff") ? 0x0000 : 0x3E90;
        length = 1;
    }

    for (int i = 0; i < 8; ++i)
        memory->SetMpr(i, 0xF8);
    if (variant == "cross")
        memory->SetMpr(2, 0xFF);
    if (variant == "srcread")
        memory->SetMpr(2, 0xFF);
    if (variant == "srcreadff") {
        memory->SetMpr(0, 0xFF);
        memory->SetMpr(2, 0xFF);
    }

    std::vector<u8> code = {opcode, (u8)(src & 0xFF), (u8)(src >> 8),
                            (u8)(dst & 0xFF), (u8)(dst >> 8), (u8)(length & 0xFF), (u8)(length >> 8),
                            0x80, 0xFE};  // BRA *
    unsigned long end_pc = guest_base + code.size() - 2;
    u8* wram = memory->GetWorkingRAM();  // banks $F8-$FB all alias m_wram[0] on non-SGX
    for (size_t i = 0; i < code.size(); ++i)
        wram[0x1E00 + i] = code[i];
    // Source pattern (physical WRAM indices; $1E80.. is page 0, $3Exx is page 1, same offset).
    wram[0x1E80] = 0x13;
    wram[0x1E81] = 0x27;
    wram[0x1E82] = 0x3B;
    // Destination area preset, so an unwritten byte is distinguishable from a written one.
    wram[0x1FFE] = 0xCC;  // $3FFE
    wram[0x1FFF] = 0xCC;  // $3FFF
    wram[0x1E90] = 0xCC;  // $3E90 / $1E90
    wram[0x0102] = 0;
    wram[0x0103] = 0;
    wram[0x0104] = 0;

    st->PC->SetValue((u16)guest_base);
    st->S->SetValue(0x04);
    st->A->SetValue(0x12);
    st->X->SetValue(0x34);
    st->Y->SetValue(0x07);
    st->P->SetValue(0x04);

    // VDC status preset for the source-read cases: bits the VDC cannot raise on its own at reset.
    *vs->READ_BUFFER = (u16)0xBEEF;
    *vs->SR = (u16)0x05;

    gg_verify_block_write_guard = g_armed ? block_guard : nullptr;
    gg_verify_transfer_start_guard = start_guard;
    gg_verify_transfer_abort_guard = nullptr;
    gg_verify_block_byte_observer = observe;

    std::vector<u8> mpr_before(8), mpr_after(8);
    for (int i = 0; i < 8; ++i)
        mpr_before[i] = memory->GetMpr(i);
    unsigned vdc_ar_before = (u8)*vs->AR;
    unsigned vdc_r0_before = vs->R[0];
    unsigned vdc_r7_before = vs->R[7];
    unsigned vdc_sr_before = *vs->SR;
    unsigned vdc_rb_before = *vs->READ_BUFFER;
    unsigned sp_before = st->S->GetValue();
    unsigned p_before = st->P->GetValue();

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
    unsigned vdc_r0_after = vs->R[0];
    unsigned vdc_r7_after = vs->R[7];
    unsigned vdc_sr_after = *vs->SR;
    unsigned vdc_rb_after = *vs->READ_BUFFER;

    const char* status = halted ? "ok" : "budget_exhausted";
    std::fprintf(stderr, "{");
    std::fprintf(stderr, "\"mode\":\"%s\",\"status\":\"%s\",\"armed\":%s,", mode.c_str(), status,
                 g_armed ? "true" : "false");
    std::fprintf(stderr, "\"op\":\"%s\",\"variant\":\"%s\",", op_name.c_str(), variant.c_str());
    std::fprintf(stderr, "\"opcode\":%u,\"block_op\":%u,\"src\":%u,\"dst\":%u,\"length\":%u,", opcode, block_index, src, dst, length);
    std::fprintf(stderr, "\"guard_calls\":%u,\"guard_refusals\":%u,\"guard_dests\":[", g_calls, g_refusals);
    for (size_t i = 0; i < g_guard_dests.size(); ++i)
        std::fprintf(stderr, "%s%u", i ? "," : "", g_guard_dests[i]);
    std::fprintf(stderr, "],\"byte_src\":[");
    for (size_t i = 0; i < g_byte_src.size(); ++i)
        std::fprintf(stderr, "%s%u", i ? "," : "", g_byte_src[i]);
    std::fprintf(stderr, "],\"byte_dst\":[");
    for (size_t i = 0; i < g_byte_dst.size(); ++i)
        std::fprintf(stderr, "%s%u", i ? "," : "", g_byte_dst[i]);
    std::fprintf(stderr, "],\"expected_end_pc\":%lu,\"stop_pc\":%u,\"frames_run\":%u,", end_pc, (unsigned)st->PC->GetValue(), frames_run);
    std::fprintf(stderr, "\"before\":{\"mpr\":[%u,%u,%u,%u,%u,%u,%u,%u]},",
                 mpr_before[0], mpr_before[1], mpr_before[2], mpr_before[3],
                 mpr_before[4], mpr_before[5], mpr_before[6], mpr_before[7]);
    std::fprintf(stderr, "\"after\":{\"mpr\":[%u,%u,%u,%u,%u,%u,%u,%u]},",
                 mpr_after[0], mpr_after[1], mpr_after[2], mpr_after[3],
                 mpr_after[4], mpr_after[5], mpr_after[6], mpr_after[7]);
    std::fprintf(stderr, "\"vdc_ar\":[%u,%u],\"vdc_r0\":[%u,%u],\"vdc_r7\":[%u,%u],\"vdc_sr\":[%u,%u],\"vdc_read_buffer\":[%u,%u],",
                 vdc_ar_before, vdc_ar_after, vdc_r0_before, vdc_r0_after,
                 vdc_r7_before, vdc_r7_after, vdc_sr_before, vdc_sr_after,
                 vdc_rb_before, vdc_rb_after);
    std::fprintf(stderr, "\"sp\":[%u,%u],\"p\":[%u,%u],\"a_after\":%u,\"x_after\":%u,\"y_after\":%u,",
                 sp_before, st->S->GetValue(), p_before, st->P->GetValue(),
                 st->A->GetValue(), st->X->GetValue(), st->Y->GetValue());
    std::fprintf(stderr, "\"stack_wram\":[%u,%u,%u],\"wram_3ffe\":%u,\"wram_3fff\":%u,\"wram_1e90\":%u",
                 wram[0x0102], wram[0x0103], wram[0x0104], wram[0x1FFE], wram[0x1FFF], wram[0x1E90]);
    std::fprintf(stderr, "}");
    std::fflush(stderr);
    gg_verify_block_write_guard = nullptr;
    gg_verify_transfer_start_guard = nullptr;
    gg_verify_transfer_abort_guard = nullptr;
    gg_verify_block_byte_observer = nullptr;
    return 0;
}
