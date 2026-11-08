// Local-only instruction-boundary monitor probe for ST0/ST1/ST2 (Issue #365, v2 arbitrary-PC scope).
// Never distribute this GPL-linked executable or the linked core. Synthetic BIOS/CD only.
//
// The probe single-steps the pinned Geargrafx instruction debugger one instruction at
// a time. BEFORE every step (including the entry instruction) it resolves the opcode at
// the current PC through the same MPR mapping the CPU fetches with, using the core's own
// side-effect-free peek (Memory::TryPeek, the same API the core disassembler uses).
// TryPeek fails (returns false, no device access) for the hardware page (bank $FF) and
// other unpeekable banks. ST0 ($03) / ST1 ($13) / ST2 ($23) are refused before the step,
// an unsafe fetch fails closed (no step is issued), and resumes re-enter through the same
// guarded execution entry (guarded_execute) as the initial run: a resume is refused iff that
// entry refuses to invoke the core, so execution count, PC, VDC state and RAM must all be
// unchanged by the three resume attempts (verified by the Python classifier). The monitor is given no PC hint: every boundary of
// the single-stepped run is checked - entry, branch, JSR/RTS return, MPR-remapped,
// page/bank-boundary, ROM-bank, unused-bank and IRQ-handler heads alike.
//
// The *-unsafe-read mutation fetches with Memory::Read instead: at a hardware-page PC that
// read itself lands a VDC side effect (status register clear) BEFORE any refusal decision.
// It exists to prove the fetch hazard is real, so the fail-closed peek design is load-bearing.
//
// argv: bios cue slot budget allow_start allow_end mode neg_addr   (same shape as
// geargrafx_reference_jsr.cpp so geargrafx_reference_guard.run_mode() can drive it).
// Modes (base family, optionally followed by one mutation suffix):
//   stN-entry            ST is the entry instruction (0 executed before refusal)
//   stN-unknown          JMP reaches the ST at $3E10 (monitor has no PC hint)
//   st1-branch-unknown   taken BNE reaches the ST at $3E10
//   st0-jsr-rts          JSR subroutine returns; ST0 waits at $3E03 right after RTS
//   st1-page-boundary    ST1 opcode at $1FFF (operand would cross into page 1 / WRAM)
//   st0-rom-page0        ST0 opcode at $0000 (ROM bank 0 through MPR0=$00)
//   st1-unknown-far      JMP reaches the ST at $3E20 (a second unknown PC)
//   st2-mpr2-unknown     guest TAM sets MPR2=$F8; ST2 fetched at $4010 through page 2
//   st0-hwpage-fetch-blocked  guest TAM sets MPR2=$FF; fetch at $4000 unsafe -> fail closed
//   st0-bbr3-hwpage-taken     BBR3 ($3F, branch if bit clear) tests the WRAM zp byte at
//                             CPU $2010 with bit3 clear; the taken branch lands at $4002
//                             (hardware page) and the fetch fails closed
//   st0-bbr3-hwpage-skip      same guest with the bit set; BBR3 falls through to ST0
//   st1-unused-bank      guest TAM sets MPR3=$88; ST1 fetched from unused bank $88
//   irq2-handler-st0-survey   IRQ2 accepted at an instruction boundary; handler-head ST0 refused
//   irq2-core-stop-survey     same, plus the core debugger stop_on_irq flag (core-level IRQ stop)
//   pos-write            positive control: allowlisted RAM store must run unrefused
// Mutation suffixes: -unguarded (monitor off), -no-entry-check, -no-recheck, -wrong-opcode,
//   -knownpc-only (regression: refuse only at two hardcoded PCs), -resume-allowed (the
//   refusal latch does not hold the stop), -unsafe-read (Read-based fetch hazard demo),
//   -peek-fail-step (peek failure fails open), -no-hazard-latch (unsafe reads repeat).
#include "geargrafx_core.h"
#include "huc6270.h"
#include "huc6280.h"
#include "memory.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

