// EX_SATCLR B1-analogous (slot $36) WRAM write-tracer probe. DEV_EXPERIMENTAL, NOT reference ABI.
// argv: rom cue steps spinit
// Guest (patched at the reset vector, HuCARD-style): SEI/CLD, MPR1=$F8,
// SP=spinit, MPR0=$F8, entry regs A=$3C/X=$11/Y=$22/P=$07, JSR $E0A2, halt.
// The C++ side snapshots the whole WRAM (GetWorkingRAMSize, 8 KiB on the
// pinned core), single-steps the guest, and OR-accumulates per-instruction
// WRAM diffs into a written[] bitmap. A write-then-restore inside the call
// is still recorded (history, not just final values). Runs cold then warm
// (ResetMedia between, like the return probe) and reports one RESULT line
// per cycle with the written ranges merged. Genuine $36 semantics stay
// UNKNOWN/BLOCKED.
// Residual limit: this tracer OR-accumulates per-instruction WRAM value
// diffs, so its RESULT is observed value changes only. Same-value stores
// (writing back the byte already present) are invisible by construction and
// remain NOT_RUN/BLOCKED (unverified), never PASS (see docs/64-ex-satclr-b1.md
// §3). Only state-changing writes are claimed as detected.
// Halt arrival (first PC==halt step H) is reported as independent `arrival`
// field via first-sighting logic separate from the 16-confirmation counter;
// boundary budgets H-1..H+17 are tested against that independent H, never
// against steps_full derived from the threshold under test.
#include "geargrafx_core.h"
#include "input.h"
#include "media.h"
#include "memory.h"
#include "huc6280.h"
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iterator>
#include <cerrno>
#include <vector>

static void dummy_clock(void*, u32) {}

static bool parse_u8_arg(const char* s, long* out) {
    if (!s || *s == '\0') return false;
    errno = 0;
    char* end = nullptr;
    long v = std::strtol(s, &end, 0);
    if (errno != 0 || end == s || !end || *end != '\0') return false;
    if (v < 0 || v > 255) return false;
    *out = v;
    return true;
}

