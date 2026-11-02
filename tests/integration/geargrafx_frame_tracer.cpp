// Bounded, observation-only Geargrafx frame tracer.
//
// This local-only probe links the pinned GPL Geargrafx objects through the existing
// test build recipe.  It deliberately uses normal RunToVBlank execution rather than
// the instruction debugger.  It does not call Memory::Read, write guest memory,
// alter CPU registers/MPRs, inject interrupts, or perform MMIO reads. RAM, CPU
// state, MPRs, and CD/SCSI state are sampled through state pointers at frame
// boundaries only. RAM output is counts, offset ranges, and mapped bank ranges;
// it never serializes RAM bytes.
//
// argv: rom cue|- frames event_capacity run_start_frame|0 run_hold_frames|0
// Output: CAP, INPUT, EVENT (bounded ring, oldest entries overwritten), END
// with a cumulative RAM summary that is deliberately independent of that ring.
#include "geargrafx_core.h"
#include "huc6280.h"
#include "memory.h"
#include "media.h"
#include "cdrom.h"
#include "scsi_controller.h"
#include "trace_logger.h"
#include "geargrafx_frame_tracer_ring.h"
#include "geargrafx_frame_tracer_ram.h"
#include "geargrafx_frame_tracer_ram_summary.h"
#include "geargrafx_trace_cpu_region.h"
#include <array>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

