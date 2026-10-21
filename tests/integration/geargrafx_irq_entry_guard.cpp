// Free-running IRQ-entry guard probe for IRQ2 / IRQ1 / TIQ (Issue #366, parent #173).
// Local-only: links the GPL-3.0 pinned Geargrafx core (b49ae82a012eade566d72e9d61c9d95a5caa4da3)
// with the VERIFICATION-ONLY patch tests/integration/patches/geargrafx-b49ae82-irq-entry-guard.patch.
// Never distribute this executable or the linked core. Synthetic BIOS/CD only.
//
// The run is free-running (RunToVBlank, no debugger stepping, no breakpoints). The IRQ is injected by
// setting the interrupt request register (IRR) and the guest executes CLI then BRA *. The refusal happens
// INSIDE HuC6280::HandleIRQ, after the vector is selected and before the entry stack pushes and the PC
// change, through gg_verify_irq_entry_guard.
//
// argv: bios cue case frames
// Case = <vector>-<variant>, vector in {irq2, irq1, tiq}, variant in:
//   unguarded    null hook (upstream behaviour): the entry lands, handler runs (positive control)
//   armed        hook refuses every entry (negative)
//   masked       hook installed, IDR masks the vector: the hook must not be consulted (positive, mask respected)
//   region-allow hook allows an entry whose handler is inside page 1 [$2000,$4000) (positive)
//   outregion    hook refuses an entry whose handler is outside page 1 (negative, out-of-guard-region vector)
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

// Symbols defined only by the VERIFICATION-ONLY core patch (see patches/geargrafx-b49ae82-irq-entry-guard.patch).
extern bool (*gg_verify_irq_entry_guard)(void* context, unsigned short vector, unsigned short pc, unsigned char p);
extern void* gg_verify_irq_entry_context;

