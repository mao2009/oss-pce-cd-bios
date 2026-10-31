// Bounded, observation-only Geargrafx frame tracer.
//
// This local-only probe links the pinned GPL Geargrafx objects through the existing
// test build recipe.  It deliberately uses normal RunToVBlank execution rather than
// the instruction debugger.  It does not call Memory::Read, write guest memory,
// alter CPU registers/MPRs, inject interrupts, or perform MMIO reads.  Working RAM,
// CPU state, MPRs, and CD/SCSI state are sampled through state pointers at frame
// boundaries only.
//
// argv: rom cue|- frames event_capacity
// Output: CAP, EVENT (bounded ring, oldest entries overwritten), END.
#include "geargrafx_core.h"
#include "huc6280.h"
#include "memory.h"
#include "media.h"
#include "cdrom.h"
#include "scsi_controller.h"
#include "trace_logger.h"
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
constexpr unsigned kWorkRamBytes = 0x2000;

struct CpuSnap {
    unsigned master_clock, pc, pc_page, pc_bank, physical_pc, pc_is_wram, a, x, y, p, sp;
    unsigned mpr[8];
};

struct CdSnap {
    int phase;
    unsigned signals, load_sector, load_count, irq_active, irq_enabled;
};

struct Event {
    enum Kind { SAMPLE, CD, WRAM, HANDOFF } kind;
    unsigned frame;
    CpuSnap cpu;
    CdSnap cd;
    unsigned changed, first, last, cd_event, cd_command, cd_lba, cd_status;
};

class Ring {
public:
    explicit Ring(unsigned capacity) : capacity_(capacity), begin_(0), size_(0), dropped_(0) {}

    void push(const Event& event) {
        if (size_ < capacity_) {
            events_[(begin_ + size_) % kRingSize] = event;
            ++size_;
            return;
        }
        events_[begin_] = event;
        begin_ = (begin_ + 1) % kRingSize;
        ++dropped_;
    }

    unsigned size() const { return size_; }
    unsigned dropped() const { return dropped_; }
    const Event& at(unsigned index) const { return events_[(begin_ + index) % capacity_]; }

private:
    std::array<Event, kRingSize> events_{};
    unsigned capacity_, begin_, size_, dropped_;
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
                       event.kind == Event::WRAM ? "wram" : "handoff";
    std::printf("EVENT {\"kind\":\"%s\",\"frame\":%u,", kind, event.frame);
    print_cpu(event.cpu);
    if (event.kind == Event::CD) {
        // SCSI event metadata only; command payload bytes are never serialized.
        std::printf(",\"cd_event\":%u,\"command\":%u,\"lba\":%u,\"status\":%u,",
                    event.cd_event, event.cd_command, event.cd_lba, event.cd_status);
        print_cd(event.cd);
    } else if (event.kind == Event::WRAM) {
        // Addresses and counts only: never serialize guest bytes.
        std::printf(",\"changed\":%u,\"first_wram\":%u,\"last_wram\":%u", event.changed,
                    event.first, event.last);
    }
    std::printf("}\n");
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 5)
        return 2;
    char* end = nullptr;
    long frames = std::strtol(argv[3], &end, 10);
    if (!end || *end || frames < 1 || frames > kMaxFrames)
        return 2;
    end = nullptr;
    long capacity = std::strtol(argv[4], &end, 10);
    if (!end || *end || capacity < 1 || capacity > kRingSize)
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
    Ring ring((unsigned)capacity);
    std::array<u8, kWorkRamBytes> previous_wram{};
    std::memcpy(previous_wram.data(), memory->GetWorkingRAM(), previous_wram.size());
    CpuSnap previous_cpu = cpu_snap(core, cpu, memory);
    CdSnap previous_cd = cd_snap(scsi, cdrom);
    std::printf("CAP {\"normal_frame_execution\":true,\"instruction_step\":false,\"guest_mutation\":false,"
                 "\"mmio_reads\":false,\"scsi_event_notification\":true,\"ring_capacity\":%ld,\"frames\":%ld,\"disc_given\":%s}\n",
                 capacity, frames, have_disc ? "true" : "false");
    TraceLogger* trace = core.GetTraceLogger();
    if (!trace->SetCapacity(kRingSize)) return 3;
    trace->SetEnabledFlags(TRACE_FLAG_SCSI);
    trace->SetEventFilter(TRACE_SCSI, TRACE_SCSI_FILTER_COMMANDS | TRACE_SCSI_FILTER_PHASES | TRACE_SCSI_FILTER_RESPONSES);
    trace->Reset();
    u64 observed_scsi = 0;
    u64 dropped_scsi = 0;

    for (unsigned number = 1; number <= (unsigned)frames; ++number) {
        int samples = 0;
        core.RunToVBlank(frame.data(), audio.data(), &samples, &normal, false);
        CpuSnap now_cpu = cpu_snap(core, cpu, memory);
        CdSnap now_cd = cd_snap(scsi, cdrom);
        Event sample = {Event::SAMPLE, number, now_cpu, now_cd, 0, 0, 0, 0, 0, 0, 0};
        ring.push(sample);
        if (!same_cd(previous_cd, now_cd))
            ring.push({Event::CD, number, now_cpu, now_cd, 0, 0, 0, 255, 0, 0, 0});
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
                           entry.scsi.command, entry.scsi.param, entry.scsi.status});
        }
        unsigned changed = 0, first = kWorkRamBytes, last = 0;
        const u8* wram = memory->GetWorkingRAM();
        for (unsigned offset = 0; offset != kWorkRamBytes; ++offset) {
            if (previous_wram[offset] != wram[offset]) {
                ++changed;
                if (first == kWorkRamBytes) first = offset;
                last = offset;
            }
        }
        if (changed)
            ring.push({Event::WRAM, number, now_cpu, now_cd, changed, first, last, 0, 0, 0, 0});
        // This is deliberately only a candidate: a mapped WRAM PC plus a recent
        // WRAM update, never a claim of a BIOS-to-game control transfer.
        if (now_cpu.pc_is_wram && changed &&
            (!previous_cpu.pc_is_wram || previous_cpu.physical_pc != now_cpu.physical_pc))
            ring.push({Event::HANDOFF, number, now_cpu, now_cd, 0, 0, 0, 0, 0, 0, 0});
        std::memcpy(previous_wram.data(), wram, previous_wram.size());
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
