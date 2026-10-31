// Issue #366: debugger single-step proof of the post-accept IRQ entry boundary.
// This local-only probe links the pinned Geargrafx debugger objects.  It uses no
// reference firmware or media.  A step which accepts an IRQ finishes with PC at
// the synthetic handler; the monitor refuses before making the next step.
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

namespace {
const u16 kGuest = 0x3E00, kHandler = 0x3F80, kMarker = 0x3E60;
const u16 kVectors[] = {0xFFF6, 0xFFF8, 0xFFFA};
const u8 kIrr[] = {1, 2, 4};
int kind(const std::string& name) {
    if (name == "irq2") return 0;
    if (name == "irq1") return 1;
    if (name == "tiq") return 2;
    return -1;
}
}

int main(int argc, char** argv) {
    if (argc != 4) return 2;
    const std::string source = argv[3];
    const size_t dash = source.find('-');
    const int irq = kind(source.substr(0, dash));
    const std::string mutation = dash == std::string::npos ? "" : source.substr(dash + 1);
    if (irq < 0 || (mutation != "armed" && mutation != "unguarded" &&
                    mutation != "no-opcode" && mutation != "wrong-type" &&
                    mutation != "no-handler-recheck" && mutation != "resume-allow" &&
                    mutation != "no-stack-check")) return 2;
    std::ifstream f(argv[1], std::ios::binary);
    std::vector<u8> bios((std::istreambuf_iterator<char>(f)), {});
    if (bios.size() < 0x2000) return 3;
    // Every vector has a distinct target: handler PC is then evidence of the
    // actual selected vector, rather than a host-selected case label.
    const u16 handler = kHandler + irq * 0x20;
    for (int i = 0; i < 3; ++i) {
        const unsigned voff = kVectors[i] - 0xE000;
        const u16 target = kHandler + i * 0x20;
        bios[voff] = target & 0xff; bios[voff + 1] = target >> 8;
    }
    GeargrafxCore core; core.Init(nullptr);
    if (!core.LoadBiosFromBuffer(bios.data(), bios.size(), true) || !core.LoadMedia(argv[2])) return 4;
    HuC6280* cpu = core.GetHuC6280();
    HuC6280::HuC6280_State* s = cpu->GetState();
    Memory* memory = core.GetMemory();
    for (int page = 0; page < 8; ++page) memory->SetMpr(page, page == 1 ? 0xF8 : 0);
    u8* wram = memory->GetWorkingRAM();
    // CLI; BRA *. The IRQ is accepted while executing the BRA (not by a host PC write).
    const u8 guest[] = {0x58, 0x80, 0xFE};
    // Deliberately observable first handler instruction: ST0 #$07 changes VDC AR.
    const u8 handler_code[] = {0x03, 0x07, 0xA9, 0x77, 0x8D, 0x60, 0x3E, 0x80, 0xFE};
    for (unsigned i = 0; i < sizeof(guest); ++i) wram[0x1E00 + i] = guest[i];
    for (unsigned i = 0; i < sizeof(handler_code); ++i) wram[0x1F80 + irq * 0x20 + i] = handler_code[i];
    wram[0x1E60] = 0;
    // Distinct physical sentinels make absence/partial/wrong-offset pushes visible.
    wram[0x1FF] = 0xA1; wram[0x1FE] = 0xB2; wram[0x1FD] = 0xC3;
    s->PC->SetValue(kGuest); s->S->SetValue(0xFF); s->A->SetValue(0); s->X->SetValue(0); s->Y->SetValue(0); s->P->SetValue(0x14);
    if (irq == 0) cpu->AssertIRQ2(true);
    else if (irq == 1) cpu->AssertIRQ1(true);
    else { cpu->WriteTimerRegister(0x0C00, 0); cpu->WriteTimerRegister(0x0C01, 1); cpu->ClockTimer(100000); }
    const std::vector<u8> wram_before_accept(wram, wram + 0x2000);
    GeargrafxCore::GG_Debug_Run debug = {}; debug.step_debugger = true;
    std::vector<u8> frame(1024 * 512 * 4); std::vector<s16> audio(65536);
    int samples = 0;
    // CLI (request remains pending because CheckIRQs observes I before CLI), then BRA accepts it.
    core.RunToVBlank(frame.data(), audio.data(), &samples, &debug, false);
    const unsigned stack_before_accept[3] = {wram[0x1FF], wram[0x1FE], wram[0x1FD]};
    core.RunToVBlank(frame.data(), audio.data(), &samples, &debug, false);
    const unsigned pc_after_accept = s->PC->GetValue(), sp_after_accept = s->S->GetValue();
    HuC6270::HuC6270_State* vdc = core.GetHuC6270_1()->GetState();
    const unsigned ar_before = (u8)*vdc->AR, r_before = vdc->R[0];
    const unsigned fetch_page = s->PC->GetValue() >> 13, fetch_mpr = memory->GetMpr(fetch_page);
    const unsigned fetch_opcode = memory->Read(s->PC->GetValue());
    unsigned observed_vector = 0;
    for (int i = 0; i < 3; ++i)
        if (pc_after_accept == kHandler + i * 0x20) observed_vector = kVectors[i];
    bool accepted = pc_after_accept == handler && sp_after_accept == 0xFC;
    bool check_opcode = mutation != "unguarded" && mutation != "no-opcode" && mutation != "no-handler-recheck";
    bool wrong_type = mutation == "wrong-type";
    bool stack_checked = mutation != "no-stack-check";
    bool refused = accepted && check_opcode && fetch_opcode == 0x03;
    unsigned handler_steps = 0, resume_attempts = 0, resume_refused = 0;
    if (!refused) {
        core.RunToVBlank(frame.data(), audio.data(), &samples, &debug, false);
        ++handler_steps;
    } else {
        for (unsigned i = 0; i < 3; ++i) {
            ++resume_attempts;
            // This is a real resume request to the monitor. It checks the unchanged
            // handler PC before deciding whether a debugger step may be issued.
            if (mutation == "resume-allow" && i == 0) { core.RunToVBlank(frame.data(), audio.data(), &samples, &debug, false); ++handler_steps; }
            else if (s->PC->GetValue() == handler && memory->Read(handler) == 0x03) ++resume_refused;
        }
    }
    const unsigned ar_after = (u8)*vdc->AR, r_after = vdc->R[0];
    const unsigned classified_vector = wrong_type ? kVectors[(irq + 1) % 3] : observed_vector;
    // Read through CPU mapping as well as recording the physical WRAM offsets.
    const unsigned stack[3] = {memory->Read(0x21FF), memory->Read(0x21FE), memory->Read(0x21FD)};
    // Geargrafx's BRA implementation leaves its post-fetch PC at $3E01 here;
    // record the measured stacked return rather than assuming a hardware sequence.
    const unsigned physical[] = {0x1FF, 0x1FE, 0x1FD};
    bool only_stack_writes = true;
    for (unsigned i = 0; i < 0x2000; ++i) {
        bool is_stack = i == physical[0] || i == physical[1] || i == physical[2];
        if (wram_before_accept[i] != wram[i] && !is_stack) only_stack_writes = false;
    }
    const bool stack_ok = stack_before_accept[0] == 0xA1 && stack_before_accept[1] == 0xB2 && stack_before_accept[2] == 0xC3 &&
                          stack[0] == 0x3E && stack[1] == 0x01 && stack[2] == 0x00 &&
                          wram[physical[0]] == stack[0] && wram[physical[1]] == stack[1] &&
                          wram[physical[2]] == stack[2] && only_stack_writes;
    // This is the observed byte layout ordered by CPU stack addresses; it is
    // not a cycle-by-cycle chronological trace of the writes.
    std::fprintf(stderr, "{\"case\":\"%s\",\"accepted\":%s,\"observed_vector\":%u,\"classified_vector\":%u,\"handler_pc\":%u,\"pc_after_accept\":%u,\"sp_before\":255,\"sp_after\":%u,\"irr\":%u,\"idr\":%u,\"fetch_page\":%u,\"fetch_mpr\":%u,\"fetch_opcode\":%u,\"handler_steps\":%u,\"resume_attempts\":%u,\"resume_refused\":%u,\"stack_checked\":%s,\"stack_ok\":%s,\"stack_cpu\":[8703,8702,8701],\"stack_physical\":[511,510,509],\"stack_before\":[%u,%u,%u],\"stack\":[%u,%u,%u],\"stack_frame_layout\":[%u,%u,%u],\"only_stack_writes\":%s,\"vdc_ar_before\":%u,\"vdc_ar_after\":%u,\"vdc_r_before\":%u,\"vdc_r_after\":%u,\"marker\":%u}", source.c_str(), accepted ? "true" : "false", observed_vector, classified_vector, handler, pc_after_accept, sp_after_accept, *s->IRR, *s->IDR, fetch_page, fetch_mpr, fetch_opcode, handler_steps, resume_attempts, resume_refused, stack_checked ? "true" : "false", stack_ok ? "true" : "false", stack_before_accept[0], stack_before_accept[1], stack_before_accept[2], stack[0], stack[1], stack[2], stack[0], stack[1], stack[2], only_stack_writes ? "true" : "false", ar_before, ar_after, r_before, r_after, wram[0x1E60]);
    return 0;
}
