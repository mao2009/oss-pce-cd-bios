// MPR1 TAM + IRQ1 measurement probe (Issue #498, synthetic). Test-only frontend. It links the pinned GPL-3.0
// Geargrafx core (b49ae82a012eade566d72e9d61c9d95a5caa4da3) UNMODIFIED. Never distribute this executable or the core.
// Synthetic HuCARD only (tests/fixtures/mpr1_tam_irq.s). Each guest instruction is one HuC6280::RunInstruction call.
// It prints one tagged JSON line per record:
//   CAP    instruction stepping works (the first step is SEI)
//   IRQ    IRQ1 asserted by the host before the tam_site step, or released at the IRQ1 entry
//   STEP   state before (phase "before") and after (phase "after") the RunInstruction that starts at tam_site:
//          PC, A, X, Y, P, S, MPR0-7, IRR and IDR (the IRQ request and disable registers)
//   RET    state at the FIRST arrival at after_tam: the step after the TAM (no IRQ) or after the IRQ1 RTI
//   WRW    a WRAM byte changed in a step (offset, old, new)
//   VDW    a VDC register changed in a step (AR, SR, R0-R31; VDC reads are not observed; a same-value write is invisible)
//   HITS   how many steps began at each label
//   END    final PC and MPR0-7
// The probe CONTINUES execution after the TAM for the whole STEPS budget (the guest loops on after_tam). It does
// not stop the guest at a denied bank; the method-A verdict is derived from the recorded state, not from a stop.
// argv: rom labels steps irq (none | at-tam). Exit: 0 run, 2 usage, 3 ROM load, 4 label file, 5 stepping refused.
#include "geargrafx_core.h"
#include "huc6270.h"
#include "huc6280.h"
#include "media.h"
#include "memory.h"
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iterator>
#include <map>
#include <string>
#include <vector>

namespace {

struct VdcSnap {
    u16 ar, sr;
    u16 r[32];
};

VdcSnap snap_vdc(HuC6270::HuC6270_State* vs) {
    VdcSnap s{};
    s.ar = *vs->AR;
    s.sr = *vs->SR;
    for (int i = 0; i < 32; ++i)
        s.r[i] = vs->R[i];
    return s;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 5)
        return 2;
    std::ifstream input(argv[1], std::ios::binary);
    if (!input)
        return 3;
    std::vector<u8> rom((std::istreambuf_iterator<char>(input)), {});

    std::map<std::string, unsigned> labels;
    {
        std::ifstream file(argv[2]);
        if (!file)
            return 4;
        std::string name;
        unsigned address;
        while (file >> name >> std::hex >> address)
            labels[name] = address;
    }
    for (const char* name : {"tam_site", "after_tam", "irq1_entry"})
        if (!labels.count(name))
            return 4;
    char* end = nullptr;
    long steps = std::strtol(argv[3], &end, 10);
    if (!end || *end || steps < 2 || steps > 2048)
        return 2;
    std::string irq = argv[4];
    if (irq != "none" && irq != "at-tam")
        return 2;

    GeargrafxCore core;
    core.Init(nullptr);
    core.GetMedia()->SetCDROMType(GG_CDROM_STANDARD);
    if (!core.LoadHuCardFromBuffer(rom.data(), (int)rom.size(), "mpr1-tam-irq.pce"))
        return 3;

    HuC6280* processor = core.GetHuC6280();
    HuC6280::HuC6280_State* cpu = processor->GetState();
    Memory* memory = core.GetMemory();
    HuC6270::HuC6270_State* vs = core.GetHuC6270_1()->GetState();

    std::vector<u8> frame(1024 * 512 * 4);
    std::vector<s16> audio(65536);
    GeargrafxCore::GG_Debug_Run debug = {};
    debug.step_debugger = true;

    unsigned first_pc = cpu->PC->GetValue();
    if (memory->GetMpr(7) != 0 || memory->Read(first_pc) != 0x78)  // reset: MPR7 = ROM bank 0, first byte SEI
        return 5;
    int samples = 0;
    core.RunToVBlank(frame.data(), audio.data(), &samples, &debug, false);  // the first debugger step (SEI)
    bool stepping = cpu->PC->GetValue() == first_pc + 1;
    std::printf("CAP {\"instruction_step\":%s,\"pc_before\":%u,\"pc_after\":%u}\n", stepping ? "true" : "false",
                first_pc, cpu->PC->GetValue());
    if (!stepping)
        return 5;

