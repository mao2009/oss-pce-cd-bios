// EX_RCROFF D2 desync probe for Issue #861 L4 (slot $2B, synthetic only).
// DEV_EXPERIMENTAL, NOT reference ABI. NOT release eligible.
// Genuine $2B semantics stay UNKNOWN/BLOCKED. No commercial bytes, no ./rom/ access.
//
// Purpose: separate the shadow image from the real VDC CR value to confirm
// the L2 host-model false-ok end to end on the pinned real core:
//   shadow $2502 = shadow_lo, marker $2503 = marker (explicit byte),
//   real VDC CR low = real_lo (programmed through the window BEFORE the JSR,
//   independent of the shadow), CR high = crhi, RCR = rcr_lo sentinel.
// The $2B body reads ONLY the shadow; the gate checks ONLY the marker.
// Desync case (hypothesis, not fudged): real $00 + shadow $FF + marker $A5
// must take the ok path and publish the WRONG image CR=$FB (stale $FF & $FB).
//
// argv: rom cue steps entry_ar areg xreg pseed shadow_lo marker real_lo crhi rcr_lo ncalls
// marker is the explicit $2503 seed byte (0..255); the old seed_en?0xA5:0x00
// encoding is retired because it could never seed $5A (blind spot: the $5A
// loader-fill test passed seed_en=0 and therefore exercised marker $00).
// Guest (patched at reset vector, HuCARD-style): SEI/CLD, MPR1=$F8,
// seed shadow, SP=$FF, MPR0=$FF window programs real CR/RCR/AR, restore
// MPR0=$F8, entry regs, JSR $E081 (ncalls 1..2), record into $2200, halt.
// Runs cold then warm (ResetMedia(true) between, RAM preserved like the
// return probe) and reports one RESULT line per cycle. SR scope: GetState
// reads only, frozen clock (dummy_clock); pending-SR/IRQ timing NOT_RUN.
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
    if (argc != 14) { fprintf(stderr, "argc want 14 got %d\n", argc); return 2; }
    errno = 0;
    char* end = nullptr;
    long steps = std::strtol(argv[3], &end, 10);
    if (errno != 0 || end == argv[3] || !end || *end != '\0' || steps < 1 || steps > 65536) {
        fprintf(stderr, "steps\n"); return 2;
    }
    long entry_ar = 0, areg = 0, xreg = 0, pseed = 0, shadow_lo = 0, marker = 0;
    long real_lo = 0, crhi = 0, rcr_lo = 0, ncalls = 0;
    if (!parse_u8_arg(argv[4], &entry_ar)) { fprintf(stderr, "entry_ar\n"); return 2; }
    if (!parse_u8_arg(argv[5], &areg)) { fprintf(stderr, "areg\n"); return 2; }
    if (!parse_u8_arg(argv[6], &xreg)) { fprintf(stderr, "xreg\n"); return 2; }
    if (!parse_u8_arg(argv[7], &pseed)) { fprintf(stderr, "pseed\n"); return 2; }
    if (!parse_u8_arg(argv[8], &shadow_lo)) { fprintf(stderr, "shadow_lo\n"); return 2; }
    if (!parse_u8_arg(argv[9], &marker)) { fprintf(stderr, "marker\n"); return 2; }
    if (!parse_u8_arg(argv[10], &real_lo)) { fprintf(stderr, "real_lo\n"); return 2; }
    if (!parse_u8_arg(argv[11], &crhi)) { fprintf(stderr, "crhi\n"); return 2; }
    if (!parse_u8_arg(argv[12], &rcr_lo)) { fprintf(stderr, "rcr_lo\n"); return 2; }
    if (!parse_u8_arg(argv[13], &ncalls)) { fprintf(stderr, "ncalls\n"); return 2; }
    if (ncalls != 1 && ncalls != 2) { fprintf(stderr, "ncalls domain\n"); return 2; }

    std::ifstream input(argv[1], std::ios::binary);
    std::vector<u8> rom((std::istreambuf_iterator<char>(input)), {});
    if (rom.size() != 0x40000) { fprintf(stderr, "size %zu\n", rom.size()); return 3; }

    const u16 reset = (u16)rom[0x1FFE] | ((u16)rom[0x1FFF] << 8);
    const u16 entry = 0xE000 + 3 * 0x2B;
    const u16 store = 0x2200;
    std::vector<u8> code = {
        0x78, 0xD8,                         // SEI, CLD
        0xA9, 0xF8, 0x53, 0x02,               // MPR1 = $F8 (work RAM)
        0xA9, (u8)(shadow_lo & 0xFF),
        0x8D, 0x02, 0x25,                     // $2502 = shadow_lo (shadow image, may differ from real CR)
        0xA9, (u8)(marker & 0xFF),
        0x8D, 0x03, 0x25,                     // $2503 = marker (explicit; $A5 ok, else reject)
        0xA2, 0xFF, 0x9A,                     // SP = $FF
        0xA9, 0xFF, 0x53, 0x01,               // MPR0 = $FF (I/O window)
        0xA9, 0x05,                           // select VDC R05 (CR)
        0x8D, 0x00, 0x00,                     // ST0: AR = 5
        0xA9, (u8)(real_lo & 0xFF),
        0x8D, 0x02, 0x00,                     // ST1: real CR low = real_lo (INDEPENDENT of shadow)
        0xA9, (u8)(crhi & 0xFF),
        0x8D, 0x03, 0x00,                     // ST2: real CR high = crhi
        0xA9, 0x06,                           // select VDC R06 (RCR)
        0x8D, 0x00, 0x00,                     // ST0: AR = 6
        0xA9, (u8)(rcr_lo & 0xFF),
        0x8D, 0x02, 0x00,                     // ST1: RCR low = rcr_lo (sentinel)
        0xA9, 0x00,
        0x8D, 0x03, 0x00,                     // ST2: RCR high = $00
        0xA9, (u8)(entry_ar & 0xFF),
        0x8D, 0x00, 0x00,                     // ST0: AR = entry_ar
        0xA9, 0xF8, 0x53, 0x01,               // MPR0 = $F8 (restore before JSR)
        0xA0, 0xA5,                           // Y = $A5 sentinel
        0xA2, (u8)(xreg & 0xFF),              // X = xreg
        0xA9, (u8)(pseed & 0xFF), 0x48,       // PHA (push pseed image)
        0xA9, (u8)(areg & 0xFF),              // A = areg (differs from P)
        0x28,                                 // PLP: P = pseed latch, A stays areg
        0x20, (u8)(entry & 0xFF), (u8)(entry >> 8),  // JSR $E081 (call 1)
    };
    if (ncalls == 2) {
        code.push_back(0x20); code.push_back((u8)(entry & 0xFF)); code.push_back((u8)(entry >> 8));
    }
    code.insert(code.end(), {
        0x8D, (u8)(store & 0xFF), (u8)(store >> 8),
        0x08,
        0x68, 0x8D, (u8)((store + 1) & 0xFF), (u8)((store + 1) >> 8),
        0x8E, (u8)((store + 2) & 0xFF), (u8)((store + 2) >> 8),
        0x8C, (u8)((store + 3) & 0xFF), (u8)((store + 3) >> 8),
        0xBA, 0x8E, (u8)((store + 4) & 0xFF), (u8)((store + 4) >> 8),
        0x43, 0x01, 0x8D, (u8)((store + 5) & 0xFF), (u8)((store + 5) >> 8),
        0x43, 0x02, 0x8D, (u8)((store + 6) & 0xFF), (u8)((store + 6) >> 8),
        0x43, 0x04, 0x8D, (u8)((store + 7) & 0xFF), (u8)((store + 7) >> 8),
        0x43, 0x08, 0x8D, (u8)((store + 8) & 0xFF), (u8)((store + 8) >> 8),
        0x43, 0x10, 0x8D, (u8)((store + 9) & 0xFF), (u8)((store + 9) >> 8),
        0x43, 0x20, 0x8D, (u8)((store + 10) & 0xFF), (u8)((store + 10) >> 8),
        0x43, 0x40, 0x8D, (u8)((store + 11) & 0xFF), (u8)((store + 11) >> 8),
        0x43, 0x80, 0x8D, (u8)((store + 12) & 0xFF), (u8)((store + 12) >> 8),
        0xAD, 0x02, 0x25, 0x8D, (u8)((store + 13) & 0xFF), (u8)((store + 13) >> 8),
        0xAD, 0x03, 0x25, 0x8D, (u8)((store + 14) & 0xFF), (u8)((store + 14) >> 8),
    });
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
    Memory* mem = core.GetMemory();
    HuC6270::HuC6270_State* vs = core.GetHuC6270_1()->GetState();

    u16 jsr_pc = 0;
    for (size_t i = 0; i + 2 < code.size(); ++i) {
        if (code[i] == 0x20 && code[i + 1] == (u8)(entry & 0xFF) && code[i + 2] == (u8)(entry >> 8)) {
            jsr_pc = (u16)(reset + i);
            break;
        }
    }
    if (jsr_pc == 0) { fprintf(stderr, "jsr not found\n"); return 3; }

    const char* cycle = "cold";
    auto run_once = [&](bool warm) {
        cycle = warm ? "warm" : "cold";
        u16 vdc_r_before[20], vdc_r_after[20];
        unsigned vdc_ar_before = 0, vdc_ar_after = 0;
        unsigned sr_before = 0, sr_after = 0;
        unsigned mpr_before[8], mpr_after[8];
        unsigned idr_before = 0, idr_after = 0, irr_before = 0, irr_after = 0;
        unsigned entry_a = 0, entry_p = 0, entry_x = 0, entry_y = 0, entry_sp = 0;
        long run_steps = 0;
        for (long i = 0; i < steps; ++i) {
            if (cpu->GetState()->PC->GetValue() == jsr_pc) break;
            cpu->RunInstruction();
            if (++run_steps >= steps) { fprintf(stderr, "jsr never reached (%s)\n", cycle); exit(2); }
        }
        if (cpu->GetState()->PC->GetValue() != jsr_pc) { fprintf(stderr, "jsr never reached (%s)\n", cycle); exit(2); }
        // Observed pre-JSR WRAM seeds (what the guest actually left at the
        // call boundary; must equal the argv seeds when seeding dominates).
        unsigned wram_pre_lo = mem->GetWorkingRAM()[0x502];
        unsigned wram_pre_hi = mem->GetWorkingRAM()[0x503];
        for (int i = 0; i < 20; i++) vdc_r_before[i] = vs->R[i];
        vdc_ar_before = *vs->AR;
        sr_before = *vs->SR;
        for (int i = 0; i < 8; i++) mpr_before[i] = mem->GetMpr(i);
        idr_before = *cpu->GetState()->IDR;
        irr_before = *cpu->GetState()->IRR;
        entry_a = cpu->GetState()->A->GetValue();
        entry_p = cpu->GetState()->P->GetValue();
        entry_x = cpu->GetState()->X->GetValue();
        entry_y = cpu->GetState()->Y->GetValue();
        entry_sp = cpu->GetState()->S->GetValue();
        for (long i = run_steps; i < steps; ++i) {
            cpu->RunInstruction();
            if (cpu->GetState()->PC->GetValue() == halt) break;
        }
        const HuC6280::HuC6280_State* st = cpu->GetState();
        const u8* wram = mem->GetWorkingRAM() + (store - 0x2000);
        for (int i = 0; i < 20; i++) vdc_r_after[i] = vs->R[i];
        vdc_ar_after = *vs->AR;
        sr_after = *vs->SR;
        for (int i = 0; i < 8; i++) mpr_after[i] = mem->GetMpr(i);
        std::printf("RESULT {\"cycle\":\"%s\",\"pc\":%u,\"halt\":%u,\"jsr_pc\":%u,\"a\":%u,\"p\":%u,\"x\":%u,\"y\":%u,\"sp\":%u,",
            cycle, st->PC->GetValue(), halt, jsr_pc, wram[0], wram[1], wram[2], wram[3], wram[4]);
        std::printf("\"entry_a\":%u,\"entry_p\":%u,\"entry_x\":%u,\"entry_y\":%u,\"entry_sp\":%u,",
            entry_a, entry_p, entry_x, entry_y, entry_sp);
        std::printf("\"mpr_store\":[%u,%u,%u,%u,%u,%u,%u,%u],", wram[5], wram[6], wram[7], wram[8],
            wram[9], wram[10], wram[11], wram[12]);
        std::printf("\"shadow_post\":[%u,%u],", wram[13], wram[14]);
        std::printf("\"shadow_pre\":[%u,%u],\"real_pre\":[%u,%u],", (unsigned)shadow_lo, (unsigned)marker,
            (unsigned)real_lo, (unsigned)crhi);
        std::printf("\"wram_pre\":[%u,%u],", wram_pre_lo, wram_pre_hi);
        std::printf("\"mpr_before\":[%u,%u,%u,%u,%u,%u,%u,%u],", mpr_before[0], mpr_before[1],
            mpr_before[2], mpr_before[3], mpr_before[4], mpr_before[5], mpr_before[6], mpr_before[7]);
        std::printf("\"mpr_after\":[%u,%u,%u,%u,%u,%u,%u,%u],", mpr_after[0], mpr_after[1],
            mpr_after[2], mpr_after[3], mpr_after[4], mpr_after[5], mpr_after[6], mpr_after[7]);
        std::printf("\"vdc_ar_before\":%u,\"vdc_ar_after\":%u,\"sr_before\":%u,\"sr_after\":%u,\"vdc_r_before\":[",
            vdc_ar_before, vdc_ar_after, sr_before, sr_after);
        for (int i = 0; i < 20; i++) std::printf("%s%u", i ? "," : "", vdc_r_before[i]);
        std::printf("],\"vdc_r_after\":[");
        for (int i = 0; i < 20; i++) std::printf("%s%u", i ? "," : "", vdc_r_after[i]);
        std::printf("],\"idr_before\":%u,\"idr_after\":%u,\"irr_before\":%u,\"irr_after\":%u}\n",
            idr_before, *st->IDR, irr_before, *st->IRR);
        (void)idr_after; (void)irr_after;
    };
    run_once(false);
    core.ResetMedia(true);
    run_once(true);
    return 0;
}
