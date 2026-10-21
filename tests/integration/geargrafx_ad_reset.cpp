// Local-only ADPCM reset device-layer diagnostic (Issue #294).
// Never distribute this GPL-linked executable or the linked core. Synthetic BIOS/CD only.
//
// Drives the pinned Geargrafx ADPCM device (Adpcm::Write / Adpcm::Read, the same entry points the CD
// unit uses for $1808-$180E) and advances emulated time with RunToVBlank. The guest is a project-owned
// synthetic loop (BRA *) in work RAM; no System Card / AD_RESET code is executed. Internal state is read
// through Adpcm::GetState() and is labelled as internal; bus-visible readbacks use Read($0B/$0C/$0D/$0E)
// only (Read($0A) has a side effect and is never called).
//
// argv: bios cue mode
// Modes:
//   pos-reset            from idle: set ADDRESS, then write CONTROL=$80 (reset, play bit clear)
//   neg-reset-playing    start playback (CONTROL=$20), then write CONTROL=$A0 (reset + play bit set)
#include "geargrafx_core.h"
#include "adpcm.h"
#include "memory.h"
#include "huc6280.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

namespace {

const unsigned long kGuestBase = 0x3E00;   // page 1 -> work RAM
const unsigned long kStubIndex = 0x1E00;   // WRAM index of the guest loop
const unsigned kIdleFrames = 1;
const unsigned kPostFrames = 2;
const unsigned kPlayBudgetFrames = 240;
const unsigned kResetAddress = 0x1234;

struct Snap {
    unsigned control, playing, status, dma, sample_rate;
    unsigned address, read_address, write_address, length, end_irq, half_irq;
};

Snap snap(Adpcm* adpcm)
{
    Adpcm::Adpcm_State* st = adpcm->GetState();
    Snap s;
    s.control = adpcm->Read(0x0D);
    s.status = adpcm->Read(0x0C);
    s.dma = adpcm->Read(0x0B);
    s.sample_rate = adpcm->Read(0x0E);
    s.playing = *st->PLAYING ? 1 : 0;
    s.address = *st->ADDRESS;
    s.read_address = *st->READ_ADDRESS;
    s.write_address = *st->WRITE_ADDRESS;
    s.length = *st->LENGTH;
    s.end_irq = *st->END_IRQ ? 1 : 0;
    s.half_irq = *st->HALF_IRQ ? 1 : 0;
    return s;
}

void emit(const char* label, const Snap& s)
{
    std::fprintf(stderr, "\"%s\":{\"control\":%u,\"playing\":%u,\"status\":%u,\"dma\":%u,\"sample_rate\":%u,"
                 "\"address\":%u,\"read_address\":%u,\"write_address\":%u,\"length\":%u,"
                 "\"end_irq\":%u,\"half_irq\":%u},",
                 label, s.control, s.playing, s.status, s.dma, s.sample_rate, s.address,
                 s.read_address, s.write_address, s.length, s.end_irq, s.half_irq);
}

}  // namespace

int main(int argc, char** argv)
{
    if (argc != 4)
        return 2;
    std::string mode = argv[3];
    if (mode != "pos-reset" && mode != "neg-reset-playing")
        return 2;

    std::ifstream file(argv[1], std::ios::binary);
    if (!file)
        return 3;
    std::vector<u8> bios((std::istreambuf_iterator<char>(file)), {});

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
    memory->SetMpr(0, 0x00);
    memory->SetMpr(1, 0xF8);  // page 1 -> work RAM
    for (int i = 2; i <= 7; ++i)
        memory->SetMpr(i, 0x00);
    const u8 guest[] = {0x80, 0xFE};  // BRA *
    u8* wram = memory->GetWorkingRAM();
    wram[kStubIndex] = guest[0];
    wram[kStubIndex + 1] = guest[1];
    cs->PC->SetValue((u16)kGuestBase);
    cs->S->SetValue(0xFF);
    cs->P->SetValue(0x04);

    Adpcm* adpcm = core.GetAdpcm();
    GeargrafxCore::GG_Debug_Run debug = {};
    std::vector<u8> frame(1024 * 512 * 4, 0);
    std::vector<s16> audio(65536, 0);
    auto run_frame = [&]() {
        int samples = 0;
        core.RunToVBlank(frame.data(), audio.data(), &samples, &debug, false);
    };

    // Preset: a non-zero ADDRESS so the reset's effect on it is observable.
    adpcm->Write(0x08, kResetAddress & 0xFF);
    adpcm->Write(0x09, kResetAddress >> 8);

    unsigned frames_to_playing = 0;
    Snap pre, post;
    if (mode == "pos-reset") {
        for (unsigned i = 0; i < kIdleFrames; ++i)
            run_frame();
        pre = snap(adpcm);
        adpcm->Write(0x0D, 0x80);
        for (unsigned i = 0; i < kPostFrames; ++i)
            run_frame();
        post = snap(adpcm);
    } else {
        adpcm->Write(0x0D, 0x20);
        while (!*adpcm->GetState()->PLAYING && frames_to_playing < kPlayBudgetFrames) {
            run_frame();
            ++frames_to_playing;
        }
        pre = snap(adpcm);
        adpcm->Write(0x0D, 0xA0);
        for (unsigned i = 0; i < kPostFrames; ++i)
            run_frame();
        post = snap(adpcm);
    }

    const char* status = (mode == "neg-reset-playing" && !pre.playing) ? "not_playing" : "ok";
    std::fprintf(stderr, "{\"mode\":\"%s\",\"status\":\"%s\",\"frames_to_playing\":%u,\"post_frames\":%u,",
                 mode.c_str(), status, frames_to_playing, kPostFrames);
    emit("pre", pre);
    emit("post", post);
    std::fprintf(stderr, "\"stop_pc\":%u}", (unsigned)cs->PC->GetValue());
    std::fflush(stderr);
    return 0;
}
