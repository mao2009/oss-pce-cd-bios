// Caller-stack contract probe (Issue #473, synthetic experiment). Test-only frontend.
// Local-only: links the pinned GPL-3.0 Geargrafx core (b49ae82a012eade566d72e9d61c9d95a5caa4da3) UNMODIFIED
// (no verification patch). Never distribute this executable or the linked core. Synthetic HuCARD only.
//
// It runs a test-only 8 KiB HuCARD one debugger step at a time and prints one JSON object per line:
//   CAP    instruction stepping works (the first step advances PC by one byte, SEI)
//   TRACE  first arrival at a label: step index, A/X/Y/P/S and MPR0-7, read BEFORE that instruction runs
//   IRQ    IRQ1 line asserted (after a step) or deasserted (at the IRQ1 entry)
//   WRW    a WRAM byte changed in a step: wram index (= logical address while MPR0 maps WRAM at $0000-$1FFF)
//   VDW    a VDC register changed in a step: AR, SR or R0-R31 (the VDC is the only I/O device observed; reads
//          are NOT observed, the probe reads the VDC state pointers only)
//   XFER   one CPU step while PC is at the TII label: completed flag and PC after the step. A TII runs one byte
//          per CPU step with PC held on the opcode until the transfer ends (huc6280_opcodes_inline.h, TII state 2)
//   HITS   how many steps began at each label
//   END    final PC, step count, MPR0-7, WRAM $0200-$023F
// Steps: the first step is the debugger step of RunToVBlank (it also sets the core's hardware clock callback).
// Every later step is one HuC6280::RunInstruction call (public, huc6280.h:124): one instruction, or one TII/TIA
// byte chunk. This is the finest granularity the core exposes; RunToVBlank would run a whole block transfer in one
// debugger step (geargrafx_core_inline.h, stop = instruction_completed).
// argv: rom labels steps irq   (irq: none | tii-irq1)
// Exit: 0 run, 2 usage, 3 ROM load, 4 label file, 5 probe refused (no instruction stepping).
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
#include <set>
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
    char* end = nullptr;
    long steps = std::strtol(argv[3], &end, 10);
    if (!end || *end || steps < 1 || steps > 2048)
        return 2;
    std::string irq = argv[4];
    if (irq != "none" && irq != "tii-irq1")
        return 2;
    for (const char* name : {"tii_start", "irq1_entry", "guard_rts"})
        if (!labels.count(name))
            return 4;

    GeargrafxCore core;
    core.Init(nullptr);
    core.GetMedia()->SetCDROMType(GG_CDROM_STANDARD);
    if (!core.LoadHuCardFromBuffer(rom.data(), (int)rom.size(), "caller-stack-contract.pce"))
        return 3;

    HuC6280* processor = core.GetHuC6280();
    HuC6280::HuC6280_State* cpu = processor->GetState();
    Memory* memory = core.GetMemory();
    HuC6270::HuC6270_State* vs = core.GetHuC6270_1()->GetState();

    std::vector<u8> frame(1024 * 512 * 4);
    std::vector<s16> audio(65536);
    GeargrafxCore::GG_Debug_Run debug = {};
    debug.step_debugger = true;
    auto first_step = [&]() {
        int samples = 0;
        core.RunToVBlank(frame.data(), audio.data(), &samples, &debug, false);
    };

    unsigned first_pc = cpu->PC->GetValue();
    if (memory->GetMpr(7) != 0 || memory->Read(first_pc) != 0x78)  // reset: MPR7 = ROM bank 0, first byte SEI
        return 5;
    first_step();
    bool stepping = cpu->PC->GetValue() == first_pc + 1;
    std::printf("CAP {\"instruction_step\":%s,\"pc_before\":%u,\"pc_after\":%u}\n", stepping ? "true" : "false",
                first_pc, cpu->PC->GetValue());
    if (!stepping)
        return 5;

    std::vector<u8> wram_prev(memory->GetWorkingRAM(), memory->GetWorkingRAM() + 0x2000);
    VdcSnap vdc_prev = snap_vdc(vs);
    std::map<std::string, long> hits;
    std::set<std::string> seen;
    bool irq_armed = irq == "tii-irq1", irq_line = false;
    long xfer_count = 0;
    const unsigned tii_label = labels["tii_start"];

    auto print_trace = [&](const std::string& name, long step, unsigned pc) {
        std::printf("TRACE {\"name\":\"%s\",\"step\":%ld,\"pc\":%u,\"a\":%u,\"x\":%u,\"y\":%u,\"p\":%u,\"sp\":%u,\"mpr\":[",
                    name.c_str(), step, pc, cpu->A->GetValue(), cpu->X->GetValue(), cpu->Y->GetValue(),
                    cpu->P->GetValue(), cpu->S->GetValue());
        for (int i = 0; i < 8; ++i)
            std::printf("%s%u", i ? "," : "", memory->GetMpr(i));
        std::printf("]}\n");
    };

    // Step 0 was the SEI above; it is counted as step 0 so the indices match the instruction order.
    for (long i = 1; i < steps; ++i) {
        unsigned pc = cpu->PC->GetValue();
        for (const auto& item : labels) {
            if (pc != item.second)
                continue;
            hits[item.first] += 1;
            if (!seen.count(item.first)) {
                print_trace(item.first, i, pc);
                seen.insert(item.first);
            }
        }
        if (irq_line && pc == labels["irq1_entry"]) {  // IRQ1 entry: release the line, as the other probes do
            processor->AssertIRQ1(false);
            irq_line = false;
            std::printf("IRQ {\"event\":\"deasserted_at_entry\",\"step\":%ld,\"pc\":%u}\n", i, pc);
        }
        bool completed = false;
        processor->RunInstruction(&completed);
        unsigned pc_after = cpu->PC->GetValue();
        if (pc == tii_label) {
            xfer_count += 1;
            std::printf("XFER {\"step\":%ld,\"pc_before\":%u,\"pc_after\":%u,\"completed\":%s}\n", i, pc,
                        pc_after, completed ? "true" : "false");
        }
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
                std::printf("VDW {\"step\":%ld,\"pc\":%u,\"reg\":\"R%d\",\"old\":%u,\"new\":%u}\n", i, pc, r,
                            vdc_prev.r[r], vdc.r[r]);
        vdc_prev = vdc;
        if (irq_armed && pc == tii_label && !completed && xfer_count == 3) {  // mid-transfer: 2 bytes already moved
            processor->AssertIRQ1(true);
            irq_armed = false;
            irq_line = true;
            std::printf("IRQ {\"event\":\"asserted\",\"step\":%ld,\"pc\":%u}\n", i, pc_after);
        }
    }

    std::printf("HITS {");
    bool first = true;
    for (const auto& item : hits) {
        std::printf("%s\"%s\":%ld", first ? "" : ",", item.first.c_str(), item.second);
        first = false;
    }
    std::printf("}\n");
    std::printf("END {\"steps\":%ld,\"pc\":%u,\"mpr\":[", steps, cpu->PC->GetValue());
    for (int i = 0; i < 8; ++i)
        std::printf("%s%u", i ? "," : "", memory->GetMpr(i));
    std::printf("],\"wram_0200_hex\":\"");
    for (int i = 0x200; i < 0x240; ++i)
        std::printf("%02x", memory->GetWorkingRAM()[i]);
    std::printf("\"}\n");
    return 0;
}
