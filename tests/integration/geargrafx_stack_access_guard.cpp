// Stack access pre-guard probe (Issue #499, method C of #473; parents #459 / #367 / #173).
// Local-only: links the GPL-3.0 pinned Geargrafx core (b49ae82a012eade566d72e9d61c9d95a5caa4da3)
// rebuilt with the VERIFICATION-ONLY patch tests/integration/patches/geargrafx-b49ae82-stack-access-guard.patch.
// Never distribute this executable or the linked core. Synthetic BIOS/CD only; no reference BIOS is read.
//
// The patch makes StackPush8/16 and StackPop8/16 ask gg_verify_stack_guard BEFORE any stack memory access
// and before S changes, and observes every CPU access of the hardware page ($FF bank) through
// gg_verify_io_observer. The refusal policy below (allow only MPR1 = $F8, refuse everything else, refuse
// everything after the first refusal) is a TEST policy defined here, not a production gate.
//
// argv: bios cue mode frames
// mode = <route>-<variant>
//   route:   jsr pha php bsr rts rti irq tstart tend brk pla plp phx phy plx ply irqtam phasame pharestore
//            (#647) irqtam2 irqtamt irqtam1i irqtam2i irqtamti tamcode tmapos tmaneg tam<mask><A> (hex)
//            (#648) x<op><kind>, op = tii tin tdd tia tai, kind = d (destination page = variant bank),
//            s (source page), m (destination page set mid-transfer), m1 (MPR1 set mid-transfer),
//            q / q2 / qt (IRQ1 / IRQ2 / TIQ pending during the transfer), c (range crosses into the page
//            under test: refused), w (range ends before it: allowed), e (destination page set before the
//            TransferEnd step); xself (TII over its own opcode), xlen0 (length 0 = 65536).
//            For x routes MPR1 is always $F8: the variant bank goes to the page under test, not to MPR1.
//            (#653) ni_<base>_<src>_<k>[_i]: an IRQ newly asserted at instruction k. base = tam (TAM #$02;
//            armed: A = $FF), sta (STA $4000 on the hardware page: a mid-instruction clock point),
//            tii tin tdd tia tai (WRAM -> WRAM, 4 bytes), tiiv tinv tddv tiav taiv (to the VDC data port:
//            clock points inside the byte steps). src = t (timer armed at the start of k to expire at the
//            first hardware clock after it), q1 / q2 (IRQ1 / IRQ2 set at the start of k), q1m / q2m (set at
//            the first mid-instruction clock point of k, if k has one). _i = start with I = 1.
//   variant: allowed     MPR1 = $F8 (WRAM): the guard is consulted and allows (positive)
//            armed       MPR1 = $FF (I/O): the guard refuses before the access (negative)
//            unguarded   MPR1 = $FF, null guard: the accesses land on the I/O page (control)
//            mut-late16 | mut-popinc | mut-nolatch | mut-ignore | mut-nostop
//                        armed with a patch mutant switched on (the classifier must reject the run)
//   irqtam starts with MPR1 = $F8 and the guest's TAM sets MPR1 = A ($F8 allowed, $FF otherwise) with
//   IRQ1 pending and I = 0, so the IRQ entry pushes happen inside the TAM's RunInstruction.
//   tend starts with MPR1 = $F8; for every variant except allowed the harness sets MPR1 = $FF at the
//   start of the RunInstruction whose transfer state is 1 (the TransferEnd step).
// Exit 0 with one JSON observation on stderr; 2 = usage; 3..6 = load failures.
#include "geargrafx_core.h"
#include "huc6270.h"
#include "huc6280.h"
#include "memory.h"
#include "memory_inline.h"   // Memory::TryPeek (no bus side effects)
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