namespace {

const unsigned long kGuestBase = 0x3E00;   // page 1 -> WRAM, guest entry
const unsigned long kStubIndex = 0x1E00;   // WRAM index of the guest program
const unsigned long kJumpTarget = 0x3E10;  // unknown-PC ST location (JMP/BNE target)
const unsigned long kFarTarget = 0x3E20;   // second unknown-PC ST location
const unsigned long kMpr2Target = 0x4010;  // page 2, guest-remapped to WRAM bank $F8
const unsigned long kPageBoundaryTarget = 0x1FFF;  // last byte of ROM page 0
const unsigned long kRomPage0Target = 0x0000;      // first byte of ROM bank 0
const unsigned long kHwPageTarget = 0x4000;        // page 2 remapped to the hardware page
const unsigned long kUnusedBankTarget = 0x6010;    // page 3 remapped to unused bank $88
const unsigned long kIrqHandler = 0x3F80;          // IRQ2 handler head in WRAM
const unsigned long kBbr3Pc = 0x3F80;              // BBR3 instruction address (page-1 WRAM)
const unsigned long kBbr3HwTarget = 0x4002;        // taken BBR3 lands in the hardware page
const unsigned long kBbr3SkipSt = 0x3F83;          // not-taken BBR3 falls through to ST0
const unsigned kBbr3ZpAddr = 0x10;                 // zp byte tested by BBR3 (host-WRAM bit)
const unsigned kResumeAttempts = 3;
const unsigned long kKnownPc[2] = {0x3E00, 0x3E10};  // known-PC regression list

bool is_st_opcode(unsigned op)
{
    return op == 0x03 || op == 0x13 || op == 0x23;
}

bool ends_with(const std::string& value, const std::string& suffix)
{
    return value.size() >= suffix.size() &&
           value.compare(value.size() - suffix.size(), suffix.size(), suffix) == 0;
}

// One guest program plan. The monitor never sees this table: it only peeks PC opcodes.
struct CasePlan
{
    std::string family;     // entry / remote / branch / jsr-rts / page-boundary / rom-page0 /
                            // mpr2 / hwpage / unused-bank / irq-survey / pos
    int st_kind;            // 0=none 1=st0 2=st1 3=st2
    unsigned long st_pc;    // PC where the ST (or the unsafe fetch) is expected
    unsigned long executed; // instructions executed before the refusal
    bool preset_vdc;        // host-side VDC AR/SR sentinels (hwpage hazard visibility)
    bool stop_on_irq;       // core debugger stop_on_irq flag (core-stop survey)
};

bool plan_for(const std::string& base, CasePlan& plan)
{
    if (base == "st0-entry" || base == "st1-entry" || base == "st2-entry") {
        plan = {"entry", base[2] == '0' ? 1 : (base[2] == '1' ? 2 : 3), kGuestBase, 0, false, false};
    } else if (base == "st1-unknown" || base == "st2-unknown") {
        plan = {"remote", base[2] == '1' ? 2 : 3, kJumpTarget, 1, false, false};
    } else if (base == "st1-branch-unknown") {
        plan = {"branch", 2, kJumpTarget, 2, false, false};
    } else if (base == "st0-jsr-rts") {
        plan = {"jsr-rts", 1, 0x3E03, 3, false, false};
    } else if (base == "st1-page-boundary") {
        plan = {"page-boundary", 2, kPageBoundaryTarget, 1, false, false};
    } else if (base == "st0-rom-page0") {
        plan = {"rom-page0", 1, kRomPage0Target, 1, false, false};
    } else if (base == "st1-unknown-far") {
        plan = {"remote", 2, kFarTarget, 1, false, false};
    } else if (base == "st2-mpr2-unknown") {
        plan = {"mpr2", 3, kMpr2Target, 3, false, false};
    } else if (base == "st0-hwpage-fetch-blocked") {
        plan = {"hwpage", 1, kHwPageTarget, 3, true, false};
    } else if (base == "st0-bbr3-hwpage-taken") {
        plan = {"bbr3-taken", 1, kBbr3HwTarget, 4, true, false};
    } else if (base == "st0-bbr3-hwpage-skip") {
        // No VDC sentinel here: the fall-through path never fetches the hardware page, and the
        // landed check expects AR 0 -> 0x07 from a clean preset.
        plan = {"bbr3-skip", 1, kBbr3SkipSt, 4, false, false};
    } else if (base == "st1-unused-bank") {
        plan = {"unused-bank", 2, kUnusedBankTarget, 3, false, false};
    } else if (base == "irq2-handler-st0-survey" || base == "irq2-core-stop-survey") {
        plan = {"irq-survey", 1, kIrqHandler, 2, false, base == "irq2-core-stop-survey"};
    } else if (base == "pos-write") {
        plan = {"pos", 0, 0, 0, false, false};
    } else {
        return false;
    }
    return true;
}

const char* bool_str(bool value)
{
    return value ? "true" : "false";
}

}  // namespace

