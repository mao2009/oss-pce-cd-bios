// DEV EX_JOYSNS (slot $21) real-core probe. DEV_EXPERIMENTAL, NOT reference ABI.
// argv: rom cue steps keymask
#include "geargrafx_core.h"
#include "input.h"
#include "media.h"
#include "memory.h"
#include "huc6280.h"
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

static void dummy_clock(void*, u32) {}

int main(int argc, char** argv) {
    if (argc != 5) { fprintf(stderr, "argc\n"); return 2; }
    char* end = nullptr;
    long steps = std::strtol(argv[3], &end, 10);
    if (!end || *end || steps < 1 || steps > 4096) { fprintf(stderr, "steps\n"); return 2; }
    long keymask = std::strtol(argv[4], &end, 0);
    if (!end || *end) { fprintf(stderr, "keymask\n"); return 2; }

    std::ifstream input(argv[1], std::ios::binary);
    std::vector<u8> rom((std::istreambuf_iterator<char>(input)), {});
    if (rom.size() != 0x40000) { fprintf(stderr, "size %zu\n", rom.size()); return 3; }

    const u16 reset = (u16)rom[0x1FFE] | ((u16)rom[0x1FFF] << 8);
    const u16 entry = 0xE000 + 3 * 0x21;
    const u16 store = 0x2200;
    // Guest: SEI,CLD, MPR1=$F8, SP=$FF, MPR0=$FF (known), preset X=$5A Y=$A5 A=$C3,
    // JSR entry, PHP, store A/P/X/Y, TSX store SP, TMA#1 store MPR0-after, BRA -2
    std::vector<u8> code = {
        0x78, 0xD8,
        0xA9, 0xF8, 0x53, 0x02,       // MPR1 = work RAM
        0xA2, 0xFF, 0x9A,             // SP = $FF
        0xA9, 0xFF, 0x53, 0x01,       // MPR0 = $FF (known initial)
        0xA2, 0x5A, 0xA0, 0xA5,       // X,Y presets
        0xA9, 0xC3,                   // A preset
        0x20, (u8)(entry & 0xFF), (u8)(entry >> 8),
        0x08,                         // PHP
        0x8D, (u8)(store & 0xFF), (u8)(store >> 8),       // A
        0x68,
        0x8D, (u8)((store + 1) & 0xFF), (u8)((store + 1) >> 8), // P
        0x8E, (u8)((store + 2) & 0xFF), (u8)((store + 2) >> 8), // X
        0x8C, (u8)((store + 3) & 0xFF), (u8)((store + 3) >> 8), // Y
        0xBA,
        0x8E, (u8)((store + 4) & 0xFF), (u8)((store + 4) >> 8), // SP
        0x43, 0x01,                                            // TMA #$01
        0x8D, (u8)((store + 5) & 0xFF), (u8)((store + 5) >> 8), // MPR0 after
    };
    const u16 halt = reset + (u16)code.size();
    code.push_back(0x80); code.push_back(0xFE);
    if (reset < 0xE000 || reset + code.size() > 0xFFF6) { fprintf(stderr, "reset\n"); return 3; }
    for (size_t i = 0; i < code.size(); ++i) rom[reset - 0xE000 + i] = code[i];

    GeargrafxCore core;
    core.Init(nullptr);
    core.GetMedia()->SetCDROMType(GG_CDROM_STANDARD);
    if (!core.LoadBiosFromBuffer(rom.data(), (int)rom.size(), true)) { fprintf(stderr, "loadbios\n"); return 3; }
    if (!core.LoadMedia(argv[2])) { fprintf(stderr, "loadmedia\n"); return 3; }
    core.GetHuC6280()->SetHardwareClock(dummy_clock, nullptr);

    core.KeyReleased(GG_CONTROLLER_1, GG_KEY_I);
    core.KeyReleased(GG_CONTROLLER_1, GG_KEY_II);
    core.KeyReleased(GG_CONTROLLER_1, GG_KEY_SELECT);
    core.KeyReleased(GG_CONTROLLER_1, GG_KEY_RUN);
    core.KeyReleased(GG_CONTROLLER_1, GG_KEY_UP);
    core.KeyReleased(GG_CONTROLLER_1, GG_KEY_RIGHT);
    core.KeyReleased(GG_CONTROLLER_1, GG_KEY_DOWN);
    core.KeyReleased(GG_CONTROLLER_1, GG_KEY_LEFT);
    if (keymask & GG_KEY_I) core.KeyPressed(GG_CONTROLLER_1, GG_KEY_I);
    if (keymask & GG_KEY_II) core.KeyPressed(GG_CONTROLLER_1, GG_KEY_II);
    if (keymask & GG_KEY_SELECT) core.KeyPressed(GG_CONTROLLER_1, GG_KEY_SELECT);
    if (keymask & GG_KEY_RUN) core.KeyPressed(GG_CONTROLLER_1, GG_KEY_RUN);
    if (keymask & GG_KEY_UP) core.KeyPressed(GG_CONTROLLER_1, GG_KEY_UP);
    if (keymask & GG_KEY_RIGHT) core.KeyPressed(GG_CONTROLLER_1, GG_KEY_RIGHT);
    if (keymask & GG_KEY_DOWN) core.KeyPressed(GG_CONTROLLER_1, GG_KEY_DOWN);
    if (keymask & GG_KEY_LEFT) core.KeyPressed(GG_CONTROLLER_1, GG_KEY_LEFT);

    HuC6280* cpu = core.GetHuC6280();
    for (long i = 0; i < steps; ++i) {
        cpu->RunInstruction();
        if (cpu->GetState()->PC->GetValue() == halt) break;
    }
    const HuC6280::HuC6280_State* st = cpu->GetState();
    Memory* mem = core.GetMemory();
    const u8* wram = mem->GetWorkingRAM() + (store - 0x2000);
    std::printf("RESULT {\"pc\":%u,\"sp\":%u,\"mpr\":[%u,%u,%u,%u,%u,%u,%u,%u],\"a\":%u,\"p\":%u,\"x\":%u,\"y\":%u,\"sp_stored\":%u,\"mpr0_stored\":%u,\"halt\":%u}\n",
        st->PC->GetValue(), st->S->GetValue(),
        mem->GetMpr(0),mem->GetMpr(1),mem->GetMpr(2),mem->GetMpr(3),mem->GetMpr(4),mem->GetMpr(5),mem->GetMpr(6),mem->GetMpr(7),
        wram[0], wram[1], wram[2], wram[3], wram[4], wram[5], halt);
    return 0;
}
