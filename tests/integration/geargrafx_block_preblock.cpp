// Local-only whole-instruction preblock probe for TII/TDD/TIN/TAI/TIA block moves (Issue #742).
// Never distribute this GPL-linked executable or the linked core. Synthetic BIOS/CD only.
//
// Whole-instruction preblock (method D): the boundary decision refuses a block-transfer
// opcode BEFORE its first step, so no source byte is read and no destination byte is
// written. This is not the per-byte guard of geargrafx_block_midrefusal (method B):
// a per-byte refusal still runs the instruction, this probe never starts it.
//
// The probe single-steps the pinned Geargrafx instruction debugger one instruction at
// a time. BEFORE every step (including the entry instruction) it resolves the opcode at
// the current PC through the same MPR mapping the CPU fetches with, using the core's own
// side-effect-free peek (Memory::TryPeek, the same API the core disassembler uses).
// TryPeek fails (returns false, no device access) for the hardware page (bank $FF) and
// other unpeekable banks. A block-transfer opcode (TII/TDD/TIN/TAI/TIA) is refused
// before its first step, so no source byte is read and no destination byte is written.
// an unsafe fetch fails closed (no step is issued), and resumes re-enter through the same
// guarded execution entry (guarded_execute) as the initial run: a resume is refused iff that
// entry refuses to invoke the core, so execution count, PC, VDC state and RAM must all be
// unchanged by the three resume attempts (verified by the Python classifier). The monitor is given no PC hint: every boundary of
// the single-stepped run is checked - entry, JMP reach, MPR-remapped,
// hardware-page and IRQ-handler heads alike.
//
// The unsafe-Read fetch hazard is demonstrated by geargrafx_st_boundary (its *-unsafe-read
// mutation); the same side-effect-free TryPeek API is used here, and the hwpage family
// proves the fail-closed fetch for block guests.
//
// argv: bios cue slot budget allow_start allow_end mode neg_addr   (same shape as
// geargrafx_reference_jsr.cpp so geargrafx_reference_guard.run_mode() can drive it).
// Modes (base family, optionally followed by one mutation suffix):
//   tii-entry            TII is the entry instruction (0 executed before refusal)
//   tdd-unknown          JMP reaches the TDD at $3E10 (monitor has no PC hint)
//   tin-unknown-far      JMP reaches the TIN at $3E20 (a second unknown PC)
//   tia-wrap-forbidden   guest TAM sets MPR2=$FF; TIA dest alternates into the
//                        hardware page (forbidden destination, refused pre-step)
//   tai-mpr2-unknown     guest TAM sets MPR2=$F8; TAI fetched at $4010 through page 2
//   tii-hwpage-fetch-blocked  guest TAM sets MPR2=$FF; fetch at $4000 unsafe -> fail closed
//   block-irq-handler    IRQ2 accepted at an instruction boundary; handler-head
//                        TII refused (no transfer starts inside the handler)
//   pos-write            positive control: allowlisted RAM store must run unrefused
// Mutation suffixes: -unguarded (monitor off), -no-entry-check, -no-recheck, -wrong-opcode
//   (forbidden-opcode set omits TAI), -knownpc-only (regression: refuse only at two
//   hardcoded PCs), -resume-allowed (the refusal latch does not hold the stop).
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
const unsigned long kJumpTarget = 0x3E10;  // unknown-PC block location (JMP target)
const unsigned long kFarTarget = 0x3E20;   // second unknown-PC block location
const unsigned long kMpr2Target = 0x4010;  // page 2, guest-remapped to WRAM bank $F8
const unsigned long kHwPageTarget = 0x4000;        // page 2 remapped to the hardware page
const unsigned long kIrqHandler = 0x3F80;          // IRQ2 handler head in WRAM
const unsigned long kBlockSrc = 0x3E80;    // transfer source (page-1 WRAM)
const unsigned long kBlockDst = 0x3E90;    // transfer destination (page-1 WRAM)
const unsigned long kBlockLen = 4;         // transfer length (small; length 0 = 64K is
                                           // unmeasurable on-core and never executed here)
