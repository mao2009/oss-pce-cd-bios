// Local-only warm-series probe (Issue #295; contract docs/research/295-ad-reset-busy-contract.md, section 14).
// Never distribute this GPL-linked executable or the linked core. Synthetic BIOS/CD only.
//
// One process = one series of two trials on ONE GeargrafxCore instance.
//   trial 1: warm-up (outside the measurement classes, contract 14.9)
//   trial 2: warm (the only trial counted as warm, contract 14.8)
// Between the trials the order of contract 14.3 is followed exactly:
//   2 SetResetValues + SetResetValue(0); 3 IsReady; 4 W0 (breakpoint count before ResetMedia);
//   5 ResetMedia(false); 6 W1; 7 guest placement (MPR, WRAM bytes); 8 PC, S, P set and readback;
//   9 W2; 10 (i) static readback check, (ii) one frame for PC stability (the dynamic MPR detector
//   of 10(ii) is NOT_IMPLEMENTED here); then the trial body (contract 2.1).
// Contract 14.12 (eight conditions) is judged before trial 2 starts. A condition that cannot be
// captured through the public API is NOT_CAPTURED (never PASS). Trial 2 starts only when all eight
// are PASS; otherwise trial 2 is NOT_STARTED and the series is BLOCKED.
// Known gaps (reported, not worked around):
//   - ADPCM RAM (64 KiB) is private (adpcm.h) and has no accessor: the W1 zero check is NOT_CAPTURED.
//   - Dynamic MPR detection (contract 10(ii)) is not reachable from this probe: NOT_IMPLEMENTED.
// ADPCM writes use Adpcm::Write (device layer, harness side). Reads are side-effect free (Read($0B/$0C/
// $0D/$0E), GetState(), GetStatusRegisterSnapshot()). Read($0A) is never called. Checks happen only at
// frame boundaries (RunToVBlank) and around Adpcm::Write. The harness never forces a CPU stop.
//
// argv: bios cue bios_sha256 cue_sha256   (the runner computes the hashes and they are echoed back)
// stderr: one JSON line. Exit 0 only when the series is COMPLETED (trial 2 ran to COMPLETED and all
// eight conditions are PASS).
#include "geargrafx_core.h"
#include "adpcm.h"
#include "media.h"
#include "memory.h"
#include "huc6280.h"
#include "huc6270.h"
#include "cdrom.h"
#include <cctype>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>
#ifdef _WIN32
#include <process.h>
#define WARM_GETPID() _getpid()
#else
#include <unistd.h>
#define WARM_GETPID() getpid()
#endif

