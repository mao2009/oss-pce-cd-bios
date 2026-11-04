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
        g_memory->SetMpr(1, (u8)g_stack_bank);
        g_flipped = true;
        g_flip_instruction = g_instructions;
    }
    g_instr_start = snap();
    if (!g_method_a)
        return true;
    if (!g_instruction_refused && g_memory->GetMpr(1) == 0xF8)
        return true;
    if (!g_refused_seen) {
        g_refused_seen = true;
        g_refusal_kind = 2;
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
    else if (name == "irqtam") {
        r->code = {0x53, 0x02, bra, self}; r->irr = 0x02; r->vector_lo = 0x1FF8; r->p = 0x00;
        r->loops = {kHandler};
    }
    else if (name == "tstart" || name == "tend" || name == "tin" || name == "tia" || name == "tdd" || name == "tai") {
        // TransferStart/TransferEnd through each block transfer opcode (all share OPCodes_TransferStart/End).
        u8 op = name == "tin" ? 0xD3 : name == "tia" ? 0xE3 : name == "tdd" ? 0xC3 : name == "tai" ? 0xF3 : 0x73;
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
    // A instruction refusal), "AC:" (both).
    std::string method = "C", rest = mode;
    size_t colon = mode.find(':');
    if (colon != std::string::npos) {
        method = mode.substr(0, colon);
        rest = mode.substr(colon + 1);
    }
    if (method != "C" && method != "A" && method != "AC")
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
    else return 2;
    Route route;
    if (!route_for(route_name, &route))
        return 2;
    bool io_stack = variant != "allowed";
    g_stack_bank = bank;
    bool use_c = guarded && method.find('C') != std::string::npos;
    g_method_a = guarded && method.find('A') != std::string::npos;

    std::ifstream file(argv[1], std::ios::binary);
    if (!file)
        return 3;
    std::vector<u8> bios((std::istreambuf_iterator<char>(file)), {});
    if (bios.size() < 0x2000)
        return 3;
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
    bool changes_later = route_name == "irqtam" || route_name == "tend" || route_name == "flippha" ||
                         route_name == "tamjsr";
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
    bool tam_route = route_name == "irqtam" || route_name == "tamjsr";
    g_st->A->SetValue((u8)(tam_route ? (io_stack ? bank : 0xF8) : route.a));
    g_st->X->SetValue((u8)route.x);
    g_st->Y->SetValue((u8)route.y);
    g_st->P->SetValue((u8)route.p);
    *g_st->IRR = (u8)route.irr;
    *g_st->IDR = 0;

    *g_vdc->READ_BUFFER = (u16)route.read_buffer;
    *g_vdc->SR = (u16)route.status;
    *g_vdc->AR = (u8)route.ar;

    g_flip_tend = route_name == "tend" && io_stack;
    g_flip_at = route_name == "flippha" && io_stack ? 2 : 0;   // before the PHA (instruction 2)
    gg_verify_stack_mutation = mutation;
    gg_verify_stack_refused = false;
    gg_verify_stack_context = nullptr;
    gg_verify_stack_guard = use_c ? stack_guard : nullptr;
    gg_verify_io_observer = io_observer;
    gg_verify_pre_instruction = pre_instruction;

    Snapshot start = snap();
    unsigned irr_before = *g_st->IRR;
    unsigned vdc_ar_before = (u8)*g_vdc->AR, vdc_r0_before = g_vdc->R[0], vdc_r1_before = g_vdc->R[1];
    unsigned vdc_r7_before = g_vdc->R[7], vdc_sr_before = *g_vdc->SR, vdc_buffer_before = *g_vdc->READ_BUFFER;
    unsigned stack_before[256];
    for (unsigned i = 0; i < 256; ++i)
        stack_before[i] = wram[0x0100 + i];
    unsigned dst_before = wram[kDst];
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
        if (gg_verify_stack_refused || g_instruction_refused) {
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
    gg_verify_stack_mutation = 0;
    return 0;
}
