// Local-only observation probe for the ADPCM DMA-from-CD guest (Issue #400). Never distribute this
// GPL-linked executable or the linked core. Synthetic CD only; no BIOS, System Card or AD_TRANS code.
//
// Runs the guest HuC6280 code until it reaches a halt label (BRA self-loop confirmed for 64 steps) or the
// budget ends, then dumps the result block and the pinned core's ADPCM RAM. The probe never calls
// Adpcm::Write or any other ADPCM entry point: every byte in RAM came from the CPU's $1808-$180D MMIO
// writes and the device's own DMA. Internal state (ADDRESS, WRITE_ADDRESS) is read through GetState().
// argv: rom cue labels max_steps
#include "geargrafx_core.h"
#include "huc6280.h"
#include "memory.h"
#include "adpcm.h"
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iterator>
#include <map>
#include <string>
#include <vector>

static const unsigned kResultOffset = 0x300;  // work RAM offset of CPU $2300
static const long kHaltConfirmSteps = 64;

int main(int argc, char** argv)
{
    if (argc != 5)
        return 2;
    std::ifstream input(argv[1], std::ios::binary);
    if (!input)
        return 2;
    std::vector<u8> rom((std::istreambuf_iterator<char>(input)), {});
    long max_steps = std::atol(argv[4]);
    if (max_steps < 1 || max_steps > 200000000L)
        return 2;
    std::map<std::string, unsigned> labels;
    {
        std::ifstream file(argv[3]);
        if (!file)
            return 2;
        std::string name;
        unsigned address;
        while (file >> name >> std::hex >> address)
            labels[name] = address;
    }
    const char* halts[] = {"cd_halt_ok", "cd_halt_fail", "cd_unexpected_interrupt"};
    for (const char* name : halts)
        if (!labels.count(name))
            return 2;

    GeargrafxCore core;
    core.Init(nullptr);
    if (!core.LoadBiosFromBuffer(rom.data(), rom.size(), true))
        return 3;
    if (!core.LoadMedia(argv[2]))
        return 4;
    if (!core.GetMedia()->IsCDROMHardwareEnabled())
        return 5;

    auto cpu = core.GetHuC6280()->GetState();
    auto memory = core.GetMemory();
    Adpcm* adpcm = core.GetAdpcm();
    u8* ram = adpcm->GetRAM();
    long ram_pre_nonzero = 0;
    for (unsigned i = 0; i < 0x10000; i++)
        ram_pre_nonzero += ram[i] != 0;
    std::vector<u8> frame(1024 * 512 * 4);
    std::vector<s16> audio(65536);
    GeargrafxCore::GG_Debug_Run debug = {};
    debug.step_debugger = true;  // one instruction per RunToVBlank call

    auto halt_name = [&](unsigned pc) -> const char* {
        for (const char* name : halts)
            if (pc == labels[name])
                return name;
        return nullptr;
    };

    unsigned halt_pc = 0;
    long confirmed_steps = 0;
    bool confirmed = false;
    std::string reason = "budget";
    long step = 0;
    while (step < max_steps) {
        unsigned pc = cpu->PC->GetValue();
        if (halt_name(pc)) {
            if (pc != halt_pc) {
                halt_pc = pc;
                confirmed_steps = 0;
            }
        } else {
            halt_pc = 0;
            confirmed_steps = 0;
        }
        int samples = 0;
        core.RunToVBlank(frame.data(), audio.data(), &samples, &debug, false);
        step++;
        if (halt_pc && cpu->PC->GetValue() == halt_pc) {
            if (++confirmed_steps >= kHaltConfirmSteps) {
                confirmed = true;
                reason = halt_name(halt_pc);
                break;
            }
        } else if (halt_pc) {
            halt_pc = 0;
            confirmed_steps = 0;
        }
    }

    Adpcm::Adpcm_State* st = adpcm->GetState();
    long ram_post_nonzero = 0;
    for (unsigned i = 0; i < 0x10000; i++)
        ram_post_nonzero += ram[i] != 0;
    std::printf("{\"reason\":\"%s\",\"halt_confirmed\":%s,\"steps\":%ld,\"pc\":%u,\"ram_pre_nonzero\":%ld,"
                "\"ram_post_nonzero\":%ld,\"adpcm\":{\"control\":%u,\"dma\":%u,\"status\":%u,\"playing\":%u,"
                "\"address\":%u,\"write_address\":%u,\"read_address\":%u,\"length\":%u},\"result_hex\":\"",
                reason.c_str(), confirmed ? "true" : "false", step, cpu->PC->GetValue(), ram_pre_nonzero,
                ram_post_nonzero, adpcm->Read(0x0D), adpcm->Read(0x0B), adpcm->Read(0x0C),
                *st->PLAYING ? 1u : 0u, *st->ADDRESS, *st->WRITE_ADDRESS, *st->READ_ADDRESS, *st->LENGTH);
    for (unsigned i = 0; i < 32; i++)
        std::printf("%02x", memory->GetWorkingRAM()[kResultOffset + i]);
    std::printf("\",\"ram_hex\":\"");
    for (unsigned i = 0; i < 0x10000; i++)
        std::printf("%02x", ram[i]);
    std::printf("\"}\n");
    std::fflush(stdout);
    return 0;
}
