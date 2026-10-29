// Local-only ADPCM reset full-capture probe (Issue #295; contract docs/research/295-ad-reset-busy-contract.md).
// Never distribute this GPL-linked executable or the linked core. Synthetic BIOS/CD only.
//
// One trial per process. The only guest code is a project-owned BRA * at $3E00 (WRAM); no BIOS code runs.
// ADPCM writes use Adpcm::Write (device layer, harness side). Reads are side-effect free: Read($0B/$0C/$0D/$0E)
// and GetState(). Read($0A) is never called. Checks happen only at frame boundaries (RunToVBlank) and around
// Adpcm::Write. The harness never forces a CPU stop.
//
// argv: bios cue mode bios_sha256 cue_sha256   (the runner computes the hashes and they are echoed back)
// modes:
//   pos-reset          from idle, CONTROL=$80 (existing #294 case)
//   play-reset-80      PLAY_ACTIVE, the contract's standard observation: play, then CONTROL=$80
//   neg-reset-playing  arm-B: play, then CONTROL=$A0 (its own class, not in the positive set)
//   transfer-busy      rejected: TRANSFER_BUSY is BLOCKED (contract section 3.4)
// stderr: one JSON line. Exit 0 only when end_state is COMPLETED.
#include "geargrafx_core.h"
#include "adpcm.h"
#include "memory.h"
#include "huc6280.h"
#include <cctype>
#include <chrono>
#include <cstdio>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

namespace {

const char* const kPinnedCommit = "b49ae82a012eade566d72e9d61c9d95a5caa4da3";
const unsigned kGuestBase = 0x3E00;   // logical; MPR1 = $F8 maps it to WRAM index $1E00
const unsigned kGuestEnd = 0x4000;
const unsigned kStubIndex = 0x1E00;
const unsigned kFrameBudget = 300;    // contract 6: frames per trial
const unsigned kObserveWait = 60;     // contract 6: frames allowed to observe PLAY_ACTIVE
const double kWallSeconds = 120.0;    // contract 6: checked at frame boundaries only
const unsigned kPlayAddress = 0x1234;

struct Point {
    unsigned frame;
    unsigned pc, a, x, y, s, p;
    unsigned mpr[8];
    std::vector<u8> wram;             // m_wram[0x0000-0x1FFF]
    unsigned status, control, dma, sample_rate;
    unsigned playing, address, read_address, write_address, length, end_irq, half_irq;
    unsigned irr, idr;                // raw HuC6280 IRQ registers; semantics UNKNOWN (contract 5)
};

// Read-only capture. Every accessor here is side-effect free (see the header comment).
Point capture(GeargrafxCore& core, unsigned frame)
{
    HuC6280::HuC6280_State* cs = core.GetHuC6280()->GetState();
    Memory* memory = core.GetMemory();
    Adpcm* adpcm = core.GetAdpcm();
    Adpcm::Adpcm_State* st = adpcm->GetState();
    Point pt;
    pt.frame = frame;
    pt.pc = (unsigned)cs->PC->GetValue();
    pt.a = (unsigned)cs->A->GetValue();
    pt.x = (unsigned)cs->X->GetValue();
    pt.y = (unsigned)cs->Y->GetValue();
    pt.s = (unsigned)cs->S->GetValue();
    pt.p = (unsigned)cs->P->GetValue();
    for (int i = 0; i < 8; ++i)
        pt.mpr[i] = (unsigned)memory->GetMpr(i);
    const u8* wram = memory->GetWorkingRAM();
    pt.wram.assign(wram, wram + 0x2000);
    pt.status = adpcm->Read(0x0C);
    pt.control = adpcm->Read(0x0D);
    pt.dma = adpcm->Read(0x0B);
    pt.sample_rate = adpcm->Read(0x0E);
    pt.playing = *st->PLAYING ? 1 : 0;
    pt.address = *st->ADDRESS;
    pt.read_address = *st->READ_ADDRESS;
    pt.write_address = *st->WRITE_ADDRESS;
    pt.length = *st->LENGTH;
    pt.end_irq = *st->END_IRQ ? 1 : 0;
    pt.half_irq = *st->HALF_IRQ ? 1 : 0;
    pt.irr = (unsigned)*cs->IRR;
    pt.idr = (unsigned)*cs->IDR;
    return pt;
}

void hex_bytes(std::string& out, const std::vector<u8>& bytes)
{
    static const char digits[] = "0123456789abcdef";
    for (u8 b : bytes) {
        out += digits[b >> 4];
        out += digits[b & 15];
    }
}

void emit_point(std::string& out, const char* name, const Point* pt, bool last)
{
    out += "\"";
    out += name;
    out += "\":";
    if (!pt) {
        out += "\"NOT_CAPTURED\"";
    } else {
        char buf[1024];
        std::snprintf(buf, sizeof buf,
                      "{\"frame\":%u,\"cpu\":{\"pc\":%u,\"a\":%u,\"x\":%u,\"y\":%u,\"s\":%u,\"p\":%u,"
                      "\"mpr\":[%u,%u,%u,%u,%u,%u,%u,%u]},\"wram_8k_hex\":\"",
                      pt->frame, pt->pc, pt->a, pt->x, pt->y, pt->s, pt->p, pt->mpr[0], pt->mpr[1],
                      pt->mpr[2], pt->mpr[3], pt->mpr[4], pt->mpr[5], pt->mpr[6], pt->mpr[7]);
        out += buf;
        hex_bytes(out, pt->wram);
        std::snprintf(buf, sizeof buf,
                      "\",\"adpcm\":{\"status_0c\":%u,\"control_0d\":%u,\"dma_0b\":%u,\"sample_rate_0e\":%u,"
                      "\"playing\":%u,\"address\":%u,\"read_address\":%u,\"write_address\":%u,\"length\":%u,"
                      "\"end_irq\":%u,\"half_irq\":%u,\"read_cycles\":\"NOT_CAPTURED\","
                      "\"write_cycles\":\"NOT_CAPTURED\",\"play_pending\":\"NOT_CAPTURED\"},"
                      "\"irq\":{\"irr\":%u,\"idr\":%u,\"semantics\":\"UNKNOWN\"}}",
                      pt->status, pt->control, pt->dma, pt->sample_rate, pt->playing, pt->address,
                      pt->read_address, pt->write_address, pt->length, pt->end_irq, pt->half_irq, pt->irr,
                      pt->idr);
        out += buf;
    }
    out += last ? "" : ",";
}

bool is_hex_sha256(const std::string& s)
{
    if (s.size() != 64)
        return false;
    for (char c : s)
        if (!std::isxdigit((unsigned char)c))
            return false;
    return true;
}

}  // namespace

