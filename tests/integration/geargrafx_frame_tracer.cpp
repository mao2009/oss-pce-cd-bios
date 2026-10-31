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
// Output: CAP, INPUT, EVENT (bounded ring, oldest entries overwritten), END.
#include "geargrafx_core.h"
#include "huc6280.h"
#include "memory.h"
#include "media.h"
#include "cdrom.h"
#include "scsi_controller.h"
#include "trace_logger.h"
#include "geargrafx_frame_tracer_ring.h"
#include "geargrafx_frame_tracer_ram.h"
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
    enum Kind { SAMPLE, CD, RAM, HANDOFF } kind;
    unsigned frame;
    CpuSnap cpu;
    CdSnap cd;
    unsigned changed, first, last, cd_event, cd_command, cd_lba, cd_status;
    const FrameTracerRamMapping* ram;
};

struct RamTracker {
    FrameTracerRamMapping mapping;
    const u8* current;
    std::vector<u8> previous;
    bool is_wram;
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
                        event.kind == Event::RAM ? "ram" : "handoff";
    std::printf("EVENT {\"kind\":\"%s\",\"frame\":%u,", kind, event.frame);
    print_cpu(event.cpu);
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

}  // namespace

int main(int argc, char** argv) {
    if (argc != 7)
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
    const auto add_ram = [&rams](FrameTracerRamKind kind, unsigned size, unsigned card_start,
                                 const u8* current, bool is_wram) {
        if (!size)
            return true;  // The accessor exists, but this loaded configuration has no such RAM.
        FrameTracerRamMapping mapping = {};
        if (!FrameTracerMapRam(kind, size, card_start, &mapping))
            return false;
        RamTracker ram = {mapping, current, {}, is_wram};
        ram.previous.assign(current, current + mapping.size);
        rams.push_back(ram);
        return true;
    };
    if (!add_ram(FRAME_TRACER_WRAM, (unsigned)memory->GetWorkingRAMSize(), 0, memory->GetWorkingRAM(), true) ||
        !add_ram(FRAME_TRACER_CDROM_RAM, (unsigned)memory->GetCDROMRAMSize(), 0, memory->GetCDROMRAM(), false) ||
        !add_ram(FRAME_TRACER_CARD_RAM, (unsigned)memory->GetCardRAMSize(),
                 (unsigned)memory->GetCardRAMStart(), memory->GetCardRAM(), false))
        return 3;
    CpuSnap previous_cpu = cpu_snap(core, cpu, memory);
    CdSnap previous_cd = cd_snap(scsi, cdrom);
    std::printf("CAP {\"normal_frame_execution\":true,\"instruction_step\":false,\"guest_mutation\":false,"
                  "\"mmio_reads\":false,\"scsi_event_notification\":true,\"ram_observation\":true,\"ring_capacity\":%ld,\"frames\":%ld,"
                 "\"disc_given\":%s,\"run_start_frame\":%ld,\"run_hold_frames\":%ld,\"run_input_requested\":%s}\n",
                 capacity, frames, have_disc ? "true" : "false", run_start, run_hold, run_start ? "true" : "false");
    TraceLogger* trace = core.GetTraceLogger();
    if (!trace->SetCapacity(kRingSize)) return 3;
    trace->SetEnabledFlags(TRACE_FLAG_SCSI);
    trace->SetEventFilter(TRACE_SCSI, TRACE_SCSI_FILTER_COMMANDS | TRACE_SCSI_FILTER_PHASES | TRACE_SCSI_FILTER_RESPONSES);
    trace->Reset();
    u64 observed_scsi = 0;
    u64 dropped_scsi = 0;

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
        if (observed_scsi < first_event) {
            dropped_scsi += first_event - observed_scsi;
            observed_scsi = first_event;
        }
        for (; observed_scsi < total; ++observed_scsi) {
            const GG_Trace_Entry& entry = trace->GetEntry((unsigned)(observed_scsi - first_event));
            if (entry.type == TRACE_SCSI)
                ring.push({Event::CD, number, now_cpu, now_cd, 0, 0, 0, entry.scsi.event,
                           entry.scsi.command, entry.scsi.param, entry.scsi.status, nullptr});
        }
        unsigned wram_changed = 0;
        for (RamTracker& ram : rams) {
            const FrameTracerRamDelta delta = FrameTracerCompareRam(ram.previous.data(), ram.current, ram.mapping.size);
            if (ram.is_wram)
                wram_changed = delta.changed;
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
    std::printf("END {\"frames\":%ld,\"events\":%u,\"dropped\":%u,\"scsi_events_dropped\":%llu,\"overflow\":%s}", frames,
                ring.size(), ring.dropped(), (unsigned long long)dropped_scsi,
                (ring.dropped() || dropped_scsi) ? "true" : "false");
    std::printf("\n");
    return 0;
}