namespace {

constexpr unsigned kMaxFrames = 600;
constexpr unsigned kRingSize = 256;

struct CpuSnap {
    unsigned master_clock, pc, pc_page, pc_bank, physical_pc, pc_is_wram, a, x, y, p, sp;
    unsigned mpr[8];
};

struct CdSnap {
    int phase;
    unsigned signals, load_sector, load_count, irq_active, irq_enabled;
};

struct Event {
    enum Kind { SAMPLE, CD, RAM, HANDOFF, REGION } kind;
    unsigned frame;
    CpuSnap cpu;
    CdSnap cd;
    unsigned changed, first, last, cd_event, cd_command, cd_lba, cd_status;
    const FrameTracerRamMapping* ram;
    unsigned event_pc, event_bank;
    unsigned long long event_cycle;
    const char* from_region;
    const char* to_region;
    bool trace_gap;
};

struct RamTracker {
    const char* name;
    bool present, unknown;
    FrameTracerRamMapping mapping;
    const u8* current;
    std::vector<u8> previous;
    FrameTracerRamSummary summary;
    std::vector<unsigned> scsi_update_frames;
};

CpuSnap cpu_snap(GeargrafxCore& core, HuC6280::HuC6280_State* cpu, Memory* memory) {
    const unsigned pc = cpu->PC->GetValue();
    const unsigned page = pc >> 13;
    CpuSnap snap = {(unsigned)core.GetMasterClockCycles(), pc, page, memory->GetMpr(page),
                    memory->GetMpr(page) * 0x2000 + (pc & 0x1fff),
                    memory->GetMpr(page) >= 0xf8 && memory->GetMpr(page) <= 0xfb,
                    cpu->A->GetValue(), cpu->X->GetValue(), cpu->Y->GetValue(),
                    cpu->P->GetValue(), cpu->S->GetValue(), {}};
    for (int i = 0; i != 8; ++i)
        snap.mpr[i] = memory->GetMpr(i);
    return snap;
}

CdSnap cd_snap(ScsiController::Scsi_State* scsi, CdRom::CdRom_State* cdrom) {
    return {(int)*scsi->PHASE, (unsigned)*scsi->SIGNALS, (unsigned)*scsi->LOAD_SECTOR,
            (unsigned)*scsi->LOAD_SECTOR_COUNT, (unsigned)*cdrom->ACTIVE_IRQS,
            (unsigned)*cdrom->ENABLED_IRQS};
}

bool same_cpu(const CpuSnap& left, const CpuSnap& right) {
    return left.pc == right.pc && left.pc_bank == right.pc_bank && left.a == right.a && left.x == right.x && left.y == right.y &&
           left.p == right.p && left.sp == right.sp && std::memcmp(left.mpr, right.mpr, sizeof(left.mpr)) == 0;
}

bool same_cd(const CdSnap& left, const CdSnap& right) {
    return left.phase == right.phase && left.signals == right.signals && left.load_sector == right.load_sector &&
           left.load_count == right.load_count && left.irq_active == right.irq_active &&
           left.irq_enabled == right.irq_enabled;
}

void print_cpu(const CpuSnap& cpu) {
    std::printf("\"master_clock\":%u,\"pc\":%u,\"pc_page\":%u,\"pc_bank\":%u,\"physical_pc\":%u,\"pc_is_wram\":%s,\"a\":%u,\"x\":%u,\"y\":%u,\"p\":%u,\"sp\":%u,\"mpr\":[",
                 cpu.master_clock, cpu.pc, cpu.pc_page, cpu.pc_bank, cpu.physical_pc,
                 cpu.pc_is_wram ? "true" : "false", cpu.a, cpu.x, cpu.y, cpu.p, cpu.sp);
    for (int i = 0; i != 8; ++i)
        std::printf("%s%u", i ? "," : "", cpu.mpr[i]);
    std::printf("]");
}

void print_cd(const CdSnap& cd) {
    std::printf("\"scsi_phase\":%d,\"scsi_signals\":%u,\"load_sector\":%u,\"load_count\":%u,"
                "\"cd_irq_active\":%u,\"cd_irq_enabled\":%u",
                cd.phase, cd.signals, cd.load_sector, cd.load_count, cd.irq_active, cd.irq_enabled);
}

void print_event(const Event& event) {
    const char* kind = event.kind == Event::SAMPLE ? "sample" : event.kind == Event::CD ? "cd" :
                        event.kind == Event::RAM ? "ram" : event.kind == Event::REGION ? "region" : "handoff";
    std::printf("EVENT {\"kind\":\"%s\",\"frame\":%u,", kind, event.frame);
    if (event.kind == Event::REGION) {
        // These fields come only from the TRACE_CPU entry, which contains the
        // resolved bank and PC at instruction time.  Never serialize opcode
        // bytes or disassembly text from that entry.
        std::printf("\"event_cycle\":%llu,\"event_pc\":%u,\"event_bank\":%u,\"from_region\":\"%s\",\"to_region\":\"%s\",\"trace_gap\":%s",
                    event.event_cycle, event.event_pc, event.event_bank, event.from_region,
                    event.to_region, event.trace_gap ? "true" : "false");
    } else {
        print_cpu(event.cpu);
    }
    if (event.kind == Event::CD) {
        // SCSI metadata is event-originated, but CPU/CD fields are sampled only
        // after RunToVBlank returns; never present them as event-time state.
        // Command payload bytes are never serialized.
        std::printf(",\"snapshot\":\"FRAME_SNAPSHOT\",\"cd_event\":%u,\"command\":%u,\"lba\":%u,\"status\":%u,",
                    event.cd_event, event.cd_command, event.cd_lba, event.cd_status);
        print_cd(event.cd);
    } else if (event.kind == Event::RAM) {
        // Counts and mapped ranges only: never serialize guest bytes or pointers.
        std::printf(",\"ram\":\"%s\",\"size\":%u,\"changed\":%u,\"first_offset\":%u,"
                    "\"last_offset\":%u,\"bank_first\":%u,\"bank_last\":%u",
                    event.ram->name, event.ram->size, event.changed, event.first, event.last,
                    event.ram->first_bank, event.ram->last_bank);
    }
    std::printf("}\n");
}

void print_optional(unsigned value, bool have_value) {
    if (have_value) std::printf("%u", value);
    else std::printf("null");
}

void print_ram_summary(const std::vector<RamTracker>& rams) {
    std::printf(",\"ram_deltas\":[");
    for (unsigned index = 0; index != rams.size(); ++index) {
        const RamTracker& ram = rams[index];
        const bool changed = ram.summary.updates != 0;
        std::printf("%s{\"region\":\"%s\",\"present\":", index ? "," : "", ram.name);
        if (ram.unknown) {
            std::printf("null,\"availability\":\"UNKNOWN\"");
        } else if (!ram.present) {
            std::printf("false,\"availability\":\"ABSENT\"");
        } else {
            std::printf("true,\"availability\":\"PRESENT\",\"size\":%u,\"bank_first\":%u,\"bank_last\":%u",
                        ram.mapping.size, ram.mapping.first_bank, ram.mapping.last_bank);
        }
        std::printf(",\"updates\":%u,\"first_frame\":", ram.summary.updates);
        print_optional(ram.summary.first_frame, changed);
        std::printf(",\"last_frame\":");
        print_optional(ram.summary.last_frame, changed);
        std::printf(",\"min_offset\":");
        print_optional(ram.summary.min_offset, changed);
        std::printf(",\"max_offset\":");
        print_optional(ram.summary.max_offset, changed);
        std::printf(",\"max_changed\":%u,\"scsi_correlation\":\"%s\",\"scsi_update_frames\":[",
                    ram.summary.max_changed, ram.summary.scsi_correlation_unknown ? "UNKNOWN" :
                    (ram.scsi_update_frames.empty() ? "NONE_OBSERVED" : "OBSERVED"));
        for (unsigned frame = 0; frame != ram.scsi_update_frames.size(); ++frame)
            std::printf("%s%u", frame ? "," : "", ram.scsi_update_frames[frame]);
        std::printf("]}");
    }
    std::printf("]");
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 8)
        return 2;
    char* end = nullptr;
    long frames = std::strtol(argv[3], &end, 10);
    if (!end || *end || frames < 1 || frames > kMaxFrames)
        return 2;
    end = nullptr;
    long capacity = std::strtol(argv[4], &end, 10);
    if (!end || *end || capacity < 1 || capacity > kRingSize)
        return 2;
    end = nullptr;
    long run_start = std::strtol(argv[5], &end, 10);
    if (!end || *end || run_start < 0 || run_start > frames)
        return 2;
    end = nullptr;
    long run_hold = std::strtol(argv[6], &end, 10);
    if (!end || *end || run_hold < 0 || run_hold > frames ||
        ((run_start == 0) != (run_hold == 0)) || (run_start && run_start + run_hold - 1 > frames))
        return 2;
    end = nullptr;
    long observe_ram = std::strtol(argv[7], &end, 10);
    if (!end || *end || (observe_ram != 0 && observe_ram != 1))
        return 2;
    std::ifstream input(argv[1], std::ios::binary);
    if (!input)
        return 2;
    std::vector<u8> rom((std::istreambuf_iterator<char>(input)), {});
    const std::string cue(argv[2]);
    const bool have_disc = cue != "-";