namespace {

const char* const kPinnedCommit = "b49ae82a012eade566d72e9d61c9d95a5caa4da3";
const unsigned kGuestBase = 0x3E00;   // logical; MPR1 = $F8 maps it to WRAM index $1E00
const unsigned kGuestEnd = 0x4000;
const unsigned kStubIndex = 0x1E00;
const unsigned kFrameBudget = 300;    // contract 6: frames per trial
const unsigned kObserveWait = 60;     // contract 6: frames allowed to observe PLAY_ACTIVE
const double kTrialSeconds = 120.0;   // contract 6: per trial, checked at frame boundaries
const double kProcessSeconds = 600.0; // contract 6: per process, checked at frame boundaries
const unsigned kPlayAddress = 0x1234;
const unsigned kResetValue = 0x80;    // CONTROL $80 (standard class; $A0 is not used here)

struct Point {
    unsigned frame;
    unsigned pc, a, x, y, s, p;
    unsigned mpr[8];
    std::vector<u8> wram;             // m_wram[0x0000-0x1FFF]
    unsigned status, control, dma, sample_rate;
    unsigned playing, address, read_address, write_address, length, end_irq, half_irq;
    unsigned irr, idr;                // raw HuC6280 IRQ registers; semantics UNKNOWN (contract 5)
    unsigned long long master_clock;  // GetMasterClockCycles(): monotonic since reset
    bool cd_ok;                       // CD state readable (GetCDROM() != nullptr)
    unsigned cd_active, cd_enabled;   // raw ACTIVE_IRQS / ENABLED_IRQS bytes; semantics not claimed
    bool vdc_ok;                      // HuC6270 (VDC1) readable
    unsigned vdc_sr, vdc_hpos, vdc_vpos;  // raw state; semantics UNKNOWN
    bool read_busy, write_busy;       // (m_read_cycles > 0), (m_write_cycles > 0) via GetStatusRegisterSnapshot
    unsigned frame_samples, control_state, dma_state, sample_rate_state;  // remaining Adpcm_State scalars
};

// Read-only capture. Every accessor here is side-effect free.
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
    pt.master_clock = core.GetMasterClockCycles();
    CdRom* cdrom = core.GetCDROM();
    pt.cd_ok = cdrom != nullptr;
    if (cdrom) {
        CdRom::CdRom_State* cds = cdrom->GetState();
        pt.cd_active = *cds->ACTIVE_IRQS;
        pt.cd_enabled = *cds->ENABLED_IRQS;
    }
    HuC6270* vdc = core.GetHuC6270_1();
    pt.vdc_ok = vdc != nullptr;
    if (vdc) {
        HuC6270::HuC6270_State* vs = vdc->GetState();
        pt.vdc_sr = *vs->SR;
        pt.vdc_hpos = (unsigned)*vs->HPOS;
        pt.vdc_vpos = (unsigned)*vs->VPOS;
    }
    const u8 snap = adpcm->GetStatusRegisterSnapshot();   // read-only: returns flags from bool members
    pt.read_busy = (snap & 0x80) != 0;
    pt.write_busy = (snap & 0x04) != 0;
    pt.frame_samples = (unsigned)*st->FRAME_SAMPLES;
    pt.control_state = *st->CONTROL;
    pt.dma_state = *st->DMA;
    pt.sample_rate_state = *st->SAMPLE_RATE;
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

// Same point format as geargrafx_ad_reset_full.cpp (the 25-trial records use it).
void emit_point(std::string& out, const char* name, const Point* pt, bool last)
{
    out += "\"";
    out += name;
    out += "\":";
    if (!pt) {
        out += "\"NOT_CAPTURED\"";
    } else {
        char buf[2048];
        std::snprintf(buf, sizeof buf,
                      "{\"frame\":%u,\"clock\":{\"master_clock_cycles\":%llu,\"clock_calls\":\"NOT_CAPTURED\"},"
                      "\"cpu\":{\"pc\":%u,\"a\":%u,\"x\":%u,\"y\":%u,\"s\":%u,\"p\":%u,"
                      "\"mpr\":[%u,%u,%u,%u,%u,%u,%u,%u]},\"wram_8k_hex\":\"",
                      pt->frame, pt->master_clock, pt->pc, pt->a, pt->x, pt->y, pt->s, pt->p, pt->mpr[0],
                      pt->mpr[1], pt->mpr[2], pt->mpr[3], pt->mpr[4], pt->mpr[5], pt->mpr[6], pt->mpr[7]);
        out += buf;
        hex_bytes(out, pt->wram);
        std::snprintf(buf, sizeof buf,
                      "\",\"adpcm\":{\"status_0c\":%u,\"control_0d\":%u,\"dma_0b\":%u,\"sample_rate_0e\":%u,"
                      "\"playing\":%u,\"address\":%u,\"read_address\":%u,\"write_address\":%u,\"length\":%u,"
                      "\"end_irq\":%u,\"half_irq\":%u,\"read_busy_bool\":%s,\"write_busy_bool\":%s,"
                      "\"read_cycles\":\"NOT_CAPTURED\",\"write_cycles\":\"NOT_CAPTURED\","
                      "\"play_pending\":\"NOT_CAPTURED\",\"frame_samples\":%u,\"state_control\":%u,"
                      "\"state_dma\":%u,\"state_sample_rate\":%u,\"state_buffer\":\"NOT_CAPTURED\","
                      "\"ram_64k\":\"NOT_CAPTURED\"},"
                      "\"irq\":{\"irr\":%u,\"idr\":%u,\"cd_active\":%s,\"cd_enabled\":%s,\"semantics\":\"UNKNOWN\"},",
                      pt->status, pt->control, pt->dma, pt->sample_rate, pt->playing, pt->address,
                      pt->read_address, pt->write_address, pt->length, pt->end_irq, pt->half_irq,
                      pt->read_busy ? "true" : "false", pt->write_busy ? "true" : "false",
                      pt->frame_samples, pt->control_state, pt->dma_state, pt->sample_rate_state, pt->irr,
                      pt->idr, pt->cd_ok ? std::to_string(pt->cd_active).c_str() : "\"NOT_CAPTURED\"",
                      pt->cd_ok ? std::to_string(pt->cd_enabled).c_str() : "\"NOT_CAPTURED\"");
        out += buf;
        if (pt->vdc_ok)
            std::snprintf(buf, sizeof buf,
                          "\"vdc\":{\"unit\":\"HuC6270_1\",\"sr\":%u,\"hpos\":%u,\"vpos\":%u,"
                          "\"semantics\":\"UNKNOWN\"}}",
                          pt->vdc_sr, pt->vdc_hpos, pt->vdc_vpos);
        else
            std::snprintf(buf, sizeof buf, "\"vdc\":\"NOT_CAPTURED\"}");
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

// A gate item. status is PASS, FAIL, NOT_CAPTURED, NOT_IMPLEMENTED or NOT_EVALUATED. Only PASS counts.
struct Check {
    std::string id;
    std::string status;
    std::string detail;   // no double quotes inside
    std::string source;   // function and line of the capture or the rule
};

struct Trial {
    int index = 0;
    std::string role;                      // "warmup" or "warm"
    std::string end_state = "NOT_STARTED"; // COMPLETED, BUDGET_EXCEEDED, TIMEOUT, ABORTED, BLOCKED, NOT_STARTED
    std::string end_reason;
    std::string cls = "NOT_STARTED";       // PLAY_ACTIVE or UNOBSERVED_BUSY when the body runs
    bool body_ran = false;
    unsigned long long pid = 0, instance = 0;
    unsigned reset_count_w1 = 0;
    bool ready_w0 = false, ready_w1 = false;
    unsigned long long bp_w0 = 0, bp_w1 = 0;
    bool has_w0 = false, has_w1 = false, has_w2 = false;
    Point w0, w1, w2;
    unsigned pc_set_rb = 0, s_set_rb = 0, p_set_rb = 0;
    unsigned pc_after_frame = 0;
    bool reset_issued = false;
    unsigned reset_write_value = 0, r2a_readback = 0;
    bool has_p0 = false, has_p1 = false, has_p2 = false;
    Point p0, p1, p2, p3;
    unsigned frames = 0;
};

std::string json_string(const std::string& s)
{
    std::string out = "\"";
    for (char c : s) {
        if (c == '"' || c == '\\')
            out += '\\';
        out += c;
    }
    return out + "\"";
}

}  // namespace

int main(int argc, char** argv)
{
    if (argc != 5)
        return 2;
    if (!is_hex_sha256(argv[3]) || !is_hex_sha256(argv[4]))
        return 2;

    std::ifstream file(argv[1], std::ios::binary);
    if (!file)
        return 3;
    std::vector<u8> bios((std::istreambuf_iterator<char>(file)), {});
    if (bios.empty())
        return 3;

    const auto process_t0 = std::chrono::steady_clock::now();
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
    Adpcm* adpcm = core.GetAdpcm();
    const unsigned long long pid = (unsigned long long)WARM_GETPID();
    const unsigned long long instance = (unsigned long long)(std::uintptr_t)&core;
    unsigned reset_count = 0;   // ResetMedia calls on this instance (contract 14.5)

    GeargrafxCore::GG_Debug_Run debug = {};
    std::vector<u8> frame(1024 * 512 * 4, 0);
    std::vector<s16> audio(65536, 0);

    auto run_frame = [&](Trial& t) {
        int samples = 0;
        core.RunToVBlank(frame.data(), audio.data(), &samples, &debug, false);
        ++t.frames;
    };
    // Stop checks at a frame boundary only. Returns true when the trial must stop (contract 7.1).
    auto stop_check = [&](Trial& t, std::chrono::steady_clock::time_point trial_t0) {
        const double process_secs =
            std::chrono::duration<double>(std::chrono::steady_clock::now() - process_t0).count();
        const double trial_secs =
            std::chrono::duration<double>(std::chrono::steady_clock::now() - trial_t0).count();
        const unsigned pc = (unsigned)cs->PC->GetValue();
        if (process_secs > kProcessSeconds) {
            t.end_state = "TIMEOUT";
            t.end_reason = "process_wall_clock";
        } else if (trial_secs > kTrialSeconds) {
            t.end_state = "TIMEOUT";
            t.end_reason = "trial_wall_clock";
        } else if (pc < kGuestBase || pc >= kGuestEnd) {
            t.end_state = "ABORTED";
            t.end_reason = "pc_outside_guest";
        }
        return t.end_state != "COMPLETED";
    };
    auto playing_observed = [&]() {
        return (adpcm->Read(0x0C) & 0x08) != 0 && *adpcm->GetState()->PLAYING;
    };

    // Contract 14.3 steps 2 to 10 (the pre-trial part). Returns false when a 14.2 stop applies.
    auto pre_trial = [&](Trial& t) -> bool {
        // step 2: reset values are consumed by ResetMedia below; set them before it
        memory->SetResetValues(0, 0, 0, 0);
        core.GetHuC6280()->SetResetValue(0);
        // step 3 and 14.2: IsReady and the breakpoint list before ResetMedia
        t.ready_w0 = core.GetMedia()->IsReady();
        const std::vector<HuC6280::GG_Breakpoint>* bp0 = core.GetHuC6280()->GetBreakpoints();
        t.bp_w0 = bp0 ? bp0->size() : 0;
        // step 4: W0 (residual state; recorded, not judged)
        t.w0 = capture(core, t.frames);
        t.has_w0 = true;
        if (!t.ready_w0 || t.bp_w0 != 0) {
            t.end_state = "BLOCKED";
            t.end_reason = !t.ready_w0 ? "media_not_ready" : "breakpoints_not_empty";
            return false;
        }
        // step 5: the reset itself (no-op if the media is not ready, which was checked above)
        core.ResetMedia(false);
        ++reset_count;
        // step 6: W1 (after ResetMedia, before the placement)
        t.w1 = capture(core, t.frames);
        t.has_w1 = true;
        t.reset_count_w1 = reset_count;
        t.ready_w1 = core.GetMedia()->IsReady();
        const std::vector<HuC6280::GG_Breakpoint>* bp1 = core.GetHuC6280()->GetBreakpoints();
        t.bp_w1 = bp1 ? bp1->size() : 0;
        // step 7: placement (MPR0 = $00 leaves the I/O page unmapped; ADPCM is driven by the harness)
        memory->SetMpr(0, 0x00);
        memory->SetMpr(1, 0xF8);
        for (int i = 2; i <= 7; ++i)
            memory->SetMpr(i, 0x00);
        u8* wram = memory->GetWorkingRAM();
        wram[kStubIndex] = 0x80;      // BRA *
        wram[kStubIndex + 1] = 0xFE;
        // step 8: PC, S, P and their readback
        cs->PC->SetValue((u16)kGuestBase);
        cs->S->SetValue(0xFF);
        cs->P->SetValue(0x04);        // I set
        t.pc_set_rb = (unsigned)cs->PC->GetValue();
        t.s_set_rb = (unsigned)cs->S->GetValue();
        t.p_set_rb = (unsigned)cs->P->GetValue();
        // step 9: W2
        t.w2 = capture(core, t.frames);
        t.has_w2 = true;
        // step 10 (ii), PC stability part: one frame before any ADPCM operation
        run_frame(t);
        t.pc_after_frame = (unsigned)cs->PC->GetValue();
        return true;
    };

    // Trial body (contract 2.1, play-reset-80 standard class). Runs only after the pre-trial part.
    auto trial_body = [&](Trial& t, std::chrono::steady_clock::time_point trial_t0) {
        t.body_ran = true;
        t.end_state = "COMPLETED";
        t.end_reason = "budget_end";
        t.cls = "UNOBSERVED_BUSY";
        bool stopped = false;
        bool observed = false;
        adpcm->Write(0x08, kPlayAddress & 0xFF);   // ADDRESS = $1234
        adpcm->Write(0x09, kPlayAddress >> 8);
        adpcm->Write(0x0D, 0x10);                   // LENGTH copy from ADDRESS (contract 2.1 step 2)
        run_frame(t);
        stopped = stop_check(t, trial_t0);
        if (!stopped) {
            adpcm->Write(0x0D, 0x20);               // play request (contract 2.1 step 4)
            for (unsigned i = 0; i < kObserveWait && !observed && !stopped; ++i) {
                run_frame(t);
                stopped = stop_check(t, trial_t0);
                observed = !stopped && playing_observed();
            }
            if (!stopped && !observed) {
                t.end_state = "BUDGET_EXCEEDED";
                t.end_reason = "observe_wait_60_frames";
                stopped = true;
            }
        }
        if (observed)
            t.cls = "PLAY_ACTIVE";
        if (observed && !stopped) {
            t.p0 = capture(core, t.frames);
            t.has_p0 = true;
            adpcm->Write(0x0D, kResetValue);        // the reset, issued once, from the harness only
            t.reset_issued = true;
            t.reset_write_value = kResetValue;
            t.p1 = capture(core, t.frames);         // P1: no Clock has run since the write
            t.has_p1 = true;
            t.r2a_readback = t.p1.control;
            run_frame(t);                           // CheckReset runs inside Clock
            stopped = stop_check(t, trial_t0);
            if (!stopped) {
                t.p2 = capture(core, t.frames);
                t.has_p2 = true;
            }
            while (!stopped && t.frames < kFrameBudget) {
                run_frame(t);
                stopped = stop_check(t, trial_t0);
            }
        }
        t.p3 = capture(core, t.frames);             // P3: budget end or the stop point
    };

    // ---- trial 1 (warm-up) ----
    Trial t1;
    t1.index = 1;
    t1.role = "warmup";
    t1.pid = pid;
    t1.instance = instance;
    {
        const auto t0 = std::chrono::steady_clock::now();
        if (pre_trial(t1))
            trial_body(t1, t0);
    }

    // ---- trial 2 (warm), only after trial 1 is COMPLETED (contract 14.8) ----
    Trial t2;
    t2.index = 2;
    t2.role = "warm";
    t2.pid = pid;
    t2.instance = instance;
    std::vector<Check> gate;
    const bool t1_completed = t1.end_state == "COMPLETED";
    if (t1_completed)
        pre_trial(t2);   // sets t2 fields; a 14.2 stop here leaves t2.end_state BLOCKED

    // ---- contract 14.12: eight conditions, judged before trial 2 starts ----
    auto add = [&](const char* id, const std::string& status, const std::string& detail, const char* source) {
        Check c;
        c.id = id;
        c.status = status;
        c.detail = detail;
        c.source = source;
        gate.push_back(c);
    };
    add("1", t1_completed ? "PASS" : "FAIL", "trial 1 end_state=" + t1.end_state, "trial 1 end_state");

    const bool t2_pre = t2.has_w0 && t2.has_w1 && t2.has_w2;
    if (!t2_pre) {
        add("2", "NOT_EVALUATED", "trial 2 pre-trial part not reached", "pre_trial");
        add("3", "NOT_EVALUATED", "trial 2 pre-trial part not reached", "pre_trial");
    } else {
        add("2", t2.ready_w0 && t2.ready_w1 ? "PASS" : "FAIL",
            std::string("IsReady at W0=") + (t2.ready_w0 ? "true" : "false") + " W1=" +
                (t2.ready_w1 ? "true" : "false"),
            "GeargrafxCore::GetMedia()->IsReady() geargrafx_core.h:103, media.h:43");
        add("3", t2.bp_w0 == 0 && t2.bp_w1 == 0 ? "PASS" : "FAIL",
            "breakpoints W0=" + std::to_string(t2.bp_w0) + " W1=" + std::to_string(t2.bp_w1),
            "HuC6280::GetBreakpoints() huc6280.h:172");
    }

    if (!t2_pre) {
        add("4", "NOT_EVALUATED", "trial 2 W1 not reached", "W1 capture");
    } else {
        const Point& w1 = t2.w1;
        bool wram_zero = true;
        for (u8 b : w1.wram)
            if (b != 0)
                wram_zero = false;
        const bool mpr_low_zero = w1.mpr[0] == 0 && w1.mpr[1] == 0 && w1.mpr[2] == 0 && w1.mpr[3] == 0 &&
                                  w1.mpr[4] == 0 && w1.mpr[5] == 0 && w1.mpr[6] == 0;
        const bool mpr7_zero = w1.mpr[7] == 0;
        const bool i_set = (w1.p & 0x04) != 0;
        const bool irr_idr_zero = w1.irr == 0 && w1.idr == 0;
        const bool control_zero = w1.control == 0;
        const bool rate_0f = w1.sample_rate == 0x0F;
        const bool reset_twice = t2.reset_count_w1 == 2;
        std::string detail = std::string("wram_zero=") + (wram_zero ? "true" : "false") +
                             " mpr0_6_zero=" + (mpr_low_zero ? "true" : "false") +
                             " mpr7_zero=" + (mpr7_zero ? "true" : "false") + " P_I_set=" +
                             (i_set ? "true" : "false") + " irr_idr_zero=" + (irr_idr_zero ? "true" : "false") +
                             " adpcm_control_zero=" + (control_zero ? "true" : "false") +
                             " adpcm_rate_0f=" + (rate_0f ? "true" : "false") +
                             " adpcm_ram_zero=NOT_CAPTURED reset_count_is_2=" + (reset_twice ? "true" : "false");
        const bool any_fail = !wram_zero || !mpr_low_zero || !mpr7_zero || !i_set || !irr_idr_zero ||
                              !control_zero || !rate_0f || !reset_twice;
        add("4", any_fail ? "FAIL" : "NOT_CAPTURED", detail,
            "capture() geargrafx_ad_reset_warm.cpp; ADPCM RAM has no public accessor (adpcm.h:95 private)");
    }

    if (!t2_pre) {
        add("5", "NOT_EVALUATED", "trial 2 W2 not reached", "W2 capture");
        add("6", "NOT_EVALUATED", "trial 2 W2 not reached", "PC stability");
        add("7", "NOT_EVALUATED", "trial 2 W2 not reached", "static (i) and dynamic (ii)");
    } else {
        const Point& w2 = t2.w2;
        const bool pc_ok = t2.pc_set_rb == kGuestBase && w2.pc == kGuestBase;
        const bool s_ok = t2.s_set_rb == 0xFF && w2.s == 0xFF;
        const bool p_ok = t2.p_set_rb == 0x04 && w2.p == 0x04;
        const bool mpr_ok = w2.mpr[0] == 0x00 && w2.mpr[1] == 0xF8 && w2.mpr[2] == 0x00 && w2.mpr[3] == 0x00 &&
                            w2.mpr[4] == 0x00 && w2.mpr[5] == 0x00 && w2.mpr[6] == 0x00 && w2.mpr[7] == 0x00;
        const bool bytes_ok = w2.wram.size() > kStubIndex + 1 && w2.wram[kStubIndex] == 0x80 &&
                              w2.wram[kStubIndex + 1] == 0xFE;
        const bool w2_ok = pc_ok && s_ok && p_ok && mpr_ok && bytes_ok;
        add("5", w2_ok ? "PASS" : "FAIL",
            std::string("pc=") + (pc_ok ? "ok" : "mismatch") + " s=" + (s_ok ? "ok" : "mismatch") + " p=" +
                (p_ok ? "ok" : "mismatch") + " mpr=" + (mpr_ok ? "ok" : "mismatch") + " guest_bytes=" +
                (bytes_ok ? "ok" : "mismatch"),
            "W2 readback; setters geargrafx_ad_reset_warm.cpp pre_trial steps 7 to 9");
        const bool stable = t2.pc_set_rb == kGuestBase && t2.pc_after_frame == kGuestBase;
        add("6", stable ? "PASS" : "FAIL",
            "pc_set_readback=" + std::to_string(t2.pc_set_rb) + " pc_after_first_frame=" +
                std::to_string(t2.pc_after_frame),
            "HuC6280 PC readback huc6280.h:59-73; one RunToVBlank after W2");
        add("7", w2_ok ? "NOT_IMPLEMENTED" : "FAIL",
            "(i) static readback equals condition 5; (ii) dynamic MPR detection not reachable from this probe",
            "contract 14.3 step 10; dynamic detector lives in geargrafx_reference_jsr.cpp (not used)");
    }

    if (!t2_pre) {
        add("8", "NOT_EVALUATED", "trial 2 W1 not reached", "identity evidence");
    } else {
        const bool same_process = t1.pid == t2.pid && t1.instance == t2.instance;
        const bool counts = t1.reset_count_w1 == 1 && t2.reset_count_w1 == 2;
        add("8", same_process && counts ? "PASS" : "FAIL",
            "pid=" + std::to_string(t2.pid) + " instance_equal=" + (t1.instance == t2.instance ? "true" : "false") +
                " reset_count_t1=" + std::to_string(t1.reset_count_w1) + " reset_count_t2=" +
                std::to_string(t2.reset_count_w1),
            "GETPID and &core at trial start; ResetMedia counter");
    }

    bool all_pass = true;
    std::string failed_ids;
    for (const Check& c : gate)
        if (c.status != "PASS") {
            all_pass = false;
            failed_ids += (failed_ids.empty() ? "" : ",") + c.id;
        }

    if (t2_pre && all_pass) {
        const auto t0 = std::chrono::steady_clock::now();
        trial_body(t2, t0);
    } else if (t2_pre && t2.end_state != "BLOCKED") {
        t2.end_state = "BLOCKED";
        t2.end_reason = "14.12 not all PASS: " + failed_ids;
    }
    if (!t1_completed && t2.end_state == "NOT_STARTED")
        t2.end_reason = "trial 1 not COMPLETED (contract 14.8)";

    std::string series_end = "COMPLETED";
    std::string series_reason = "budget_end";
    if (!t1_completed) {
        series_end = "BLOCKED";
        series_reason = "trial 1 not COMPLETED";
    } else if (!all_pass || !t2.body_ran) {
        series_end = "BLOCKED";
        series_reason = t2.end_state == "BLOCKED" && !t2_pre
                            ? "trial 2 not started: " + t2.end_reason
                            : "trial 2 not started: 14.12 not all PASS (" + failed_ids + ")";
    } else if (t2.end_state != "COMPLETED") {
        series_end = t2.end_state;
        series_reason = t2.end_reason;
    }
    const bool eligible_warm = t2.body_ran && t2.cls == "PLAY_ACTIVE" && t2.end_state == "COMPLETED" && all_pass;

    auto emit_trial = [&](const Trial& t) {
        std::string out = "{\"index\":" + std::to_string(t.index) + ",\"role\":" + json_string(t.role) +
                          ",\"end_state\":" + json_string(t.end_state) + ",\"end_reason\":" +
                          json_string(t.end_reason) + ",\"measurement_class\":" + json_string(t.cls) + ",";
        out += "\"evidence\":{\"pid\":" + std::to_string(t.pid) + ",\"instance_id\":\"0x" + [&]() {
            char b[32];
            std::snprintf(b, sizeof b, "%llx", t.instance);
            return std::string(b);
        }() + "\",\"reset_count_at_w1\":" + std::to_string(t.reset_count_w1) + ",\"ready_w0\":" +
               (t.ready_w0 ? "true" : "false") + ",\"ready_w1\":" + (t.ready_w1 ? "true" : "false") +
               ",\"breakpoints_w0\":" + std::to_string(t.bp_w0) + ",\"breakpoints_w1\":" +
               std::to_string(t.bp_w1) + "},";
        out += "\"reset\":{\"issued\":" + std::string(t.reset_issued ? "true" : "false") + ",\"write_value\":" +
               (t.reset_issued ? std::to_string(t.reset_write_value) : std::string("\"NOT_ISSUED\"")) +
               ",\"r2a_readback\":" +
               (t.reset_issued ? std::to_string(t.r2a_readback) : std::string("\"NOT_CAPTURED\"")) + "},";
        out += "\"frames\":" + std::to_string(t.frames) + ",\"points\":{";
        if (t.has_w0)
            emit_point(out, "W0", &t.w0, false);
        else
            emit_point(out, "W0", nullptr, false);
        emit_point(out, "W1", t.has_w1 ? &t.w1 : nullptr, false);
        emit_point(out, "W2", t.has_w2 ? &t.w2 : nullptr, false);
        emit_point(out, "P0", t.has_p0 ? &t.p0 : nullptr, false);
        emit_point(out, "P1", t.has_p1 ? &t.p1 : nullptr, false);
        emit_point(out, "P2", t.has_p2 ? &t.p2 : nullptr, false);
        emit_point(out, "P3", t.body_ran ? &t.p3 : nullptr, true);
        out += "}}";
        return out;
    };

    std::string out = "{\"probe\":\"geargrafx_ad_reset_warm\",\"pinned_commit\":\"";
    out += kPinnedCommit;
    out += "\",\"series\":\"warm\",\"end_state\":" + json_string(series_end) + ",\"end_reason\":" +
           json_string(series_reason) + ",\"eligible_warm_set\":" + (eligible_warm ? "true" : "false") + ",";
    out += "\"input_sha256\":{\"bios\":\"" + std::string(argv[3]) + "\",\"cue\":\"" + std::string(argv[4]) + "\"},";
    out += "\"guest\":{\"base\":\"0x3E00\",\"bytes_hex\":\"80fe\"},";
    out += "\"process\":{\"pid\":" + std::to_string(pid) + "},";
    out += "\"conditions_14_12\":[";
    for (size_t i = 0; i < gate.size(); ++i) {
        const Check& c = gate[i];
        out += "{\"id\":" + json_string(c.id) + ",\"status\":" + json_string(c.status) + ",\"detail\":" +
               json_string(c.detail) + ",\"source\":" + json_string(c.source) + "}";
        out += i + 1 < gate.size() ? "," : "";
    }
    out += "],\"trials\":[" + emit_trial(t1) + "," + emit_trial(t2) + "]}\n";

    if (std::fwrite(out.data(), 1, out.size(), stderr) != out.size())
        return 1;
    std::fflush(stderr);
    return series_end == "COMPLETED" ? 0 : 1;
}