const unsigned kResumeAttempts = 3;
const unsigned long kKnownPc[2] = {0x3E00, 0x3E10};  // known-PC regression list

// HuC6280 block-move opcodes: TII=$73 TDD=$C3 TIN=$D3 TIA=$E3 TAI=$F3.
bool is_block_opcode(unsigned op)
{
    return op == 0x73 || op == 0xC3 || op == 0xD3 || op == 0xE3 || op == 0xF3;
}

const char* block_name(unsigned op)
{
    switch (op) {
    case 0x73: return "tii";
    case 0xC3: return "tdd";
    case 0xD3: return "tin";
    case 0xE3: return "tia";
    case 0xF3: return "tai";
    default: return "";
    }
}

bool ends_with(const std::string& value, const std::string& suffix)
{
    return value.size() >= suffix.size() &&
           value.compare(value.size() - suffix.size(), suffix.size(), suffix) == 0;
}

// One guest program plan. The monitor never sees this table: it only peeks PC opcodes.
struct CasePlan
{
    std::string family;     // entry / remote / wrap-forbidden / mpr2 /
                            // hwpage / irq-handler / pos
    int blk_kind;           // 0=none 1=TII 2=TDD 3=TIN 4=TAI 5=TIA
    unsigned long blk_pc;   // PC where the block op (or the unsafe fetch) is expected
    unsigned long executed; // instructions executed before the refusal
    bool preset_vdc;        // host-side VDC AR/SR sentinels (hwpage hazard visibility)
};