    GeargrafxCore core;
    core.Init(nullptr);
    bool loaded = false;
    if (have_disc) {
        loaded = core.LoadBiosFromBuffer(rom.data(), rom.size(), true) && core.LoadMedia(cue.c_str());
    } else {
        core.GetMedia()->SetCDROMType(GG_CDROM_STANDARD);
        loaded = core.LoadHuCardFromBuffer(rom.data(), (int)rom.size(), "frame-trace-card.pce");
    }
    if (!loaded)
        return 3;

    HuC6280::HuC6280_State* cpu = core.GetHuC6280()->GetState();
    Memory* memory = core.GetMemory();
    ScsiController::Scsi_State* scsi = core.GetScsiController()->GetState();
    CdRom::CdRom_State* cdrom = core.GetCDROM()->GetState();
    std::vector<u8> frame(1024 * 512 * 4);
    std::vector<s16> audio(65536);
    GeargrafxCore::GG_Debug_Run normal = {};  // no step_debugger: normal whole-frame execution
    FrameTracerRing<Event, kRingSize> ring((unsigned)capacity);
    std::vector<RamTracker> rams;
    const auto add_ram = [&rams, observe_ram](const char* name, FrameTracerRamKind kind, unsigned size, unsigned start,
                                              const u8* current) {
        RamTracker ram = {name, false, false, {}, current, {}, FrameTracerEmptyRamSummary(), {}};
        if (!observe_ram) {
            ram.unknown = true;  // Observer A/B run: RAM is deliberately not observed.
            rams.push_back(ram);
            return true;
        }
        if (!size) {
            rams.push_back(ram);  // Absent in this loaded configuration.
            return true;
        }
        if (!current || !FrameTracerMapRam(kind, size, start, &ram.mapping))
            return false;  // Fail closed: an unconfirmed mapping is never summarized.
        ram.present = true;
        ram.previous.assign(current, current + ram.mapping.size);
        rams.push_back(ram);
        return true;
    };
    if (!add_ram("wram", FRAME_TRACER_WRAM, (unsigned)memory->GetWorkingRAMSize(), 0, memory->GetWorkingRAM()) ||
        !add_ram("cdrom_ram", FRAME_TRACER_CDROM_RAM, (unsigned)memory->GetCDROMRAMSize(), 0, memory->GetCDROMRAM()) ||
        !add_ram("card_ram", FRAME_TRACER_CARD_RAM, (unsigned)memory->GetCardRAMSize(),
                 (unsigned)memory->GetCardRAMStart(), memory->GetCardRAM()))
        return 3;
    CpuSnap previous_cpu = cpu_snap(core, cpu, memory);
    CdSnap previous_cd = cd_snap(scsi, cdrom);
    std::printf("CAP {\"normal_frame_execution\":true,\"instruction_step\":false,\"guest_mutation\":false,"
                "\"mmio_reads\":false,\"scsi_event_notification\":true,\"cpu_event_notification\":true,"
                "\"ram_observation\":%s,\"ram_delta_summary\":true,\"ram_regions\":[",
                observe_ram ? "true" : "false");
    bool first_region = true;
    for (const RamTracker& ram : rams) {
        if (!ram.present) continue;
        std::printf("%s{\"ram\":\"%s\",\"size\":%u,\"bank_first\":%u,\"bank_last\":%u}", first_region ? "" : ",",
                    ram.mapping.name, ram.mapping.size, ram.mapping.first_bank, ram.mapping.last_bank);
        first_region = false;
    }
    std::printf("],\"ring_capacity\":%ld,\"frames\":%ld,\"disc_given\":%s,\"run_start_frame\":%ld,"
                "\"run_hold_frames\":%ld,\"run_input_requested\":%s}\n",
                capacity, frames, have_disc ? "true" : "false", run_start, run_hold, run_start ? "true" : "false");
    TraceLogger* trace = core.GetTraceLogger();
    if (!trace->SetCapacity(kRingSize)) return 3;
    trace->SetEnabledFlags(TRACE_FLAG_CPU | TRACE_FLAG_SCSI);
    trace->SetEventFilter(TRACE_SCSI, TRACE_SCSI_FILTER_COMMANDS | TRACE_SCSI_FILTER_PHASES | TRACE_SCSI_FILTER_RESPONSES);
    trace->Reset();
    u64 observed_trace = 0;
    u64 dropped_trace = 0;
    const char* previous_region = nullptr;

