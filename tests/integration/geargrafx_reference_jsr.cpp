// Local-only, guarded reference-BIOS guest JSR observer (slot 0x1E / EX_GETVER).
// Never distribute this GPL-linked executable, the linked core, or private BIOS bytes.
//
// In-core safety mechanisms (implemented against the pinned Geargrafx core itself,
// NOT against a Python flag and NOT from a post-run empty diff):
//   1. RAM write protection: Memory::GetMemoryMapWrite() is cleared for every bank
//      except banks the configured allowlist pages actually cover. The core then
//      drops forbidden stores during execution (prevention).
//   2. CPU-address write breakpoints: a write covering every non-allowlisted page is
//      registered so the core flags the offending instruction while it executes
//      (detection).
//   3. Hardware page: no MPR is allowed to hold 0xFF, so the memory-mapped I/O page
//      is unreachable. If any MPR becomes 0xFF the run is rejected before another
//      instruction executes.
//   4. Dynamic MPR change: every MPR is snapshotted before execution and compared
//      after each step. Any change (e.g. via TAM) rejects the run before the next
//      guest instruction can exploit the new mapping.
//   5. Instruction budget: stepped one instruction at a time with actual execution
//      counting.
//
// Issue #179 measurement mode "mmio1804": deliberately maps MPR0 = $FF (item 3
// does NOT apply to this mode) to measure whether the resulting $1804 store can
// be rejected before its device side effect. It cannot: the CPU-address write
// breakpoint only raises a flag inside Memory::Write, the device write still
// completes, and the core stops at the next instruction boundary. Evidence and
// verdict: tests/integration/geargrafx_mmio1804_trap.py (BLOCKED).
//
// Issue #173 mode "mmio1804-preblock": same guest plus an EXECUTE breakpoint on
// the STA opcode. HuC6280::CheckBreakpoints() runs at the END of RunInstruction()
// with the NEXT instruction's PC, so the flag rises before the STA executes and
// the step monitor refuses to re-enter the core. Honest limit: it needs the
// store's PC (known guest layout) - prevention for known code, not a general MMIO
// interceptor. Verdict: tests/integration/geargrafx_mmio1804_preblock.py. No gate
// change: slot "$01" stays BLOCKED.
//
// Known limitation (not blocked by this probe, documented here):
//   ST0/ST1/ST2 (opcodes 0x03/0x13/0x23) write to VDC registers via
//   m_huc6202->WriteFromCPU(), bypassing Memory::Write(), map_write, and
//   CPU-address breakpoints entirely. They produce irreversible device side
//   effects. The slot "$1E" guest code (LDA/LDX/LDY/JSR/BRA) and the measured
//   EX_GETVER callee returns X=3/Y=0 with A preserved) contain no ST0/ST1/ST2 or TAM,
//   so they are not exercised by the measured execution path. Any future API
//   allowing arbitrary guest code must address this path before being unblocked.
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

const unsigned long kGuestBase = 0x3E00;  // page1 -> WRAM, guest program entry
const unsigned long kStubIndex = 0x1E00;   // WRAM index the guest program is written to
const unsigned long kSlot = 0x1E;
// Synthetic mmio guest: LDA #$02, STA $1804 ($3E02), BRA *; preblock arms here.
const unsigned long kMmioStaPc = kGuestBase + 2;
const unsigned long kEntry = 0xE000 + 3 * kSlot;
const unsigned kAllowPages = 8 * 0x2000;   // full CPU space

template <typename Cpu>
std::string state_json(Cpu* cpu, Memory* memory)
{
    char buf[256];
    int n = std::snprintf(buf, sizeof(buf),
                 "{\"a\":%u,\"x\":%u,\"y\":%u,\"p\":%u,\"sp\":%u,\"pc\":%u,\"mpr\":[",
                 (unsigned)cpu->A->GetValue(), (unsigned)cpu->X->GetValue(),
                 (unsigned)cpu->Y->GetValue(), (unsigned)cpu->P->GetValue(),
                 (unsigned)cpu->S->GetValue(), (unsigned)cpu->PC->GetValue());
    std::string s(buf, n < 0 ? 0 : (size_t)n);
    for (int i = 0; i < 8; ++i) {
        n = std::snprintf(buf, sizeof(buf), "%s%u", i ? "," : "", (unsigned)memory->GetMpr(i));
        if (n > 0)
            s.append(buf, (size_t)n);
    }
    s.append("]}");
    return s;
}