bool plan_for(const std::string& base, CasePlan& plan)
{
    if (base == "tii-entry") {
        plan = {"entry", 1, kGuestBase, 0, false};
    } else if (base == "tdd-unknown") {
        plan = {"remote", 2, kJumpTarget, 1, false};
    } else if (base == "tin-unknown-far") {
        plan = {"remote", 3, kFarTarget, 1, false};
    } else if (base == "tia-wrap-forbidden") {
        plan = {"wrap-forbidden", 5, kGuestBase + 4, 2, true};
    } else if (base == "tai-mpr2-unknown") {
        plan = {"mpr2", 4, kMpr2Target, 3, false};
    } else if (base == "tii-hwpage-fetch-blocked") {
        plan = {"hwpage", 1, kHwPageTarget, 3, true};
    } else if (base == "block-irq-handler") {
        plan = {"irq-handler", 1, kIrqHandler, 2, false};
    } else if (base == "pos-write") {
        plan = {"pos", 0, 0, 0, false};
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
    const char* suffix_names[] = {"-no-entry-check", "-knownpc-only", "-resume-allowed",
                                  "-wrong-opcode", "-no-recheck", "-unguarded"};
    bool unguarded = false, skip_entry_check = false, stale_after_first_boundary = false;
    bool wrong_opcode_set = false, knownpc_only = false, resume_allowed = false;
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
            break;
        }
    }

    CasePlan plan;
    if (!plan_for(base, plan))
        return 2;
    bool armed = !unguarded;
    // Block opcode per kind: 1=TII($73) 2=TDD($C3) 3=TIN($D3) 4=TAI($F3) 5=TIA($E3).
    unsigned blk_op = plan.blk_kind == 1 ? 0x73 : plan.blk_kind == 2 ? 0xC3 :
                      plan.blk_kind == 3 ? 0xD3 : plan.blk_kind == 4 ? 0xF3 :
                      plan.blk_kind == 5 ? 0xE3 : 0x00;

    std::ifstream file(argv[1], std::ios::binary);
    if (!file)
        return 3;
    std::vector<u8> bios((std::istreambuf_iterator<char>(file)), {});
    if (bios.size() < 256 * 1024)
        return 3;

    // Synthetic BIOS patches (the image stays fully synthetic: zeros plus these bytes).
    if (plan.family == "irq-handler") {
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
    debug.stop_on_irq = false;
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
    // Block instruction encoding (7 bytes): opcode, src_lo, src_hi, dst_lo, dst_hi, len_lo, len_hi.
    // All guests run in page-1 WRAM (MPR1=$F8): src CPU $3E80, dst CPU $3E90, length 4.
    // Length 0 (65536 bytes) is unmeasurable on-core and never executed here.
    auto block7 = [&](unsigned op, unsigned long src, unsigned long dst) {
        return std::vector<u8>{(u8)op, (u8)(src & 0xFF), (u8)(src >> 8),
                               (u8)(dst & 0xFF), (u8)(dst >> 8),
                               (u8)(kBlockLen & 0xFF), (u8)(kBlockLen >> 8)};
    };
    if (plan.family == "entry") {
        code = block7((unsigned)blk_op, kBlockSrc, kBlockDst);
        code.push_back(0x80);  // BRA * after the transfer: unguarded runs halt cleanly
        code.push_back(0xFE);
    } else if (plan.family == "remote") {
        unsigned long target = plan.blk_pc;
        code = {0x4C, (u8)(target & 0xFF), (u8)(target >> 8)};  // JMP target
        while (kGuestBase + code.size() < target)
            code.push_back(0xEA);  // NOP padding
        std::vector<u8> blk = block7((unsigned)blk_op, kBlockSrc, kBlockDst);
        code.insert(code.end(), blk.begin(), blk.end());
        code.push_back(0x80);  // BRA *
        code.push_back(0xFE);
    } else if (plan.family == "wrap-forbidden") {
        // The guest maps page 2 to the hardware page; the TIA destination alternates
        // $4080/$4081 (VDC). Armed refusal precedes the first step, so no VDC write lands.
        code = {0xA9, 0xFF, 0x53, 0x04};  // LDA #$FF; TAM #$04 (MPR2=$FF)
        std::vector<u8> blk = block7((unsigned)blk_op, kBlockSrc, 0x4080);
        code.insert(code.end(), blk.begin(), blk.end());
        code.push_back(0x80);  // BRA *
        code.push_back(0xFE);
    } else if (plan.family == "mpr2") {
        // The guest, not host setup, changes the MPR used to fetch the target.
        // $4010 is CPU page 2, and MPR2=$F8 maps it to WRAM[0x10].
        code = {0xA9, 0xF8, 0x53, 0x04, 0x4C, 0x10, 0x40};
        extra_code = block7((unsigned)blk_op, kBlockSrc, kBlockDst);
        extra_code.push_back(0x80);
        extra_code.push_back(0xFE);
    } else if (plan.family == "hwpage") {
        // The guest remaps page 2 to the hardware page; the monitor's fetch at $4000 must
        // fail closed before any device access.
        code = {0xA9, 0xFF, 0x53, 0x04, 0x4C, 0x00, 0x40};
    } else if (plan.family == "irq-handler") {
        code = {0x58, 0x80, 0xFE};  // CLI; BRA *  (IRQ2 is accepted while BRA executes)
        extra_code = block7((unsigned)blk_op, kBlockSrc, kBlockDst);  // handler: TII (refused)
        extra_code.push_back(0x80);
        extra_code.push_back(0xFE);  // BRA *
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
    } else if (plan.family == "irq-handler") {
        for (size_t i = 0; i < extra_code.size(); ++i)
            wram[0x1F80 + i] = extra_code[i];  // handler at $3F80
    }
    // Transfer fixture: source pattern at CPU $3E80, destination preset at CPU $3E90.
    // Armed refusal precedes the first step, so only unguarded runs move these bytes.
    // The fill is linear (non-repeating) across $3E40-$3E9F so decrementing (TDD) and
    // alternating (TAI/TIA) sources read distinct bytes too: a net diff then proves
    // the transfer. A repeating pattern would be invisible to a same-offset shift.
    if (plan.blk_kind != 0) {
        for (int i = 0; i < 0x60; ++i)
            wram[0x1E40 + i] = (u8)((0x20 + i) & 0xFF);
        for (int i = 0; i < 4; ++i)
            wram[0x1E90 + i] = 0xCC;
    }

    st->PC->SetValue((u16)kGuestBase);
    st->S->SetValue(0xFF);
    st->A->SetValue(0);
    st->X->SetValue(0);
    st->Y->SetValue(0);
    st->P->SetValue(plan.family == "irq-handler" ? 0x14 : 0x04);
    if (plan.family == "irq-handler")
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
    unsigned long blk_pc_expected = plan.blk_pc;

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
    bool refused = false, fetch_blocked = false;
    unsigned refused_op = 0;
    unsigned long refused_pc = 0;
    std::vector<unsigned> operand_bytes;  // 7 opcode+operand bytes peeked at refusal
    bool operand_complete = false;
    unsigned resume_attempts = 0, resume_refused = 0;
    unsigned long executed_at_refusal = 0, executed_after_resume = 0;
    bool halted = false;
    unsigned handler_steps = 0;
    unsigned fetch_page = 0, fetch_mpr = 0, fetch_opcode = 0;
    bool fetch_safe = false, fetch_failed = false, fetch_unsafe_bank = false;

    // Boundary decision, taken BEFORE every step (including the entry instruction).
    enum Decision { DECISION_ALLOW, DECISION_REFUSE_BLOCK, DECISION_REFUSE_FETCH };
    auto boundary_decision = [&](unsigned pc) -> Decision {
        unsigned bank = memory->GetMpr((u16)pc >> 13);
        fetch_page = (u16)pc >> 13;
        fetch_mpr = bank;
        fetch_unsafe_bank = (bank == 0xFF);
        fetch_failed = false;
        fetch_safe = false;
        unsigned op = 0;
        u8 peeked = 0;
        if (memory->TryPeek((u16)pc, &peeked)) {
            op = peeked;
            fetch_safe = true;
        } else {
            fetch_failed = true;  // hardware page or unpeekable bank: no device access
        }
        fetch_opcode = op;
        if (fetch_failed) {
            return DECISION_REFUSE_FETCH;
        }
        bool check = armed;
        if (skip_entry_check && pc == kGuestBase)
            check = false;
        if (stale_after_first_boundary && pc == kMpr2Target && memory->GetMpr(2) == 0xF8)
            check = false;  // MUTATION: omit only the re-check after the guest changes MPR2.
        if (wrong_opcode_set && op == 0xF3)
            check = false;  // MUTATION: forbidden-opcode set omits TAI.
        if (knownpc_only && pc != kKnownPc[0] && pc != kKnownPc[1])
            check = false;  // MUTATION: regression to two hardcoded known PCs only.
        if (check && is_block_opcode(op)) {
            refused = true;
            refused_op = op;
            refused_pc = pc;
            // Capture the 7 opcode+operand bytes as refusal evidence (side-effect-free).
            // A partial capture is missing evidence, never an implicit pass.
            operand_bytes.clear();
            operand_complete = true;
            for (int i = 0; i < 7; ++i) {
                u8 byte = 0;
                if (memory->TryPeek((u16)(pc + i), &byte)) {
                    operand_bytes.push_back(byte);
                } else {
                    operand_complete = false;
                    break;
                }
            }
            return DECISION_REFUSE_BLOCK;
        }
        return DECISION_ALLOW;
    };

    auto step_once = [&]() {
        unsigned pc_before = st->PC->GetValue();
        int samples = 0;
        core.RunToVBlank(frame.data(), audio.data(), &samples, &debug, false);
        ++executed;
        if (plan.family == "irq-handler" && pc_before >= kIrqHandler)
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
        if (decision == DECISION_REFUSE_BLOCK) {
            if (resume_allowed) {
                // MUTATION: the refusal latch does not hold the stop; the guest steps anyway
                // (the prohibited transfer executes) and the run continues to its natural halt.
                step_once();
                return 1;
            }
            return 0;
        }
        fetch_blocked = true;  // fail closed: no step is issued on an unsafe fetch
        return 0;
    };

    while (!halted && executed < budget) {
        if (guarded_execute() == 0)
            break;
    }

    std::vector<u8> wram_at_stop;
    if (refused || fetch_blocked) {
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
    bool detected = refused || fetch_blocked;
    // detected_pc is the PC at which the detection actually happened (measured, never the plan
    // expectation): block refusal -> the refused PC; fail-closed -> the stop PC at which
    // the guarded entry refused to step. expected_block_pc stays the independent expectation.
    unsigned detected_pc = 0;
    if (detected)
        detected_pc = refused ? (unsigned)refused_pc : st->PC->GetValue();

    const char* status = fetch_blocked ? "fetch_blocked" :
                         (halted ? "ok" : (refused ? "refused" : "budget_exhausted"));
    const char* reason = refused ? "block_opcode_at_instruction_boundary" :
                         (fetch_blocked ? "unsafe_opcode_fetch_fail_closed" :
                          (halted ? "" : "budget_exhausted"));

    unsigned stack_cpu[3] = {0x21FF, 0x21FE, 0x21FD};
    unsigned stack_frame[3] = {wram[0x1FF], wram[0x1FE], wram[0x1FD]};
    unsigned irr = *st->IRR, idr = *st->IDR;
    bool irq_accepted = plan.family == "irq-handler" && st->S->GetValue() == 0xFC;
    // Read the IRQ vector itself (side-effect-free TryPeek, ROM page) instead of inferring it
    // from SP. SP=$FC alone does not prove the vector resolution; the measured bytes do.
    unsigned observed_vector = 0;
    bool vector_read = false;
    if (plan.family == "irq-handler") {
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
    std::fprintf(stderr, "\"refused_name\":\"%s\",", block_name(refused_op));
    std::fprintf(stderr, "\"operand_bytes\":[");
    for (size_t i = 0; i < operand_bytes.size(); ++i)
        std::fprintf(stderr, "%s%u", i ? "," : "", operand_bytes[i]);
    std::fprintf(stderr, "],\"operand_complete\":%s,", bool_str(operand_complete));
    std::fprintf(stderr, "\"fetch_blocked\":%s,", bool_str(fetch_blocked));
    std::fprintf(stderr, "\"expected_block_pc\":%lu,", blk_pc_expected);
    std::fprintf(stderr, "\"resume_attempts\":%u,\"resume_refused\":%u,", resume_attempts, resume_refused);
    std::fprintf(stderr, "\"executed_at_refusal\":%lu,\"executed_after_resume\":%lu,",
                 executed_at_refusal, executed_after_resume);
    std::fprintf(stderr, "\"stop_pc\":%u,\"instructions_executed\":%lu,\"budget\":%lu,",
                 st->PC->GetValue(), executed, budget);
    std::fprintf(stderr, "\"block_mode\":\"%s\",", block_name(blk_op));
    std::fprintf(stderr, "\"fetch_page\":%u,\"fetch_mpr\":%u,\"fetch_opcode\":%u,",
                 fetch_page, fetch_mpr, fetch_opcode);
    std::fprintf(stderr, "\"fetch_safe\":%s,\"fetch_failed\":%s,\"fetch_via\":\"peek\",\"fetch_unsafe_bank\":%s,",
                 bool_str(fetch_safe), bool_str(fetch_failed), bool_str(fetch_unsafe_bank));
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
                 bool_str(false), handler_steps, bool_str(irq_accepted));
    std::fprintf(stderr, "\"observed_vector\":%u,\"vector_read\":%s,\"sp_before\":%u,\"sp_after\":%u,",
                 observed_vector, bool_str(vector_read), cpu_before.sp, cpu_after.sp);
    std::fprintf(stderr, "\"stack_cpu\":[%u,%u,%u],\"stack_frame_layout\":[%u,%u,%u],",
                 stack_cpu[0], stack_cpu[1], stack_cpu[2], stack_frame[0], stack_frame[1], stack_frame[2]);
    std::fprintf(stderr, "\"irr\":%u,\"idr\":%u,", irr, idr);
    std::fprintf(stderr, "\"ram_unchanged_during_resume\":%s,",
                 bool_str(ram_unchanged_during_resume));
    std::fprintf(stderr, "\"detected\":%s,\"detected_pc\":%u", bool_str(detected), detected_pc);
    std::fprintf(stderr, "}");
    std::fflush(stderr);
    return 0;
}