namespace {

const unsigned kGuest = 0x1E00;      // page 0 (MPR0 = $F8): WRAM index $1E00
const unsigned kHandler = 0x1E40;    // IRQ1 / BRK handler: BRA *
const unsigned kSrc = 0x1E80;        // block transfer source byte
const unsigned kDst = 0x1E90;        // block transfer destination byte
const unsigned kMaxEvents = 64;

struct Event {
    unsigned seq;
    bool stack;        // true = stack guard call, false = I/O page access
    unsigned op;       // stack: 0 push8 1 push16 2 pop8 3 pop16; io: 0 read 1 write
    unsigned address;  // stack: S at the call; io: CPU address
    unsigned value;    // stack: transfer state; io: written value (0 for reads)
    int decision;      // stack: 1 allowed, 0 refused; io: -1
    bool latched;
    unsigned mpr1;
    unsigned instruction;
};

struct Snapshot {
    unsigned pc, a, x, y, p, s, transfer_state, cycles;
    int irq_pending;
    unsigned mpr[8];
};

HuC6280* g_cpu = nullptr;
Memory* g_memory = nullptr;
HuC6280::HuC6280_State* g_st = nullptr;
std::vector<Event> g_events;
unsigned g_seq = 0, g_io_reads = 0, g_io_writes = 0;
unsigned g_guard_calls = 0, g_guard_refusals = 0, g_guard_latched_calls = 0;
unsigned g_instructions = 0, g_instructions_after_refusal = 0, g_io_after_refusal = 0;
bool g_refused_seen = false;
bool g_flip_tend = false, g_flipped = false;
unsigned g_flip_instruction = 0, g_flip_at = 0, g_stack_bank = 0xFF;
bool g_method_a = false, g_instruction_refused = false;
unsigned g_refusal_kind = 0;        // 0 none, 1 stack access (method C), 2 instruction (method A)
unsigned g_instruction_refusals = 0;
// Issue #647 (S5): D = decode-time refusal of a TAM that would map MPR1 to a forbidden bank; E = defer the
// IRQ that would be taken inside a TAM instruction. Both are TEST policies defined here.
bool g_method_d = false, g_method_e = false, g_budget_stop = false;
unsigned g_decode_refusals = 0, g_irq_deferrals = 0, g_handler_entry = 0, g_budget = 0;
int g_d_mut = 0, g_e_mut = 0;     // probe-side mutants: D off (1) / D checks bit 0 (2); E off (1)
const char* g_refusal_reason = "none";
// Issue #648 (S6): T = block transfer guard. Before TransferStart: every source and destination byte of
// the transfer (operands peeked without a bus read) must lie in a page mapped to WRAM $F8; during the
// transfer: no MPR may differ from its value at TransferStart. TEST policy defined here.
bool g_method_t = false, g_t_active = false;
int g_t_mut = 0;                  // 1 resume after a T refusal, 2 no mid-transfer check, 3 first byte only,
                                  // 4 every opcode modelled as TII (wrong address sequence), 5 no self check
unsigned g_t_op = 0, g_x_forbid = 0;   // opcode at TransferStart; x route: page under test
unsigned g_t_mpr[8] = {}, g_t_refusals = 0, g_flip_mpr = 1;
unsigned g_t_at_refusal[4] = {};  // transfer source, destination, remaining length, step count
bool g_t_resumed = false;
// Issue #653: an IRQ newly asserted at instruction g_ni_k (see the header). Observation only.
bool g_ni = false, g_ni_done = false, g_ni_mid_armed = false, g_ni_injected = false, g_ni_noinject = false;
unsigned g_ni_k = 0, g_ni_bits = 0, g_ni_mode = 0;   // mode 0 timer, 1 boundary IRR, 2 mid-instruction IRR
struct NiTime { unsigned i, pc, irr, pend, ts, a, x, y, p, s, src, dst, len; unsigned mpr[8]; };
struct NiClock { unsigned i, cycles, irr; bool injected; };
std::vector<NiTime> g_ni_timeline;
std::vector<NiClock> g_ni_clocks;
unsigned g_ni_clock_calls = 0;
Snapshot g_instr_start = {};        // snapshot at the start of the instruction that is running
Snapshot g_refusal_instr = {};      // snapshot at the start of the instruction of the first refusal
Snapshot g_at_refusal = {};         // live CPU state inside the first refused guard call
unsigned g_refusal_op = 0, g_refusal_s = 0, g_refusal_ts = 0, g_refusal_mpr1 = 0, g_refusal_cycles = 0;
unsigned g_vdc_ar_at_refusal = 0, g_io_before_refusal = 0;
HuC6270::HuC6270_State* g_vdc = nullptr;

Snapshot snap()
{
    Snapshot s = {};
    s.pc = g_st->PC->GetValue();
    s.a = g_st->A->GetValue();
    s.x = g_st->X->GetValue();
    s.y = g_st->Y->GetValue();
    s.p = g_st->P->GetValue();
    s.s = g_st->S->GetValue();
    s.transfer_state = g_cpu->GG_VerifyTransferState();
    s.cycles = g_cpu->GG_VerifyCycles();
    s.irq_pending = g_cpu->GG_VerifyIrqPending();
    for (int i = 0; i < 8; ++i)
        s.mpr[i] = g_memory->GetMpr(i);
    return s;
}

void record(const Event& e)
{
    if (g_events.size() < kMaxEvents)
        g_events.push_back(e);
}

bool stack_guard(void*, unsigned op, unsigned s, unsigned transfer_state, bool latched)
{
    ++g_guard_calls;
    unsigned mpr1 = g_memory->GetMpr(1);
    bool allow = !latched && mpr1 == 0xF8;   // test policy: WRAM stack page only (S1 #497 contract)
    if (latched)
        ++g_guard_latched_calls;
    if (!allow) {
        ++g_guard_refusals;
        if (!g_refused_seen) {
            g_refused_seen = true;
            g_refusal_kind = 1;
            g_refusal_instr = g_instr_start;
            g_at_refusal = snap();
            g_refusal_op = op;
            g_refusal_s = s;
            g_refusal_ts = transfer_state;
            g_refusal_mpr1 = mpr1;
            g_refusal_cycles = g_cpu->GG_VerifyCycles();
            g_vdc_ar_at_refusal = (u8)*g_vdc->AR;
            g_io_before_refusal = g_io_reads + g_io_writes;
        }
    }
    Event e = {g_seq++, true, op, s, transfer_state, allow ? 1 : 0, latched, mpr1, g_instructions};
    record(e);
    return allow;
}

void io_observer(void*, unsigned is_write, unsigned address, unsigned value)
{
    if (is_write) ++g_io_writes; else ++g_io_reads;
    if (g_refused_seen)
        ++g_io_after_refusal;
    Event e = {g_seq++, false, is_write, address, value, -1, false, g_memory->GetMpr(1), g_instructions};
    record(e);
}

// Issue #653: hardware clock point inside an instruction (called after the clock, before CheckIRQs).
void clock_observer(void*, unsigned master_cycles)
{
    ++g_ni_clock_calls;
    bool inject = g_ni_mid_armed && !g_ni_injected && !g_ni_noinject && g_instructions == g_ni_k;
    if (inject) {
        *g_st->IRR = (u8)(*g_st->IRR | g_ni_bits);
        g_ni_injected = true;
    }
    if (g_ni_clocks.size() < 32) {
        NiClock e = {g_instructions, master_cycles, (unsigned)*g_st->IRR, inject};
        g_ni_clocks.push_back(e);
    }
}

// Method A (Issue #498, #473): refuse an instruction BEFORE it executes when the stack page (MPR1) is not
// the allowed WRAM bank at its start. Test policy, defined here (S1 #497 contract: $F8 only).
bool pre_instruction(void*, unsigned, unsigned transfer_state)
{
    if (g_refused_seen)
        ++g_instructions_after_refusal;
    ++g_instructions;
    // Harness-side direct MPR1 setting in the middle of the run (S1 #497 DIRECT_SETMPR path; the contract
    // makes any mid-run direct setting a FAIL, so these are negative cases).
    bool flip_now = (g_flip_tend && transfer_state == 1) || (g_flip_at && g_instructions == g_flip_at);
    if (flip_now && !g_flipped) {
        g_memory->SetMpr(g_flip_mpr, (u8)g_stack_bank);
        g_flipped = true;
        g_flip_instruction = g_instructions;
    }
    if (g_ni && !g_ni_done && g_instructions == g_ni_k) {
        g_ni_done = true;
        if (g_ni_mode == 0) {           // timer: counter 0, one master cycle to the tick -> expires at the
            *g_st->TIMER = true;        // first hardware clock after this point
            *g_st->TIMER_COUNTER = 0;
            *g_st->TIMER_RELOAD = 0x7F;
            g_cpu->GG_VerifySetTimerCycles(1);
        } else if (g_ni_mode == 1) {
            if (!g_ni_noinject) { *g_st->IRR = (u8)(*g_st->IRR | g_ni_bits); g_ni_injected = true; }
        } else {
            g_ni_mid_armed = true;
        }
    }
    if (g_ni && g_ni_timeline.size() < 16) {
        NiTime e = {g_instructions, (unsigned)g_st->PC->GetValue(), (unsigned)*g_st->IRR,
                    (unsigned)g_cpu->GG_VerifyIrqPending(), transfer_state, (unsigned)g_st->A->GetValue(),
                    (unsigned)g_st->X->GetValue(), (unsigned)g_st->Y->GetValue(), (unsigned)g_st->P->GetValue(),
                    (unsigned)g_st->S->GetValue(), g_cpu->GG_VerifyTransferSource(), g_cpu->GG_VerifyTransferDest(),
                    g_cpu->GG_VerifyTransferLength(), {}};
        for (int m = 0; m < 8; ++m)
            e.mpr[m] = g_memory->GetMpr(m);
        g_ni_timeline.push_back(e);
    }
    g_instr_start = snap();
    if (!g_handler_entry && g_instr_start.pc == kHandler)
        g_handler_entry = g_instructions;        // first instruction of the IRQ/BRK handler
    // Peek the opcode (and a TAM operand) without a bus read; not peekable = fail closed.
    u8 op = 0, bits = 0;
    u16 pc = (u16)g_instr_start.pc;
    bool peek_ok = g_memory->TryPeek(pc, &op) && (op != 0x53 || g_memory->TryPeek((u16)(pc + 1), &bits));
    gg_verify_defer_irq = g_method_e && g_e_mut == 0 && (!peek_ok || op == 0x53);
    if (gg_verify_defer_irq)
        ++g_irq_deferrals;
    unsigned checked_bit = g_d_mut == 2 ? 0x01 : 0x02;   // MPR1 is bit 1 of the TAM operand
    bool decode_refuse = g_method_d && g_d_mut != 1 &&
                         (!peek_ok || (op == 0x53 && (bits & checked_bit) && g_instr_start.a != 0xF8));
    bool a_refuse = g_method_a && (g_instruction_refused || g_memory->GetMpr(1) != 0xF8);
    const char* t_reason = nullptr;
    if (g_method_t && !peek_ok) {
        t_reason = "transfer_unpeekable";           // fail closed: the opcode is not known
    } else if (g_method_t && transfer_state == 0) {
        g_t_active = false;
        bool block = peek_ok && (op == 0x73 || op == 0xC3 || op == 0xD3 || op == 0xE3 || op == 0xF3);
        if (block) {
            u8 b[6] = {};
            bool ok = true;
            for (int i = 0; i < 6; ++i)
                ok = ok && g_memory->TryPeek((u16)(pc + 1 + i), &b[i]);
            if (!ok)
                t_reason = "transfer_unpeekable";
            unsigned src = b[0] | (b[1] << 8), dst = b[2] | (b[3] << 8), len = b[4] | (b[5] << 8);
            if (!len)
                len = 0x10000;                        // length 0 = 65536 bytes (pinned source)
            unsigned limit = g_t_mut == 3 ? 1 : len;
            // The instruction's own seven bytes, as physical (bank, offset): a destination byte there would
            // rewrite the opcode that every byte step re-runs.
            unsigned self[7];
            for (unsigned k = 0; k < 7; ++k) {
                unsigned a = (pc + k) & 0xFFFF;
                self[k] = (g_memory->GetMpr(a >> 13) << 13) | (a & 0x1FFF);
            }
            for (unsigned i = 0; ok && !t_reason && i < limit; ++i) {
                // Address sequences of the pinned source: TDD decrements both, TIN keeps the
                // destination, TIA alternates the destination, TAI alternates the source.
                bool tii = g_t_mut == 4;
                unsigned s = tii ? src + i : op == 0xC3 ? src - i : op == 0xF3 ? src + (i & 1) : src + i;
                unsigned d = tii ? dst + i : op == 0xC3 ? dst - i : op == 0xD3 ? dst : op == 0xE3 ? dst + (i & 1)
                                                                                            : dst + i;
                s &= 0xFFFF;
                d &= 0xFFFF;
                if (g_memory->GetMpr(s >> 13) != 0xF8 || g_memory->GetMpr(d >> 13) != 0xF8)
                    t_reason = "transfer_range";
                unsigned dp = (g_memory->GetMpr(d >> 13) << 13) | (d & 0x1FFF);
                for (unsigned k = 0; g_t_mut != 5 && !t_reason && k < 7; ++k)
                    if (dp == self[k])
                        t_reason = "transfer_self";
            }
            if (!t_reason) {
                for (int i = 0; i < 8; ++i)
                    g_t_mpr[i] = g_memory->GetMpr(i);
                g_t_op = op;
                g_t_active = true;
            }
        }
    } else if (g_method_t && g_t_active && g_t_mut != 2) {
        if (op != g_t_op)
            t_reason = "transfer_opcode_changed";     // the transfer rewrote its own opcode
        for (int i = 0; i < 8 && !t_reason; ++i)
            if (g_memory->GetMpr(i) != g_t_mpr[i])
                t_reason = "transfer_mpr_changed";
    }
    bool t_refuse = t_reason != nullptr;
    if (t_refuse && !a_refuse)
        ++g_t_refusals;
    // Harness budget (TAM bitmask sweep): stop before the next instruction unless method A refuses it. It
    // takes precedence over D there, because the sweep judges only the TAM (instruction 1); D's not-peekable
    // branch is measured by its own route (tamcode).
    if (!a_refuse && g_budget && g_instructions > g_budget) {
        g_budget_stop = true;
        return false;
    }
    if (!a_refuse && !decode_refuse && !t_refuse)
        return true;
    if (decode_refuse && !a_refuse)
        ++g_decode_refusals;
    if (!g_refused_seen) {
        g_refused_seen = true;
        g_refusal_kind = 2;
        g_refusal_reason = a_refuse ? "mpr1" : decode_refuse ? (peek_ok ? "decode_tam" : "decode_unpeekable")
                                                           : t_reason;
        g_t_at_refusal[0] = g_cpu->GG_VerifyTransferSource();
        g_t_at_refusal[1] = g_cpu->GG_VerifyTransferDest();
        g_t_at_refusal[2] = g_cpu->GG_VerifyTransferLength();
        g_t_at_refusal[3] = g_cpu->GG_VerifyTransferCount();
        g_refusal_instr = g_instr_start;
        g_at_refusal = g_instr_start;
        g_refusal_op = 255;
        g_refusal_s = g_instr_start.s;
        g_refusal_ts = transfer_state;
        g_refusal_mpr1 = g_memory->GetMpr(1);
        g_refusal_cycles = 0;
        g_vdc_ar_at_refusal = (u8)*g_vdc->AR;
        g_io_before_refusal = g_io_reads + g_io_writes;
    }
    if (g_t_mut == 1 && t_refuse && !a_refuse && !decode_refuse) {
        g_t_resumed = true;      // mutant: the refusal is recorded but the guest is resumed
        return true;
    }
    g_instruction_refused = true;
    ++g_instruction_refusals;
    return false;
}

void print_snapshot(const char* name, const Snapshot& s)
{
    std::fprintf(stderr, "\"%s\":{\"pc\":%u,\"a\":%u,\"x\":%u,\"y\":%u,\"p\":%u,\"s\":%u,\"transfer_state\":%u,"
                 "\"cycles\":%u,\"irq_pending\":%d,\"mpr\":[%u,%u,%u,%u,%u,%u,%u,%u]},",
                 name, s.pc, s.a, s.x, s.y, s.p, s.s, s.transfer_state, s.cycles, s.irq_pending,
                 s.mpr[0], s.mpr[1], s.mpr[2], s.mpr[3], s.mpr[4], s.mpr[5], s.mpr[6], s.mpr[7]);
}

struct Route {
    const char* name;
    std::vector<u8> code;          // at $1E00
    unsigned s, a, x, y, p;
    std::vector<unsigned> prefill; // pairs: WRAM index, value
    unsigned irr;                  // IRR bits to inject
    unsigned vector_lo;            // bios offset of the vector to point at kHandler (0 = none)
    unsigned read_buffer, status;  // VDC presets (the I/O page pops read them)
    unsigned ar;                   // VDC AR preset
    std::vector<unsigned> loops;   // extra BRA * landing loops (absolute addresses in page 0)
};

bool route_for(const std::string& name, Route* r)
{
    // Default: S = $04, so pushes reach $2104 (VDC AR), $2103 (data MSB), $2102 (data LSB) on an I/O stack page.
    Route d = {"", {}, 0x04, 0x5A, 0x34, 0x07, 0x04, {}, 0, 0, 0x1E1F, 0x1E, 0x00, {}};
    *r = d;
    r->name = "";
    const u8 bra = 0x80, self = 0xFE;
    if (name == "jsr") { r->code = {0x20, 0x10, 0x1E}; r->loops = {0x1E10}; }
    else if (name == "pha") { r->code = {0x48, bra, self}; }
    else if (name == "php") { r->code = {0x08, bra, self}; }
    else if (name == "phx") { r->code = {0xDA, bra, self}; }
    else if (name == "phy") { r->code = {0x5A, bra, self}; }
    else if (name == "bsr") { r->code = {0x44, 0x0E}; r->loops = {0x1E10}; }
    else if (name == "brk") { r->code = {0x00, 0x00}; r->vector_lo = 0x1FF6; r->loops = {kHandler}; }
    else if (name == "rts") {
        r->code = {0x60}; r->s = 0x01; r->prefill = {0x0102, 0x0F, 0x0103, 0x1E};
        r->loops = {0x1E10, 0x1E20};   // $1E10: WRAM return; $1E20: return read from the VDC buffer $1E1F
    }
    else if (name == "rti") {
        r->code = {0x40}; r->s = 0x01; r->prefill = {0x0102, 0x04, 0x0103, 0x10, 0x0104, 0x1E};
        r->loops = {0x1E10, 0x1E1E};   // $1E1E: PC lo = buffer MSB $1E, PC hi = status $1E
    }
    else if (name == "pla" || name == "plp" || name == "plx" || name == "ply") {
        u8 op = name == "pla" ? 0x68 : name == "plp" ? 0x28 : name == "plx" ? 0xFA : 0x7A;
        r->code = {op, bra, self}; r->s = 0x01; r->prefill = {0x0102, name == "plp" ? 0x05u : 0x66u};
    }
    else if (name == "irq") {
        r->code = {0x58, bra, self}; r->irr = 0x02; r->vector_lo = 0x1FF8; r->loops = {kHandler};
    }
    else if (name == "irqtam" || name == "irqtam2" || name == "irqtamt" || name == "irqtam1i" ||
             name == "irqtam2i" || name == "irqtamti") {
        // TAM #$02 with IRQ1 / IRQ2 / TIQ requested; the "i" variants start with I = 1 (no entry).
        bool irq2 = name.compare(0, 7, "irqtam2") == 0, tiq = name.compare(0, 7, "irqtamt") == 0;
        r->code = {0x53, 0x02, bra, self};
        r->irr = irq2 ? 0x01 : tiq ? 0x04 : 0x02;
        r->vector_lo = irq2 ? 0x1FF6 : tiq ? 0x1FFA : 0x1FF8;
        r->p = name.back() == 'i' ? 0x04 : 0x00;
        r->loops = {kHandler};
    }
    else if (name.compare(0, 3, "ni_") == 0) {
        // ni_<base>_<src>_<k>[_i]
        std::vector<std::string> f;
        size_t at = 3;
        while (at <= name.size()) {
            size_t us = name.find('_', at);
            f.push_back(name.substr(at, us == std::string::npos ? std::string::npos : us - at));
            if (us == std::string::npos) break;
            at = us + 1;
        }
        if (f.size() < 3 || f.size() > 4 || (f.size() == 4 && f[3] != "i"))
            return false;
        const std::string& base = f[0];
        const std::string& src = f[1];
        char* kend = nullptr;
        unsigned k = (unsigned)std::strtoul(f[2].c_str(), &kend, 10);
        if (f[2].empty() || !kend || *kend || k < 1 || k > 8) return false;
        if (src == "t") { g_ni_mode = 0; g_ni_bits = 0x04; }
        else if (src == "q1" || src == "q2") { g_ni_mode = 1; g_ni_bits = src == "q1" ? 0x02 : 0x01; }
        else if (src == "q1m" || src == "q2m") { g_ni_mode = 2; g_ni_bits = src == "q1m" ? 0x02 : 0x01; }
        else return false;
        g_ni_k = k;
        g_ni = true;
        r->p = f.size() == 4 ? 0x04 : 0x00;
        r->loops = {kHandler};
        if (base == "tam") { r->code = {0x53, 0x02, bra, self}; }
        else if (base == "sta") { r->a = 0x05; r->code = {0x8D, 0x00, 0x40, bra, self}; }   // STA $4000 (VDC AR)
        else {
            bool vdc = base.size() == 4 && base[3] == 'v';
            std::string opn = base.substr(0, 3);
            u8 op = opn == "tii" ? 0x73 : opn == "tdd" ? 0xC3 : opn == "tin" ? 0xD3 : opn == "tia" ? 0xE3 :
                    opn == "tai" ? 0xF3 : 0;
            if (!op || (base.size() != 3 && !vdc)) return false;
            unsigned src_a = 0x1E80, dst = vdc ? 0x4002 : 0x4000;   // $4002 = VDC data port (MPR2 = $FF)
            if (op == 0xC3) { src_a += 3; dst += 3; }
            r->code = {op, (u8)(src_a & 0xFF), (u8)(src_a >> 8), (u8)(dst & 0xFF), (u8)(dst >> 8), 0x04, 0x00,
                       bra, self};
        }
    }
    else if (name == "xself" || name == "xlen0") {   // TII over its own opcode / length 0 (65536)
        bool own = name == "xself";
        r->code = {0x73, 0x80, 0x1E, 0x00, (u8)(own ? 0x1E : 0x40), (u8)(own ? 4 : 0), 0x00, bra, self};
    }
    else if (name.size() >= 5 && name[0] == 'x') {
        std::string opn = name.substr(1, 3), kind = name.substr(4);
        u8 op = opn == "tii" ? 0x73 : opn == "tdd" ? 0xC3 : opn == "tin" ? 0xD3 : opn == "tia" ? 0xE3 :
                opn == "tai" ? 0xF3 : 0;
        if (!op || (kind != "d" && kind != "s" && kind != "m" && kind != "m1" && kind != "q" && kind != "q2" &&
                    kind != "qt" && kind != "c" && kind != "w" && kind != "e"))
            return false;
        unsigned src = kind == "s" ? 0x6000 : 0x1E80, dst = kind == "s" ? 0x4010 : 0x4000;
        if (op == 0xC3) { src += 3; dst += 3; }   // TDD runs downwards over the same four bytes
        g_x_forbid = kind == "d" ? 2 : kind == "s" ? 3 : 0;
        if (kind == "c" || kind == "w") {
            // Page-boundary fixtures per opcode (pinned address sequences). c: the range reaches the page
            // under test; w: it stops just before it (an incrementing model would wrongly reach it).
            static const struct { const char* name; unsigned src, dst, page; } k_cross[] = {
                {"tiic", 0x1E80, 0x3FFE, 2}, {"tiiw", 0x1E80, 0x3FFC, 2},
                {"tinc", 0x3FFE, 0x3F00, 2}, {"tinw", 0x1E80, 0x3FFF, 2},
                {"tddc", 0x1E83, 0x6001, 2}, {"tddw", 0x1E83, 0x6003, 2},
                {"tiac", 0x1E80, 0x5FFF, 3}, {"tiaw", 0x1E80, 0x3FFE, 2},
                {"taic", 0x5FFF, 0x3F00, 3}, {"taiw", 0x3FFE, 0x3F00, 2},
            };
            std::string key = opn + kind;
            for (const auto& c : k_cross)
                if (key == c.name) { src = c.src; dst = c.dst; g_x_forbid = c.page; }
        }
        r->code = {op, (u8)(src & 0xFF), (u8)(src >> 8), (u8)(dst & 0xFF), (u8)(dst >> 8), 0x04, 0x00, bra, self};
        if (kind[0] == 'q') {   // IRQ1 (q) / IRQ2 (q2) / TIQ (qt) pending with I = 0 during the transfer
            r->irr = kind == "q2" ? 0x01 : kind == "qt" ? 0x04 : 0x02;
            r->vector_lo = kind == "q2" ? 0x1FF6 : kind == "qt" ? 0x1FFA : 0x1FF8;
            r->p = 0x00;
            r->loops = {kHandler};
        }
    }
    else if (name == "tamcode") {   // TAM #$01 with A = $FF: the code page (MPR0) becomes the hardware page
        r->a = 0xFF; r->code = {0x53, 0x01, bra, self};
    }
    else if (name == "tmapos") { r->code = {0x43, 0x02, 0x53, 0x02, bra, self}; }   // TMA #$02 (A = MPR1); TAM #$02
    else if (name == "tmaneg") { r->code = {0x43, 0x80, 0x53, 0x02, bra, self}; }   // TMA #$80 (A = MPR7 = $00); TAM #$02
    else if (name.size() == 7 && name.compare(0, 3, "tam") == 0 &&
             name.find_first_not_of("0123456789abcdef", 3) == std::string::npos) {
        // TAM bitmask sweep: tam<mask hex><A hex>; the harness stops before the second instruction.
        unsigned mask = (unsigned)std::strtoul(name.substr(3, 2).c_str(), nullptr, 16);
        r->a = (unsigned)std::strtoul(name.substr(5, 2).c_str(), nullptr, 16);
        r->code = {0x53, (u8)mask, bra, self};
    }
    else if (name == "tstart" || name == "tend" || name == "tin" || name == "tia" || name == "tdd" || name == "tai" ||
             name == "tendtin" || name == "tendtia" || name == "tendtdd" || name == "tendtai") {
        // TransferStart/TransferEnd through each block transfer opcode (all share OPCodes_TransferStart/End).
        // tend<op>: the same transfer with the harness MPR1 change before the TransferEnd step.
        std::string base = name.compare(0, 4, "tend") == 0 && name.size() > 4 ? name.substr(4) : name;
        u8 op = base == "tin" ? 0xD3 : base == "tia" ? 0xE3 : base == "tdd" ? 0xC3 : base == "tai" ? 0xF3 : 0x73;
        r->code = {op, (u8)(kSrc & 0xFF), (u8)(kSrc >> 8), (u8)(kDst & 0xFF), (u8)(kDst >> 8), 0x01, 0x00,
                   bra, self};
        r->a = 0x12;
        r->read_buffer = 0xBEEF; r->status = 0x05;
    }
    else if (name == "flippha") { r->code = {0xEA, 0x48, bra, self}; }   // NOP; PHA (MPR1 set before the PHA)
    else if (name == "tamjsr") { r->code = {0x53, 0x02, 0x20, 0x10, 0x1E}; r->loops = {0x1E10}; }  // TAM #$02; JSR
    else if (name == "phasame") { r->code = {0x48, bra, self}; r->a = 0x05; r->ar = 0x05; }
    else if (name == "pharestore") { r->code = {0x48, 0x03, 0x00, bra, self}; r->a = 0x07; r->ar = 0x00; }
    else return false;
    return true;
}

}  // namespace

