// EX_IRQON D2 limited-functional (slot $2C) VDC-state probe. DEV_EXPERIMENTAL, NOT reference ABI.
// argv: rom cue steps entry_ar areg xreg pseed seed_lo seed_en
// Guest (patched at the reset vector, HuCARD-style like the scrsiz probe):
//   SEI/CLD, MPR1=$F8, seed $2502=seed_lo + $2503=(seed_en?$A5:$00),
//   SP=$FF, MPR0=$FF to select VDC AR=entry_ar via ST0,
//   MPR0=$F8 restore, entry regs A=areg/X=xreg/Y=$A5/P=pseed, JSR $E084,
//   record A/P/X/Y/SP/MPR0-7 into $2200, halt (BRA -2).
// The C++ side snapshots VDC R[0..19] + AR + MPRs + IDR/IRR BEFORE running
// and AFTER the halt. Python asserts per path: ok (seed_en=1) CR low ==
// seed_lo|$08 with other VDC regs unchanged and AR_after == 5; reject
// (seed_en=0) all VDC regs unchanged. MPRs/IDR/IRR unchanged, A/X/Y/SP
// preserved, C per the D2 convention. Genuine $2C semantics stay
// UNKNOWN/BLOCKED.
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
    if (argc != 10) { fprintf(stderr, "argc\n"); return 2; }
    errno = 0;
    char* end = nullptr;
    long steps = std::strtol(argv[3], &end, 10);
    if (errno != 0 || end == argv[3] || !end || *end != '\0' || steps < 1 || steps > 65536) {
        fprintf(stderr, "steps\n"); return 2;
    }
    long entry_ar = 0, areg = 0, xreg = 0, pseed = 0, seed_lo = 0, seed_en = 0;
    if (!parse_u8_arg(argv[4], &entry_ar)) { fprintf(stderr, "entry_ar\n"); return 2; }
    if (!parse_u8_arg(argv[5], &areg)) { fprintf(stderr, "areg\n"); return 2; }
    if (!parse_u8_arg(argv[6], &xreg)) { fprintf(stderr, "xreg\n"); return 2; }
    if (!parse_u8_arg(argv[7], &pseed)) { fprintf(stderr, "pseed\n"); return 2; }
    if (!parse_u8_arg(argv[8], &seed_lo)) { fprintf(stderr, "seed_lo\n"); return 2; }
    if (!parse_u8_arg(argv[9], &seed_en)) { fprintf(stderr, "seed_en\n"); return 2; }
    if (seed_en != 0 && seed_en != 1) { fprintf(stderr, "seed_en domain\n"); return 2; }

    std::ifstream input(argv[1], std::ios::binary);
    std::vector<u8> rom((std::istreambuf_iterator<char>(input)), {});
    if (rom.size() != 0x40000) { fprintf(stderr, "size %zu\n", rom.size()); return 3; }

    const u16 reset = (u16)rom[0x1FFE] | ((u16)rom[0x1FFF] << 8);
    const u16 entry = 0xE000 + 3 * 0x2C;
    const u16 store = 0x2200;
    std::vector<u8> code = {
        0x78, 0xD8,                         // SEI, CLD
        0xA9, 0xF8, 0x53, 0x02,               // MPR1 = $F8 (work RAM)
        0xA9, (u8)(seed_lo & 0xFF),
        0x8D, 0x02, 0x25,                     // $2502 = seed_lo (shadow image)
        0xA9, (u8)(seed_en ? 0xA5 : 0x00),
        0x8D, 0x03, 0x25,                     // $2503 = $A5 iff seeded (else $00)
        0xA2, 0xFF, 0x9A,                     // SP = $FF
        0xA9, 0xFF, 0x53, 0x01,               // MPR0 = $FF (I/O window)
        0xA9, (u8)(entry_ar & 0xFF),
        0x8D, 0x00, 0x00,                     // ST0: AR = entry_ar
        0xA9, 0xF8, 0x53, 0x01,               // MPR0 = $F8 (restore before JSR)
        0xA0, 0xA5,                           // Y = $A5 sentinel
        0xA2, (u8)(xreg & 0xFF),              // X = xreg
        0xA9, (u8)(pseed & 0xFF), 0x48,       // PHA (push pseed image)
        0xA9, (u8)(areg & 0xFF),              // A = areg (differs from P)
        0x28,                                 // PLP: P = pseed latch, A stays areg
        0x20, (u8)(entry & 0xFF), (u8)(entry >> 8),  // JSR $E084
        0x8D, (u8)(store & 0xFF), (u8)(store >> 8),  // store+0 = A
        0x08,                                     // PHP
        0x68, 0x8D, (u8)((store + 1) & 0xFF), (u8)((store + 1) >> 8),  // store+1 = P
        0x8E, (u8)((store + 2) & 0xFF), (u8)((store + 2) >> 8),  // store+2 = X
        0x8C, (u8)((store + 3) & 0xFF), (u8)((store + 3) >> 8),  // store+3 = Y
        0xBA, 0x8E, (u8)((store + 4) & 0xFF), (u8)((store + 4) >> 8),  // store+4 = SP
        0x43, 0x01, 0x8D, (u8)((store + 5) & 0xFF), (u8)((store + 5) >> 8),
        0x43, 0x02, 0x8D, (u8)((store + 6) & 0xFF), (u8)((store + 6) >> 8),
        0x43, 0x04, 0x8D, (u8)((store + 7) & 0xFF), (u8)((store + 7) >> 8),
        0x43, 0x08, 0x8D, (u8)((store + 8) & 0xFF), (u8)((store + 8) >> 8),
        0x43, 0x10, 0x8D, (u8)((store + 9) & 0xFF), (u8)((store + 9) >> 8),
        0x43, 0x20, 0x8D, (u8)((store + 10) & 0xFF), (u8)((store + 10) >> 8),
        0x43, 0x40, 0x8D, (u8)((store + 11) & 0xFF), (u8)((store + 11) >> 8),
        0x43, 0x80, 0x8D, (u8)((store + 12) & 0xFF), (u8)((store + 12) >> 8),
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
    HuC6270::HuC6270_State* vs = core.GetHuC6270_1()->GetState();

    u16 vdc_r_before[20], vdc_r_after[20];
    for (int i = 0; i < 20; i++) vdc_r_before[i] = vs->R[i];
    unsigned vdc_ar_before = *vs->AR;
    unsigned mpr_before[8], mpr_after[8];
    for (int i = 0; i < 8; i++) mpr_before[i] = mem->GetMpr(i);
    unsigned idr_before = *cpu->GetState()->IDR;
    unsigned irr_before = *cpu->GetState()->IRR;

    for (long i = 0; i < steps; ++i) {
        cpu->RunInstruction();
        if (cpu->GetState()->PC->GetValue() == halt) break;
    }
    const HuC6280::HuC6280_State* st = cpu->GetState();
    const u8* wram = mem->GetWorkingRAM() + (store - 0x2000);
    for (int i = 0; i < 20; i++) vdc_r_after[i] = vs->R[i];
    unsigned vdc_ar_after = *vs->AR;
    for (int i = 0; i < 8; i++) mpr_after[i] = mem->GetMpr(i);
    unsigned idr_after = *st->IDR;
    unsigned irr_after = *st->IRR;
    std::printf("RESULT {\"pc\":%u,\"halt\":%u,\"a\":%u,\"p\":%u,\"x\":%u,\"y\":%u,\"sp\":%u,",
        st->PC->GetValue(), halt, wram[0], wram[1], wram[2], wram[3], wram[4]);
    std::printf("\"mpr_store\":[%u,%u,%u,%u,%u,%u,%u,%u],", wram[5], wram[6], wram[7], wram[8],
        wram[9], wram[10], wram[11], wram[12]);
    std::printf("\"mpr_before\":[%u,%u,%u,%u,%u,%u,%u,%u],", mpr_before[0], mpr_before[1],
        mpr_before[2], mpr_before[3], mpr_before[4], mpr_before[5], mpr_before[6], mpr_before[7]);
    std::printf("\"mpr_after\":[%u,%u,%u,%u,%u,%u,%u,%u],", mpr_after[0], mpr_after[1],
        mpr_after[2], mpr_after[3], mpr_after[4], mpr_after[5], mpr_after[6], mpr_after[7]);
    std::printf("\"vdc_ar_before\":%u,\"vdc_ar_after\":%u,\"vdc_r_before\":[", vdc_ar_before, vdc_ar_after);
    for (int i = 0; i < 20; i++) std::printf("%s%u", i ? "," : "", vdc_r_before[i]);
    std::printf("],\"vdc_r_after\":[");
    for (int i = 0; i < 20; i++) std::printf("%s%u", i ? "," : "", vdc_r_after[i]);
    std::printf("],\"idr_before\":%u,\"idr_after\":%u,\"irr_before\":%u,\"irr_after\":%u}\n",
        idr_before, idr_after, irr_before, irr_after);
    return 0;
}