int main(int argc, char** argv) {
    if (argc != 5) { fprintf(stderr, "argc\n"); return 2; }
    errno = 0;
    char* end = nullptr;
    long steps = std::strtol(argv[3], &end, 10);
    if (errno != 0 || end == argv[3] || !end || *end != '\0' || steps < 1 || steps > 65536) {
        fprintf(stderr, "steps\n"); return 2;
    }
    long spinit = 0;
    if (!parse_u8_arg(argv[4], &spinit)) { fprintf(stderr, "spinit\n"); return 2; }
    // Contract guard: caller SP below $06 is OUT OF CONTRACT (JSR 2 bytes +
    // 4 body pushes need $2101-$2106). Reject BEFORE any guest construction
    // or execution: no ROM patch, no core init, no RunInstruction may run.
    if (spinit < 0x06) { fprintf(stderr, "spinit out of contract (need >= $06)\n"); return 2; }

    std::ifstream input(argv[1], std::ios::binary);
    std::vector<u8> rom((std::istreambuf_iterator<char>(input)), {});
    if (rom.size() != 0x40000) { fprintf(stderr, "size %zu\n", rom.size()); return 3; }

    const u16 reset = (u16)rom[0x1FFE] | ((u16)rom[0x1FFF] << 8);
    const u16 entry = 0xE000 + 3 * 0x36;
    std::vector<u8> code = {
        0x78, 0xD8,                         // SEI, CLD
        0xA9, 0xF8, 0x53, 0x02,               // MPR1 = $F8 (work RAM)
        0xA2, (u8)(spinit & 0xFF), 0x9A,      // SP = spinit
        0xA9, 0xF8, 0x53, 0x01,               // MPR0 = $F8
        0xA0, 0x22,                           // Y = $22
        0xA2, 0x11,                           // X = $11
        0xA9, 0x07, 0x48,                     // PHA (push P=$07 image)
        0xA9, 0x3C,                           // A = $3C
        0x28,                                 // PLP: P = $07 latch, A stays $3C
        0x20, (u8)(entry & 0xFF), (u8)(entry >> 8),  // JSR $E0A2
    };
    const u16 halt = (u16)(reset + (u32)code.size());
    code.push_back(0x80); code.push_back(0xFE);  // BRA -2
    if (reset < 0xE000 || (u32)reset + (u32)code.size() > 0xFFF6u) { fprintf(stderr, "reset\n"); return 3; }
    for (size_t i = 0; i < code.size(); ++i) rom[(size_t)(reset - 0xE000) + i] = code[i];

    GeargrafxCore core;
    core.Init(nullptr);
    core.GetMedia()->SetCDROMType(GG_CDROM_STANDARD);
    if (!core.LoadBiosFromBuffer(rom.data(), (int)rom.size(), true)) { fprintf(stderr, "loadbios\n"); return 3; }
    if (!core.LoadMedia(argv[2])) { fprintf(stderr, "loadmedia\n"); return 3; }
    core.GetHuC6280()->SetHardwareClock(dummy_clock, nullptr);

    HuC6280* cpu = core.GetHuC6280();
    Memory* mem = core.GetMemory();
    const int wsize = mem->GetWorkingRAMSize();
    if (wsize <= 0 || wsize > 0x8000) { fprintf(stderr, "wsize %d\n", wsize); return 3; }

    const char* cycle = "cold";
    auto run_once = [&](bool warm) {
        cycle = warm ? "warm" : "cold";
        std::vector<u8> prev(wsize);
        const u8* wram0 = mem->GetWorkingRAM();
        for (int i = 0; i < wsize; i++) prev[i] = wram0[i];
        std::vector<char> written(wsize, 0);
        long distinct = 0;
        bool halted = false;
        long run_steps = 0;
        long confirm = 0;
        long arrival = 0;
        for (long i = 0; i < steps; ++i) {
            u16 pc_before = cpu->GetState()->PC->GetValue();
            cpu->RunInstruction();
            run_steps++;
            u16 pc_after = cpu->GetState()->PC->GetValue();
            const u8* w = mem->GetWorkingRAM();
            for (int k = 0; k < wsize; k++) {
                if (w[k] != prev[k]) {
                    if (!written[k]) { written[k] = 1; distinct++; }
                    prev[k] = w[k];
                }
            }
            // Independent arrival: first step where PC lands on halt.
            // Separate from the confirmation counter below, so boundary
            // budgets H-1..H+17 do not derive expectations from the
            // threshold under test.
            if (arrival == 0 && pc_after == halt) {
                arrival = run_steps;
            }
            // Halt counts only when both the executed instruction started
            // and ended at halt (pc_before == halt && pc_after == halt),
            // i.e. a BRA -2 self-loop execution. The arrival step has
            // pc_before != halt and is never counted. Leaving halt resets.
            // 15 such executions stay INCONCLUSIVE (halted false), 16
            // confirm halt. Budget exhaustion before confirmation is
            // INCONCLUSIVE, never RED/GREEN.
            if (pc_before == halt && pc_after == halt) {
                if (++confirm >= 16) { halted = true; break; }
            } else {
                confirm = 0;
            }
        }
        // Merge written bitmap into inclusive ranges (cap 256 for output).
        std::vector<std::pair<int,int>> ranges;
        bool truncated = distinct > 256;
        int k = 0;
        while (k < wsize && (int)ranges.size() < 256) {
            if (written[k]) {
                int s = k;
                while (k < wsize && written[k]) k++;
                ranges.push_back({s, k - 1});
            } else k++;
        }
        std::printf("RESULT {\"cycle\":\"%s\",\"pc\":%u,\"halt\":%u,\"halted\":%s,"
                    "\"steps\":%ld,\"arrival\":%ld,\"truncated\":%s,\"written\":[",
                    cycle, cpu->GetState()->PC->GetValue(), halt,
                    halted ? "true" : "false", run_steps, arrival, truncated ? "true" : "false");
        for (size_t r = 0; r < ranges.size(); r++)
            std::printf("%s[%d,%d]", r ? "," : "", ranges[r].first, ranges[r].second);
        std::printf("]}\n");
    };
    run_once(false);
    core.ResetMedia(true);
    run_once(true);
    return 0;
}