namespace {

const unsigned long kGuestBase = 0x3E00;     // CLI; BRA *  (WRAM page 1)
const unsigned long kHandler = 0x3F80;       // LDA #$77; STA $3E60; BRA *  (WRAM page 1)
const unsigned long kOutHandler = 0x4010;    // outside page 1 (ROM bank 0 via MPR2 = 0)
const unsigned long kMarker = 0x3E60;        // written only by the handler

enum Policy { kRefuseAll, kRegion };
Policy g_policy = kRefuseAll;
Memory* g_memory = nullptr;
unsigned g_calls = 0, g_refusals = 0, g_allows = 0;
unsigned g_vector_at_call = 0, g_handler_at_call = 0, g_pc_at_call = 0, g_p_at_call = 0;
unsigned g_stack_at_call[3] = {0, 0, 0};

bool irq_guard(void*, unsigned short vector, unsigned short pc, unsigned char p)
{
    ++g_calls;
    if (g_calls == 1) {
        g_vector_at_call = vector;
        g_pc_at_call = pc;
        g_p_at_call = p;
        g_stack_at_call[0] = g_memory->Read(0x21FF);
        g_stack_at_call[1] = g_memory->Read(0x21FE);
        g_stack_at_call[2] = g_memory->Read(0x21FD);
    }
    unsigned handler = g_memory->Read(vector) | (g_memory->Read((u16)(vector + 1)) << 8);
    g_handler_at_call = handler;
    bool allow = g_policy == kRegion && handler >= 0x2000 && handler < 0x4000;
    if (allow) {
        ++g_allows;
        return true;
    }
    ++g_refusals;
    return false;
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

    unsigned vector_addr = 0, irr_bit = 0, bios_lo = 0;
    std::string vec_name;
    if (mode.rfind("irq2-", 0) == 0) { vector_addr = 0xFFF6; irr_bit = 0x01; vec_name = "irq2"; }
    else if (mode.rfind("irq1-", 0) == 0) { vector_addr = 0xFFF8; irr_bit = 0x02; vec_name = "irq1"; }
    else if (mode.rfind("tiq-", 0) == 0) { vector_addr = 0xFFFA; irr_bit = 0x04; vec_name = "tiq"; }
    else return 2;
    bios_lo = vector_addr - 0xE000;   // bank 0 is bios[0..8K) mapped at $E000-$FFFF
    std::string variant = mode.substr(vec_name.size() + 1);

    bool out_region = variant == "outregion";
    bool masked = variant == "masked";
    bool unguarded = variant == "unguarded";
    if (variant == "region-allow" || out_region) g_policy = kRegion;
    else if (variant == "armed" || masked) g_policy = kRefuseAll;
    else if (!unguarded) return 2;

    std::ifstream file(argv[1], std::ios::binary);
    if (!file)
        return 3;
    std::vector<u8> bios((std::istreambuf_iterator<char>(file)), {});
    if (bios.size() < 0x2000)
        return 3;
    // Synthetic vector table: the selected vector points at the handler (or outside page 1 for outregion).
    unsigned long target = out_region ? kOutHandler : kHandler;
    bios[bios_lo] = (u8)(target & 0xFF);
    bios[bios_lo + 1] = (u8)(target >> 8);

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
    g_memory = memory;

    GeargrafxCore::GG_Debug_Run debug = {};  // free-running: no stepping, no breakpoints
    std::vector<u8> frame(1024 * 512 * 4, 0);
    std::vector<s16> audio(65536, 0);

    // Same synthetic mapping as geargrafx_st_write_guard.cpp: page 1 -> WRAM, everything else ROM/0.
    memory->SetMpr(1, 0xF8);
    memory->SetMpr(7, 0x00);
    for (int i = 2; i <= 6; ++i)
        memory->SetMpr(i, 0x00);
    memory->SetMpr(0, 0x00);

    // Guest: CLI; BRA *   Handler: LDA #$77; STA $3E60; BRA *
    const u8 guest[] = {0x58, 0x80, 0xFE};
    const u8 handler[] = {0xA9, 0x77, 0x8D, (u8)(kMarker & 0xFF), (u8)(kMarker >> 8), 0x80, 0xFE};
    u8* wram = memory->GetWorkingRAM();
    for (size_t i = 0; i < sizeof(guest); ++i)
        wram[0x1E00 + i] = guest[i];
    for (size_t i = 0; i < sizeof(handler); ++i)
        wram[0x1F80 + i] = handler[i];
    wram[0x1FFD] = wram[0x1FFE] = wram[0x1FFF] = 0;   // stack page cleared before entry
    wram[kMarker - 0x2000 + 0x0000] = 0;              // marker cleared (physical 0x1E60)

    st->PC->SetValue((u16)kGuestBase);
    st->S->SetValue(0xFF);
    st->A->SetValue(0);
    st->X->SetValue(0);
    st->Y->SetValue(0);
    st->P->SetValue(0x04);  // I=1 until the guest CLI

    *st->IRR = (u8)irr_bit;                       // inject the vector request
    *st->IDR = masked ? (u8)irr_bit : (u8)0x00;   // mask only in the masked variant

    gg_verify_irq_entry_context = nullptr;
    gg_verify_irq_entry_guard = unguarded ? nullptr : irq_guard;

    unsigned frames_run = 0;
    for (; frames_run < frames; ++frames_run) {
        int samples = 0;
        core.RunToVBlank(frame.data(), audio.data(), &samples, &debug, false);
    }

    unsigned stack_after[3] = {memory->Read(0x21FF), memory->Read(0x21FE), memory->Read(0x21FD)};
    unsigned marker_after = memory->Read((u16)kMarker);

    std::fprintf(stderr, "{");
    std::fprintf(stderr, "\"mode\":\"%s\",\"status\":\"ok\",\"vector\":\"%s\",\"vector_addr\":%lu,",
                 mode.c_str(), vec_name.c_str(), (unsigned long)vector_addr);
    std::fprintf(stderr, "\"variant\":\"%s\",\"guarded\":%s,", variant.c_str(), unguarded ? "false" : "true");
    std::fprintf(stderr, "\"guard_calls\":%u,\"guard_refusals\":%u,\"guard_allows\":%u,", g_calls, g_refusals, g_allows);
    std::fprintf(stderr, "\"vector_at_call\":%u,\"handler_at_call\":%u,\"pc_at_call\":%u,\"p_at_call\":%u,",
                 g_vector_at_call, g_handler_at_call, g_pc_at_call, g_p_at_call);
    std::fprintf(stderr, "\"stack_at_call\":[%u,%u,%u],", g_stack_at_call[0], g_stack_at_call[1], g_stack_at_call[2]);
    std::fprintf(stderr, "\"stack_after\":[%u,%u,%u],", stack_after[0], stack_after[1], stack_after[2]);
    std::fprintf(stderr, "\"stop_pc\":%u,\"a_after\":%u,\"marker_after\":%u,", (unsigned)st->PC->GetValue(),
                 (unsigned)st->A->GetValue(), marker_after);
    std::fprintf(stderr, "\"irr_after\":%u,\"expected_guest_pc\":%lu,\"expected_handler_pc\":%lu,",
                 (unsigned)*st->IRR, kGuestBase + 1, kHandler + 5);
    std::fprintf(stderr, "\"frames_run\":%u,\"frame_budget\":%lu", frames_run, frames);
    std::fprintf(stderr, "}");
    std::fflush(stderr);
    gg_verify_irq_entry_guard = nullptr;
    return 0;
}
