// Local-only ad_reset_idle guest probe (Issue #295). Never distribute this GPL-linked executable or the core.
// Synthetic BIOS/CD only. The guest is the project-own routine and driver (tests/fixtures), run as CPU code
// on the pinned Geargrafx core: the guest's own STA to $180C/$180D goes through the normal bus.
// The probe only loads the bytes, sets PC and dumps the result block; it never edits guest state.
//
// argv: bios cue guest.bin
// stderr: one JSON line {"frames","stop_pc","result_hex","final":{control,playing,address,status}}
#include "geargrafx_core.h"
#include "adpcm.h"
#include "memory.h"
#include "huc6280.h"
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iterator>
#include <vector>

namespace {

const unsigned kGuestBase = 0x3E00;     // logical; MPR1 = $F8 maps it to work RAM index $1E00
const unsigned kGuestMax = 0x200;       // $3E00-$3FFF
const unsigned kResultBase = 0x2300;    // logical; work RAM index $0300
const unsigned kResultBytes = 0x80;
const unsigned kFrames = 60;

std::vector<u8> read_file(const char* path)
{
    std::ifstream file(path, std::ios::binary);
    return std::vector<u8>((std::istreambuf_iterator<char>(file)), {});
}

}  // namespace

int main(int argc, char** argv)
{
    if (argc != 4)
        return 2;
    std::vector<u8> bios = read_file(argv[1]);
    std::vector<u8> guest = read_file(argv[3]);
    if (bios.empty() || guest.empty() || guest.size() > kGuestMax)
        return 3;

    GeargrafxCore core;
    core.Init(nullptr);
    if (!core.LoadBiosFromBuffer(bios.data(), bios.size(), true))
        return 4;
    if (!core.LoadMedia(argv[2]))
        return 5;
    if (!core.GetMedia()->IsCDROMHardwareEnabled())
        return 6;

    HuC6280* cpu = core.GetHuC6280();
    HuC6280::HuC6280_State* cs = cpu->GetState();
    Memory* memory = core.GetMemory();
    memory->SetMpr(0, 0xFF);   // hardware I/O: ADPCM registers at $1808-$180E
    memory->SetMpr(1, 0xF8);   // work RAM: guest, result block and stack page
    for (int i = 2; i <= 7; ++i)
        memory->SetMpr(i, 0x00);
    u8* wram = memory->GetWorkingRAM();
    std::memcpy(wram + (kGuestBase - 0x2000), guest.data(), guest.size());
    std::memset(wram + (kResultBase - 0x2000), 0xEE, kResultBytes);   // stale data cannot pass as a result
    cs->PC->SetValue((u16)kGuestBase);
    cs->S->SetValue(0xFF);
    cs->P->SetValue(0x04);     // I set: no IRQ during the probe

    GeargrafxCore::GG_Debug_Run debug = {};
    std::vector<u8> frame(1024 * 512 * 4, 0);
    std::vector<s16> audio(65536, 0);
    for (unsigned i = 0; i < kFrames; ++i) {
        int samples = 0;
        core.RunToVBlank(frame.data(), audio.data(), &samples, &debug, false);
    }

    // Internal device state after the guest finished (labelled INTERNAL in the judge; not bus-visible).
    Adpcm* adpcm = core.GetAdpcm();
    Adpcm::Adpcm_State* st = adpcm->GetState();
    std::fprintf(stderr, "{\"frames\":%u,\"stop_pc\":%u,\"result_hex\":\"", kFrames,
                 (unsigned)cs->PC->GetValue());
    for (unsigned i = 0; i < kResultBytes; ++i)
        std::fprintf(stderr, "%02x", wram[(kResultBase - 0x2000) + i]);
    std::fprintf(stderr, "\",\"final\":{\"control\":%u,\"playing\":%u,\"address\":%u,\"status\":%u}}\n",
                 adpcm->Read(0x0D), *st->PLAYING ? 1u : 0u, (unsigned)*st->ADDRESS, adpcm->Read(0x0C));
    std::fflush(stderr);
    return 0;
}