    for (unsigned number = 1; number <= (unsigned)frames; ++number) {
        // This is the pinned core's public controller API.  No guest RAM, CPU,
        // MPR, IRQ, CD, or SCSI state is changed by the tracer.
        if (run_start && number == (unsigned)run_start) {
            core.KeyPressed(GG_CONTROLLER_1, GG_KEY_RUN);
            std::printf("INPUT {\"frame\":%u,\"action\":\"press\",\"run_pressed\":%s}\n", number,
                        core.GetInput()->IsKeyPressed(GG_CONTROLLER_1, GG_KEY_RUN) ? "true" : "false");
        }
        int samples = 0;
        core.RunToVBlank(frame.data(), audio.data(), &samples, &normal, false);
        if (run_start && number == (unsigned)(run_start + run_hold - 1)) {
            core.KeyReleased(GG_CONTROLLER_1, GG_KEY_RUN);
            std::printf("INPUT {\"frame\":%u,\"action\":\"release\",\"run_pressed\":%s}\n", number,
                        core.GetInput()->IsKeyPressed(GG_CONTROLLER_1, GG_KEY_RUN) ? "true" : "false");
        }
        CpuSnap now_cpu = cpu_snap(core, cpu, memory);
        CdSnap now_cd = cd_snap(scsi, cdrom);
        Event sample = {Event::SAMPLE, number, now_cpu, now_cd, 0, 0, 0, 0, 0, 0, 0, nullptr};
        ring.push(sample);
        if (!same_cd(previous_cd, now_cd))
            ring.push({Event::CD, number, now_cpu, now_cd, 0, 0, 0, 255, 0, 0, 0, nullptr});
        // Consume the core's read-only event notifications.  This retains phase
        // changes that begin and end within one video frame.
        const u64 total = trace->GetTotalLogged();
        const u64 first_event = total > kRingSize ? total - kRingSize : 0;
        bool trace_gap = false;
        // A core trace gap means this frame's SCSI notifications may be incomplete.
        // Output-ring overflow is a separate, bounded-output concern and never reaches here.
        bool frame_scsi_incomplete = false;
        if (observed_trace < first_event) {
            dropped_trace += first_event - observed_trace;
            observed_trace = first_event;
            previous_region = nullptr;
            trace_gap = true;
            frame_scsi_incomplete = true;
        }
        bool scsi_observed_this_frame = false;
        for (; observed_trace < total; ++observed_trace) {
            const GG_Trace_Entry& entry = trace->GetEntry((unsigned)(observed_trace - first_event));
            if (entry.type == TRACE_SCSI) {
                scsi_observed_this_frame = true;
                ring.push({Event::CD, number, now_cpu, now_cd, 0, 0, 0, entry.scsi.event,
                           entry.scsi.command, entry.scsi.param, entry.scsi.status, nullptr});
            }
            if (entry.type == TRACE_CPU) {
                const char* region = trace_cpu_region(entry.cpu.bank);
                if (!previous_region || std::strcmp(previous_region, region) != 0) {
                    ring.push({Event::REGION, number, now_cpu, now_cd, 0, 0, 0, 0, 0, 0, 0, nullptr,
                               entry.cpu.pc, entry.cpu.bank, (unsigned long long)entry.cycle,
                               previous_region ? previous_region : "unknown", region, trace_gap});
                    previous_region = region;
                    trace_gap = false;
                }
            }
        }
        unsigned wram_changed = 0;
        // Correlation is frame-local and only uses notifications seen above;
        // it is never presented as event-time RAM state.
        for (RamTracker& ram : rams) {
            if (!ram.present) continue;
            const FrameTracerRamDelta delta = FrameTracerCompareRam(ram.previous.data(), ram.current, ram.mapping.size);
            if (std::strcmp(ram.name, "wram") == 0) wram_changed = delta.changed;
            // Only a frame that changed this RAM region and lacked complete SCSI notifications
            // can make its correlation UNKNOWN; unchanged frames carry no correlation claim.
            FrameTracerAccumulateRam(&ram.summary, delta, number, scsi_observed_this_frame,
                                     frame_scsi_incomplete && delta.changed != 0);
            if (delta.changed && scsi_observed_this_frame) ram.scsi_update_frames.push_back(number);
            if (delta.changed)
                ring.push({Event::RAM, number, now_cpu, now_cd, delta.changed, delta.first, delta.last,
                           0, 0, 0, 0, &ram.mapping});
            std::memcpy(ram.previous.data(), ram.current, ram.mapping.size);
        }
        // This is deliberately only a candidate: a mapped WRAM PC plus a recent
        // WRAM update, never a claim of a BIOS-to-game control transfer.
        if (now_cpu.pc_is_wram && wram_changed &&
            (!previous_cpu.pc_is_wram || previous_cpu.physical_pc != now_cpu.physical_pc))
            ring.push({Event::HANDOFF, number, now_cpu, now_cd, 0, 0, 0, 0, 0, 0, 0, nullptr});
        previous_cpu = now_cpu;
        previous_cd = now_cd;
    }
    for (unsigned index = 0; index != ring.size(); ++index)
        print_event(ring.at(index));
    std::printf("END {\"frames\":%ld,\"events\":%u,\"dropped\":%u,\"trace_events_dropped\":%llu,\"overflow\":%s", frames,
                ring.size(), ring.dropped(), (unsigned long long)dropped_trace,
                (ring.dropped() || dropped_trace) ? "true" : "false");
    print_ram_summary(rams);
    std::printf("}");
    std::printf("\n");
    return 0;
}
