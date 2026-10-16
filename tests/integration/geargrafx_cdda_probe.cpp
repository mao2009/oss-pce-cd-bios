// Original test frontend for the Issue #15 CD-DA observation diagnostic. The GPL-linked
// executable stays local and is never distributed.
// Steps the guest one instruction at a time and only READS emulator state:
//   CdRomAudio::GetState()  CURRENT_STATE / START_LBA / STOP_LBA / CURRENT_LBA / CURRENT_SAMPLE /
//                           STOP_EVENT / SEEK_CYCLES / PLAYBACK_DELAY_CYCLES / FRAME_SAMPLES / BUFFER
//   ScsiController state    PHASE
// It never writes guest RAM, never issues SCSI commands and never edits emulator state.
#include "geargrafx_core.h"
#include "huc6280.h"
#include "memory.h"
#include "media.h"
#include "cdrom_audio.h"
#include "scsi_controller.h"
#include <fstream>
#include <iterator>
#include <vector>
#include <map>
#include <string>
#include <cstdio>
#include <cstdlib>

static const unsigned kResultOffset = 0x300, kResultBytes = 0xB0;   // CPU $2300-$23AF
static const unsigned kBufferOffset = 0x800, kBufferBytes = 2304;   // CPU $2800, sector + guard page
static const long kHaltConfirmSteps = 64;
static const long kSamplesPerSector = 2352 / 4;