struct Observer {
    GeargrafxCore core;
    HuC6280* cpu = nullptr;
    HuC6280::HuC6280_State* st = nullptr;
    Memory* memory = nullptr;
    std::vector<u8> frame;
    std::vector<s16> audio;
    GeargrafxCore::GG_Debug_Run debug = {};
    unsigned long executed = 0;
    unsigned long budget = 0;

    void step()
    {
        int samples = 0;
        core.RunToVBlank(frame.data(), audio.data(), &samples, &debug, false);
        ++executed;
    }

    bool hw_reachable() const
    {
        for (int i = 0; i < 8; ++i)
            if (memory->GetMpr(i) == 0xFF)
                return true;
        return false;
    }
};

}  // namespace

int main(int argc, char** argv)
{
    // argv: bios cue slot budget allow_start allow_end mode neg_addr
    if (argc != 9)
        return 2;
    char* end = nullptr;
    unsigned long slot = std::strtoul(argv[3], &end, 0);
    if (!end || *end || slot != kSlot)
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
    if (allow_start >= allow_end || allow_end > kAllowPages)
        return 2;
    if (allow_start % 0x2000 != 0 || allow_end % 0x2000 != 0)
        return 2;  // enforcement granularity is the 8 KiB MPR page
    std::string mode = argv[7];
    if (mode != "call" && mode != "neg-write" && mode != "pos-write" && mode != "tam" &&
        mode != "mmio1804" && mode != "mmio1804-preblock" && mode != "st0" && mode != "st1" &&
        mode != "st2")
        return 2;
    unsigned long neg_addr = std::strtoul(argv[8], &end, 0);
    if (!end || *end || neg_addr > 65535UL)
        return 2;
    // Issue #179 measures exactly one path: a guest store to the CD-ROM reset
    // register at CPU address $1804 through the Hardware Page (MPR0 == $FF).
    if ((mode == "mmio1804" || mode == "mmio1804-preblock") && neg_addr != 0x1804)
        return 2;
    if (mode == "neg-write" && neg_addr >= allow_start && neg_addr < allow_end)
        return 2;  // forbidden target must lie outside the allowlist
    if (mode == "pos-write" && (neg_addr < allow_start || neg_addr >= allow_end))
        return 2;  // allowed-control target must lie inside the allowlist
    // slot "$1E" execution environment fixes the RAM write allowlist to page 1
    // ([0x2000, 0x4000)): this is where this core's zero page and stack live.
    // Any other allowlist is rejected at the C++ level so that bypassing the
    // Python adapter still cannot create an unsafe configuration.
    if (allow_start != 0x2000UL || allow_end != 0x4000UL)
        return 9;  // BLOCKED: slot-1e allowlist must be exactly [0x2000, 0x4000)

    std::ifstream file(argv[1], std::ios::binary);
    if (!file)
        return 3;
    std::vector<u8> bios((std::istreambuf_iterator<char>(file)), {});

    Observer o;
    o.budget = budget;
    o.frame.assign(1024 * 512 * 4, 0);
    o.audio.assign(65536, 0);
    o.debug.step_debugger = true;
    o.debug.stop_on_breakpoint = true;  // activate CPU_ADDRESS write breakpoints
    o.debug.stop_on_irq = false;
    o.debug.stop_on_brk = false;
    o.debug.stop_on_run_to_breakpoint = false;

    o.core.Init(nullptr);
    if (!o.core.LoadBiosFromBuffer(bios.data(), bios.size(), true))
        return 4;
    if (!o.core.LoadMedia(argv[2]))
        return 5;
    if (!o.core.GetMedia()->IsCDROMHardwareEnabled())
        return 6;

    o.cpu = o.core.GetHuC6280();
    o.st = o.cpu->GetState();
    o.memory = o.core.GetMemory();

    // Deterministic synthetic caller environment.
    // This core hardwires ZERO_PAGE_ADDR=0x2000 and STACK_ADDR=0x2100, so the
    // zero page and the 8-bit stack both live in page 1: MPR1 must map WRAM and
    // the allowlist must cover page 1 for any realistic call to return.
    //
    // MPR0 is intentionally mapped to ROM bank 0, NOT to the same WRAM bank as
    // MPR1. If MPR0 == MPR1 == 0xF8 then physical bank 0xF8 (WRAM) is reachable
    // from both page 0 ($0000-$1FFF) and page 1 ($2000-$3FFF). Because
    // Memory::GetMemoryMapWrite() is indexed by physical bank, allowing page 1
    // would wrongly also allow page 0. Mapping page 0 to ROM keeps every
    // non-allowlisted page on a non-shared bank so per-bank write protection can
    // isolate CPU addresses. Set PCE_REF_TEST_ALIAS to force MPR0 == MPR1 and
    // exercise the aliasing BLOCKED path below.
    o.memory->SetMpr(1, 0xF8);  // zero page + stack + guest code -> work RAM
    o.memory->SetMpr(7, 0x00);  // API jump table at $E000 -> ROM bank 0
    for (int i = 2; i <= 6; ++i)
        o.memory->SetMpr(i, 0x00);
    if (mode == "mmio1804" || mode == "mmio1804-preblock")
        o.memory->SetMpr(0, 0xFF);  // Issue #179: page 0 -> Hardware Page, $1804 reachable
    else if (std::getenv("PCE_REF_TEST_ALIAS"))
        o.memory->SetMpr(0, 0xF8);  // alias page 0 onto the page-1 WRAM bank
    else
        o.memory->SetMpr(0, 0x00);  // page 0 -> ROM (not shared with page 1)
    if (mode == "neg-write")
        o.memory->SetMpr(2, 0x80);  // expose core-writable CD-ROM RAM on page2

    // Guest program written directly into work RAM (not through the CPU).
    std::vector<u8> code;
    unsigned long jsr_offset = 0;
    if (mode == "call") {
        // Preload distinct sentinels so the callee's true register output is measurable.
        const u8 pre[] = {0xA9, 0xAA,   // LDA #$AA
                          0xA2, 0x55,   // LDX #$55
                          0xA0, 0xCC};  // LDY #$CC
        code.assign(pre, pre + sizeof(pre));
        jsr_offset = code.size();
        code.push_back(0x20);                       // JSR entry
        code.push_back((u8)(kEntry & 0xFF));
        code.push_back((u8)(kEntry >> 8));
        code.push_back(0x80);                       // BRA *
        code.push_back(0xFE);
    } else {
        code = {0xA9, 0x5A, 0x8D, (u8)(neg_addr & 0xFF), (u8)(neg_addr >> 8), 0x80, 0xFE};
    }
    if (mode == "tam") {
        // Dynamic MPR change test: LDA #$F8; TAM #$01 sets MPR0 to WRAM bank,
        // then STA $0000 would write through page 0 into WRAM if the MPR change
        // were not detected. The MPR change detector must block before STA runs.
        code = {0xA9, 0xF8,   // LDA #$F8
                0x53, 0x01,   // TAM #$01  (MPR0 = 0xF8)
                0x8D, 0x00, 0x00,  // STA $0000
                0x80, 0xFE}; // BRA *
    }
    if (mode == "mmio1804" || mode == "mmio1804-preblock") {
        // Issue #179 synthetic guest: LDA #$02 / STA $1804 / BRA *.
        // $02 is the SCSI RST assert bit (docs/cd-reset-contract.md), so a
        // landing store is observable as the $1804 readback (m_reset).
        code = {0xA9, 0x02, 0x8D, (u8)(neg_addr & 0xFF), (u8)(neg_addr >> 8),
                0x80, 0xFE};
    }
    // Issue #198: VDC register index selected by ST0 (0 = MAWR). Used for code
    // generation below and for the HUC6270_REGISTER breakpoint armed later.
    u8 st_reg = 0;
    if (mode == "st0" || mode == "st1" || mode == "st2") {
        // Issue #198 synthetic guest: ST0 #$nn / ST1 #$nn / ST2 #$nn then BRA *.
        // ST opcodes call m_huc6202->WriteFromCPU() directly: they bypass
        // Memory::Write(), map_write and CPU-address breakpoints, and reach the
        // VDC whatever MPR0 holds (here fixed to $00, i.e. != $FF - the reachability
        // denial that works for STA $1804 does NOT mitigate ST0/ST1/ST2).
        st_reg = 0x00;  // VDC register index selected by the leading ST0 (0 = MAWR, mask 0xFFFF)
        u8 st_op = (mode == "st0") ? 0x03 : (mode == "st1") ? 0x13 : 0x23;
        u8 st_val = (mode == "st0") ? 0x07 : (mode == "st1") ? 0x5A : 0xA5;
        if (mode == "st0")
            code = {st_op, st_val, 0x80, 0xFE};   // ST0 #$07 at $3E00, BRA * at $3E02
        else
            code = {0x03, st_reg, st_op, st_val, 0x80, 0xFE};  // ST0 #$00, ST1/ST2 #nn, BRA * at $3E04
    }
    const unsigned long kReturnAddr = kGuestBase + jsr_offset + 3;
    if (kStubIndex + code.size() > 0x2000)
        return 2;
    u8* wram = o.memory->GetWorkingRAM();
    for (size_t i = 0; i < code.size(); ++i)
        wram[kStubIndex + i] = code[i];
    // Sentinel at the physical WRAM byte that STA $0000 would target after TAM
    // remaps MPR0 to the WRAM bank. CPU address $0000 initially maps to ROM
    // (MPR0 = 0x00), but after TAM sets MPR0 = 0xF8 it maps to physical WRAM[0].
    // Writing a sentinel here lets us verify the prohibited STA never executes
    // by comparing the same physical byte before/after TAM detection.
    const u8 kTamSentinel = 0xA5;
    u8 tam_sentinel_before = 0;
    if (mode == "tam")
        tam_sentinel_before = wram[0] = kTamSentinel;
    // --- write protection: deny by default, only allowlist-covered pages stay writable.
    //     Memory::GetMemoryMapWrite() is indexed by PHYSICAL bank (the MPR value), not by
    //     CPU address. If one physical bank is mapped to both an allowlisted and a
    //     non-allowlisted CPU page then per-bank write permission cannot isolate CPU
    //     addresses: allowing the allowlisted page would wrongly also allow the other.
    //     Detect that situation and refuse to run (fail closed, BLOCKED before any
    //     guest instruction executes).
    int bank_page_mask[0x100] = {};
    for (int page = 0; page < 8; ++page)
        bank_page_mask[o.memory->GetMpr(page)] |= (1 << page);
    unsigned allow_page_mask = 0;
    for (int page = 0; page < 8; ++page) {
        unsigned long lo = page * 0x2000UL;
        if (lo >= allow_start && lo + 0x2000UL <= allow_end)
            allow_page_mask |= (1u << page);
    }
    for (int bank = 0; bank < 0x100; ++bank) {
        int pages = bank_page_mask[bank];
        if (!pages) continue;
        int allowlisted = pages & (int)allow_page_mask;
        int nonallowlisted = pages & ~(int)allow_page_mask;
        if (allowlisted && nonallowlisted) {
            std::fprintf(stderr,
                "BLOCKED: physical bank %02X shared by allowlisted and non-allowlisted CPU pages\n",
                bank);
            return 8;
        }
    }

    bool* map_write = o.memory->GetMemoryMapWrite();
    for (int bank = 0; bank < 0x100; ++bank)
        map_write[bank] = false;
    for (int page = 0; page < 8; ++page) {
        unsigned long lo = page * 0x2000UL;
        if (lo >= allow_start && lo + 0x2000UL <= allow_end)
            map_write[o.memory->GetMpr(page)] = true;
    }

    // --- detection: core flags any store to a page outside the allowlist.
    //     Cover the COMPLETE non-allowlisted CPU range [0, allow_start) and
    //     [allow_end, 0xFFFF], not just the high half: writes to page 0 (which may
    //     alias an allowlisted work-RAM bank) must be detected too. Breakpoints fire
    //     inside Memory::Write() before the store is performed (detection); the
    //     map_write deny-by-default above is what prevents the store (prevention).
    if (allow_start > 0 &&
        !o.cpu->AddBreakpoint(HuC6280::HuC6280_BREAKPOINT_TYPE_CPU_ADDRESS,
                              0, (u32)(allow_start - 1), /*range=*/true,
                              /*read=*/false, /*write=*/true, /*execute=*/false))
        return 7;
    if (allow_end <= 0xFFFFUL &&
        !o.cpu->AddBreakpoint(HuC6280::HuC6280_BREAKPOINT_TYPE_CPU_ADDRESS,
                              (u32)allow_end, 0xFFFFUL, /*range=*/true,
                              /*read=*/false, /*write=*/true, /*execute=*/false))
        return 7;

    // --- pre-execution prevention (Issue #173, "mmio1804-preblock"): EXECUTE
    //     breakpoint on the STA opcode. CheckBreakpoints() runs at the END of
    //     RunInstruction() with the PC of the instruction about to run, so the
    //     flag rises BEFORE the STA is fetched and the step monitor below
    //     refuses to re-enter the core for it (needs the store's PC).
    if (mode == "mmio1804-preblock" &&
        !o.cpu->AddBreakpoint(HuC6280::HuC6280_BREAKPOINT_TYPE_CPU_ADDRESS,
                              (u32)kMmioStaPc, 0, /*range=*/false,
                              /*read=*/false, /*write=*/false, /*execute=*/true))
        return 7;
    // Issue #198: arm a HUC6270_REGISTER write breakpoint on VDC register 0 (MAWR)
    // to observe whether ST1/ST2 data writes can be intercepted at all. ST0's
    // address-register case has no CheckMemoryBreakpoints call site, so even an
    // armed breakpoint can never fire for it.
    if ((mode == "st0" || mode == "st1" || mode == "st2") &&
        !o.cpu->AddBreakpoint(HuC6280::HuC6280_BREAKPOINT_TYPE_HUC6270_REGISTER,
                              (u32)st_reg, 0, /*range=*/false,
                              /*read=*/false, /*write=*/true, /*execute=*/false))
        return 7;

    // --- pin the guest CPU context
    o.st->PC->SetValue((u16)kGuestBase);
    o.st->S->SetValue(0xFF);
    o.st->A->SetValue(0);
    o.st->X->SetValue(0);
    o.st->Y->SetValue(0);
    o.st->P->SetValue(0x04);  // I=1 (mask IRQs), D=0, T=0, B=0

    // Snapshot the initial MPR configuration. Any MPR change during guest
    // execution (e.g. via TAM) invalidates the allowlist isolation computed
    // above: a page remapped to a writable bank would bypass the per-bank
    // write protection. Detect this fail-closed before the next guest
    // instruction can exploit it.
    u8 initial_mpr[8];
    for (int i = 0; i < 8; ++i)
        initial_mpr[i] = o.memory->GetMpr(i);

    auto wram_snap = [&]() { return std::vector<u8>(wram, wram + 0x2000); };
    auto stub_ok = [&]() {
        for (size_t i = 0; i < code.size(); ++i)
            if (wram[kStubIndex + i] != code[i])
                return false;
        return true;
    };

    std::string reason;
    bool ok = true;
    bool got_entry = false;
    bool got_callee = false;
    unsigned long callee_pc = 0;
    bool returned = false;
    bool detected = false;
    bool execbp_hit = false;   // execute breakpoint raised BEFORE the STA ran
    bool mpr_changed = false;
    u16 detected_pc = 0;
    std::vector<u8> wram0 = wram_snap();
    u8 cdram0 = 0;
    u8 tam_sentinel_after = 0;
    if (mode == "neg-write") {
        // Forbidden target outside the allowlist. Read the byte at the CPU address
        // (through the same MPR mapping the store uses) so prevention can be
        // verified by comparing before/after regardless of which physical bank the
        // address maps to.
        cdram0 = o.memory->Read((u16)neg_addr);
    }
    std::string before_state = state_json(o.st, o.memory);
    // Issue #179 device-state observation (measured, never assumed): the $1804
    // readback is CdRom::m_reset (docs/cd-reset-contract.md: $02 asserted /
    // $00 released) and $1403 reads the interrupt request register (IRQ2 = bit 0).
    u8 mmio_rst_before = 0, mmio_irq2_before = 0;
    u8 mmio_rst_after = 0, mmio_irq2_after = 0;
    // Issue #198: VDC readback (core public API).
    u16 vdc_ar_before = 0, vdc_ar_after = 0;
    u16 vdc_r_before = 0, vdc_r_after = 0;
    if (mode == "mmio1804" || mode == "mmio1804-preblock") {
        mmio_rst_before = o.memory->Read((u16)neg_addr);
        mmio_irq2_before = o.memory->Read(0x1403);
    }
    if (mode == "st0" || mode == "st1" || mode == "st2") {
        // Issue #198: VDC state before the ST opcode (core public API readback).
        HuC6270* vdc = o.core.GetHuC6270_1();
        HuC6270::HuC6270_State* vs = vdc->GetState();
        vdc_ar_before = (u8)*vs->AR;
        vdc_r_before = vs->R[st_reg];
    }

    FILE* trace = nullptr;
    if (const char* tp = std::getenv("PCE_REF_TRACE"))
        trace = std::fopen(tp, "w");

    while (ok && o.executed < o.budget) {
        o.step();
        if (trace)
            std::fprintf(trace, "%lu pc=%04X a=%02X x=%02X y=%02X p=%02X sp=%02X\n",
                         o.executed, (unsigned)o.st->PC->GetValue(),
                         (unsigned)o.st->A->GetValue(), (unsigned)o.st->X->GetValue(),
                         (unsigned)o.st->Y->GetValue(), (unsigned)o.st->P->GetValue(),
                         (unsigned)o.st->S->GetValue());

        // --- dynamic MPR change detection (Phase 1).
        //     TAM or any other instruction that changes an MPR during execution
        //     invalidates the allowlist isolation: a page remapped to a writable
        //     bank (e.g. MPR0 -> 0xF8) would let a later store through that page
        //     bypass the per-bank write protection. Detect fail-closed before the
        //     next guest instruction can exploit the new mapping.
        for (int i = 0; i < 8; ++i) {
            if (o.memory->GetMpr(i) != initial_mpr[i]) {
                mpr_changed = true;
                ok = false;
                char buf[64];
                std::snprintf(buf, sizeof(buf), "mpr_changed_during_execution[mpr%d]", i);
                reason = buf;
                if (mode == "tam")
                    tam_sentinel_after = wram[0];  // physical WRAM byte STA $0000 would target
                break;
            }
        }
        if (!ok)
            break;

        if (mode != "mmio1804" && mode != "mmio1804-preblock" && o.hw_reachable()) {
            // mmio1804 modes deliberately keep MPR0 == $FF: the whole point of the
            // measurement is a Hardware-Page-reachable $1804.
            ok = false;
            reason = "hardware_page_made_reachable";
            break;
        }
        if (mode == "mmio1804-preblock" && o.cpu->BreakpointHit() &&
            !o.cpu->MemoryBreakpointHit()) {
            // Flag raised at the boundary while PC still points at the STA: the
            // prohibited store has NOT executed; never re-enter the core for it.
            execbp_hit = true;
            break;
        }
        if (o.cpu->MemoryBreakpointHit()) {
            o.cpu->GetBreakpointHitAddress(&detected_pc);
            detected = true;
            if (mode == "call") {
                char buf[72];
                std::snprintf(buf, sizeof(buf), "write_outside_allowlist_at_pc=%04X", detected_pc);
                ok = false;
                reason = buf;
                break;
            }
            if (mode == "pos-write") {
                ok = false;
                reason = "allowed_write_was_flagged";
                break;
            }
            break;  // neg-write: detection reached, verify prevention below
        }
        if (!stub_ok()) {
            ok = false;
            reason = "guest_stub_corrupted";
            break;
        }

        u16 pc = o.st->PC->GetValue();
        if (mode == "call") {
            if (!got_entry && pc == kEntry) {
                got_entry = true;
                continue;
            }
            if (got_entry && !got_callee && pc != kEntry) {
                got_callee = true;
                callee_pc = pc;
            }
            if (pc == kReturnAddr) {
                returned = true;
                break;
            }
        } else if (mode == "tam") {
            // Tam mode: MPR change must be detected after TAM, before STA runs.
            // If PC advances past the TAM without mpr_changed being set, the STA
            // would have written through the remapped page - a security failure.
            if (pc >= kGuestBase + 7) {
                ok = false;
                reason = "tam_mpr_change_not_detected";
                break;
            }
        } else {
            if (pc == kGuestBase + 5)
                break;  // reached BRA* without a detected store
            if ((mode == "st0" || mode == "st1" || mode == "st2") &&
                pc == kGuestBase + (mode == "st0" ? 2UL : 4UL))
                break;  // BRA* directly follows the ST opcode: the VDC side effect has landed
        }
    }

    if (ok && mode == "call") {
        if (!got_entry)
            { ok = false; reason = "jsr_did_not_reach_slot_entry"; }
        else if (!returned)
            { ok = false; reason = "budget_exhausted_before_rts"; }
    }
    if (mode == "tam" && !mpr_changed) {
        // Tam mode expects the MPR change to be detected; if it was not, the
        // subsequent STA would have written through the remapped page.
        ok = false;
        if (reason.empty())
            reason = "tam_mpr_change_not_detected";
    }

    // --- write-guard verdicts
    unsigned long guard_before = 0, guard_after = 0;
    if (mode == "mmio1804" || mode == "mmio1804-preblock") {
        // Read the device state after the core stopped; no state is written back.
        mmio_rst_after = o.memory->Read((u16)neg_addr);
        mmio_irq2_after = o.memory->Read(0x1403);
    }
    if (mode == "st0" || mode == "st1" || mode == "st2") {
        // Issue #198: VDC state after the core stopped; the side effect has
        // already landed (stop is at the instruction boundary AFTER the ST).
        HuC6270* vdc = o.core.GetHuC6270_1();
        HuC6270::HuC6270_State* vs = vdc->GetState();
        vdc_ar_after = (u8)*vs->AR;
        vdc_r_after = vs->R[st_reg];
    }
    if (mode == "neg-write") {
        // Forbidden store must be both detected and prevented.
        u8 cdram_after = o.memory->Read((u16)neg_addr);
        guard_before = cdram0;
        guard_after = cdram_after;
        if (!detected) {
            ok = false;
            reason = "forbidden_write_not_detected";
        } else if (cdram_after != cdram0) {
            ok = false;
            reason = "forbidden_write_not_prevented";
        }
    } else if (mode == "pos-write") {
        // Allowed control store must actually land (proves the guard is not a blanket drop).
        guard_before = wram0[neg_addr & 0x1FFF];
        guard_after = wram[neg_addr & 0x1FFF];
        if (guard_after != 0x5A) {
            ok = false;
            reason = "allowed_write_did_not_land";
        } else if (detected) {
            ok = false;
            reason = "allowed_write_was_flagged";
        }
    } else if (mode == "tam") {
        // Tam mode: the prohibited STA $0000 must not execute. After TAM remaps
        // MPR0 to the WRAM bank, CPU address $0000 maps to physical WRAM[0],
        // which holds the sentinel. The sentinel must be unchanged.
        guard_before = tam_sentinel_before;
        guard_after = tam_sentinel_after;
        if (guard_after != guard_before) {
            ok = false;
            reason = "tam_prohibited_store_modified_ram";
        }
    } else if (mode == "mmio1804") {
        // Classification (side effect before/after the stop) is made from the
        // device-state readback above, in the Python adapter. Here we only
        // fail closed when the store itself was never detected.
        if (!detected) {
            ok = false;
            reason = "mmio1804_write_not_detected";
        }
    } else if (mode == "mmio1804-preblock") {
        // Pre-side-effect block: the flag must have risen at the STA PC, the STA
        // must not have executed, and $1804 must be unchanged (fail closed).
        if (!execbp_hit) {
            ok = false;
            reason = "preblock_flag_not_raised_before_sta";
        } else if ((unsigned long)o.st->PC->GetValue() != kMmioStaPc) {
            ok = false;
            reason = "preblock_stopped_not_at_sta_pc";
        } else if (mmio_rst_after != mmio_rst_before) {
            ok = false;
            reason = "side_effect_landed_before_stop";
        }
    }

    FILE* out = stderr;
    std::fprintf(out, "{");
    std::fprintf(out, "\"mode\":\"%s\",", mode.c_str());
    std::fprintf(out, "\"status\":\"%s\",", ok ? "ok" : "rejected");
    std::fprintf(out, "\"reason\":\"%s\",", reason.c_str());
    std::fprintf(out, "\"before\":%s,", before_state.c_str());
    std::fprintf(out, "\"after\":%s,", state_json(o.st, o.memory).c_str());
    if (mode != "call" && mode != "mmio1804" && mode != "mmio1804-preblock") {
        std::fprintf(out, "\"guard_target\":%lu,\"guard_before\":%lu,\"guard_after\":%lu,",
                     neg_addr, guard_before, guard_after);
    }
    std::fprintf(out, "\"callee_pc\":%lu,", got_callee ? callee_pc : 0);
    std::fprintf(out, "\"entry_reached\":%s,", got_entry ? "true" : "false");
    std::fprintf(out, "\"returned\":%s,", returned ? "true" : "false");
    std::fprintf(out, "\"detected\":%s,", detected ? "true" : "false");
    std::fprintf(out, "\"mpr_changed\":%s,", mpr_changed ? "true" : "false");
    std::fprintf(out, "\"detected_pc\":%u,", (unsigned)detected_pc);
    std::fprintf(out, "\"stop_pc\":%u,", (unsigned)o.st->PC->GetValue());
    std::fprintf(out, "\"instructions_executed\":%lu,", o.executed);
    std::fprintf(out, "\"budget\":%lu,", budget);
    std::fprintf(out, "\"ram_diff\":[");
    std::vector<u8> wramN = wram_snap();
    bool first = true;
    for (unsigned long i = 0; i < 0x2000UL; ++i) {
        if (i >= kStubIndex && i < kStubIndex + code.size())
            continue;
        if (wramN[i] == wram0[i])
            continue;
        if (!first)
            std::fprintf(out, ",");
        first = false;
        // Report a CPU address: MPR1 maps page 1 (0x2000-0x3FFF) onto WRAM index 0.
        std::fprintf(out, "{\"address\":%lu,\"before\":%u,\"after\":%u}",
                     0x2000UL + i, (unsigned)wram0[i], (unsigned)wramN[i]);
    }
    std::fprintf(out, "]");
    if (mode == "mmio1804" || mode == "mmio1804-preblock") {
        std::fprintf(out, ",\"mmio1804_addr\":%u,\"mmio_rst_before\":%u,"
                          "\"mmio_rst_after\":%u,\"mmio_irq2_before\":%u,"
                          "\"mmio_irq2_after\":%u",
                     (unsigned)neg_addr, (unsigned)mmio_rst_before,
                     (unsigned)mmio_rst_after, (unsigned)mmio_irq2_before,
                     (unsigned)mmio_irq2_after);
        if (mode == "mmio1804-preblock")
            std::fprintf(out, ",\"execbp_hit\":%s,\"execbp_addr\":%u",
                         execbp_hit ? "true" : "false", (unsigned)kMmioStaPc);
    }
    if (mode == "st0" || mode == "st1" || mode == "st2") {
        // Issue #198: measured VDC side-effect observation for the ST path.
        std::fprintf(out, ",\"st_mode\":\"%s\",\"st_opcode_pc\":%u,\"vdc_reg_index\":%u,"
                          "\"vdc_ar_before\":%u,\"vdc_ar_after\":%u,"
                          "\"vdc_r_before\":%u,\"vdc_r_after\":%u",
                     mode.c_str(),
                     (unsigned)(mode == "st0" ? kGuestBase : kGuestBase + 2),
                     (unsigned)st_reg,
                     (unsigned)vdc_ar_before, (unsigned)vdc_ar_after,
                     (unsigned)vdc_r_before, (unsigned)vdc_r_after);
    }
    std::fprintf(out, "}");
    std::fflush(out);
    if (trace)
        std::fclose(trace);
    return 0;
}