int main(int argc, char** argv)
{
    if (argc != 6)
        return 2;
    const std::string mode = argv[3];
    if (mode.find("busy") != std::string::npos || mode.find("transfer") != std::string::npos) {
        std::fprintf(stderr, "REJECTED mode=%s: TRANSFER_BUSY is BLOCKED (contract section 3.4); not implemented\n",
                     argv[3]);
        return 7;
    }
    if (mode != "pos-reset" && mode != "play-reset-80" && mode != "neg-reset-playing")
        return 2;
    if (!is_hex_sha256(argv[4]) || !is_hex_sha256(argv[5]))
        return 2;

    std::ifstream file(argv[1], std::ios::binary);
    if (!file)
        return 3;
    std::vector<u8> bios((std::istreambuf_iterator<char>(file)), {});
    if (bios.empty())
        return 3;

    GeargrafxCore core;
    core.Init(nullptr);
    if (!core.LoadBiosFromBuffer(bios.data(), bios.size(), true))
        return 4;
    if (!core.LoadMedia(argv[2]))
        return 5;
    if (!core.GetMedia()->IsCDROMHardwareEnabled())
        return 6;

    HuC6280::HuC6280_State* cs = core.GetHuC6280()->GetState();
    Memory* memory = core.GetMemory();
    memory->SetMpr(0, 0x00);
    memory->SetMpr(1, 0xF8);  // page 1 -> work RAM
    for (int i = 2; i <= 7; ++i)
        memory->SetMpr(i, 0x00);
    u8* wram = memory->GetWorkingRAM();
    wram[kStubIndex] = 0x80;      // BRA *
    wram[kStubIndex + 1] = 0xFE;
    cs->PC->SetValue((u16)kGuestBase);
    cs->S->SetValue(0xFF);
    cs->P->SetValue(0x04);        // I set: no IRQ taken during the trial

    Adpcm* adpcm = core.GetAdpcm();
    GeargrafxCore::GG_Debug_Run debug = {};
    std::vector<u8> frame(1024 * 512 * 4, 0);
    std::vector<s16> audio(65536, 0);

    unsigned frames = 0;
    const auto t0 = std::chrono::steady_clock::now();
    std::string end_state = "COMPLETED";
    std::string end_reason = "budget_end";
    std::string cls = "UNOBSERVED_BUSY";

    auto run_frame = [&]() {
        int samples = 0;
        core.RunToVBlank(frame.data(), audio.data(), &samples, &debug, false);
        ++frames;
    };
    // Stop checks at a frame boundary only (contract 7.1). Returns true when the trial must stop.
    auto stop_now = [&]() {
        const double secs = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
        const unsigned pc = (unsigned)cs->PC->GetValue();
        if (secs > kWallSeconds) {
            end_state = "TIMEOUT";
            end_reason = "wall_clock";
        } else if (pc < kGuestBase || pc >= kGuestEnd) {
            end_state = "ABORTED";
            end_reason = "pc_outside_guest";
        }
        return end_state != "COMPLETED";
    };
    auto playing_observed = [&]() {
        return (adpcm->Read(0x0C) & 0x08) != 0 && *adpcm->GetState()->PLAYING;
    };

    const bool play_mode = mode != "pos-reset";
    const unsigned reset_value = mode == "neg-reset-playing" ? 0xA0 : 0x80;
    Point p0, p1, p2, p3;
    bool has_p0 = false, has_p1 = false, has_p2 = false;
    bool stopped = false;
    bool may_reset = false;

    adpcm->Write(0x08, kPlayAddress & 0xFF);   // preset ADDRESS = $1234 (both modes)
    adpcm->Write(0x09, kPlayAddress >> 8);

    if (!play_mode) {
        run_frame();                           // idle frame
        stopped = stop_now();
        cls = "IDLE_RESET_EXISTING";
        may_reset = !stopped;
    } else {
        // Contract 2.1: LENGTH is copied from ADDRESS while CONTROL bit4 is set, then the play request.
        adpcm->Write(0x0D, 0x10);
        run_frame();
        stopped = stop_now();
        if (!stopped) {
            adpcm->Write(0x0D, 0x20);
            bool observed = false;
            for (unsigned i = 0; i < kObserveWait && !observed && !stopped; ++i) {
                run_frame();
                stopped = stop_now();
                observed = !stopped && playing_observed();
            }
            if (!stopped && !observed) {
                end_state = "BUDGET_EXCEEDED";
                end_reason = "observe_wait_60_frames";
            }
            if (observed) {
                // T_b and T_r are the same frame boundary: the reset is issued with no frame in between.
                cls = mode == "neg-reset-playing" ? "ARM_B_NOT_IN_POSITIVE_SET" : "PLAY_ACTIVE";
                may_reset = !stopped;
            }
        }
    }

    if (may_reset) {
        p0 = capture(core, frames);
        has_p0 = true;
        adpcm->Write(0x0D, reset_value);       // contract 4.1: issued once, from the harness only
        p1 = capture(core, frames);            // P1: no Clock has run since the write
        has_p1 = true;
        run_frame();                           // CheckReset runs inside Clock
        stopped = stop_now();
        if (!stopped) {
            p2 = capture(core, frames);        // P2
            has_p2 = true;
        }
        while (!stopped && frames < kFrameBudget) {
            run_frame();
            stopped = stop_now();
        }
    }
    p3 = capture(core, frames);                // P3: budget end or the stop point

    std::string out = "{\"probe\":\"geargrafx_ad_reset_full\",\"pinned_commit\":\"";
    out += kPinnedCommit;
    out += "\",\"mode\":\"" + mode + "\",\"measurement_class\":\"" + cls + "\",\"eligible_positive_set\":";
    out += (cls == "PLAY_ACTIVE" && end_state == "COMPLETED") ? "true" : "false";
    out += ",\"end_state\":\"" + end_state + "\",\"end_reason\":\"" + end_reason + "\",";
    out += "\"input_sha256\":{\"bios\":\"" + std::string(argv[4]) + "\",\"cue\":\"" + std::string(argv[5]) + "\"},";
    out += "\"guest\":{\"base\":\"0x3E00\",\"bytes_hex\":\"80fe\"},";
    char buf[384];
    std::snprintf(buf, sizeof buf,
                  "\"budget\":{\"frame_budget\":%u,\"observe_wait\":%u,\"wall_seconds\":%.0f},"
                  "\"frames\":%u,\"clock\":{\"run_to_vblank_calls\":%u,\"clock_calls\":\"NOT_CAPTURED\","
                  "\"master_clock_cycles\":\"NOT_CAPTURED\"},",
                  kFrameBudget, kObserveWait, kWallSeconds, frames, frames);
    out += buf;
    if (has_p1) {
        std::snprintf(buf, sizeof buf,
                      "\"reset\":{\"issued\":true,\"write_value\":%u,\"r2a_readback\":%u},", reset_value,
                      p1.control);
    } else {
        std::snprintf(buf, sizeof buf, "\"reset\":{\"issued\":false,\"write_value\":\"NOT_ISSUED\","
                                       "\"r2a_readback\":\"NOT_CAPTURED\"},");
    }
    out += buf;
    out += "\"points\":{";
    emit_point(out, "P0", has_p0 ? &p0 : nullptr, false);
    emit_point(out, "P1", has_p1 ? &p1 : nullptr, false);
    emit_point(out, "P2", has_p2 ? &p2 : nullptr, false);
    emit_point(out, "P3", &p3, true);
    out += "}}\n";

    if (std::fwrite(out.data(), 1, out.size(), stderr) != out.size())
        return 1;
    std::fflush(stderr);
    return end_state == "COMPLETED" ? 0 : 1;
}