int main(int argc, char** argv) {
    // usage: probe rom cue labels max_steps
    if (argc != 5) return 2;
    std::ifstream input(argv[1], std::ios::binary);
    if (!input) return 2;
    std::vector<u8> rom((std::istreambuf_iterator<char>(input)), {});
    long max_steps = std::atol(argv[4]);
    if (max_steps < 1 || max_steps > 200000000L) return 2;
    std::map<std::string, unsigned> labels;
    {
        std::ifstream file(argv[3]);
        if (!file) return 2;
        std::string name; unsigned address;
        while (file >> name >> std::hex >> address) labels[name] = address;
    }
    for (const char* needed : {"cdda_cmd_done", "cdda_wait_done", "cdda_halt_ok", "cdda_halt_fail", "cdda_unexpected_interrupt"})
        if (!labels.count(needed)) return 2;

    GeargrafxCore core;
    core.Init(nullptr);
    if (!core.LoadBiosFromBuffer(rom.data(), rom.size(), true)) return 3;
    if (!core.LoadMedia(argv[2])) return 4;
    auto cpu = core.GetHuC6280()->GetState();
    auto memory = core.GetMemory();
    auto scsi = core.GetScsiController()->GetState();
    auto audio = core.GetCDROMAudio()->GetState();
    std::vector<u8> frame(1024 * 512 * 4);
    std::vector<s16> mixed(65536);
    GeargrafxCore::GG_Debug_Run debug = {};
    debug.step_debugger = true;
    // A non-null sample buffer makes Audio::EndFrame hand each step's CD-DA samples to CdRomAudio::EndFrame,
    // which leaves them in BUFFER[0..FRAME_SAMPLES) for us to read.
    auto one_instruction = [&]() { int samples = 0; core.RunToVBlank(frame.data(), mixed.data(), &samples, &debug, false); };

    unsigned first_pc = cpu->PC->GetValue();
    if (memory->Read(first_pc) != 0x78) return 5;
    one_instruction();
    bool stepping = cpu->PC->GetValue() == first_pc + 1;
    std::printf("CAP {\"instruction_step\":%s,\"cdrom_hardware\":%s,\"cdrom_media\":%s}\n", stepping ? "true" : "false",
                core.GetMedia()->IsCDROMHardwareEnabled() ? "true" : "false", core.GetMedia()->IsCDROM() ? "true" : "false");
    if (!stepping) return 6;

    auto snapshot = [&](const char* tag, const char* name, long step) {
        std::printf("%s {\"name\":\"%s\",\"step\":%ld,\"pc\":%u,\"x\":%u,\"scsi_phase\":%d,\"audio\":{\"state\":%d,\"start_lba\":%u,"
                    "\"stop_lba\":%u,\"current_lba\":%u,\"current_sample\":%u,\"stop_event\":%d,\"seek_cycles\":%d,\"playback_delay_cycles\":%d}",
                    tag, name, step, cpu->PC->GetValue(), cpu->X->GetValue(), (int)*scsi->PHASE, (int)*audio->CURRENT_STATE,
                    *audio->START_LBA, *audio->STOP_LBA, *audio->CURRENT_LBA, *audio->CURRENT_SAMPLE, (int)*audio->STOP_EVENT,
                    *audio->SEEK_CYCLES, *audio->PLAYBACK_DELAY_CYCLES);
    };
    auto hex = [&](const char* key, unsigned offset, unsigned length) {
        std::printf(",\"%s\":\"", key);
        for (unsigned i = 0; i < length; i++) std::printf("%02x", memory->GetWorkingRAM()[offset + i]);
        std::printf("\"");
    };

    // Audio accounting: every step's CD-DA samples are either attributed to a play-position advance (AUD rows)
    // or counted as "unattributed"; nonzero unattributed samples would mean audio we cannot explain.
    long unattributed = 0, unattributed_nonzero = 0;
    std::string reason = "budget";
    unsigned halt_pc = 0, last_pc = 0;
    long confirmed = 0, step = 0;
    while (step < max_steps) {
        unsigned pc = cpu->PC->GetValue();
        if (pc != last_pc && (pc == labels["cdda_cmd_done"] || pc == labels["cdda_wait_done"])) {
            snapshot("HIT", pc == labels["cdda_cmd_done"] ? "cdda_cmd_done" : "cdda_wait_done", step);
            std::printf("}\n");
        }
        last_pc = pc;
        long before = (long)*audio->CURRENT_LBA * kSamplesPerSector + *audio->CURRENT_SAMPLE;
        unsigned lba0 = *audio->CURRENT_LBA, sample0 = *audio->CURRENT_SAMPLE;
        one_instruction();
        step++;
        long advance = (long)*audio->CURRENT_LBA * kSamplesPerSector + *audio->CURRENT_SAMPLE - before;
        int pairs = *audio->FRAME_SAMPLES / 2;
        if (advance > 0 && advance <= pairs) {
            for (long k = 0; k < advance; k++) {
                long position = (long)lba0 * kSamplesPerSector + sample0 + k;
                const s16* pair = audio->BUFFER + 2 * (pairs - advance + k);
                std::printf("AUD [%ld,%ld,%d,%d,%ld]\n", position / kSamplesPerSector, position % kSamplesPerSector,
                            pair[0], pair[1], step);
            }
            pairs -= (int)advance;
        } else if (advance != 0) {
            // a position change not paid for by samples: a seek ($D8) or an unexplained jump; the judge decides
            std::printf("JUMP [%u,%u,%u,%u,%ld]\n", lba0, sample0, *audio->CURRENT_LBA, *audio->CURRENT_SAMPLE, step);
        }
        for (int i = 0; i < pairs; i++) {
            unattributed++;
            if (audio->BUFFER[2 * i] || audio->BUFFER[2 * i + 1]) unattributed_nonzero++;
        }
        unsigned now = cpu->PC->GetValue();
        bool on_halt = now == labels["cdda_halt_ok"] || now == labels["cdda_halt_fail"] || now == labels["cdda_unexpected_interrupt"];
        if (on_halt && now == halt_pc) {
            if (++confirmed >= kHaltConfirmSteps) {
                reason = now == labels["cdda_halt_ok"] ? "cdda_halt_ok" : now == labels["cdda_halt_fail"] ? "cdda_halt_fail" : "cdda_unexpected_interrupt";
                break;
            }
        } else {
            halt_pc = on_halt ? now : 0;
            confirmed = 0;
        }
    }
    snapshot("RUN", reason.c_str(), step);
    std::printf(",\"reason\":\"%s\",\"halt_confirmed\":%s,\"unattributed_samples\":%ld,\"unattributed_nonzero\":%ld",
                reason.c_str(), reason == "budget" ? "false" : "true", unattributed, unattributed_nonzero);
    hex("result_hex", kResultOffset, kResultBytes);
    hex("buffer_hex", kBufferOffset, kBufferBytes);
    std::printf("}\n");
    return 0;
}
