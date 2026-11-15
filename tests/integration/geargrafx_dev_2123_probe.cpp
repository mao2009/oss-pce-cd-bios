// DEV EX_JOYSNS slot-21 + EX_SCRSIZ slot-23 sequential-call probe.
// DEV_EXPERIMENTAL, NOT reference ABI. Synthetic only.
// argv: rom cue steps keymask lo order
//   order 0 = 21->23, 1 = 23->21. Both calls go through the API table
//   (0xE000+3*N). MPR0/SP/X/Y are seeded once; the second call runs
//   without re-seeding so a missing restore in the first call is visible.
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
#include <vector>

static void dummy_clock(void*, u32) {}

int main(int argc, char** argv) {
    if (argc != 7) { fprintf(stderr, "argc want 7 got %d\n", argc); return 2; }
    char* end = nullptr;
    long steps = std::strtol(argv[3], &end, 10);
    if (!end || *end || steps < 1 || steps > 8192) { fprintf(stderr, "steps\n"); return 2; }
    long keymask = std::strtol(argv[4], &end, 0);
    if (!end || *end || keymask < 0 || keymask > 255) { fprintf(stderr, "keymask\n"); return 2; }
    long lo = std::strtol(argv[5], &end, 0);
    if (!end || *end || lo < 0 || lo > 255) { fprintf(stderr, "lo\n"); return 2; }
    long order = std::strtol(argv[6], &end, 10);
    if (!end || *end || (order != 0 && order != 1)) { fprintf(stderr, "order\n"); return 2; }
    std::ifstream input(argv[1], std::ios::binary);
    std::vector<u8> rom((std::istreambuf_iterator<char>(input)), {});
    if (rom.size() != 0x40000) { fprintf(stderr, "size %zu\n", rom.size()); return 3; }
    const u16 reset = (u16)rom[0x1FFE] | ((u16)rom[0x1FFF] << 8);
    const u16 e21 = 0xE000 + 3 * 0x21;
    const u16 e23 = 0xE000 + 3 * 0x23;
    const u16 store = 0x2200;
    std::vector<u8> code;
    auto emit8 = [&](u8 b){ code.push_back(b); };
    auto emit16 = [&](u16 w){ code.push_back((u8)(w & 0xFF)); code.push_back((u8)(w >> 8)); };
    emit8(0x78); emit8(0xD8);
    emit8(0xA9); emit8(0xF8); emit8(0x53); emit8(0x02);
    emit8(0xA2); emit8(0xFF); emit8(0x9A);
    emit8(0xA9); emit8(0xF8); emit8(0x53); emit8(0x01);
    emit8(0xA2); emit8(0x5A); emit8(0xA0); emit8(0xA5);
    const u16 tmpX = store + 12;
    auto emit_call = [&](u16 entry, u8 aset, u16 base) {
        emit8(0xA9); emit8(aset);
        emit8(0x20); emit16(entry);
        emit8(0x08);
        emit8(0x8D); emit16(base);
        emit8(0x68);
        emit8(0x8D); emit16(base+1);
        // preserve X across TSX-based SP capture via tmpX
        emit8(0x8E); emit16(tmpX);
        emit8(0xBA);
        emit8(0x8E); emit16(base+2);
        emit8(0xAE); emit16(tmpX);
        emit8(0x43); emit8(0x01);
        emit8(0x8D); emit16(base+3);
    };
    if (order == 0) {
        emit_call(e21, 0xC3, store);
        emit_call(e23, (u8)lo, store+4);
    } else {
        emit_call(e23, (u8)lo, store);
        emit_call(e21, 0xC3, store+4);
    }
    emit8(0x8E); emit16(store+8);
    emit8(0x8C); emit16(store+9);
    emit8(0xBA); emit8(0x8E); emit16(store+10);
    emit8(0x43); emit8(0x01); emit8(0x8D); emit16(store+11);
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
    HuC6270::HuC6270_State* vs = core.GetHuC6270_1()->GetState();
    unsigned vdc_ar = *vs->AR;
    unsigned vdc_r9 = vs->R[9];
    std::printf("RESULT {\"pc\":%u,\"sp\":%u,\"mpr\":[%u,%u,%u,%u,%u,%u,%u,%u],\"a1\":%u,\"p1\":%u,\"sp1\":%u,\"mpr1\":%u,\"a2\":%u,\"p2\":%u,\"sp2\":%u,\"mpr2\":%u,\"x\":%u,\"y\":%u,\"sp_f\":%u,\"mpr_f\":%u,\"halt\":%u,\"vdc_ar\":%u,\"vdc_r9\":%u,\"order\":%ld}\n",
        st->PC->GetValue(), st->S->GetValue(),
        mem->GetMpr(0),mem->GetMpr(1),mem->GetMpr(2),mem->GetMpr(3),mem->GetMpr(4),mem->GetMpr(5),mem->GetMpr(6),mem->GetMpr(7),
        wram[0], wram[1], wram[2], wram[3], wram[4], wram[5], wram[6], wram[7], wram[8], wram[9], wram[10], wram[11], halt, vdc_ar, vdc_r9, order);
    return 0;
}