    auto print_state = [&](const char* tag, const char* phase, long step, unsigned pc) {
        std::printf("%s {\"phase\":\"%s\",\"step\":%ld,\"pc\":%u,\"a\":%u,\"x\":%u,\"y\":%u,\"p\":%u,\"s\":%u,\"mpr\":[",
                    tag, phase, step, pc, cpu->A->GetValue(), cpu->X->GetValue(), cpu->Y->GetValue(),
                    cpu->P->GetValue(), cpu->S->GetValue());
        for (int i = 0; i < 8; ++i)
            std::printf("%s%u", i ? "," : "", memory->GetMpr(i));
        std::printf("],\"irr\":%u,\"idr\":%u}\n", *cpu->IRR, *cpu->IDR);
    };

    std::vector<u8> wram_prev(memory->GetWorkingRAM(), memory->GetWorkingRAM() + 0x2000);
    VdcSnap vdc_prev = snap_vdc(vs);
    std::map<std::string, long> hits;
    bool irq_armed = irq == "at-tam", irq_line = false, ret_done = false;
    const unsigned tam_site = labels["tam_site"], after_tam = labels["after_tam"], irq1_entry = labels["irq1_entry"];

    for (long i = 1; i < steps; ++i) {
        unsigned pc = cpu->PC->GetValue();
        if (pc == tam_site)
            hits["tam_site"] += 1;
        if (pc == after_tam) {
            hits["after_tam"] += 1;
            if (!ret_done) {  // the first arrival after the TAM step (directly, or after the IRQ1 RTI)
                ret_done = true;
                print_state("RET", "after_return", i, pc);
            }
        }
        if (pc == irq1_entry)
            hits["irq1_entry"] += 1;
        if (irq_line && pc == irq1_entry) {  // IRQ1 entry: release the line, as the other probes do
            processor->AssertIRQ1(false);
            irq_line = false;
            std::printf("IRQ {\"event\":\"deasserted_at_entry\",\"step\":%ld,\"pc\":%u}\n", i, pc);
        }
        bool at_tam = pc == tam_site;
        if (at_tam && irq_armed) {  // assert before the snapshot, so the "before" record shows the pending request
            processor->AssertIRQ1(true);
            irq_armed = false;
            irq_line = true;
            std::printf("IRQ {\"event\":\"asserted_before_step\",\"step\":%ld,\"pc\":%u}\n", i, pc);
        }
        if (at_tam)
            print_state("STEP", "before", i, pc);
        bool completed = false;
        processor->RunInstruction(&completed);
        if (at_tam)
            print_state("STEP", "after", i, cpu->PC->GetValue());  // the PC after the step (IRQ vector if taken)
        std::vector<u8> wram(memory->GetWorkingRAM(), memory->GetWorkingRAM() + 0x2000);
        for (unsigned a = 0; a < 0x2000; ++a)
            if (wram[a] != wram_prev[a])
                std::printf("WRW {\"step\":%ld,\"pc\":%u,\"wram\":%u,\"old\":%u,\"new\":%u}\n", i, pc, a,
                            wram_prev[a], wram[a]);
        wram_prev = wram;
        VdcSnap vdc = snap_vdc(vs);
        if (vdc.ar != vdc_prev.ar)
            std::printf("VDW {\"step\":%ld,\"pc\":%u,\"reg\":\"AR\",\"old\":%u,\"new\":%u}\n", i, pc, vdc_prev.ar, vdc.ar);
        if (vdc.sr != vdc_prev.sr)
            std::printf("VDW {\"step\":%ld,\"pc\":%u,\"reg\":\"SR\",\"old\":%u,\"new\":%u}\n", i, pc, vdc_prev.sr, vdc.sr);
        for (int r = 0; r < 32; ++r)
            if (vdc.r[r] != vdc_prev.r[r])
                std::printf("VDW {\"step\":%ld,\"pc\":%u,\"reg\":\"R%d\",\"old\":%u,\"new\":%u}\n", i, pc,
                            r, vdc_prev.r[r], vdc.r[r]);
        vdc_prev = vdc;
    }

    std::printf("HITS {\"tam_site\":%ld,\"after_tam\":%ld,\"irq1_entry\":%ld}\n", hits["tam_site"], hits["after_tam"],
                hits["irq1_entry"]);
    std::printf("END {\"steps\":%ld,\"pc\":%u,\"mpr\":[", steps, cpu->PC->GetValue());
    for (int i = 0; i < 8; ++i)
        std::printf("%s%u", i ? "," : "", memory->GetMpr(i));
    std::printf("]}\n");
    return 0;
}