int main(int argc, char** argv)
{
    if (argc != 9)
        return 2;
    char* end = nullptr;
    unsigned long slot = std::strtoul(argv[3], &end, 0);
    if (!end || *end || slot != 0x1E)
        return 2;
    unsigned long budget = std::strtoul(argv[4], &end, 10);
    if (!end || *end || budget < 4 || budget > 1000000UL)
        return 2;
    unsigned long allow_start = std::strtoul(argv[5], &end, 0);
    if (!end || *end)
        return 2;
    unsigned long allow_end = std::strtoul(argv[6], &end, 0);
    if (!end || *end)
        return 2;
    // Slot-1e allowlist is fixed: page 1 only (zero page and stack live there on this core).
    if (allow_start != 0x2000UL || allow_end != 0x4000UL)
        return 9;
    std::string mode = argv[7];
    unsigned long neg_addr = std::strtoul(argv[8], &end, 0);
    if (!end || *end || neg_addr > 65535UL)
        return 2;

    // Split one mutation suffix off the mode; the remainder is the base family.
    const char* suffix_names[] = {"-no-entry-check", "-knownpc-only", "-resume-allowed", "-unsafe-read",
                                  "-peek-fail-step", "-wrong-opcode", "-no-recheck", "-unguarded",
                                  "-no-hazard-latch"};
    bool unguarded = false, skip_entry_check = false, stale_after_first_boundary = false;
    bool wrong_opcode_set = false, knownpc_only = false, resume_allowed = false, unsafe_read = false;
    bool peek_fail_step = false, no_hazard_latch = false;
    std::string base = mode;
    for (const char* suffix : suffix_names) {
        if (ends_with(mode, suffix)) {
            base = mode.substr(0, mode.size() - std::strlen(suffix));
            if (std::string(suffix) == "-unguarded") unguarded = true;
            else if (std::string(suffix) == "-no-entry-check") skip_entry_check = true;
            else if (std::string(suffix) == "-no-recheck") stale_after_first_boundary = true;
            else if (std::string(suffix) == "-wrong-opcode") wrong_opcode_set = true;
            else if (std::string(suffix) == "-knownpc-only") knownpc_only = true;
            else if (std::string(suffix) == "-resume-allowed") resume_allowed = true;
            else if (std::string(suffix) == "-unsafe-read") unsafe_read = true;
            else if (std::string(suffix) == "-peek-fail-step") peek_fail_step = true;
            else if (std::string(suffix) == "-no-hazard-latch") {
                // The latch only exists on the unsafe-read hazard path; removing it implies
                // the Read-based fetch itself (otherwise there is nothing to latch).
                no_hazard_latch = true;
                unsafe_read = true;
            }
            break;
        }
    }

    CasePlan plan;
    if (!plan_for(base, plan))
        return 2;
    bool armed = !unguarded;
    int st_kind = plan.st_kind;
    unsigned st_op = st_kind == 1 ? 0x03 : st_kind == 2 ? 0x13 : st_kind == 3 ? 0x23 : 0x00;
    unsigned st_val = st_kind == 1 ? 0x07 : st_kind == 2 ? 0x5A : st_kind == 3 ? 0xA5 : 0x00;

    std::ifstream file(argv[1], std::ios::binary);
    if (!file)
        return 3;
    std::vector<u8> bios((std::istreambuf_iterator<char>(file)), {});
    if (bios.size() < 256 * 1024)
        return 3;

    // Synthetic BIOS patches (the image stays fully synthetic: zeros plus these bytes).
    if (plan.family == "bbr3-taken" || plan.family == "bbr3-skip") {
        // BBR3 (opcode $3F) branches when the bit of its zp operand is RESET. On the PCE
        // configuration the CPU zero page starts at $2000 (WRAM page 1), so the zp byte $10
        // is WRAM offset 0x10 (CPU $2010). The host sets that byte: 0x00 (bit clear -> branch
        // taken into the hardware page) vs 0x08 (bit set -> fall through to ST0). The guest
        // itself performs no MMIO access; the hardware-page fetch after the branch is what
        // the fail-closed monitor must block, exactly as for the fixed-JMP entry.
    } else if (plan.family == "page-boundary") {
        bios[kPageBoundaryTarget] = (u8)st_op;  // ST opcode at CPU $1FFF (ROM bank 0)
        // The operand byte would live at $2000 (WRAM); it is never fetched: the refusal
        // precedes the step, which is exactly the page-boundary property under test.
    } else if (plan.family == "rom-page0") {
        bios[kRomPage0Target] = (u8)st_op;
        bios[kRomPage0Target + 1] = (u8)st_val;
        bios[kRomPage0Target + 2] = 0x80;  // BRA * after the ST operand: unguarded runs halt cleanly
        bios[kRomPage0Target + 3] = 0xFE;
    } else if (plan.family == "irq-survey") {
        // IRQ2 vector $FFF6 -> handler $3F80 (WRAM through MPR1=$F8). A distinct target makes
        // the observed handler PC evidence of the vector actually taken, not a host label.
        bios[0xFFF6 - 0xE000] = (u8)(kIrqHandler & 0xFF);
        bios[0xFFF6 - 0xE000 + 1] = (u8)(kIrqHandler >> 8);
    }

    GeargrafxCore core;
    core.Init(nullptr);
    if (!core.LoadBiosFromBuffer(bios.data(), bios.size(), true))
        return 4;
    if (!core.LoadMedia(argv[2]))
        return 5;
    if (!core.GetMedia()->IsCDROMHardwareEnabled())
        return 6;

    HuC6280* cpu = core.GetHuC6280();
    HuC6280::HuC6280_State* st = cpu->GetState();
    Memory* memory = core.GetMemory();

    GeargrafxCore::GG_Debug_Run debug = {};
    debug.step_debugger = true;
    debug.stop_on_breakpoint = true;
    debug.stop_on_irq = plan.stop_on_irq;
    debug.stop_on_brk = false;
    debug.stop_on_run_to_breakpoint = false;
    std::vector<u8> frame(1024 * 512 * 4, 0);
    std::vector<s16> audio(65536, 0);

    // Deterministic synthetic environment (same mapping as geargrafx_reference_jsr.cpp slot 1E).
    memory->SetMpr(1, 0xF8);  // page 1 -> work RAM
    memory->SetMpr(7, 0x00);
    for (int i = 2; i <= 6; ++i)
        memory->SetMpr(i, 0x00);
    memory->SetMpr(0, 0x00);  // page 0 -> ROM, never the hardware page

    std::vector<u8> code, extra_code;
    if (plan.family == "entry") {
        code = {(u8)st_op, (u8)st_val, 0x80, 0xFE};  // STx #$val at entry, BRA *
    } else if (plan.family == "remote" || plan.family == "branch") {
        unsigned long target = plan.family == "branch" ? kJumpTarget : plan.st_pc;
        if (plan.family == "branch")
            code = {0xA9, 0x01, 0xD0, 0x0C};  // LDA #$01; BNE $3E10 (Z clear -> taken)
        else
            code = {0x4C, (u8)(target & 0xFF), (u8)(target >> 8)};  // JMP target
        while (kGuestBase + code.size() < target)
            code.push_back(0xEA);  // NOP padding
        code.push_back((u8)st_op);
        code.push_back((u8)st_val);
        code.push_back(0x80);  // BRA *
        code.push_back(0xFE);
    } else if (plan.family == "jsr-rts") {
        // JSR $3E10 at $3E00; subroutine (LDA #$00; RTS) at $3E10; ST0 #$07 at $3E03 right
        // after the return; BRA * at $3E05.
        code = {0x20, 0x10, 0x3E, 0x03, 0x07, 0x80, 0xFE, 0xEA};
        extra_code = {0xA9, 0x00, 0x60};
    } else if (plan.family == "page-boundary" || plan.family == "rom-page0") {
        unsigned long target = plan.st_pc;
        code = {0x4C, (u8)(target & 0xFF), (u8)(target >> 8)};
    } else if (plan.family == "mpr2") {
        // The guest, not host setup, changes the MPR used to fetch the target.
        // $4010 is CPU page 2, and MPR2=$F8 maps it to WRAM[0x10].
        code = {0xA9, 0xF8, 0x53, 0x04, 0x4C, 0x10, 0x40};
        extra_code = {(u8)st_op, (u8)st_val, 0x80, 0xFE};
    } else if (plan.family == "hwpage") {
        // The guest remaps page 2 to the hardware page; the monitor's fetch at $4000 must
        // fail closed before any device access.
        code = {0xA9, 0xFF, 0x53, 0x04, 0x4C, 0x00, 0x40};
    } else if (plan.family == "bbr3-taken" || plan.family == "bbr3-skip") {
        // LDA #$FF; TAM #$04 (MPR2=$FF); JMP $3F80; NOP pad. BBR3 $10,+$7F sits at $3F80
        // (opcode $3F; branches when the zp bit is clear): taken it lands at $3F83+$7F =
        // $4002 (the hardware page); not taken it falls through to ST0 #$07 at $3F83. Same
        // guest bytes for both conditions; only the host-patched zp bit decides the dynamic
        // branch outcome, so this is a genuine data-dependent branch.
        code = {0xA9, 0xFF, 0x53, 0x04, 0x4C, (u8)(kBbr3Pc & 0xFF), (u8)(kBbr3Pc >> 8)};
        while (kGuestBase + code.size() < kBbr3Pc)
            code.push_back(0xEA);  // NOP padding
        extra_code = {0x3F, (u8)kBbr3ZpAddr, 0x7F, 0x03, 0x07, 0x80, 0xFE};
    } else if (plan.family == "unused-bank") {
        // The guest remaps page 3 to unused bank $88; the ST sits in that bank's dummy page.
        code = {0xA9, 0x88, 0x53, 0x08, 0x4C, 0x10, 0x60};
    } else if (plan.family == "irq-survey") {
        code = {0x58, 0x80, 0xFE};  // CLI; BRA *  (IRQ2 is accepted while BRA executes)
        extra_code = {0x03, 0x07, 0x80, 0xFE};  // handler: ST0 #$07 (refused), BRA *
    } else {
        code = {0xA9, 0x5A, 0x8D, (u8)(neg_addr & 0xFF), (u8)(neg_addr >> 8), 0x80, 0xFE};
    }
    if (kStubIndex + code.size() > 0x2000)
        return 2;
    u8* wram = memory->GetWorkingRAM();
    for (size_t i = 0; i < code.size(); ++i)
        wram[kStubIndex + i] = code[i];
    if (plan.family == "mpr2") {
        for (size_t i = 0; i < extra_code.size(); ++i)
            wram[0x10 + i] = extra_code[i];
    } else if (plan.family == "bbr3-taken" || plan.family == "bbr3-skip") {
        for (size_t i = 0; i < extra_code.size(); ++i)
            wram[0x1F80 + i] = extra_code[i];  // BBR3 program at $3F80
        wram[0x10] = (u8)(plan.family == "bbr3-taken" ? 0x00 : 0x08);  // CPU $2010: BBR3 zp bit
    } else if (plan.family == "jsr-rts") {
        for (size_t i = 0; i < extra_code.size(); ++i)
            wram[kStubIndex + 0x10 + i] = extra_code[i];  // subroutine at $3E10
    } else if (plan.family == "irq-survey") {
        for (size_t i = 0; i < extra_code.size(); ++i)
            wram[0x1F80 + i] = extra_code[i];  // handler at $3F80
    } else if (plan.family == "unused-bank") {
        // Host-side fixture setup: the unused bank's dummy page carries the ST program.
        u8** memory_map = memory->GetMemoryMap();
        if (memory_map[0x88] == nullptr)
            return 2;
        memory_map[0x88][0x10] = (u8)st_op;
        memory_map[0x88][0x11] = (u8)st_val;
        memory_map[0x88][0x12] = 0x80;
        memory_map[0x88][0x13] = 0xFE;
    } else if (plan.family == "page-boundary") {
        // Cross-page operand and halt loop in WRAM page 1 (CPU $2000-$2002): the armed
        // refusal precedes the step, so only an unguarded mutation ever fetches them.
        wram[0x0000] = (u8)st_val;
        wram[0x0001] = 0x80;
        wram[0x0002] = 0xFE;
    }

    st->PC->SetValue((u16)kGuestBase);
    st->S->SetValue(0xFF);
    st->A->SetValue(0);
    st->X->SetValue(0);
    st->Y->SetValue(0);
    st->P->SetValue(plan.family == "irq-survey" ? 0x14 : 0x04);
    if (plan.family == "irq-survey")
        cpu->AssertIRQ2(true);  // host-side request; acceptance is observed, never host-forced

    std::vector<u8> mpr_before(8), mpr_after(8);
    for (int i = 0; i < 8; ++i)
        mpr_before[i] = memory->GetMpr(i);

    HuC6270::HuC6270_State* vs = core.GetHuC6270_1()->GetState();
    if (plan.preset_vdc) {
        // Known nonzero sentinels make a fetch-landed device side effect visible.
        *vs->AR = 5;
        *vs->SR = 0x3F;
    }
    unsigned vdc_ar_before = (u8)*vs->AR;
    unsigned vdc_r_before = vs->R[0];
    unsigned vdc_sr_before = (u8)*vs->SR;
    unsigned vdc_rb_before = (u16)*vs->READ_BUFFER;
    unsigned guard_before = memory->Read((u16)neg_addr);
    unsigned long st_pc_expected = plan.st_pc;

    // CPU state snapshots. WRAM is captured after fixture setup so guest code bytes never
    // appear as run differences; the diff is the run's own RAM footprint.
    struct CpuSnap { unsigned a, x, y, p, sp, pc; };
    auto snapshot = [&]() {
        CpuSnap s = {st->A->GetValue(), st->X->GetValue(), st->Y->GetValue(),
                     st->P->GetValue(), st->S->GetValue(), st->PC->GetValue()};
        return s;
    };
    CpuSnap cpu_before = snapshot();
    std::vector<u8> wram_before(wram, wram + memory->GetWorkingRAMSize());

    unsigned long executed = 0;
    bool refused = false, fetch_blocked = false, hazard_landed = false, fetch_open_stepped = false;
    unsigned refused_op = 0;
    unsigned long refused_pc = 0;
    unsigned resume_attempts = 0, resume_refused = 0;
    unsigned long executed_at_refusal = 0, executed_after_resume = 0;
    bool halted = false;
    bool core_irq_stop = false;
    unsigned handler_steps = 0;
    unsigned mmio_read_attempts = 0;  // unsafe Read-based fetch decisions (hazard evidence)
    unsigned fetch_page = 0, fetch_mpr = 0, fetch_opcode = 0;
    bool fetch_safe = false, fetch_failed = false, fetch_via_read = false, fetch_unsafe_bank = false;

    // Boundary decision, taken BEFORE every step (including the entry instruction).
    enum Decision { DECISION_ALLOW, DECISION_REFUSE_ST, DECISION_REFUSE_FETCH, DECISION_HAZARD,
                    DECISION_FETCH_OPEN_STEP };
    auto boundary_decision = [&](unsigned pc) -> Decision {
        // Hazard latch: once an unsafe Read-based fetch landed its side effect, the stop is
        // latched. Resume attempts then fail here without touching the device (no further
        // Memory::Read(PC)); without the latch every resume would re-attempt the read.
        if (hazard_landed && !no_hazard_latch)
            return DECISION_HAZARD;
        unsigned bank = memory->GetMpr((u16)pc >> 13);
        fetch_page = (u16)pc >> 13;
        fetch_mpr = bank;
        fetch_unsafe_bank = (bank == 0xFF);
        fetch_via_read = false;
        fetch_failed = false;
        fetch_safe = false;
        unsigned op = 0;
        if (unsafe_read) {
            // MUTATION: fetch with Memory::Read. At a hardware-page PC this read itself
            // lands a device side effect (VDC status clear) before any refusal decision.
            // Only reads issued on the unsafe bank count as hazard evidence.
            fetch_via_read = true;
            if (bank == 0xFF)
                ++mmio_read_attempts;
            op = memory->Read((u16)pc);
        } else {
            u8 peeked = 0;
            if (memory->TryPeek((u16)pc, &peeked)) {
                op = peeked;
                fetch_safe = true;
            } else {
                fetch_failed = true;  // hardware page or unpeekable bank: no device access
            }
        }
        fetch_opcode = op;
        if (fetch_failed) {
            if (peek_fail_step)
                return DECISION_FETCH_OPEN_STEP;  // MUTATION: fail open, the step is issued anyway
            return DECISION_REFUSE_FETCH;
        }
        if (unsafe_read && fetch_unsafe_bank)
            return DECISION_HAZARD;  // the unsafe read already landed its side effect
        bool check = armed;
        if (skip_entry_check && pc == kGuestBase)
            check = false;
        if (stale_after_first_boundary && pc == kMpr2Target && memory->GetMpr(2) == 0xF8)
            check = false;  // MUTATION: omit only the re-check after the guest changes MPR2.
        if (wrong_opcode_set && op == 0x23)
            check = false;  // MUTATION: forbidden-opcode set omits ST2.
        if (knownpc_only && pc != kKnownPc[0] && pc != kKnownPc[1])
            check = false;  // MUTATION: regression to two hardcoded known PCs only.
        if (check && is_st_opcode(op)) {
            refused = true;
            refused_op = op;
            refused_pc = pc;
            return DECISION_REFUSE_ST;
        }
        return DECISION_ALLOW;
    };

    auto step_once = [&]() {
        unsigned pc_before = st->PC->GetValue();
        int samples = 0;
        bool core_stop = core.RunToVBlank(frame.data(), audio.data(), &samples, &debug, false);
        ++executed;
        if (core_stop)
            core_irq_stop = true;  // core debugger flagged the stop (IRQ entry with stop_on_irq)
        if (plan.family == "irq-survey" && pc_before >= kIrqHandler)
            ++handler_steps;
        if (st->PC->GetValue() == pc_before)
            halted = true;  // BRA * self-loop
    };

    // The single guarded execution entry. Every core step of the run (initial and resume
    // alike) goes through here, and step_once() is only reachable from here: a resume attempt
    // is refused if and only if this entry refuses to call the core. Re-calling the decision
    // lambda alone would not prove that the runtime entry itself refused the restart.
    auto guarded_execute = [&]() -> int {
        Decision decision = boundary_decision(st->PC->GetValue());
        if (decision == DECISION_ALLOW) {
            step_once();
            return 1;
        }
        if (decision == DECISION_REFUSE_ST) {
            if (resume_allowed) {
                // MUTATION: the refusal latch does not hold the stop; the guest steps anyway
                // (the prohibited ST executes) and the run continues to its natural halt.
                step_once();
                return 1;
            }
            return 0;
        }
        if (decision == DECISION_REFUSE_FETCH) {
            fetch_blocked = true;  // fail closed: no step is issued on an unsafe fetch
            return 0;
        }
        if (decision == DECISION_FETCH_OPEN_STEP) {
            // MUTATION: the peek failure did not stop the run. The CPU's own fetch
            // (Memory::Read) at the hardware-page PC lands the device side effect before
            // the opcode executes. Bounded to this single violation step, then the run
            // stops: the gate removal is already committed and further garbage execution
            // from MMIO space adds no evidence.
            step_once();
            fetch_open_stepped = true;
            return 0;
        }
        hazard_landed = true;  // MUTATION: the Read-based fetch landed its side effect
        return 0;
    };

    while (!halted && executed < budget) {
        if (guarded_execute() == 0)
            break;
    }

    std::vector<u8> wram_at_stop;
    if (refused || fetch_blocked || hazard_landed) {
        executed_at_refusal = executed;
        wram_at_stop.assign(wram, wram + memory->GetWorkingRAMSize());
        // Three resume attempts through the same guarded entry the run used. A step here
        // would increment the instruction counter or change PC/VDC/RAM; the Python
        // classifier verifies all of those invariants on the emitted row.
        for (unsigned i = 0; i < kResumeAttempts; ++i) {
            ++resume_attempts;
            if (guarded_execute() == 0)
                ++resume_refused;
        }
        executed_after_resume = executed;
    }
    bool ram_unchanged_during_resume = true;
    if (!wram_at_stop.empty()) {
        for (unsigned long offset = 0; offset < wram_at_stop.size(); ++offset) {
            if (wram[offset] != wram_at_stop[offset]) {
                ram_unchanged_during_resume = false;
                break;
            }
        }
    }

    for (int i = 0; i < 8; ++i)
        mpr_after[i] = memory->GetMpr(i);
    unsigned vdc_ar_after = (u8)*vs->AR;
    unsigned vdc_r_after = vs->R[0];
    unsigned vdc_sr_after = (u8)*vs->SR;
    unsigned vdc_rb_after = (u16)*vs->READ_BUFFER;
    unsigned guard_after = memory->Read((u16)neg_addr);
    CpuSnap cpu_after = snapshot();

    // Net WRAM difference of the run (final state vs. the post-fixture snapshot; CPU addresses
    // through the page-1 WRAM mapping; page 2 can alias the same bytes when the guest remaps
    // MPR2, so the diff is physical-WRAM based). This is a NET diff, not a write-event log:
    // a byte that was written and restored to its original value (or written with its existing
    // value) leaves no entry, and the entry list is truncated at 256 elements.
    std::vector<unsigned long> ram_diff_addresses;
    std::vector<unsigned> ram_diff_before, ram_diff_after;
    // Exact: truncated only when a 257th differing byte exists; exactly 255/256 is complete.
    bool ram_diff_truncated = false;
    for (unsigned long offset = 0; offset < wram_before.size(); ++offset) {
        if (wram[offset] != wram_before[offset]) {
            if (ram_diff_addresses.size() < 256) {
                ram_diff_addresses.push_back(0x2000 + offset);
                ram_diff_before.push_back(wram_before[offset]);
                ram_diff_after.push_back(wram[offset]);
            } else {
                ram_diff_truncated = true;
                break;
            }
        }
    }
    // During IRQ acceptance only the three architectural stack pushes may touch WRAM.
    bool only_stack_net_diffs = true;
    for (unsigned long cpu_addr : ram_diff_addresses) {
        if (cpu_addr != 0x21FF && cpu_addr != 0x21FE && cpu_addr != 0x21FD)
            only_stack_net_diffs = false;
    }
    bool mpr_changed = mpr_before != mpr_after;
    bool detected = refused || fetch_blocked || hazard_landed;
    // detected_pc is the PC at which the detection actually happened (measured, never the plan
    // expectation): ST refusal -> the refused PC; fail-closed/hazard -> the stop PC at which
    // the guarded entry refused to step. expected_st_pc stays the independent expectation.
    unsigned detected_pc = 0;
    if (detected)
        detected_pc = refused ? (unsigned)refused_pc : st->PC->GetValue();

    const char* status = fetch_blocked ? "fetch_blocked" :
                         (fetch_open_stepped ? "fetch_open_stepped" :
                          (halted ? "ok" : (refused || hazard_landed ? "refused" : "budget_exhausted")));
    const char* reason = refused ? "st_opcode_at_instruction_boundary" :
                         (fetch_blocked ? "unsafe_opcode_fetch_fail_closed" :
                          (fetch_open_stepped ? "peek_failure_stepped_anyway" :
                           (hazard_landed ? "unsafe_fetch_side_effect_landed" :
                            (halted ? "" : "budget_exhausted"))));

    unsigned stack_cpu[3] = {0x21FF, 0x21FE, 0x21FD};
    unsigned stack_frame[3] = {wram[0x1FF], wram[0x1FE], wram[0x1FD]};
    unsigned irr = *st->IRR, idr = *st->IDR;
    bool irq_accepted = plan.family == "irq-survey" && st->S->GetValue() == 0xFC;
    // Read the IRQ vector itself (side-effect-free TryPeek, ROM page) instead of inferring it
    // from SP. SP=$FC alone does not prove the vector resolution; the measured bytes do.
    unsigned observed_vector = 0;
    bool vector_read = false;
    if (plan.family == "irq-survey") {
        u8 v_lo = 0, v_hi = 0;
        if (memory->TryPeek((u16)0xFFF6, &v_lo) && memory->TryPeek((u16)0xFFF7, &v_hi)) {
            observed_vector = (unsigned)v_lo | ((unsigned)v_hi << 8);
            vector_read = true;
        }
    }

    std::fprintf(stderr, "{");
    std::fprintf(stderr, "\"mode\":\"%s\",\"base\":\"%s\",\"status\":\"%s\",\"reason\":\"%s\",",
                 mode.c_str(), base.c_str(), status, reason);
    std::fprintf(stderr, "\"family\":\"%s\",\"armed\":%s,\"boundary_check\":%s,",
                 plan.family.c_str(), bool_str(armed), bool_str(armed));
    std::fprintf(stderr, "\"refused\":%s,\"refused_opcode\":%u,\"refused_pc\":%lu,",
                 bool_str(refused), refused_op, refused_pc);
    std::fprintf(stderr, "\"fetch_blocked\":%s,\"hazard_landed\":%s,\"fetch_open_stepped\":%s,",
                 bool_str(fetch_blocked), bool_str(hazard_landed), bool_str(fetch_open_stepped));
    std::fprintf(stderr, "\"expected_st_pc\":%lu,", st_pc_expected);
    std::fprintf(stderr, "\"resume_attempts\":%u,\"resume_refused\":%u,", resume_attempts, resume_refused);
    std::fprintf(stderr, "\"executed_at_refusal\":%lu,\"executed_after_resume\":%lu,",
                 executed_at_refusal, executed_after_resume);
    std::fprintf(stderr, "\"stop_pc\":%u,\"instructions_executed\":%lu,\"budget\":%lu,",
                 st->PC->GetValue(), executed, budget);
    std::fprintf(stderr, "\"st_mode\":\"%s\",", st_kind == 1 ? "st0" : st_kind == 2 ? "st1" :
                 st_kind == 3 ? "st2" : "");
    std::fprintf(stderr, "\"fetch_page\":%u,\"fetch_mpr\":%u,\"fetch_opcode\":%u,",
                 fetch_page, fetch_mpr, fetch_opcode);
    std::fprintf(stderr, "\"fetch_safe\":%s,\"fetch_failed\":%s,\"fetch_via\":\"%s\",\"fetch_unsafe_bank\":%s,",
                 bool_str(fetch_safe), bool_str(fetch_failed),
                 fetch_via_read ? "read" : "peek", bool_str(fetch_unsafe_bank));
    std::fprintf(stderr, "\"before\":{\"a\":%u,\"x\":%u,\"y\":%u,\"p\":%u,\"sp\":%u,\"pc\":%u,"
                 "\"mpr\":[%u,%u,%u,%u,%u,%u,%u,%u]},",
                 cpu_before.a, cpu_before.x, cpu_before.y, cpu_before.p, cpu_before.sp, cpu_before.pc,
                 mpr_before[0], mpr_before[1], mpr_before[2], mpr_before[3],
                 mpr_before[4], mpr_before[5], mpr_before[6], mpr_before[7]);
    std::fprintf(stderr, "\"after\":{\"a\":%u,\"x\":%u,\"y\":%u,\"p\":%u,\"sp\":%u,\"pc\":%u,"
                 "\"mpr\":[%u,%u,%u,%u,%u,%u,%u,%u]},",
                 cpu_after.a, cpu_after.x, cpu_after.y, cpu_after.p, cpu_after.sp, cpu_after.pc,
                 mpr_after[0], mpr_after[1], mpr_after[2], mpr_after[3],
                 mpr_after[4], mpr_after[5], mpr_after[6], mpr_after[7]);
    std::fprintf(stderr, "\"vdc_ar_before\":%u,\"vdc_ar_after\":%u,\"vdc_r_before\":%u,\"vdc_r_after\":%u,"
                 "\"vdc_sr_before\":%u,\"vdc_sr_after\":%u,"
                 "\"vdc_read_buffer_before\":%u,\"vdc_read_buffer_after\":%u,",
                 vdc_ar_before, vdc_ar_after, vdc_r_before, vdc_r_after, vdc_sr_before, vdc_sr_after,
                 vdc_rb_before, vdc_rb_after);
    std::fprintf(stderr, "\"ram_diff\":[");
    for (size_t i = 0; i < ram_diff_addresses.size(); ++i)
        std::fprintf(stderr, "%s{\"address\":%lu,\"before\":%u,\"after\":%u}",
                     i ? "," : "", ram_diff_addresses[i], ram_diff_before[i], ram_diff_after[i]);
    std::fprintf(stderr, "],\"ram_diff_count\":%zu,\"ram_diff_truncated\":%s,"
                 "\"only_stack_net_diffs\":%s,",
                 ram_diff_addresses.size(), bool_str(ram_diff_truncated),
                 bool_str(only_stack_net_diffs));
    std::fprintf(stderr, "\"returned\":false,\"return_pc\":null,\"mpr_changed\":%s,", bool_str(mpr_changed));
    std::fprintf(stderr, "\"guard_target\":%lu,\"guard_before\":%u,\"guard_after\":%u,",
                 neg_addr, guard_before, guard_after);
    std::fprintf(stderr, "\"core_irq_stop\":%s,\"handler_steps\":%u,\"irq_accepted\":%s,",
                 bool_str(core_irq_stop), handler_steps, bool_str(irq_accepted));
    std::fprintf(stderr, "\"observed_vector\":%u,\"vector_read\":%s,\"sp_before\":%u,\"sp_after\":%u,",
                 observed_vector, bool_str(vector_read), cpu_before.sp, cpu_after.sp);
    std::fprintf(stderr, "\"stack_cpu\":[%u,%u,%u],\"stack_frame_layout\":[%u,%u,%u],",
                 stack_cpu[0], stack_cpu[1], stack_cpu[2], stack_frame[0], stack_frame[1], stack_frame[2]);
    std::fprintf(stderr, "\"irr\":%u,\"idr\":%u,", irr, idr);
    std::fprintf(stderr, "\"ram_unchanged_during_resume\":%s,\"mmio_read_attempts\":%u,",
                 bool_str(ram_unchanged_during_resume), mmio_read_attempts);
    std::fprintf(stderr, "\"detected\":%s,\"detected_pc\":%u", bool_str(detected), detected_pc);
    std::fprintf(stderr, "}");
    std::fflush(stderr);
    return 0;
}
