// Original test frontend for the MA_MUL16S real-core sweeps. External Geargrafx GPL-linked executable stays local.
// Observer only: the guest HuC6280 code does every multiplication; this program steps the pinned core one
// instruction at a time, records the guest's result area at a checkpoint PC, and watches the routine's own
// instructions with the core's CPU-address breakpoints (a store of the SAME value or a write-and-restore still
// raises the flag, which a before/after RAM diff cannot see).
//
// usage: probe <rom> <cue> <config>
// config lines (numbers are hex with 0x or decimal):
//   halt <pc>              stop when the guest reaches this PC (its BRA * loop)
//   max <steps>            instruction budget (exit 9 when exhausted)
//   window <lo> <hi>       the routine under test occupies PC in [lo, hi); violations are attributed by PC
//   rec <pc> <addr> <len>  at every arrival at <pc> print "REC <hex of CPU bytes addr..addr+len>"
//   poke <addr> <hexbytes> write bytes into work RAM (CPU address $2000-$3FFF) before the guest starts
//   wbp <lo> <hi>          CPU-address WRITE breakpoint range (any write there by the routine is a violation)
//   rbp <lo> <hi>          CPU-address READ breakpoint range
#include "geargrafx_core.h"
#include "huc6280.h"
#include "memory.h"
#include "media.h"
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iterator>
#include <sstream>
#include <string>
#include <vector>

int main(int argc, char** argv) {
    if (argc != 4) return 2;
    std::ifstream input(argv[1], std::ios::binary);
    if (!input) return 2;
    std::vector<u8> rom((std::istreambuf_iterator<char>(input)), {});
    unsigned long halt = 0, window_lo = 0, window_hi = 0, rec_pc = 0xFFFFFFFFUL, rec_addr = 0, rec_len = 0;
    unsigned long max_steps = 0;
    struct Range { unsigned long lo, hi; bool read; };
    std::vector<Range> ranges;
    struct Poke { unsigned long addr; std::vector<u8> bytes; };
    std::vector<Poke> pokes;
    {
        std::ifstream cfg(argv[3]);
        if (!cfg) return 2;
        std::string line;
        while (std::getline(cfg, line)) {
            std::istringstream is(line);
            std::string key;
            if (!(is >> key)) continue;
            auto num = [&](unsigned long& v) { std::string t; is >> t; v = std::strtoul(t.c_str(), nullptr, 0); };
            if (key == "halt") num(halt);
            else if (key == "max") num(max_steps);
            else if (key == "window") { num(window_lo); num(window_hi); }
            else if (key == "rec") { num(rec_pc); num(rec_addr); num(rec_len); }
            else if (key == "wbp" || key == "rbp") { Range r{0, 0, key == "rbp"}; num(r.lo); num(r.hi); ranges.push_back(r); }
            else if (key == "poke") {
                Poke p; std::string hex; num(p.addr); is >> hex;
                for (size_t i = 0; i + 1 < hex.size(); i += 2) p.bytes.push_back((u8)std::strtoul(hex.substr(i, 2).c_str(), nullptr, 16));
                pokes.push_back(p);
            } else return 2;
        }
    }
    GeargrafxCore core;
    core.Init(nullptr);
    if (!core.LoadBiosFromBuffer(rom.data(), rom.size(), true)) return 3;
    if (!core.LoadMedia(argv[2])) return 4;
    auto cpu_obj = core.GetHuC6280();
    auto cpu = cpu_obj->GetState();
    auto memory = core.GetMemory();
    u8* wram = memory->GetWorkingRAM();
    for (const auto& p : pokes) {
        if (p.addr < 0x2000 || p.addr + p.bytes.size() > 0x4000) return 2;
        for (size_t i = 0; i < p.bytes.size(); i++) wram[p.addr - 0x2000 + i] = p.bytes[i];
    }
    auto observe = [&](const char* phase) {
        std::printf("OBS {\"phase\":\"%s\",\"pc\":%u,\"a\":%u,\"x\":%u,\"y\":%u,\"p\":%u,\"sp\":%u,\"mpr\":[", phase,
                    cpu->PC->GetValue(), cpu->A->GetValue(), cpu->X->GetValue(), cpu->Y->GetValue(),
                    cpu->P->GetValue(), cpu->S->GetValue());
        for (int i = 0; i < 8; i++) std::printf("%s%u", i ? "," : "", memory->GetMpr(i));
        std::printf("],\"work_ram_hex\":\"");
        for (int i = 0; i < 0x2000; i++) std::printf("%02x", wram[i]);
        std::printf("\"}\n");
    };
    std::vector<u8> frame(1024 * 512 * 4);
    std::vector<s16> audio(65536);
    GeargrafxCore::GG_Debug_Run debug = {};
    debug.step_debugger = true;
    debug.stop_on_breakpoint = true;  // activates the CPU-address breakpoints; the flag is read and cleared after every instruction
    auto one_instruction = [&]() {
        int samples = 0;
        core.RunToVBlank(frame.data(), audio.data(), &samples, &debug, false);
    };
    observe("loaded");
    unsigned first_pc = cpu->PC->GetValue();
    if (memory->Read(first_pc) != 0x78) return 5;  // every execution probe begins SEI
    one_instruction();
    if (cpu->PC->GetValue() != first_pc + 1) return 6;
    unsigned initial_mpr[8];
    for (int i = 0; i < 8; i++) initial_mpr[i] = memory->GetMpr(i);
    for (const auto& r : ranges)
        if (!cpu_obj->AddBreakpoint(HuC6280::HuC6280_BREAKPOINT_TYPE_CPU_ADDRESS, (u32)r.lo, (u32)r.hi, true,
                                    r.read, !r.read, false))
            return 7;
    cpu_obj->ResetDebuggerExecutionState();
    unsigned long steps = 0, routine_steps = 0, viol = 0, mpr_changes = 0, recs = 0;
    bool ok = false;
    while (steps < max_steps) {
        unsigned pc = cpu->PC->GetValue();
        if (pc == halt) { ok = true; break; }
        if (pc == rec_pc) {
            std::printf("REC ");
            for (unsigned long i = 0; i < rec_len; i++) std::printf("%02x", memory->Read((u16)(rec_addr + i)));
            std::printf("\n");
            recs++;
        }
        bool inside = pc >= window_lo && pc < window_hi;
        one_instruction();
        steps++;
        if (inside) routine_steps++;
        if (cpu_obj->MemoryBreakpointHit()) {
            if (inside) {
                if (viol < 20) std::printf("VIOL {\"pc\":%u,\"step\":%lu}\n", pc, steps);
                viol++;
            }
            cpu_obj->ResetDebuggerExecutionState();
        }
        for (int i = 0; i < 8; i++)
            if (memory->GetMpr(i) != initial_mpr[i]) {
                if (inside && mpr_changes < 20) std::printf("MPRCHG {\"pc\":%u,\"step\":%lu,\"mpr\":%d}\n", pc, steps, i);
                if (inside) mpr_changes++;  // the guest's own TAM (outside the window) just moves the baseline
                initial_mpr[i] = memory->GetMpr(i);
            }
    }
    observe("final");
    std::printf("SUMMARY {\"ok\":%s,\"steps\":%lu,\"routine_steps\":%lu,\"records\":%lu,\"violations\":%lu,\"mpr_changes\":%lu}\n",
                ok ? "true" : "false", steps, routine_steps, recs, viol, mpr_changes);
    return ok ? 0 : 9;
}
