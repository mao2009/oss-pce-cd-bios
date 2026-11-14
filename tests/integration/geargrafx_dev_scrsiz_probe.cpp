// DEV EX_SCRSIZ (slot $23) real-core probe. DEV_EXPERIMENTAL, NOT reference ABI.
// argv: rom cue steps reg_lo reg_hi
#include "geargrafx_core.h"
#include "input.h"
#include "media.h"
#include "memory.h"
#include "huc6280.h"
#include "huc6270.h"
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
    if (argc != 6) { fprintf(stderr, "argc\n"); return 2; }
    errno = 0;
    char* end = nullptr;
    long steps = std::strtol(argv[3], &end, 10);
    if (errno != 0 || end == argv[3] || !end || *end != '\0' || steps < 1 || steps > 4096) {
        fprintf(stderr, "steps\n"); return 2;
    }
    long reg_lo = 0, reg_hi = 0;
    if (!parse_u8_arg(argv[4], &reg_lo)) { fprintf(stderr, "reg_lo\n"); return 2; }
    if (!parse_u8_arg(argv[5], &reg_hi)) { fprintf(stderr, "reg_hi\n"); return 2; }

    std::ifstream input(argv[1], std::ios::binary);
    std::vector<u8> rom((std::istreambuf_iterator<char>(input)), {});
    if (rom.size() != 0x40000) { fprintf(stderr, "size %zu\n", rom.size()); return 3; }

    const u16 reset = (u16)rom[0x1FFE] | ((u16)rom[0x1FFF] << 8);
    const u16 entry = 0xE000 + 3 * 0x23;
    const u16 store = 0x2200;
    // MPR0 initial $F8 (non-I/O), not $FF, so a missing restore is detectable;
    // I/O during call requires MPR0=$FF, and MPR0 must be restored to $F8 after.
    std::vector<u8> code = {
        0x78, 0xD8,
        0xA9, 0xF8, 0x53, 0x02,
        0xA2, 0xFF, 0x9A,
        0xA9, 0xF8, 0x53, 0x01,       // MPR0 = $F8 (known non-I/O initial)
        0xA0, 0xA5,
        0xA2, (u8)(reg_hi & 0xFF),
        0xA9, (u8)(reg_lo & 0xFF),
        0x20, (u8)(entry & 0xFF), (u8)(entry >> 8),
        0x08,
        0x8D, (u8)(store & 0xFF), (u8)(store >> 8),
        0x68, 0x8D, (u8)((store + 1) & 0xFF), (u8)((store + 1) >> 8),
        0x8E, (u8)((store + 2) & 0xFF), (u8)((store + 2) >> 8),
        0x8C, (u8)((store + 3) & 0xFF), (u8)((store + 3) >> 8),
        0xBA, 0x8E, (u8)((store + 4) & 0xFF), (u8)((store + 4) >> 8),
        0x43, 0x01, 0x8D, (u8)((store + 5) & 0xFF), (u8)((store + 5) >> 8),
    };
    const u16 halt = (u16)(reset + (u32)code.size());
    code.push_back(0x80); code.push_back(0xFE);
    if (reset < 0xE000 || (u32)reset + (u32)code.size() > 0xFFF6u) { fprintf(stderr, "reset\n"); return 3; }
    for (size_t i = 0; i < code.size(); ++i) rom[(size_t)(reset - 0xE000) + i] = code[i];

    GeargrafxCore core;
    core.Init(nullptr);
    core.GetMedia()->SetCDROMType(GG_CDROM_STANDARD);
    if (!core.LoadBiosFromBuffer(rom.data(), (int)rom.size(), true)) { fprintf(stderr, "loadbios\n"); return 3; }
    if (!core.LoadMedia(argv[2])) { fprintf(stderr, "loadmedia\n"); return 3; }
    core.GetHuC6280()->SetHardwareClock(dummy_clock, nullptr);

    HuC6280* cpu = core.GetHuC6280();
    for (long i = 0; i < steps; ++i) {
        cpu->RunInstruction();
        if (cpu->GetState()->PC->GetValue() == halt) break;
    }
    const HuC6280::HuC6280_State* st = cpu->GetState();
    Memory* mem = core.GetMemory();
    const u8* wram = mem->GetWorkingRAM() + (store - 0x2000);
    HuC6270::HuC6270_State* vs = core.GetHuC6270_1()->GetState();
    unsigned vdc_ar = *vs->AR;
    unsigned vdc_r9 = vs->R[9];
    std::printf("RESULT {\"pc\":%u,\"sp\":%u,\"mpr\":[%u,%u,%u,%u,%u,%u,%u,%u],\"a\":%u,\"p\":%u,\"x\":%u,\"y\":%u,\"sp_stored\":%u,\"mpr0_stored\":%u,\"halt\":%u,\"vdc_ar\":%u,\"vdc_r9\":%u}\n",
        st->PC->GetValue(), st->S->GetValue(),
        mem->GetMpr(0),mem->GetMpr(1),mem->GetMpr(2),mem->GetMpr(3),mem->GetMpr(4),mem->GetMpr(5),mem->GetMpr(6),mem->GetMpr(7),
        wram[0], wram[1], wram[2], wram[3], wram[4], wram[5], halt, vdc_ar, vdc_r9);
    return 0;
}