int main(int argc, char** argv)
{
    if (argc != 5)
        return 2;
    char* end = nullptr;
    unsigned long frames = std::strtoul(argv[4], &end, 10);
    if (!end || *end || frames < 1 || frames > 10)
        return 2;
    std::string mode = argv[3];
    // Optional method prefix (Issue #498 / #500): "C:" (default, method C stack guard of #499), "A:" (method
    // A instruction refusal), "AC:" (both). Issue #647: "AD" / "ADC" add D (decode-time TAM refusal),
    // "AE" / "AEC" add E (IRQ deferral during a TAM).
    std::string method = "C", rest = mode;
    size_t colon = mode.find(':');
    if (colon != std::string::npos) {
        method = mode.substr(0, colon);
        rest = mode.substr(colon + 1);
    }
    if (method != "C" && method != "A" && method != "AC" && method != "AD" && method != "AE" &&
        method != "ADC" && method != "AEC" && method != "AT")
        return 2;
    size_t dash = rest.find('-');
    if (dash == std::string::npos)
        return 2;
    std::string route_name = rest.substr(0, dash);
    std::string variant = rest.substr(dash + 1);
    int mutation = 0;
    bool guarded = true;
    unsigned bank = 0xFF;   // stack page bank for every variant except allowed
    if (variant == "allowed") bank = 0xF8;
    else if (variant == "armed") {}
    else if (variant == "unguarded") guarded = false;
    else if (variant == "armedram") bank = 0x80;                          // CD-ROM RAM (not the allowed WRAM)
    else if (variant == "unguardedram") { bank = 0x80; guarded = false; }
    else if (variant == "armedbram") bank = 0xF7;                         // backup RAM bank (S1: refused)
    else if (variant == "unguardedbram") { bank = 0xF7; guarded = false; }
    else if (variant == "mut-late16") mutation = 1;
    else if (variant == "mut-popinc") mutation = 2;
    else if (variant == "mut-nolatch") mutation = 3;
    else if (variant == "mut-ignore") mutation = 4;
    else if (variant == "mut-nostop") mutation = 5;
    else if (variant == "mut-aignore") mutation = 6;
    else if (variant == "mut-alate") mutation = 7;
    else if (variant == "mut-doff") g_d_mut = 1;      // probe-side: D disabled
    else if (variant == "mut-dbit") g_d_mut = 2;      // probe-side: D checks the wrong operand bit
    else if (variant == "mut-eoff") g_e_mut = 1;      // probe-side: E disabled
    else if (variant == "mut-tresume") g_t_mut = 1;   // probe-side: guest resumed after a T refusal
    else if (variant == "mut-tnomid") g_t_mut = 2;    // probe-side: no mid-transfer MPR check
    else if (variant == "mut-tfirst") g_t_mut = 3;    // probe-side: pre-start check of the first byte only
    else if (variant == "mut-tseq") g_t_mut = 4;      // probe-side: every opcode modelled as TII
    else if (variant == "mut-tnoself") g_t_mut = 5;   // probe-side: no own-opcode check before the start
    else if (variant == "mut-noinject") g_ni_noinject = true;   // probe-side: the IRQ is never injected
    else if (variant == "mut-nomidcheck") mutation = 8;         // core: no CheckIRQs at mid clock points
    else if (variant == "mut-xferirq") mutation = 9;            // core: IRQ taken inside a block transfer
    else return 2;
    Route route;
    if (!route_for(route_name, &route))
        return 2;
    bool io_stack = variant != "allowed";
    g_stack_bank = bank;
    bool use_c = guarded && method.find('C') != std::string::npos;
    g_method_a = guarded && method.find('A') != std::string::npos;
    g_method_d = guarded && method.find('D') != std::string::npos;
    g_method_e = guarded && method.find('E') != std::string::npos;
    g_method_t = guarded && method.find('T') != std::string::npos;

    std::ifstream file(argv[1], std::ios::binary);
    if (!file)
        return 3;
    std::vector<u8> bios((std::istreambuf_iterator<char>(file)), {});
    if (bios.size() < 0x2000)
        return 3;
    if (g_ni) {   // every IRQ vector (IRQ2 $FFF6, IRQ1 $FFF8, TIQ $FFFA) -> the handler
        for (unsigned v : {0x1FF6u, 0x1FF8u, 0x1FFAu}) {
            bios[v] = (u8)(kHandler & 0xFF);
            bios[v + 1] = (u8)(kHandler >> 8);
        }
    }
    if (route.vector_lo) {
        bios[route.vector_lo] = (u8)(kHandler & 0xFF);
        bios[route.vector_lo + 1] = (u8)(kHandler >> 8);
    }

    GeargrafxCore core;
    core.Init(nullptr);
    if (!core.LoadBiosFromBuffer(bios.data(), bios.size(), true))
        return 4;
    if (!core.LoadMedia(argv[2]))
        return 5;
    if (!core.GetMedia()->IsCDROMHardwareEnabled())
        return 6;

    g_cpu = core.GetHuC6280();
    g_st = g_cpu->GetState();
    g_memory = core.GetMemory();
    g_vdc = core.GetHuC6270_1()->GetState();
    Memory* memory = g_memory;

    // Page 0 = WRAM (guest), pages 2..7 = bank 0 (synthetic BIOS ROM; vectors at $FFF6..). MPR1 = stack page.
    memory->SetMpr(0, 0xF8);
    for (int i = 2; i <= 7; ++i)
        memory->SetMpr(i, 0x00);
    // Routes whose stack page changes during the run start from the allowed WRAM bank.
    bool is_tend = route_name.compare(0, 4, "tend") == 0;
    bool sweep = route_name.size() == 7 && route_name.compare(0, 3, "tam") == 0 &&
                 route_name.find_first_not_of("0123456789abcdef", 3) == std::string::npos;
    bool irqtam = route_name.compare(0, 6, "irqtam") == 0;
    bool changes_later = irqtam || is_tend || route_name == "flippha" || route_name == "tamjsr" ||
                         route_name == "tamcode" || route_name.compare(0, 3, "tma") == 0 || sweep ||
                         route_name[0] == 'x' || g_ni;
    memory->SetMpr(1, (u8)(io_stack && !changes_later ? bank : 0xF8));

    u8* wram = memory->GetWorkingRAM();
    for (unsigned i = 0x0100; i < 0x0200; ++i)
        wram[i] = 0;                                 // stack page (CPU $2100-$21FF when MPR1 = $F8)
    for (unsigned i = 0x1E00; i < 0x1F00; ++i)
        wram[i] = 0xEA;                              // NOP sled in the guest page
    for (size_t i = 0; i < route.code.size(); ++i)
        wram[kGuest + i] = route.code[i];
    for (unsigned loop : route.loops) {
        wram[loop] = 0x80;
        wram[loop + 1] = 0xFE;
    }
    wram[kHandler] = 0x80;
    wram[kHandler + 1] = 0xFE;
    wram[kSrc] = 0x5A;
    wram[kDst] = 0x00;
    for (size_t i = 0; i + 1 < route.prefill.size(); i += 2)
        wram[route.prefill[i]] = (u8)route.prefill[i + 1];

    g_st->PC->SetValue((u16)kGuest);
    g_st->S->SetValue((u8)route.s);
    // irqtam / tamjsr: the guest's TAM #$02 sets MPR1 = A.
    bool tam_route = irqtam || route_name == "tamjsr" || route_name.compare(0, 7, "ni_tam_") == 0;
    g_st->A->SetValue((u8)(tam_route ? (io_stack ? bank : 0xF8) : route.a));
    g_st->X->SetValue((u8)route.x);
    g_st->Y->SetValue((u8)route.y);
    g_st->P->SetValue((u8)route.p);
    *g_st->IRR = (u8)route.irr;
    *g_st->IDR = 0;

    *g_vdc->READ_BUFFER = (u16)route.read_buffer;
    *g_vdc->SR = (u16)route.status;
    *g_vdc->AR = (u8)route.ar;

    g_flip_tend = is_tend && io_stack;
    g_flip_at = route_name == "flippha" && io_stack ? 2 : 0;   // before the PHA (instruction 2)
    g_budget = sweep ? 1 : 0;                                   // sweep: stop before instruction 2
    if (g_ni) {
        // Page 2: the hardware page for sta and the VDC transfers, WRAM otherwise; page 3 WRAM.
        std::string base = route_name.substr(3, route_name.find('_', 3) - 3);
        bool hw = base == "sta" || (base.size() == 4 && base[3] == 'v');
        memory->SetMpr(2, (u8)(hw ? 0xFF : 0xF8));
        memory->SetMpr(3, 0xF8);
        for (unsigned i = 0; i < 4; ++i)
            wram[kSrc + i] = (u8)(0x5A + i);
        *g_st->TIMER = false;   // the timer is armed only at instruction k (timer routes)
    }
    if (route_name[0] == 'x') {
        // Pages 2 and 3 = WRAM; the variant bank goes to the page under test (d / c / w: as listed in
        // route_for, s: source page 3; m / m1: set by the harness before byte step 3 = instruction 4;
        // e: destination page set before the TransferEnd step = instruction 6).
        std::string kind = route_name.size() > 4 ? route_name.substr(4) : "";
        memory->SetMpr(2, 0xF8);
        memory->SetMpr(3, 0xF8);
        if (io_stack && g_x_forbid)
            memory->SetMpr(g_x_forbid, (u8)bank);
        if (io_stack && (kind == "m" || kind == "m1" || kind == "e")) {
            g_flip_mpr = kind == "m1" ? 1 : 2;
            g_flip_at = kind == "e" ? 6 : 4;
        }
        wram[0x1FFE] = 0x71;                         // source bytes at page boundaries ($3FFE / $5FFE)
        wram[0x1FFF] = 0x72;
        for (unsigned i = 0; i < 4; ++i) {
            wram[kSrc + i] = (u8)(0x5A + i);       // source bytes ($1E80)
            wram[i] = (u8)(0x61 + i);              // source bytes of the s routes ($6000 = WRAM $0000)
        }
    }
    gg_verify_stack_mutation = mutation;
    gg_verify_stack_refused = false;
    gg_verify_stack_context = nullptr;
    gg_verify_stack_guard = use_c ? stack_guard : nullptr;
    gg_verify_io_observer = io_observer;
    gg_verify_pre_instruction = pre_instruction;
    gg_verify_clock_observer = clock_observer;

    Snapshot start = snap();
    unsigned irr_before = *g_st->IRR;
    unsigned vdc_ar_before = (u8)*g_vdc->AR, vdc_r0_before = g_vdc->R[0], vdc_r1_before = g_vdc->R[1];
    unsigned vdc_r7_before = g_vdc->R[7], vdc_sr_before = *g_vdc->SR, vdc_buffer_before = *g_vdc->READ_BUFFER;
    unsigned stack_before[256];
    for (unsigned i = 0; i < 256; ++i)
        stack_before[i] = wram[0x0100 + i];
    unsigned dst_before = wram[kDst];
    std::vector<u8> wram_before(wram, wram + 0x2000);   // every WRAM byte (block transfer destinations)
    // Non-WRAM stack banks (Issue #498 / #500 unknown-bank cases): bank $80 = CD-ROM RAM offset 0, bank $F7 =
    // backup RAM. The stack page is offset $0100-$01FF of the bank.
    u8* cdram = memory->GetCDROMRAM();
    u8* bram = memory->GetBackupRAM();
    std::vector<u8> cdram_before(256, 0), bram_before(256, 0);
    for (unsigned i = 0; i < 256; ++i) {
        if (cdram) cdram_before[i] = cdram[0x0100 + i];
        if (bram) bram_before[i] = bram[0x0100 + i];
    }

    GeargrafxCore::GG_Debug_Run debug = {};   // debugger loop: a refusal's stop request ends the frame
    std::vector<u8> frame(1024 * 512 * 4, 0);
    std::vector<s16> audio(65536, 0);
    unsigned frames_run = 0;
    bool halted = false;
    while (frames_run < frames) {
        int samples = 0;
        core.RunToVBlank(frame.data(), audio.data(), &samples, &debug, false);
        ++frames_run;
        // Never resume the guest after a refusal. "halted" only restates this latch; the evidence that
        // nothing ran after the refusal is instructions_after_refusal (counted at every instruction start).
        if (gg_verify_stack_refused || g_instruction_refused || g_budget_stop) {
            halted = true;
            break;
        }
    }
    Snapshot stop = snap();

    unsigned stack_changed = 0, cdram_changed = 0, bram_changed = 0;
    for (unsigned i = 0; i < 256; ++i) {
        if (wram[0x0100 + i] != stack_before[i])
            ++stack_changed;
        if (cdram && cdram[0x0100 + i] != cdram_before[i])
            ++cdram_changed;
        if (bram && bram[0x0100 + i] != bram_before[i])
            ++bram_changed;
    }

    std::fprintf(stderr, "{\"mode\":\"%s\",\"route\":\"%s\",\"variant\":\"%s\",\"guarded\":%s,\"mutation\":%d,",
                 mode.c_str(), route_name.c_str(), variant.c_str(), guarded ? "true" : "false", mutation);
    std::fprintf(stderr, "\"halted\":%s,\"latched\":%s,\"frames_run\":%u,\"frame_budget\":%lu,",
                 halted ? "true" : "false", gg_verify_stack_refused ? "true" : "false", frames_run, frames);
    std::fprintf(stderr, "\"guard_calls\":%u,\"guard_refusals\":%u,\"guard_latched_calls\":%u,",
                 g_guard_calls, g_guard_refusals, g_guard_latched_calls);
    std::fprintf(stderr, "\"instructions\":%u,\"instructions_after_refusal\":%u,", g_instructions,
                 g_instructions_after_refusal);
    std::fprintf(stderr, "\"io_reads\":%u,\"io_writes\":%u,\"io_before_refusal\":%u,\"io_after_refusal\":%u,",
                 g_io_reads, g_io_writes, g_io_before_refusal, g_io_after_refusal);
    std::fprintf(stderr, "\"refused\":%s,\"refusal\":{\"op\":%u,\"s\":%u,\"transfer_state\":%u,\"mpr1\":%u,"
                 "\"cycles\":%u,\"vdc_ar\":%u,\"kind\":\"%s\"},", g_refused_seen ? "true" : "false", g_refusal_op,
                 g_refusal_s, g_refusal_ts, g_refusal_mpr1, g_refusal_cycles, g_vdc_ar_at_refusal,
                 g_refusal_kind == 1 ? "stack" : g_refusal_kind == 2 ? "instruction" : "none");
    std::fprintf(stderr, "\"method\":\"%s\",\"stack_bank\":%u,\"instruction_refused\":%s,\"instruction_refusals\":%u,"
                 "\"cdram_present\":%s,\"bram_present\":%s,\"cdram_bytes_changed\":%u,\"bram_bytes_changed\":%u,",
                 method.c_str(), bank, g_instruction_refused ? "true" : "false", g_instruction_refusals,
                 cdram ? "true" : "false", bram ? "true" : "false", cdram_changed, bram_changed);
    std::fprintf(stderr, "\"refusal_reason\":\"%s\",\"decode_refusals\":%u,\"irq_deferrals\":%u,"
                 "\"handler_entry_instruction\":%u,\"budget_stop\":%s,",
                 g_refusal_reason, g_decode_refusals, g_irq_deferrals, g_handler_entry,
                 g_budget_stop ? "true" : "false");
    unsigned wram_changed = 0;
    std::string wram_offsets;
    for (unsigned i = 0; i < 0x2000; ++i) {
        if ((i >> 8) == 1 || wram[i] == wram_before[i])
            continue;                                    // the stack page is counted separately
        if (++wram_changed <= 16)
            wram_offsets += (wram_offsets.empty() ? "" : ",") + std::to_string(i);
    }
    std::fprintf(stderr, "\"t_refusals\":%u,\"t_resumed\":%s,\"t_at_refusal\":[%u,%u,%u,%u],"
                 "\"t_stop\":[%u,%u,%u,%u],\"wram_bytes_changed\":%u,\"wram_changed_offsets\":[%s],",
                 g_t_refusals, g_t_resumed ? "true" : "false", g_t_at_refusal[0], g_t_at_refusal[1],
                 g_t_at_refusal[2], g_t_at_refusal[3], g_cpu->GG_VerifyTransferSource(),
                 g_cpu->GG_VerifyTransferDest(), g_cpu->GG_VerifyTransferLength(), g_cpu->GG_VerifyTransferCount(),
                 wram_changed, wram_offsets.c_str());
    std::fprintf(stderr, "\"ni\":{\"active\":%s,\"mode\":%u,\"k\":%u,\"bits\":%u,\"injected\":%s,"
                 "\"clock_calls\":%u,\"speed\":%d,\"timeline\":[", g_ni ? "true" : "false", g_ni_mode,
                 g_ni_k, g_ni_bits, g_ni_injected ? "true" : "false", g_ni_clock_calls, (int)*g_st->SPEED);
    for (size_t i = 0; i < g_ni_timeline.size(); ++i) {
        const NiTime& e = g_ni_timeline[i];
        std::fprintf(stderr, "%s{\"i\":%u,\"pc\":%u,\"irr\":%u,\"pend\":%u,\"ts\":%u,\"a\":%u,\"x\":%u,\"y\":%u,"
                     "\"p\":%u,\"s\":%u,\"xfer\":[%u,%u,%u],\"mpr\":[%u,%u,%u,%u,%u,%u,%u,%u]}", i ? "," : "",
                     e.i, e.pc, e.irr, e.pend, e.ts, e.a, e.x, e.y, e.p, e.s, e.src, e.dst, e.len, e.mpr[0], e.mpr[1],
                     e.mpr[2], e.mpr[3], e.mpr[4], e.mpr[5], e.mpr[6], e.mpr[7]);
    }
    std::fprintf(stderr, "],\"clocks\":[");
    for (size_t i = 0; i < g_ni_clocks.size(); ++i) {
        const NiClock& e = g_ni_clocks[i];
        std::fprintf(stderr, "%s{\"i\":%u,\"cycles\":%u,\"irr\":%u,\"injected\":%s}", i ? "," : "", e.i,
                     e.cycles, e.irr, e.injected ? "true" : "false");
    }
    std::fprintf(stderr, "]},");
    std::fprintf(stderr, "\"harness_mpr1_flip\":%s,\"flip_instruction\":%u,", g_flipped ? "true" : "false",
                 g_flip_instruction);
    print_snapshot("start", start);
    print_snapshot("refusal_instruction_start", g_refusal_instr);
    print_snapshot("at_refusal", g_at_refusal);
    print_snapshot("stop", stop);
    std::fprintf(stderr, "\"vdc\":{\"ar\":[%u,%u],\"r0\":[%u,%u],\"r1\":[%u,%u],\"r7\":[%u,%u],\"sr\":[%u,%u],"
                 "\"read_buffer\":[%u,%u]},", vdc_ar_before, (unsigned)(u8)*g_vdc->AR, vdc_r0_before,
                 (unsigned)g_vdc->R[0], vdc_r1_before, (unsigned)g_vdc->R[1], vdc_r7_before, (unsigned)g_vdc->R[7],
                 vdc_sr_before, (unsigned)*g_vdc->SR, vdc_buffer_before, (unsigned)*g_vdc->READ_BUFFER);
    std::fprintf(stderr, "\"irr_before\":%u,\"irr_after\":%u,", irr_before, (unsigned)*g_st->IRR);
    std::fprintf(stderr, "\"stack_window_before\":[%u,%u,%u,%u,%u],\"stack_window_after\":[%u,%u,%u,%u,%u],",
                 stack_before[0], stack_before[1], stack_before[2], stack_before[3], stack_before[4],
                 (unsigned)wram[0x0100], (unsigned)wram[0x0101], (unsigned)wram[0x0102], (unsigned)wram[0x0103],
                 (unsigned)wram[0x0104]);
    std::fprintf(stderr, "\"stack_bytes_changed\":%u,\"dst\":[%u,%u],", stack_changed, dst_before,
                 (unsigned)wram[kDst]);
    std::fprintf(stderr, "\"events_truncated\":%s,\"events\":[", g_seq > kMaxEvents ? "true" : "false");
    for (size_t i = 0; i < g_events.size(); ++i) {
        const Event& e = g_events[i];
        std::fprintf(stderr, "%s{\"seq\":%u,\"kind\":\"%s\",\"op\":%u,\"address\":%u,\"value\":%u,\"decision\":%d,"
                     "\"latched\":%s,\"mpr1\":%u,\"instruction\":%u}", i ? "," : "", e.seq,
                     e.stack ? "stack" : "io", e.op, e.address, e.value, e.decision, e.latched ? "true" : "false",
                     e.mpr1, e.instruction);
    }
    std::fprintf(stderr, "]}");
    std::fflush(stderr);
    gg_verify_stack_guard = nullptr;
    gg_verify_io_observer = nullptr;
    gg_verify_pre_instruction = nullptr;
    gg_verify_clock_observer = nullptr;
    gg_verify_stack_mutation = 0;
    return 0;
}
